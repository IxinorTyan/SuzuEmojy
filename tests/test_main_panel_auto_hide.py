import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from fluent_ui.views.gallery_view import GalleryInterface


class MainPanelAutoHideTests(unittest.TestCase):
    def make_view(self, enabled=False, copied=True, selection=False, target=123):
        config = Mock()
        config.get.side_effect = lambda key, default=None: (
            enabled if key == 'hide_main_after_paste' else default
        )
        return SimpleNamespace(
            config=config, clipboard=Mock(copy_image_to_clipboard=Mock(return_value=copied)),
            storage=Mock(), is_selection_mode=selection,
            _last_clicked_path=None, _shift_selected_paths=set(),
            _anchor_from_selection_click=False, last_active_window=target,
            hide_after_paste_requested=Mock(), show_success=Mock(), show_error=Mock(),
            set_selection_mode=Mock(),
            _toggle_card_selection=Mock(),
        )

    def test_enabled_hides_before_starting_guarded_paste(self):
        view = self.make_view(enabled=True)
        events = []
        view.hide_after_paste_requested.emit.side_effect = lambda: events.append('hide')
        view.clipboard.paste_to_window.side_effect = lambda hwnd: events.append('paste')
        GalleryInterface.on_image_clicked(view, 'emoji.gif')
        self.assertEqual(events, ['hide', 'paste'])
        view.clipboard.paste_to_window.assert_called_once_with(123)
        view.storage.add_recent_image.assert_called_once_with('emoji.gif', 30)

    def test_default_keeps_panel_open(self):
        view = self.make_view()
        GalleryInterface.on_image_clicked(view, 'emoji.gif')
        view.clipboard.paste_to_window.assert_called_once_with(123)
        view.hide_after_paste_requested.emit.assert_not_called()

    def test_hide_cannot_change_the_captured_paste_target(self):
        view = self.make_view(enabled=True)
        view.hide_after_paste_requested.emit.side_effect = lambda: setattr(view, 'last_active_window', 456)
        GalleryInterface.on_image_clicked(view, 'emoji.gif')
        view.clipboard.paste_to_window.assert_called_once_with(123)

    def test_copy_failure_and_selection_never_hide_or_paste(self):
        cases = [(False, False, Qt.NoModifier), (True, True, Qt.NoModifier),
                 (True, False, Qt.ControlModifier), (True, False, Qt.ShiftModifier)]
        for copied, selection, modifiers in cases:
            with self.subTest(copied=copied, selection=selection, modifiers=modifiers):
                view = self.make_view(True, copied, selection)
                GalleryInterface.on_image_clicked(view, 'emoji.gif', modifiers)
                view.clipboard.paste_to_window.assert_not_called()
                view.hide_after_paste_requested.emit.assert_not_called()
                view.storage.add_recent_image.assert_not_called()

    def test_no_target_still_copies_and_hides_like_quick_panel(self):
        view = self.make_view(enabled=True, target=None)
        GalleryInterface.on_image_clicked(view, 'emoji.gif')
        view.clipboard.paste_to_window.assert_called_once_with(None)
        view.hide_after_paste_requested.emit.assert_called_once()
        view.show_success.assert_not_called()

    def test_hide_preserves_position_and_cancels_preview(self):
        from fluent_ui.main_window import MainWindow
        window = SimpleNamespace(
            _reset_close_button_state=Mock(), gallery_interface=Mock(), hide=Mock()
        )
        MainWindow._hide_after_paste(window)
        window.gallery_interface.save_scroll_position.assert_called_once()
        window.gallery_interface.preview_controller.cancel.assert_called_once()
        window.hide.assert_called_once()

    def test_setting_persists_and_reloads(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from fluent_ui.views.setting_view import SettingInterface
        app = QApplication.instance() or QApplication([])
        values = {}
        config = Mock()
        config.get.side_effect = lambda key, default=None: values.get(key, default)
        config.set.side_effect = lambda key, value: values.update({key: value})
        with patch('fluent_ui.views.setting_view.AutostartService') as startup:
            startup.return_value.is_enabled.return_value = False
            view = SettingInterface(config)
            self.assertFalse(view.hideAfterPasteCard.isChecked())
            view.hideAfterPasteCard.setChecked(True)
            self.assertTrue(values['hide_main_after_paste'])
            second = SettingInterface(config)
            self.assertTrue(second.hideAfterPasteCard.isChecked())
            second.hideAfterPasteCard.setChecked(False)
            self.assertFalse(values['hide_main_after_paste'])
            view.deleteLater()
            second.deleteLater()


if __name__ == '__main__':
    unittest.main()
