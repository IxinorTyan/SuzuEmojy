import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QGridLayout, QSplitter
from qfluentwidgets import ScrollArea

from fluent_ui.views.gallery_view import GalleryInterface
from fluent_ui.main_window import MainWindow


class ResizeGallery(GalleryInterface):
    """复用正式事件处理和重排逻辑，不访问用户配置、图片库或收件箱。"""

    def __init__(self):
        QWidget.__init__(self)
        self.config = {'thumbnail_size': 120}
        self.LAYOUT_SPACING = 10
        self.rearrangements = []
        self._responsive_layout_timer = QTimer(self)
        self._responsive_layout_timer.setSingleShot(True)
        self._responsive_layout_timer.setInterval(50)
        self._responsive_layout_timer.timeout.connect(self._trigger_responsive_layout)
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(self.splitter)
        sidebar = QWidget()
        sidebar.setMinimumWidth(60)
        sidebar.setMaximumWidth(400)
        self.splitter.addWidget(sidebar)
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.splitter.addWidget(self.scroll_area)
        self.grid_container = QWidget()
        self.gallery_layout = QGridLayout(self.grid_container)
        self.gallery_layout.setSpacing(10)
        self.gallery_layout.setContentsMargins(16, 8, 16, 16)
        self.gallery_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll_area.setWidget(self.grid_container)
        self.scroll_area.viewport().installEventFilter(self)
        self._all_card_widgets = []
        for _ in range(80):
            card = QWidget()
            card.setFixedSize(120, 120)
            self._all_card_widgets.append(card)
        self.splitter.setSizes([140, 800])

    def _rearrange_gallery(self, columns):
        self.rearrangements.append(columns)
        super()._rearrange_gallery(columns)


class WindowResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ResizeGallery()
        self.view.resize(860, 640)
        self.view.show()
        QTest.qWait(150)
        self.view.rearrangements.clear()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def assert_layout_matches_viewport(self):
        expected = max(1, (self.view.scroll_area.viewport().width() - 32) // 130)
        self.assertEqual(self.view._current_columns, expected)
        self.assertEqual(self.view.gallery_layout.count(), 80)
        for index, card in enumerate(self.view._all_card_widgets):
            self.assertIs(self.view.gallery_layout.itemAtPosition(index // expected, index % expected).widget(), card)

    def test_resize_burst_rearranges_only_after_settling(self):
        for width in (1000, 1200, 1400, 1600):
            self.view.resize(width, 640)
            self.app.processEvents()
        self.assertEqual(self.view.rearrangements, [])
        QTest.qWait(150)
        self.assertEqual(len(self.view.rearrangements), 1)
        self.assert_layout_matches_viewport()

    def test_expand_then_restore(self):
        for width in (1600, 650, 1600, 650):
            self.view.resize(width, 640)
            QTest.qWait(150)
            self.assert_layout_matches_viewport()

    def test_viewport_only_resize_updates_columns(self):
        self.view.resize(1600, 640)
        QTest.qWait(150)
        self.view.rearrangements.clear()
        self.view.splitter.setSizes([400, 1200])
        QTest.qWait(150)
        self.assertEqual(len(self.view.rearrangements), 1)
        self.assert_layout_matches_viewport()

    def test_same_size_show_does_not_rearrange(self):
        self.view.hide()
        self.view.show()
        QTest.qWait(150)
        self.assertEqual(self.view.rearrangements, [])

    def test_rounded_corners_do_not_recalculate_native_frame(self):
        window = SimpleNamespace(winId=lambda: 123)
        with patch('fluent_ui.main_window.user32') as native, patch('fluent_ui.main_window.dwmapi') as dwm:
            native.IsWindow.return_value = True
            dwm.DwmSetWindowAttribute.return_value = 0
            self.assertTrue(MainWindow.apply_rounded_corners(window))
            dwm.DwmSetWindowAttribute.assert_called_once()
            native.SetWindowPos.assert_not_called()


if __name__ == '__main__':
    unittest.main()
