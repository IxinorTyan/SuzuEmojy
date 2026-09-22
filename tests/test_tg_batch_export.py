import gc
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from services.tg_batch_export import TGPackageBatchRunner, package_filename_stem
from services.tg_downloader import StickerItem, StickerPackInfo, StickerUnavailableError
from fluent_ui.components.tg_batch_dialog import BatchPackageWorker
from fluent_ui.views.tg_sticker_view import ImportPackThread
from test_storage_single_frame_gif import _make_storage


def pack(name, title=None, count=1):
    return StickerPackInfo(name, title or name, "regular", False, False,
                           [StickerItem(i, str(i), str(i)) for i in range(count)])


class BatchPipelineTest(unittest.TestCase):
    def test_windows_package_filenames(self):
        cases = {
            'みらつ初音贴纸2 :: @fStikBot': 'みらつ初音贴纸2 __ @fStikBot',
            'a<>:"/\\|?*\x00\n': 'a' + '_' * 11,
            'title.  ': 'title',
            ' . ': 'package',
            'con': '_con',
            'LPT1.backup': '_LPT1.backup',
            'COM¹': '_COM¹',
            '普通标题 @bot': '普通标题 @bot',
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(package_filename_stem(title), expected)

    def test_stages_overlap_but_processing_keeps_input_order(self):
        parsing_b, downloading_b, processing_a = Event(), Event(), Event()
        instances, processed, paths = [], [], []

        class Downloader:
            def __init__(self):
                self.session = Mock()
                instances.append(self)

            def get_sticker_set(self, name):
                self.name = name
                if name == "B":
                    parsing_b.set()
                return pack(name)

            def get_file_path(self, _):
                return "file.webp"

            def download_file_bytes(self, _):
                if self.name == "A":
                    if not parsing_b.wait(3):
                        raise AssertionError("B was not parsed while A was downloading")
                if self.name == "B":
                    if not processing_a.wait(3):
                        raise AssertionError("A processing did not overlap B download")
                    downloading_b.set()
                return self.name.encode()

        def process(item, cancel):
            if item.pack.name == "A":
                processing_a.set()
                self.assertTrue(downloading_b.wait(3))
            path = Path(item.sources[0])
            paths.append(path)
            self.assertEqual(path.read_bytes(), item.pack.name.encode())
            processed.append(item.pack.name)
            return dict(message="ok")

        result = TGPackageBatchRunner(["A", "B", "C"], Downloader, process, Mock()).run()
        self.assertEqual(processed, ["A", "B", "C"])
        self.assertEqual([r["status"] for r in result], ["done"] * 3)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(all(instance.session.close.call_count == 1 for instance in instances))

    def test_parse_and_process_errors_do_not_stop_later_packs(self):
        downloader = SimpleNamespace(
            session=Mock(),
            get_sticker_set=lambda name: pack(name),
            get_file_path=lambda _: "file.webp", download_file_bytes=lambda _: b"image",
        )

        def parse(name):
            if name == "bad-link":
                raise ValueError("invalid link")
            return pack(name)

        downloader.get_sticker_set = parse

        def process(item, cancel):
            if item.pack.name == "bad-conversion":
                raise RuntimeError("conversion failed")
            return dict(message="ok")

        results = TGPackageBatchRunner(
            ["bad-link", "bad-conversion", "good"], lambda: downloader, process, Mock(),
        ).run()
        self.assertEqual([r["status"] for r in results], ["failed", "failed", "done"])

    def test_cancellation_unblocks_full_queues_and_cleans_downloads(self):
        cancel, prefetched = Event(), Event()
        instances, paths = [], []

        class Downloader:
            def __init__(self):
                self.session = Mock()
                instances.append(self)

            def get_sticker_set(self, name):
                return pack(name)

            def get_file_path(self, _):
                return "file.webp"

            def download_file_bytes(self, _):
                return b"image"

        def progress(index, stage, *args):
            if index == 2 and stage == "ready":
                prefetched.set()

        def process(item, event):
            paths.extend(Path(p) for p in item.sources.values())
            self.assertTrue(prefetched.wait(3))
            event.set()
            raise InterruptedError()

        results = TGPackageBatchRunner(
            list("ABCDEFGH"), Downloader, process, progress, cancel,
        ).run()
        self.assertTrue(all(r["status"] == "cancelled" for r in results))
        self.assertTrue(all(not path.parent.parent.exists() for path in paths))
        self.assertTrue(all(instance.session.close.call_count == 1 for instance in instances))


class BatchPackageIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.storage = _make_storage(self.root)
        data = io.BytesIO()
        Image.new("RGBA", (12, 10), (255, 0, 0, 128)).save(data, "PNG")
        self.data = data.getvalue()

    def test_invalid_titles_export_without_overwrite_and_keep_metadata(self):
        payload = self.data
        titles = ['みらつ初音贴纸2 :: @fStikBot', 'same:title', 'same?title', 'CON']
        (self.root / 'same_title.zip').write_bytes(b'existing')

        class Downloader:
            def __init__(self, **kwargs):
                self.session = Mock()

            def get_sticker_set(self, name):
                return pack(name, titles[int(name)])

            def get_file_path(self, file_id):
                return file_id

            def download_file_bytes(self, file_id):
                return payload

        worker = BatchPackageWorker(['0', '1', '2', '3'], self.root, {}, self.storage, ImportPackThread)
        with patch('fluent_ui.components.tg_batch_dialog.TGStickerDownloader', Downloader):
            worker.run()
        self.assertIsNone(worker.error)
        self.assertEqual([r['status'] for r in worker.results], ['done'] * 4)
        self.assertEqual((self.root / 'same_title.zip').read_bytes(), b'existing')
        expected = ['みらつ初音贴纸2 __ @fStikBot.zip', 'same_title (2).zip',
                    'same_title (3).zip', '_CON.zip']
        for title, filename, result in zip(titles, expected, worker.results):
            self.assertEqual(Path(result['path']).name, filename)
            with zipfile.ZipFile(result['path']) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['package_id'], title)
                self.assertEqual(json.loads(archive.read('catalog.json'))['categories'][0]['name'], title)

    def test_batch_exports_distinct_packages_with_source_and_skips(self):
        (self.root / "Same title.zip").write_bytes(b"existing")
        blue = io.BytesIO()
        Image.new("RGBA", (12, 10), (0, 0, 255, 128)).save(blue, "PNG")
        payloads = {"A": self.data, "B": blue.getvalue()}
        downloads = []

        class Downloader:
            def __init__(self, **kwargs):
                self.session = Mock()

            def get_sticker_set(self, name):
                if name == "bad":
                    raise ValueError("invalid link")
                self.name = name
                return pack(name, "Same title", 2)

            def get_file_path(self, file_id):
                return file_id

            def download_file_bytes(self, file_id):
                downloads.append((self.name, file_id))
                if file_id == "1":
                    raise TimeoutError("download timeout")
                return payloads[self.name]

        worker = BatchPackageWorker(["A", "bad", "B"], self.root, {}, self.storage, ImportPackThread)
        with patch("fluent_ui.components.tg_batch_dialog.TGStickerDownloader", Downloader):
            worker.run()
        self.assertIsNone(worker.error)
        self.assertEqual([r["status"] for r in worker.results], ["done", "failed", "done"])
        self.assertEqual(len(downloads), 4)  # Import consumes the prefetched files without redownloading.
        self.assertEqual((self.root / "Same title.zip").read_bytes(), b"existing")
        for number, name in [(2, "A"), (3, "B")]:
            with zipfile.ZipFile(self.root / f"Same title ({number}).zip") as archive:
                self.assertEqual(archive.read("TG.txt").decode().strip(), f"https://t.me/addstickers/{name}")
                self.assertEqual(json.loads(archive.read("manifest.json"))["counts"]["resources"], 1)
        self.assertEqual([r["skipped"] for r in worker.results if r["status"] == "done"], [1, 1])
        for result in worker.results:
            if result["status"] == "done":
                self.assertIn("#2", result["message"])
                self.assertIn("download timeout", result["message"])

    def test_cancel_during_second_export_keeps_first_zip(self):
        payload = self.data

        class Downloader:
            def __init__(self, **kwargs):
                self.session = Mock()

            def get_sticker_set(self, name):
                return pack(name)

            def get_file_path(self, file_id):
                return file_id

            def download_file_bytes(self, file_id):
                return payload

        worker = BatchPackageWorker(["A", "B"], self.root, {}, self.storage, ImportPackThread)
        worker.progress.connect(lambda index, stage, *args: worker.cancel()
                                if index == 1 and stage == "export" else None)
        with patch("fluent_ui.components.tg_batch_dialog.TGStickerDownloader", Downloader):
            worker.run()
        self.assertIsNone(worker.error)
        self.assertEqual([r["status"] for r in worker.results], ["done", "cancelled"])
        self.assertTrue((self.root / "A.zip").exists())
        self.assertFalse((self.root / "B.zip").exists())
        self.assertEqual(list(self.root.glob("tg_package_*")), [])
