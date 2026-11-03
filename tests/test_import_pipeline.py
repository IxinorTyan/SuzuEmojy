import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from PIL import Image
from PySide6.QtWidgets import QApplication, QWidget

from services.import_pipeline import ImportThread, scan_folders
from test_storage_single_frame_gif import _make_storage


def image(path, color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (24, 24), color).save(path)
    return str(path)


def test_recursive_scan_flattens_and_orders(tmp_path):
    root = tmp_path / '表情'
    cover = image(root / '封面.png')
    second = image(root / '猫' / '2.png')
    tenth = image(root / '猫' / '10.png')
    deep = image(root / '猫' / '动图' / '更深' / '1.png')
    dog = image(root / '狗' / '搞笑' / '1.png')
    (root / '空目录').mkdir()
    (root / 'readme.txt').write_text('text')
    scan = scan_folders([str(root)], [], '原分类', ('.png',))
    assert dict(scan.items) == {cover: '表情', second: '猫', tenth: '猫', deep: '猫', dog: '狗'}
    paths = [p for p, _ in scan.items]
    assert paths.index(second) < paths.index(tenth) < paths.index(deep)
    assert scan.stats['non_images'] == 1
    assert scan.stats['image_count'] == 5


def test_missing_root_and_cancellation(tmp_path):
    good = image(tmp_path / 'good' / 'a.png')
    scan = scan_folders([str(tmp_path / 'missing'), str(tmp_path / 'good')], [], None, ('.png',))
    assert scan.items == [(good, 'good')]
    assert len(scan.stats['issues']) == 1
    assert not scan_folders([str(tmp_path)], [], None, ('.png',), lambda: True).items


def test_parallel_prepare_serial_commit_deduplicates(tmp_path):
    storage = _make_storage(tmp_path / 'library')
    paths = [image(tmp_path / 'sources' / f'{i}.png') for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared = list(pool.map(storage.prepare_file, paths))
        results = list(pool.map(storage.commit_prepared, prepared))
    assert sum(not duplicate for _, duplicate in results) == 1
    assert len({path for path, _ in results}) == 1
    assert len(os.listdir(storage.images_dir)) == 1


def test_first_successful_image_icon_and_duplicate_membership(tmp_path):
    storage = _make_storage(tmp_path / 'library')
    root = tmp_path / '表情包'
    (root / '猫').mkdir(parents=True)
    (root / '猫' / '1.png').write_bytes(b'corrupt')
    image(root / '猫' / '2.png')
    image(root / '猫' / '3.png', 'blue')
    image(root / '狗' / '1.png')
    worker = ImportThread([], storage, '未分类', folders=[str(root)])
    done = Mock()
    worker.completed.connect(done)
    worker.run()
    done.assert_called_once_with(2, 1, 1)
    cats = storage.get_images_by_category('猫')
    dogs = storage.get_images_by_category('狗')
    assert len(cats) == 2 and dogs == [cats[0]]
    assert os.path.normcase(storage.get_category_icon('猫')) == cats[0]
    assert os.path.normcase(storage.get_category_icon('狗')) == dogs[0]
    assert '表情包' not in storage.get_all_categories()
    storage.set_category_icon('猫', '🐱')
    worker = ImportThread([], storage, '未分类', folders=[str(root)])
    worker.run()
    assert storage.get_category_icon('猫') == '🐱'
    assert storage.get_images_by_category('猫') == cats


def test_actual_background_cancellation_preserves_remaining_sources(tmp_path):
    app = QApplication.instance() or QApplication([])
    storage = _make_storage(tmp_path / 'library')
    paths = [image(tmp_path / 'source' / f'{i}.png') for i in range(40)]
    original = storage.prepare_file
    started = threading.Event()

    def slow(path):
        started.set()
        time.sleep(.03)
        return original(path)

    storage.prepare_file = slow
    worker = ImportThread(paths, storage, '猫', delete_after=True)
    worker.start()
    assert started.wait(5)
    worker.requestInterruption()
    assert worker.wait(5000)
    app.processEvents()
    assert worker.was_cancelled
    assert all(os.path.isfile(path) for path in paths)


def test_preparation_does_not_hold_storage_lock(tmp_path):
    storage = _make_storage(tmp_path / 'library')
    path = image(tmp_path / 'source.png')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with storage.lock:
            prepared = pool.submit(storage.prepare_file, path).result(timeout=3)
    assert prepared[1] == '.png'


def test_adaptive_parallelism_preserves_order(monkeypatch, tmp_path):
    import services.import_pipeline as pipeline
    monkeypatch.setattr(pipeline.os, 'cpu_count', lambda: 8)
    monkeypatch.setattr(pipeline, 'memory_budget', lambda: 256 * 1024**2)
    monkeypatch.setattr(pipeline, 'estimate_memory', lambda path, budget: 1024)
    storage = Mock()
    active = peak = 0
    lock = threading.Lock()

    def prepare(path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.006 if int(path) % 2 else .012)
        with lock:
            active -= 1
        return path

    storage.prepare_file.side_effect = prepare
    storage.commit_prepared.side_effect = lambda prepared, **kwargs: (prepared, False)
    paths = [str(i) for i in range(80)]
    worker = ImportThread(paths, storage, '猫')
    worker.run()
    assert peak >= 2
    assert [call.args[0] for call in storage.commit_prepared.call_args_list] == paths
    storage.assign_missing_category_icon.assert_called_once_with('猫', '0')


def test_failed_external_source_is_retained(tmp_path):
    storage = _make_storage(tmp_path / 'library')
    source = tmp_path / 'broken.png'
    source.write_bytes(b'corrupt')
    worker = ImportThread([str(source)], storage, '猫', delete_after=True)
    worker.run()
    assert source.exists()
    assert not storage.get_all_categories()


def test_ui_queue_completes_two_batches(tmp_path):
    from fluent_ui.views.gallery_view import GalleryInterface

    app = QApplication.instance() or QApplication([])

    class Harness(GalleryInterface):
        def __init__(self, storage):
            QWidget.__init__(self)
            self.storage = storage
            self.current_category = '猫'
            self.import_thread = None
            self._import_queue = []
            self._import_shutting_down = False
            self._import_info_bar = None
            self.sidebar = Mock()
            self.on_images_changed = Mock()
            self.show_success = Mock()
            self.show_error = Mock()

    storage = _make_storage(tmp_path / 'library')
    view = Harness(storage)
    first = image(tmp_path / 'a.png')
    second = image(tmp_path / 'b.png', 'blue')
    view._start_background_import([first])
    assert view._import_info_bar is not None
    view._start_background_import([second], silent=True)
    # A repeated inbox poll must not enqueue the same source again.
    view._start_background_import([second], silent=True)
    assert len(view._import_queue) == 1
    deadline = time.monotonic() + 10
    while view.import_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    try:
        assert view.import_thread is None
        assert len(storage.get_images_by_category('猫')) == 2
        assert view.on_images_changed.call_count == 2
        view.show_error.assert_not_called()
    finally:
        view.shutdown()
        view.deleteLater()


def test_missing_image_icon_is_repaired(tmp_path):
    storage = _make_storage(tmp_path / 'library')
    source = image(tmp_path / 'source.png')
    storage.set_category_icon('猫', str(tmp_path / 'deleted.png'))
    worker = ImportThread([source], storage, '猫')
    worker.run()
    assert os.path.isfile(storage.get_category_icon('猫'))
