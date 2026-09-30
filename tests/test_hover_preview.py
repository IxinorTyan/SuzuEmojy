import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QEvent, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget, QScrollArea, QVBoxLayout

from fluent_ui.components.hover_preview_controller import HoverPreviewController


MODULE = 'fluent_ui.components.hover_preview_controller'


class Card(QWidget):
    hover_started = Signal(str)
    image_path = 'test.png'


class Popup(QWidget):
    def __init__(self):
        super().__init__()
        self.calls = []

    def show_preview(self, *args):
        self.calls.append(args)
        self.show()

    def hide_preview(self):
        self.hide()


class HoverPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.get_preview_metadata = Mock(return_value=('分类', '关键词'))
        self.owner.get_preview_size = Mock(return_value=320)
        self.owner.resize(400, 320)
        layout = QVBoxLayout(self.owner)
        self.scroll = QScrollArea(self.owner)
        layout.addWidget(self.scroll)
        self.content = QWidget()
        self.content.resize(600, 900)
        self.scroll.setWidget(self.content)
        self.card = Card(self.content)
        self.card.setGeometry(10, 10, 100, 100)
        self.card.show()
        self.owner.show()
        self.app.processEvents()
        self.popup = Popup()
        self.controller = HoverPreviewController(self.owner, self.popup, self.scroll.viewport())
        self.point = self.card.mapToGlobal(QPoint(20, 20))
        # 可重复地模拟光标和命中结果，不移动用户的真实鼠标。
        self.cursor = patch(MODULE + '.QCursor.pos', return_value=self.point).start()
        self.hit = patch(MODULE + '.QApplication.widgetAt', return_value=self.card).start()
        self.native = patch(MODULE + '._native_window_under_cursor', return_value=int(self.owner.winId())).start()
        patch(MODULE + '.QApplication.mouseButtons', return_value=Qt.NoButton).start()

    def tearDown(self):
        self.controller.shutdown()
        self.owner.close()
        self.popup.close()
        self.owner.deleteLater()
        self.popup.deleteLater()
        self.app.processEvents()
        patch.stopall()

    def show_preview(self):
        self.controller.enter(self.card, self.card.image_path)
        self.controller.hover_timer.stop()
        self.controller._on_delay_timeout()

    def test_unfocused_window_can_preview(self):
        with patch.object(self.owner, 'isActiveWindow', return_value=False):
            self.show_preview()
        self.assertTrue(self.popup.isVisible())
        self.assertTrue(self.controller.validation_timer.isActive())

    def test_cursor_leaves_before_timeout_without_leave_event(self):
        self.controller.enter(self.card, self.card.image_path)
        self.cursor.return_value = self.owner.mapToGlobal(QPoint(450, 450))
        self.controller._on_delay_timeout()
        self.assertFalse(self.popup.calls)
        self.assertIsNone(self.controller.current_hover_card)

    def test_card_below_viewport_cannot_preview(self):
        height = self.scroll.viewport().height()
        self.card.move(10, height - 20)
        self.controller.settle_timer.stop()
        self.cursor.return_value = self.card.mapToGlobal(QPoint(20, 50))
        self.assertTrue(self.card.rect().contains(self.card.mapFromGlobal(self.cursor.return_value)))
        self.show_preview()
        self.assertFalse(self.popup.calls)

    def test_lost_leave_after_show_is_caught_by_validation(self):
        self.show_preview()
        self.cursor.return_value = self.owner.mapToGlobal(QPoint(450, 450))
        self.controller._validate_visible_preview()
        self.assertFalse(self.popup.isVisible())
        self.assertFalse(self.controller.validation_timer.isActive())

    def test_other_application_covering_card_blocks_preview(self):
        with patch(MODULE + '.QGuiApplication.platformName', return_value='windows'):
            self.native.return_value = int(self.owner.winId()) + 123
            self.show_preview()
        self.assertFalse(self.popup.calls)

    def test_new_occlusion_hides_visible_preview(self):
        self.show_preview()
        self.hit.return_value = self.owner
        self.controller._validate_visible_preview()
        self.assertFalse(self.popup.isVisible())

    def test_scroll_hides_then_restarts_full_delay_without_mouse_movement(self):
        self.show_preview()
        self.scroll.verticalScrollBar().setValue(5)
        self.assertFalse(self.popup.isVisible())
        self.assertTrue(self.controller.settle_timer.isActive())
        self.controller.settle_timer.stop()
        self.controller._resume_under_cursor()
        self.assertIs(self.controller.current_hover_card, self.card)
        self.assertTrue(self.controller.hover_timer.isActive())
        self.assertFalse(self.popup.isVisible())

    def test_stale_leave_does_not_cancel_new_card(self):
        other = Card(self.content)
        other.show()
        self.controller.settle_timer.stop()
        self.controller.enter(self.card, self.card.image_path)
        self.controller.enter(other, 'other.png')
        self.controller.leave(self.card)
        self.assertIs(self.controller.current_hover_card, other)

    def test_deleted_card_cancels_pending_preview(self):
        import shiboken6
        self.controller.enter(self.card, self.card.image_path)
        shiboken6.delete(self.card)
        self.assertFalse(self.controller.hover_timer.isActive())
        self.assertIsNone(self.controller.current_hover_card)

    def test_window_resize_hides_visible_preview(self):
        self.show_preview()
        self.owner.resize(420, 340)
        self.assertFalse(self.popup.isVisible())
        self.assertTrue(self.controller.settle_timer.isActive())

    def test_page_hidden_cancels_pending_preview(self):
        self.controller.enter(self.card, self.card.image_path)
        self.owner.hide()
        self.assertFalse(self.controller.hover_timer.isActive())
        self.assertIsNone(self.controller.current_hover_card)

    def test_context_menu_cancels_pending_preview(self):
        self.controller.enter(self.card, self.card.image_path)
        self.controller.eventFilter(self.card, QEvent(QEvent.ContextMenu))
        self.assertFalse(self.controller.hover_timer.isActive())

    def test_selection_mode_and_modal_dialog_block_preview(self):
        self.owner.is_selection_mode = True
        self.show_preview()
        self.assertFalse(self.popup.calls)
        self.owner.is_selection_mode = False
        with patch(MODULE + '.QApplication.activeModalWidget', return_value=self.owner):
            self.show_preview()
        self.assertFalse(self.popup.calls)

    def test_cursor_leaves_during_metadata_lookup(self):
        def metadata(_):
            self.cursor.return_value = self.owner.mapToGlobal(QPoint(450, 450))
            return '分类', '关键词'
        self.owner.get_preview_metadata.side_effect = metadata
        self.show_preview()
        self.assertFalse(self.popup.calls)


if __name__ == '__main__':
    unittest.main()
