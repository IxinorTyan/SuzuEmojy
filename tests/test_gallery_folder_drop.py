import os
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QMimeData, QUrl

from fluent_ui.views.gallery_view import GalleryInterface, ImportThread


def make_view():
    view = SimpleNamespace(
        auto_scroll_timer=Mock(),
        import_thread=None,
        current_category='原分类',
        storage=Mock(SUPPORTED_FORMATS=('.png', '.gif'), images_dir='unused'),
        sidebar=Mock(),
        show_error=Mock(),
        _start_background_import=Mock(),
    )
    view._import_dropped_folders = lambda folders, files: GalleryInterface._import_dropped_folders(view, folders, files)
    return view


def test_drop_multiple_folders_and_file_as_one_batch(tmp_path):
    first = tmp_path / '猫'
    second = tmp_path / '狗'
    empty = tmp_path / '空文件夹'
    for folder in (first, second, empty):
        folder.mkdir()
    cat = first / 'cat.PNG'
    dog = second / 'dog.gif'
    loose = tmp_path / 'loose.png'
    for path in (cat, dog, loose, first / 'readme.txt'):
        path.touch()
    nested = first / '子文件夹'
    nested.mkdir()
    (nested / 'ignored.png').touch()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in (first, second, empty, loose)])
    event = Mock()
    event.mimeData.return_value = mime
    view = make_view()

    GalleryInterface.dropEvent(view, event)

    event.accept.assert_called_once()
    view._start_background_import.assert_called_once()
    args, kwargs = view._start_background_import.call_args
    assert {os.path.normpath(path) for path in args[0]} == {str(cat), str(dog), str(loose)}
    categories = {os.path.normpath(path): category for path, category in kwargs['file_categories'].items()}
    assert categories == {str(cat): '猫', str(dog): '狗', str(loose): '原分类'}
    stats = kwargs['folder_stats']
    assert stats['subdirs'] == 1
    assert stats['non_images'] == 1
    assert stats['image_count'] == 3
    assert len(stats['issues']) == 1


def test_busy_import_does_not_create_categories(tmp_path):
    view = make_view()
    view.import_thread = Mock()
    view.import_thread.isRunning.return_value = True
    view._import_dropped_folders([str(tmp_path)], [])
    view.storage.add_category.assert_not_called()
    view._start_background_import.assert_not_called()
    view.show_error.assert_called_once()


def test_unreadable_folder_does_not_block_later_folder(tmp_path):
    image = tmp_path / 'valid.png'
    image.touch()
    view = make_view()
    view._import_dropped_folders([str(tmp_path / 'missing'), str(tmp_path)], [])
    args, kwargs = view._start_background_import.call_args
    assert args[0] == [str(image)]
    assert len(kwargs['folder_stats']['issues']) == 1


def test_import_thread_routes_duplicate_to_each_folder():
    storage = Mock()
    storage.save_file.side_effect = [('saved.png', False), ('saved.png', True)]
    worker = ImportThread(
        ['a.png', 'b.png'], storage, '原分类',
        file_categories={'a.png': '猫', 'b.png': '狗'},
    )
    finished = Mock()
    progress = Mock()
    worker.finished.connect(finished)
    worker.progress.connect(progress)
    worker.run()
    assert [call.args for call in storage.add_image_to_category.call_args_list] == [
        ('saved.png', '猫'), ('saved.png', '狗'),
    ]
    finished.assert_called_once_with(1, 1, 0)
    assert [call.args for call in progress.call_args_list] == [(1, 2), (2, 2)]


def test_single_file_import_keeps_default_category():
    storage = Mock()
    storage.save_file.return_value = ('saved.png', False)
    worker = ImportThread(['a.png'], storage, '原分类')
    worker.run()
    storage.add_image_to_category.assert_called_once_with('saved.png', '原分类')
