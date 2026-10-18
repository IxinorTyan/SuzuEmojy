import os
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from fluent_ui.components.emoji_card import EmojiCard
from fluent_ui.components.responsive_scroll_area import ResponsiveScrollArea


class MiddleScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = ResponsiveScrollArea()
        self.area.resize(600, 400)
        self.content = QWidget()
        self.content.setMinimumSize(600, 5000)
        self.card = EmojiCard('unloaded.png', size=80, load_image=False, parent=self.content)
        self.card.move(20, 520)
        self.clicked = Mock()
        self.card.clicked.connect(self.clicked)
        self.area.setWidget(self.content)
        self.area.show()
        self.app.processEvents()
        self.bar = self.area.verticalScrollBar()
        self.bar.setValue(500)
        self.middle = self.area._middle_scroller
        self.origin = self.card.mapToGlobal(QPoint(40, 40))

    def tearDown(self):
        self.area.close()
        self.area.deleteLater()
        self.app.processEvents()
        self.assertIsNone(QApplication.overrideCursor())

    def mouse(self, kind, button, position=None, buttons=Qt.NoButton):
        position = position or self.origin
        local = self.card.mapFromGlobal(position)
        event = QMouseEvent(kind, QPointF(local), QPointF(position), button, buttons, Qt.NoModifier)
        QApplication.sendEvent(self.card, event)

    def click_middle(self):
        self.mouse(QEvent.MouseButtonPress, Qt.MiddleButton, buttons=Qt.MiddleButton)
        self.mouse(QEvent.MouseButtonRelease, Qt.MiddleButton)
        self.middle.timer.stop()  # Advance deterministically, without real cursor polling.

    def test_hold_middle_drags_content_and_release_stops_without_click(self):
        self.mouse(QEvent.MouseButtonPress, Qt.MiddleButton, buttons=Qt.MiddleButton)
        self.mouse(QEvent.MouseMove, Qt.NoButton, self.origin - QPoint(0, 60), Qt.MiddleButton)
        self.assertEqual(self.bar.value(), 560)
        self.assertTrue(self.area.is_scrolling)
        self.mouse(QEvent.MouseButtonRelease, Qt.MiddleButton, self.origin - QPoint(0, 60))
        self.assertFalse(self.middle.active)
        self.assertFalse(self.middle.timer.isActive())
        self.clicked.assert_not_called()

    def test_click_latches_autoscroll_dead_zone_reversal_and_limits(self):
        self.click_middle()
        self.assertTrue(self.middle.active)
        self.middle._advance(self.origin + QPoint(0, 8), .04)
        self.assertEqual(self.bar.value(), 500)
        self.middle._advance(self.origin + QPoint(0, 35), .04)
        self.assertEqual(self.bar.value(), 512)
        self.middle._advance(self.origin - QPoint(0, 35), .04)
        self.assertEqual(self.bar.value(), 500)
        self.bar.setValue(self.bar.maximum())
        self.middle._advance(self.origin + QPoint(0, 5000), 10)
        self.assertEqual(self.bar.value(), self.bar.maximum())
        self.area._finish_scrolling()
        self.assertTrue(self.area.is_scrolling, '中键模式不能在鼠标暂时静止时恢复加载')

    def test_cancel_click_does_not_select_or_paste_card(self):
        self.card.set_selectable(True)
        self.click_middle()
        self.mouse(QEvent.MouseButtonPress, Qt.LeftButton, buttons=Qt.LeftButton)
        self.mouse(QEvent.MouseButtonRelease, Qt.LeftButton)
        self.assertFalse(self.middle.active)
        self.assertFalse(self.card.is_selected)
        self.clicked.assert_not_called()

    def test_second_middle_click_cancels_instead_of_starting_again(self):
        self.click_middle()
        self.mouse(QEvent.MouseButtonPress, Qt.MiddleButton, buttons=Qt.MiddleButton)
        self.mouse(QEvent.MouseButtonRelease, Qt.MiddleButton)
        self.assertFalse(self.middle.active)

    def test_fast_double_middle_click_also_cancels(self):
        self.click_middle()
        self.mouse(QEvent.MouseButtonDblClick, Qt.MiddleButton, buttons=Qt.MiddleButton)
        self.mouse(QEvent.MouseButtonRelease, Qt.MiddleButton)
        self.assertFalse(self.middle.active)

    def test_escape_and_window_deactivation_cancel(self):
        self.click_middle()
        QApplication.sendEvent(self.card, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertFalse(self.middle.active)
        self.click_middle()
        QApplication.sendEvent(self.area.window(), QEvent(QEvent.WindowDeactivate))
        self.assertFalse(self.middle.active)
        self.assertIsNone(QApplication.overrideCursor())

    def test_hide_cancels_and_next_show_does_not_resume(self):
        self.click_middle()
        self.area.hide()
        self.assertFalse(self.middle.active)
        self.assertFalse(self.middle.timer.isActive())
        self.area.show()
        self.assertFalse(self.middle.active)

    def test_middle_does_not_hijack_other_widgets(self):
        outside = QWidget()
        outside.show()
        try:
            position = outside.mapToGlobal(QPoint(5, 5))
            event = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(position), Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier)
            QApplication.sendEvent(outside, event)
            self.assertFalse(self.middle.active)
        finally:
            outside.close()
            outside.deleteLater()


if __name__ == '__main__':
    unittest.main()
