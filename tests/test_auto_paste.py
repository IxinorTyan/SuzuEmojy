import ctypes
import json
import os
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from fluent_ui.components.quick_panel import QuickPanel
from fluent_ui.views.gallery_view import GalleryInterface
from services.auto_paste import AutoPasteController, INPUT
from services.clipboard import ClipboardService


class AutoPasteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.native_patch = patch('services.auto_paste.user32')
        self.native = self.native_patch.start()
        self.identities = {123: (55, os.getpid() + 1), 456: (66, os.getpid() + 2)}
        self.native.IsWindow.side_effect = lambda hwnd: hwnd in self.identities

        def identity(hwnd, process_id):
            thread, process = self.identities[hwnd]
            if process_id is not None:
                ctypes.cast(process_id, ctypes.POINTER(wintypes.DWORD)).contents.value = process
            return thread

        def class_name(hwnd, buffer, length):
            buffer.value = 'Chat'
            return 4

        self.native.GetWindowThreadProcessId.side_effect = identity
        self.native.GetClassNameW.side_effect = class_name
        self.native.IsIconic.return_value = False
        self.native.GetForegroundWindow.return_value = 123
        self.native.GetClipboardSequenceNumber.return_value = 40
        self.native.GetAsyncKeyState.return_value = 0
        self.native.SendInput.return_value = 4
        self.controller = AutoPasteController()
        self.controller._elapsed = Mock()
        self.controller._elapsed.hasExpired.return_value = False
        self.failed = Mock()
        self.controller.failed.connect(self.failed)

    def tearDown(self):
        self.controller.cancel()
        self.controller.deleteLater()
        self.app.processEvents()
        self.native_patch.stop()

    def check(self):
        self.controller._timer.stop()
        self.controller._check_focus_and_paste()

    def test_success_inserts_one_ctrl_v_batch_only_once(self):
        self.assertTrue(self.controller.start(123))
        self.native.SetForegroundWindow.assert_called_once_with(123)
        self.check()
        self.check()
        self.native.SendInput.assert_called_once()
        count, inputs, size = self.native.SendInput.call_args.args
        self.assertEqual((count, size), (4, 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28))
        self.assertEqual([(i.type, i.data.ki.wVk, i.data.ki.dwFlags) for i in inputs],
                         [(1, 0x11, 0), (1, 0x56, 0), (1, 0x56, 2), (1, 0x11, 2)])
        self.failed.assert_not_called()

    def test_wrong_foreground_retries_then_stops_without_pasting(self):
        self.native.GetForegroundWindow.return_value = 456
        self.controller.start(123)
        self.check()
        self.assertTrue(self.controller._timer.isActive())
        self.native.SendInput.assert_not_called()
        self.controller._elapsed.hasExpired.return_value = True
        self.check()
        self.native.SendInput.assert_not_called()
        self.assertFalse(self.controller._timer.isActive())
        self.failed.assert_called_once()

    def test_delayed_focus_can_succeed_before_deadline(self):
        self.native.GetForegroundWindow.return_value = 456
        self.controller.start(123)
        self.check()
        self.native.GetForegroundWindow.return_value = 123
        self.check()
        self.native.SendInput.assert_called_once()

    def test_expired_callback_does_not_paste_even_with_correct_focus(self):
        self.controller.start(123)
        self.controller._elapsed.hasExpired.return_value = True
        self.check()
        self.native.SendInput.assert_not_called()
        self.failed.assert_called_once()

    def test_focus_change_immediately_before_injection_aborts(self):
        self.controller.start(123)
        self.native.GetForegroundWindow.side_effect = [123, 456]
        self.check()
        self.native.SendInput.assert_not_called()
        self.failed.assert_called_once()

    def test_destroyed_or_reused_target_aborts(self):
        for identity in (None, (999, os.getpid() + 3)):
            with self.subTest(identity=identity):
                self.controller.start(123)
                if identity is None:
                    del self.identities[123]
                else:
                    self.identities[123] = identity
                self.check()
                self.native.SendInput.assert_not_called()
                self.identities[123] = (55, os.getpid() + 1)

    def test_new_clipboard_content_aborts(self):
        self.controller.start(123)
        self.native.GetClipboardSequenceNumber.return_value = 41
        self.check()
        self.native.SendInput.assert_not_called()
        self.failed.assert_called_once()

    def test_clipboard_replaced_between_copy_and_start_aborts(self):
        self.assertFalse(self.controller.start(123, clipboard_sequence=39))
        self.native.SetForegroundWindow.assert_not_called()
        self.native.SendInput.assert_not_called()
        self.failed.assert_called_once()

    def test_unreadable_clipboard_sequence_does_not_start_paste(self):
        self.native.GetClipboardSequenceNumber.return_value = 0
        self.assertFalse(self.controller.start(123))
        self.native.SetForegroundWindow.assert_not_called()
        self.native.SendInput.assert_not_called()
        self.failed.assert_called_once()

    def test_new_request_replaces_old_target(self):
        self.controller.start(123)
        self.controller.start(456)
        self.native.GetForegroundWindow.return_value = 123
        self.check()
        self.native.SendInput.assert_not_called()
        self.native.GetForegroundWindow.return_value = 456
        self.check()
        self.native.SendInput.assert_called_once()

    def test_cancel_and_late_callback_do_nothing(self):
        self.controller.start(123)
        self.controller.cancel()
        self.check()
        self.native.SendInput.assert_not_called()
        self.failed.assert_not_called()

    def test_invalid_and_own_process_targets_are_rejected(self):
        self.identities[456] = (66, os.getpid())
        for hwnd in (None, 0, 789, 456):
            self.assertFalse(self.controller.start(hwnd))
        self.native.SetForegroundWindow.assert_not_called()
        self.native.SendInput.assert_not_called()

    def test_desktop_and_unknown_window_class_are_rejected(self):
        def desktop(hwnd, buffer, length):
            buffer.value = 'Progman'
            return 7
        self.native.GetClassNameW.side_effect = desktop
        self.assertFalse(self.controller.start(123))
        self.native.GetClassNameW.side_effect = None
        self.native.GetClassNameW.return_value = 0
        self.assertFalse(self.controller.start(123))
        self.native.SetForegroundWindow.assert_not_called()

    def test_minimized_target_is_restored_before_activation(self):
        self.native.IsIconic.return_value = True
        self.controller.start(123)
        self.native.ShowWindow.assert_called_once_with(123, 9)
        self.native.SetForegroundWindow.assert_called_once_with(123)

    def test_refused_activation_temporarily_attaches_and_always_detaches(self):
        self.native.SetForegroundWindow.return_value = False
        self.native.GetForegroundWindow.return_value = 456
        self.native.AttachThreadInput.return_value = True
        with patch('services.auto_paste.kernel32') as kernel:
            kernel.GetCurrentThreadId.return_value = 77
            self.assertTrue(self.controller.start(123))
        self.assertEqual(self.native.SetForegroundWindow.call_count, 2)
        self.assertEqual([call.args for call in self.native.AttachThreadInput.call_args_list],
                         [(77, 55, True), (77, 66, True), (77, 66, False), (77, 55, False)])
        # A later focus change must not trigger more activation or paste into
        # the unrelated foreground window.
        self.check()
        self.native.SendInput.assert_not_called()
        self.assertEqual(self.native.SetForegroundWindow.call_count, 2)
        self.native.GetForegroundWindow.return_value = 123
        self.check()
        self.native.SendInput.assert_called_once()

    def test_refused_activation_skips_current_thread_and_failed_attach(self):
        self.native.SetForegroundWindow.return_value = False
        self.native.GetForegroundWindow.return_value = 456
        self.native.AttachThreadInput.return_value = False
        with patch('services.auto_paste.kernel32') as kernel:
            kernel.GetCurrentThreadId.return_value = 55
            self.assertTrue(self.controller.start(123))
        self.native.AttachThreadInput.assert_called_once_with(55, 66, True)
        self.check()
        self.native.SendInput.assert_not_called()

    def test_held_modifiers_delay_paste_until_release(self):
        self.controller.start(123)
        self.native.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key == 0x12 else 0
        self.check()
        self.native.SendInput.assert_not_called()
        self.native.GetAsyncKeyState.side_effect = None
        self.check()
        self.native.SendInput.assert_called_once()

    def test_blocked_injection_reports_failure_without_retry(self):
        self.native.SendInput.return_value = 0
        self.controller.start(123)
        self.check()
        self.check()
        self.native.SendInput.assert_called_once()
        self.failed.assert_called_once()

    def test_partial_injection_releases_keys(self):
        self.native.SendInput.side_effect = [2, 2]
        self.controller.start(123)
        self.check()
        self.assertEqual(self.native.SendInput.call_count, 2)
        count, inputs, _ = self.native.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual([(i.data.ki.wVk, i.data.ki.dwFlags) for i in inputs], [(0x56, 2), (0x11, 2)])
        self.failed.assert_called_once()

    def test_delayed_notification_for_copied_image_still_pastes_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder, 'image.png'))
            image = QImage(8, 8, QImage.Format_ARGB32)
            image.fill(0xffff0000)
            self.assertTrue(image.save(path))
            with patch('services.clipboard.user32', self.native):
                service = ClipboardService({'convert_static_to_gif': False})
                try:
                    with patch.object(service.clipboard, 'ownsClipboard', return_value=True):
                        self.assertTrue(service.copy_image_to_clipboard(path))
                        self.assertTrue(service.paste_to_window(123))
                        # Windows posts this notification after the click handler
                        # has copied, hidden the panel and scheduled the paste.
                        QTimer.singleShot(0, service.clipboard.dataChanged.emit)
                        self.app.processEvents()
                        self.assertEqual(service.auto_paste._target, 123)
                        service.auto_paste._timer.stop()
                        service.auto_paste._check_focus_and_paste()
                        service.auto_paste._check_focus_and_paste()
                        self.native.SendInput.assert_called_once()
                finally:
                    service.auto_paste.cancel()
                    service.deleteLater()
                    self.app.processEvents()

    def test_clipboard_replacement_still_cancels_after_own_notification(self):
        for still_owner in (False, True):
            with self.subTest(still_owner=still_owner), patch('services.clipboard.user32', self.native):
                self.native.GetClipboardSequenceNumber.return_value = 40
                service = ClipboardService({})
                try:
                    service._copied_clipboard_sequence = 40
                    self.assertTrue(service.paste_to_window(123))
                    with patch.object(service.clipboard, 'ownsClipboard', return_value=True):
                        service.clipboard.dataChanged.emit()
                        self.assertEqual(service.auto_paste._target, 123)
                    self.native.GetClipboardSequenceNumber.return_value = 41
                    with patch.object(service.clipboard, 'ownsClipboard', return_value=still_owner):
                        service.clipboard.dataChanged.emit()
                    service.auto_paste._check_focus_and_paste()
                    self.assertIsNone(service.auto_paste._target)
                    self.native.SendInput.assert_not_called()
                finally:
                    service.auto_paste.cancel()
                    service.deleteLater()
                    self.app.processEvents()


class PasteIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_quick_panel_copy_failure_stays_open_without_paste_or_recent_entry(self):
        panel = SimpleNamespace(clipboard=Mock(), _target_hwnd=123, storage=Mock(), hide_panel=Mock())
        panel.clipboard.copy_image_to_clipboard.return_value = False
        with patch('fluent_ui.components.quick_panel.InfoBar.error') as error:
            QuickPanel._on_card_clicked(panel, 'missing.gif')
        error.assert_called_once()
        panel.hide_panel.assert_not_called()
        panel.clipboard.paste_to_window.assert_not_called()
        panel.storage.add_recent_image.assert_not_called()

    def test_quick_panel_captures_target_before_hiding(self):
        panel = SimpleNamespace(clipboard=Mock(), config=Mock(), _target_hwnd=123,
                                storage=Mock(), hide_panel=Mock())
        panel.hide_panel.side_effect = lambda: setattr(panel, '_target_hwnd', 456)
        QuickPanel._on_card_clicked(panel, 'emoji.gif')
        panel.clipboard.paste_to_window.assert_called_once_with(123)

    def test_retargeting_quick_panel_cancels_pending_paste(self):
        panel = SimpleNamespace(clipboard=Mock(), _target_hwnd=123)
        QuickPanel.set_target_hwnd(panel, 456)
        panel.clipboard.auto_paste.cancel.assert_called_once()
        self.assertEqual(panel._target_hwnd, 456)

    def test_main_panel_does_not_track_another_window_of_this_app(self):
        view = SimpleNamespace(last_active_window=123, _check_inbox=Mock(),
                               scroll_area=SimpleNamespace(is_scrolling=False))
        with patch('fluent_ui.views.gallery_view.user32') as native:
            native.GetForegroundWindow.return_value = 456
            def identity(hwnd, process):
                ctypes.cast(process, ctypes.POINTER(wintypes.DWORD)).contents.value = os.getpid()
                return 55
            native.GetWindowThreadProcessId.side_effect = identity
            GalleryInterface.track_active_window(view)
        self.assertEqual(view.last_active_window, 123)

    def test_reopening_quick_panel_cancels_before_showing(self):
        storage, clipboard = Mock(), Mock()
        storage.get_recent_images.return_value = []
        panel = QuickPanel(storage, clipboard, {})
        try:
            with patch('fluent_ui.components.quick_panel.user32'):
                panel.show_at_cursor()
            clipboard.auto_paste.cancel.assert_called_once()
            self.assertTrue(panel.isVisible())
            self.app.processEvents()
        finally:
            panel.hide_panel()
            panel.preview_popup.deleteLater()
            panel.deleteLater()
            self.app.processEvents()

    def test_new_messages_have_all_translations(self):
        root = Path(__file__).resolve().parents[1]
        for lang in ('en', 'ja', 'zh_TW'):
            values = json.loads((root / 'translations' / f'{lang}.json').read_text(encoding='utf-8'))
            for key in ('复制失败', '无法复制图片，请检查文件后重试。', '未自动粘贴',
                        '未能自动粘贴，请返回目标窗口手动粘贴。'):
                self.assertTrue(values[key])


class ClipboardValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name, '图片.png'))
        img = QImage(8, 8, QImage.Format_ARGB32)
        img.fill(0xffff0000)
        self.assertTrue(img.save(self.path))
        self.service = SimpleNamespace(
            clipboard=Mock(), auto_paste=Mock(), _get_config_service=lambda: {},
            _convert_static_to_1frame_gif=Mock(return_value=None),
        )

    def tearDown(self):
        self.temp.cleanup()

    def copy(self, path=None):
        return ClipboardService.copy_image_to_clipboard(self.service, path or self.path)

    def test_missing_or_corrupt_image_does_not_replace_clipboard(self):
        self.assertFalse(self.copy(str(Path(self.temp.name, 'missing.png'))))
        Path(self.path).write_bytes(b'broken image')
        self.assertFalse(self.copy())
        self.service.clipboard.setMimeData.assert_not_called()
        self.assertEqual(self.service.auto_paste.cancel.call_count, 2)

    def test_valid_image_copies_file_and_image_data(self):
        self.assertTrue(self.copy())
        mime = self.service.clipboard.setMimeData.call_args.args[0]
        self.assertEqual(os.path.normpath(mime.urls()[0].toLocalFile()), self.path)
        self.assertTrue(mime.hasImage())

    def test_invalid_conversion_falls_back_to_original(self):
        invalid = str(Path(self.temp.name, 'invalid.gif'))
        Path(invalid).write_bytes(b'broken gif')
        self.service._convert_static_to_1frame_gif.return_value = invalid
        self.assertTrue(self.copy())
        mime = self.service.clipboard.setMimeData.call_args.args[0]
        self.assertEqual(os.path.normpath(mime.urls()[0].toLocalFile()), self.path)

    def test_clipboard_failure_returns_false_after_fallback(self):
        self.service.clipboard.setMimeData.side_effect = RuntimeError('clipboard unavailable')
        self.assertFalse(self.copy())
        self.assertEqual(self.service.clipboard.setMimeData.call_count, 2)

    def test_transient_copy_failure_can_fall_back(self):
        self.service.clipboard.setMimeData.side_effect = [RuntimeError('busy'), None]
        self.assertTrue(self.copy())

    def test_paste_uses_the_sequence_from_successful_copy(self):
        with patch('services.clipboard.user32') as native:
            native.GetClipboardSequenceNumber.return_value = 40
            self.assertTrue(self.copy())
            native.GetClipboardSequenceNumber.return_value = 41
            ClipboardService.paste_to_window(self.service, 123)
        self.service.auto_paste.start.assert_called_once_with(123, 40)

    def test_windows_clipboard_ownership_failure_returns_false(self):
        self.service.clipboard.ownsClipboard.return_value = False
        with patch('services.clipboard.QGuiApplication.platformName', return_value='windows'):
            self.assertFalse(self.copy())

    def test_clipboard_changes_cancel_the_shared_pending_request(self):
        # Offscreen Qt clipboard is local to this test process, not the user's clipboard.
        self.assertEqual(self.app.platformName(), 'offscreen')
        with patch('services.clipboard.os.makedirs'):
            service = ClipboardService({})
        service.auto_paste._target = 123
        service.auto_paste._timer.start(100)
        service.clipboard.setText('new content')
        self.assertIsNone(service.auto_paste._target)
        self.assertFalse(service.auto_paste._timer.isActive())
        service.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
