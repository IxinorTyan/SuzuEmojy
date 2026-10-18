import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.autostart import AutostartService, _process_executable, should_start_hidden


@contextmanager
def compiled_runtime():
    with patch.object(sys, 'executable', r'C:\程序 目录\python.exe'), \
            patch('services.autostart.__compiled__', True, create=True), \
            patch('services.autostart._process_executable',
                  return_value=Path(r'C:\程序 目录\SuzuEmojy.exe')):
        yield


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.service = AutostartService()
        self.service.supported = True
        self.registry = MagicMock()
        self.key = self.registry.OpenKey.return_value.__enter__.return_value
        self.registry.REG_SZ = 1
        self.registry.REG_BINARY = 3
        self.registry.QueryValueEx.return_value = ('', 1)
        self.patcher = patch.dict(sys.modules, winreg=self.registry)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_enable_writes_only_current_user_run(self):
        self.registry.QueryValueEx.return_value = ('"C:\\应用 程序\\app.exe" --autostart', 1)
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
                patch.object(sys, 'frozen', True, create=True), \
                patch('services.autostart._process_executable',
                      return_value=Path(r'C:\程序 目录\SuzuEmojy.exe'), create=True):
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_startup_visibility(self):
        self.assertTrue(should_start_hidden(['app', '--autostart'], True))
        self.assertFalse(should_start_hidden(['app'], True))
        self.assertFalse(should_start_hidden(['app', '--autostart'], False))

    def test_nuitka_command(self):
        # Nuitka 实际可能将 sys.executable 留成根本不存在的 python.exe。
        with compiled_runtime():
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_packaged_release_uses_public_launcher(self):
        executable = Path(r'C:\程序 目录\bin\SuzuEmojy.exe')
        with compiled_runtime(), patch('services.autostart._process_executable', return_value=executable), patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(self.service.command(), '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_direct_core_registration_migrates_to_launcher_without_enabling_approval(self):
        executable = Path(r'C:\程序 目录\bin\SuzuEmojy.exe')
        self.registry.QueryValueEx.return_value = ('"C:\程序 目录\\bin\\SuzuEmojy.exe" --autostart', 1)
        with compiled_runtime(), patch('services.autostart._process_executable', return_value=executable), patch.object(Path, 'is_file', return_value=True):
            self.assertTrue(self.service.repair_registration())
        self.registry.DeleteValue.assert_not_called()
        self.assertEqual(self.registry.SetValueEx.call_args.args[-1], '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_repairs_existing_broken_nuitka_command(self):
        self.registry.QueryValueEx.return_value = ('"C:\程序 目录\\python.exe" --autostart', 1)
        with compiled_runtime():
            self.assertTrue(self.service.repair_registration())
        self.registry.SetValueEx.assert_called_once_with(
            self.registry.CreateKeyEx.return_value.__enter__.return_value,
            'SuzuEmojy', 0, 1, '"C:\程序 目录\\SuzuEmojy.exe" --autostart')

    def test_repair_does_not_enable_missing_entry(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError
        with compiled_runtime():
            self.assertFalse(self.service.repair_registration())
        self.registry.CreateKeyEx.assert_not_called()

    def test_repair_still_works_if_new_runtime_corrects_sys_executable(self):
        self.registry.QueryValueEx.return_value = ('"C:\程序 目录\\python.exe" --autostart', 1)
        with compiled_runtime(), patch.object(sys, 'executable', r'C:\程序 目录\SuzuEmojy.exe'):
            self.assertTrue(self.service.repair_registration())

    def test_repair_keeps_other_installation_and_current_command(self):
        for command, kind in [
            ('"C:\另一套安装\\python.exe" --autostart', 1),
            ('"C:\程序 目录\\SuzuEmojy.exe" --autostart', 1),
            ('"C:\程序 目录\\python.exe" --autostart', 2),
        ]:
            with self.subTest(command=command, kind=kind), compiled_runtime():
                self.registry.QueryValueEx.return_value = (command, kind)
                self.assertFalse(self.service.repair_registration())
        self.registry.CreateKeyEx.assert_not_called()

    def test_source_startup_never_repairs_compiled_registration(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertFalse(self.service.repair_registration())
        self.registry.OpenKey.assert_not_called()

    def test_failed_repair_propagates_error_without_deleting_entry(self):
        self.registry.QueryValueEx.return_value = ('"C:\程序 目录\\python.exe" --autostart', 1)
        self.registry.SetValueEx.side_effect = PermissionError('denied')
        with compiled_runtime(), self.assertRaises(PermissionError):
            self.service.repair_registration()
        self.registry.DeleteValue.assert_not_called()


class ProcessExecutableTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows process path API')
    def test_real_process_image_path(self):
        self.assertEqual(_process_executable(), Path(sys.executable).resolve())

    def test_unicode_long_image_path(self):
        expected = 'C:\\程序 目录\\' + '很长的目录\\' * 60 + 'SuzuEmojy.exe'

        def read_filename(module, buffer, size):
            self.assertIsNone(module)
            self.assertGreater(size, len(expected))
            buffer.value = expected
            return len(expected)

        with patch('ctypes.WinDLL') as loader:
            loader.return_value.GetModuleFileNameW.side_effect = read_filename
            self.assertEqual(_process_executable(), Path(expected).resolve())

    def test_api_failure_is_reported(self):
        with patch('ctypes.WinDLL') as loader:
            loader.return_value.GetModuleFileNameW.return_value = 0
            with self.assertRaises(OSError):
                _process_executable()

    def test_truncated_image_path_is_rejected(self):
        with patch('ctypes.WinDLL') as loader:
            loader.return_value.GetModuleFileNameW.return_value = 32768
            with self.assertRaises(OSError):
                _process_executable()


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
