import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import win32con
import win32gui
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fluent_ui.main_window import MainWindow


class WindowConfig(dict):
    def set(self, key, value):
        self[key] = value


class StateTestWindow(MainWindow):
    """使用正式窗口逻辑，不启动图库、下载任务和全局快捷键。"""

    def _init_ui(self):
        pass

    def _init_global_hotkey(self):
        pass

    def _init_paste_shortcut(self):
        pass

    def _update_background(self):
        pass


class NativeWindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != 'windows':
            raise unittest.SkipTest('需要 QT_QPA_PLATFORM=windows 验证原生窗口状态')

    def setUp(self):
        self.window = StateTestWindow(None, None, WindowConfig(always_on_top=False))
        self.window.show()
        QTest.qWait(150)
        self.hwnd = int(self.window.winId())
        self.original_size = self.window.size()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def maximize_from_title_bar(self):
        self.window.titleBar.maxBtn.click()
        QTest.qWait(150)
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(win32gui.GetWindowPlacement(self.hwnd)[1], win32con.SW_SHOWMAXIMIZED)

    def assert_restored_and_resizable(self):
        QTest.qWait(150)
        self.assertFalse(self.window.isMaximized())
        self.assertNotEqual(win32gui.GetWindowPlacement(self.hwnd)[1], win32con.SW_SHOWMAXIMIZED)
        self.assertFalse(win32gui.IsIconic(self.hwnd))
        self.assertEqual(self.window.size(), self.original_size)
        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_STYLE)
        self.assertTrue(style & win32con.WS_THICKFRAME)
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        edges = [
            (1, height // 2, win32con.HTLEFT),
            (width - 1, height // 2, win32con.HTRIGHT),
            (width // 2, 1, win32con.HTTOP),
            (width // 2, height - 1, win32con.HTBOTTOM),
            (1, 1, win32con.HTTOPLEFT),
            (width - 1, 1, win32con.HTTOPRIGHT),
            (1, height - 1, win32con.HTBOTTOMLEFT),
            (width - 1, height - 1, win32con.HTBOTTOMRIGHT),
        ]
        for x, y, expected in edges:
            point = win32gui.ClientToScreen(self.hwnd, (x, y))
            # 无边框库从系统读取鼠标位置；模拟坐标，避免移动用户鼠标。
            with patch('qframelesswindow.windows.win32api.GetCursorPos',
                       return_value=point):
                result = win32gui.SendMessage(self.hwnd, win32con.WM_NCHITTEST, 0, 0)
            self.assertEqual(result, expected, (x, y))

    def test_title_bar_maximize_then_programmatic_restore_repeatedly(self):
        for _ in range(3):
            self.maximize_from_title_bar()
            self.window.showNormal()
            self.assert_restored_and_resizable()

    def test_title_bar_maximize_then_title_bar_restore(self):
        self.maximize_from_title_bar()
        self.window.titleBar.maxBtn.click()
        self.assert_restored_and_resizable()

    def test_hidden_maximized_window_restores_when_shown(self):
        self.maximize_from_title_bar()
        self.window.hide()
        self.window.showNormal()
        self.assertTrue(self.window.isVisible())
        self.assert_restored_and_resizable()

    def test_qt_maximize_then_programmatic_restore(self):
        self.window.showMaximized()
        QTest.qWait(150)
        self.window.showNormal()
        self.assert_restored_and_resizable()

    def test_repeated_restore_requests_in_one_event_loop(self):
        self.maximize_from_title_bar()
        # 快捷键唤起路径连续调用 showNormal()，第二次调用不能破坏第一次还原。
        self.window.showNormal()
        self.window.showNormal()
        self.assert_restored_and_resizable()

    def test_minimized_maximized_window_restores_to_normal(self):
        self.maximize_from_title_bar()
        self.window.showMinimized()
        QTest.qWait(150)
        self.assertTrue(win32gui.IsIconic(self.hwnd))
        self.window.showNormal()
        self.assert_restored_and_resizable()

    def test_close_while_maximized_saves_windowed_geometry(self):
        original_geometry = self.window.geometry().getRect()
        self.maximize_from_title_bar()
        self.window.close()
        self.assertEqual(self.window.config['window_geometry'], list(original_geometry))

    def test_close_while_minimized_saves_last_moved_and_resized_geometry(self):
        self.window.resize(800, 600)
        self.window.move(100, 100)
        QTest.qWait(150)
        original_geometry = self.window.geometry().getRect()
        self.maximize_from_title_bar()
        self.window.showMinimized()
        QTest.qWait(150)
        self.window.close()
        self.assertEqual(self.window.config['window_geometry'], list(original_geometry))


if __name__ == '__main__':
    unittest.main()
