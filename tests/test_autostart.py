import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.autostart import AutostartService, should_start_hidden


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.service = AutostartService()
        self.service.supported = True
        self.registry = MagicMock()
        self.key = self.registry.OpenKey.return_value.__enter__.return_value
        self.registry.REG_SZ = 1
        self.patcher = patch.dict(sys.modules, winreg=self.registry)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_enable_writes_only_current_user_run(self):
        with patch.object(self.service, 'command', return_value='"C:\\应用 程序\\app.exe" --autostart'):
            self.service.set_enabled(True)
        self.registry.CreateKeyEx.assert_called_once_with(
            self.registry.HKEY_CURRENT_USER, self.service.RUN_KEY,
            0, self.registry.KEY_SET_VALUE)
        self.registry.SetValueEx.assert_called_once_with(
            self.registry.CreateKeyEx.return_value.__enter__.return_value,
            'SuzuEmojy', 0, 1, '"C:\\应用 程序\\app.exe" --autostart')

    def test_disable_removes_only_our_value_and_is_idempotent(self):
        self.service.set_enabled(False)
        self.registry.DeleteValue.assert_called_once_with(self.key, 'SuzuEmojy')
        self.registry.DeleteValue.side_effect = FileNotFoundError
        self.service.set_enabled(False)

    def test_permission_errors_are_not_silenced(self):
        self.registry.OpenKey.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            self.service.is_enabled()
        with self.assertRaises(PermissionError):
            self.service.set_enabled(False)
        self.registry.CreateKeyEx.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            self.service.set_enabled(True)

    def test_status_checks_real_command_including_moved_install(self):
        with patch.object(self.service, 'command', return_value='current --autostart'):
            self.registry.QueryValueEx.return_value = ('current --autostart', 1)
            self.assertTrue(self.service.is_enabled())
            self.registry.QueryValueEx.return_value = ('old --autostart', 1)
            self.assertFalse(self.service.is_enabled())
            self.registry.QueryValueEx.side_effect = FileNotFoundError
            self.assertFalse(self.service.is_enabled())

    def test_source_command_uses_pythonw_and_absolute_script(self):
        with patch.object(sys, 'executable', r'C:\运行 环境\python.exe'), \
                patch.object(sys, 'frozen', False, create=True), \
                patch.object(Path, 'is_file', return_value=True):
            command = self.service.command()
        self.assertTrue(command.startswith('"C:\运行 环境\\pythonw.exe" '))
        self.assertIn(str(Path(__file__).resolve().parent.parent / 'main.py'), command)
        self.assertTrue(command.endswith(' --autostart'))

    def test_frozen_command_does_not_use_python_or_script(self):
        with patch.object(sys, 'executable', r'C:\程序 目录\SuzuEmojy.exe'), \
                patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_startup_visibility(self):
        self.assertTrue(should_start_hidden(['app', '--autostart'], True))
        self.assertFalse(should_start_hidden(['app'], True))
        self.assertFalse(should_start_hidden(['app', '--autostart'], False))

    def test_nuitka_command(self):
        with patch.object(sys, 'executable', r'C:\程序 目录\SuzuEmojy.exe'), \
                patch.object(sys, 'frozen', False, create=True), \
                patch('services.autostart.__compiled__', True, create=True):
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_nuitka_command(self):
        with patch.object(sys, 'executable', r'C:\程序 目录\SuzuEmojy.exe'), \
                patch.object(sys, 'frozen', False, create=True), \
                patch('services.autostart.__compiled__', True, create=True):
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')


class AutostartUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_toggle_rolls_back_and_opening_never_writes(self):
        from fluent_ui.views.setting_view import SettingInterface
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: default
        with patch('fluent_ui.views.setting_view.AutostartService') as factory, \
                patch('fluent_ui.views.setting_view.InfoBar.error') as error:
            service = factory.return_value
            service.supported = True
            service.is_enabled.return_value = False
            view = SettingInterface(config)
            service.set_enabled.assert_not_called()
            service.set_enabled.side_effect = PermissionError('denied')
            view.autostartCard.setChecked(True)
            self.assertFalse(view.autostartCard.isChecked())
            error.assert_called_once()
            config.set.assert_not_called()
            view.deleteLater()


if __name__ == '__main__':
    unittest.main()
