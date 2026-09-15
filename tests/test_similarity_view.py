import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
from PySide6.QtCore import QCoreApplication, QEvent
from qfluentwidgets import CheckBox

from fluent_ui.views.similarity_view import SimilarityDialog, GroupPreview, similarity_icon


class FakeStorage:
    def __init__(self, directory):
        self.data_dir = directory
        self.paths = []

    def get_all_categories(self):
        return {'测试分类': self.paths}

    def get_images_by_category(self, category):
        return list(self.paths)

    def get_image_to_categories_map(self):
        return {p: ['测试分类'] for p in self.paths}


class FakeGallery(QWidget):
    def __init__(self, directory):
        super().__init__()
        self.storage = FakeStorage(directory)
        self.deleted = []
        self.refreshed = 0

    def _start_async_delete(self, paths, message, callback):
        self.deleted.extend(paths)
        for p in paths:
            os.unlink(p)
            self.storage.paths.remove(p)
        callback()

    def on_images_changed(self):
        self.refreshed += 1


class SimilarityViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.gallery = FakeGallery(self.temp.name)
        for name in ('a.png', 'b.png', 'c.png'):
            path = str(Path(self.temp.name, name))
            Image.new('RGB', (32, 32), 'white').save(path)
            self.gallery.storage.paths.append(path)
        self.dialog = SimilarityDialog(self.gallery)

    def tearDown(self):
        self.dialog.shutdown()
        self.app.processEvents()
        self.dialog.close()
        self.gallery.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def wait_worker(self):
        deadline = time.monotonic() + 10
        while self.dialog.worker and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.dialog.worker, self.dialog.status.text())

    def test_entry_does_not_scan_or_select(self):
        self.assertIsNone(self.dialog.worker)
        self.assertIsNone(self.dialog.features)
        self.assertFalse(self.dialog.selected)
        self.assertFalse(self.dialog.delete_button.isEnabled())
        self.assertFalse(Path(self.temp.name, 'cache').exists())
        self.assertFalse(similarity_icon().isNull())
        self.dialog.show()
        self.dialog.reject()
        self.assertFalse(self.dialog.isVisible())

    def test_scan_rematch_and_scope_change(self):
        self.dialog.start_scan()
        self.wait_worker()
        self.assertEqual(len(self.dialog.groups), 1)
        self.assertEqual(len(self.dialog.groups[0]), 3)
        self.assertFalse(self.dialog.selected)
        boxes = self.dialog.scroll.widget().findChildren(CheckBox)
        boxes[0].setChecked(True)
        self.assertEqual(len(self.dialog.selected), 1)
        self.dialog.rematch()
        self.wait_worker()
        self.assertFalse(self.dialog.selected)
        self.dialog.scope.setCurrentIndex(1)
        self.assertIsNone(self.dialog.features)
        self.assertFalse(self.dialog.entries)

    def test_preview_navigation_and_delete_refresh(self):
        self.dialog.start_scan()
        self.wait_worker()
        group = self.dialog.groups[0]
        preview = GroupPreview(group, 0, self.dialog.categories, self.dialog)
        preview.navigate(1)
        self.assertEqual(preview.picture.feature.path, group[1].path)
        preview.done(0)
        preview.deleteLater()
        target = group[0].path
        self.dialog.select(target, True)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.dialog.delete_selected()
        self.assertTrue(os.path.exists(target))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.dialog.delete_selected()
        self.wait_worker()
        self.assertEqual(self.gallery.deleted, [target])
        self.assertEqual(self.gallery.refreshed, 1)
        self.assertEqual(len(self.dialog.groups[0]), 2)

    def test_close_cancels_worker_without_destroying_running_thread(self):
        self.dialog.show()
        self.dialog.start_scan()
        self.dialog.close()
        self.wait_worker()
        self.assertFalse(self.dialog.isVisible())

    def test_gif_preview_releases_file_on_stop(self):
        a = Image.new('RGB', (32, 32), 'white')
        b = Image.new('RGB', (32, 32), 'black')
        self.gallery.storage.paths.clear()
        for name in ('a.gif', 'b.gif'):
            path = str(Path(self.temp.name, name))
            a.save(path, save_all=True, append_images=[b], duration=100, loop=0)
            self.gallery.storage.paths.append(path)
        self.dialog.start_scan()
        self.wait_worker()
        picture = self.dialog.pictures[0]
        picture.play()
        self.assertIsNotNone(picture.movie)
        picture.stop()
        # Windows rejects deletion if a movie still owns an open file handle.
        os.unlink(picture.feature.path)

    def test_real_gallery_entry_and_storage_deletion(self):
        import gc
        from test_storage_single_frame_gif import _make_storage
        from fluent_ui.views.gallery_view import GalleryInterface
        storage = _make_storage(Path(self.temp.name, 'real'))
        # Distinct file hashes but same visual structure: retained by exact import,
        # grouped by visual cleanup.
        paths = []
        for name, color in [('x.png', 'white'), ('y.png', '#eeeeee')]:
            source = Path(self.temp.name, name)
            Image.new('RGB', (40, 40), color).save(source)
            saved, duplicate = storage.save_file(str(source))
            self.assertFalse(duplicate)
            paths.append(saved)
            storage.add_image_to_category(saved, '分类一')
            storage.add_image_to_category(saved, '分类二')
        gallery = GalleryInterface(storage, None, {})
        gallery.focus_timer.stop()
        gallery.btn_similarity.click()
        dialog = gallery._similarity_dialog
        self.assertIsNone(dialog.worker)
        self.assertLess(gallery.top_bar_layout.indexOf(gallery.btn_filter),
                        gallery.top_bar_layout.indexOf(gallery.btn_similarity))
        self.assertLess(gallery.top_bar_layout.indexOf(gallery.btn_similarity),
                        gallery.top_bar_layout.indexOf(gallery.btn_export))
        dialog.start_scan()
        deadline = time.monotonic() + 10
        while dialog.worker and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(len(dialog.groups), 1)
        dialog.select(paths[0], True)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.delete_selected()
        deadline = time.monotonic() + 10
        while (dialog.deleting or dialog.worker or gallery.delete_thread) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(dialog.deleting)
        self.assertIsNone(dialog.worker)
        self.assertFalse(os.path.exists(paths[0]))
        self.assertTrue(os.path.exists(paths[1]))
        for category in ('分类一', '分类二'):
            self.assertNotIn(paths[0], storage.get_images_by_category(category))
            self.assertIn(paths[1], storage.get_images_by_category(category))
        self.assertFalse(dialog.groups)
        dialog.close()
        gallery.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()


if __name__ == '__main__':
    unittest.main()
