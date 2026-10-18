"""Real HKCU tests confined to a temporary non-startup registry key."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from services.autostart import AutostartService


@unittest.skipUnless(os.name == 'nt', 'Windows startup integration')
class WindowsAutostartTests(unittest.TestCase):
    def setUp(self):
        import winreg
        self.reg = winreg
        self.service = AutostartService()
        self.root = r'Software\SuzuEmojy\AutostartTests' + chr(92) + uuid.uuid4().hex
        self.service.RUN_KEY = self.root + r'\Run'
        self.service.APPROVAL_KEYS = (self.root + r'\Approval', self.root + r'\Approval32')
        self.command = patch.object(self.service, 'command', return_value='"C:\\应用 程序\\SuzuEmojy.exe" --autostart')
        self.command.start()
        self.addCleanup(self.command.stop)
        self.addCleanup(self.cleanup_registry)

    def cleanup_registry(self):
        for path in (self.service.RUN_KEY,) + self.service.APPROVAL_KEYS + (self.root,):
            try:
                self.reg.DeleteKey(self.reg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass

    def put(self, path, value, kind, name='SuzuEmojy'):
        with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, path) as key:
            self.reg.SetValueEx(key, name, 0, kind, value)

    def test_explicit_enable_clears_only_our_blocked_approval_and_verifies(self):
        for path, state in zip(self.service.APPROVAL_KEYS, (3, 7)):
            self.put(path, bytes([state]) + b'\0' * 11, self.reg.REG_BINARY)
            self.put(path, b'other-app', self.reg.REG_BINARY, 'OtherApp')
        self.service._write_command(self.service.command())
        self.assertFalse(self.service.is_enabled())
        self.service.set_enabled(True)
        self.assertTrue(self.service.is_enabled())
        for path in self.service.APPROVAL_KEYS:
            with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, path) as key:
                self.assertEqual(self.reg.QueryValueEx(key, 'OtherApp')[0], b'other-app')
                with self.assertRaises(FileNotFoundError):
                    self.reg.QueryValueEx(key, 'SuzuEmojy')
        self.service.set_enabled(False)
        self.assertFalse(self.service.is_enabled())

    def test_existing_enabled_approval_is_preserved(self):
        for path in self.service.APPROVAL_KEYS:
            self.put(path, b'\x06' + b'\0' * 11, self.reg.REG_BINARY)
        self.service.set_enabled(True)
        for path in self.service.APPROVAL_KEYS:
            with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, path) as key:
                self.assertEqual(self.reg.QueryValueEx(key, 'SuzuEmojy')[0][0], 6)


@unittest.skipUnless(os.name == 'nt', 'Windows launcher integration')
class LauncherAutostartTests(unittest.TestCase):
    def test_compiled_launcher_preserves_startup_and_windows_arguments(self):
        from build_nuitka import build_launcher_stub, find_csc
        if not find_csc():
            self.skipTest('C# compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='Suzu 启动 测试 ') as temporary:
            root = Path(temporary)
            binary = root / 'bin'
            binary.mkdir()
            launcher = build_launcher_stub(str(root))
            source = root / 'probe.cs'
            source.write_text('''using System; using System.IO;
class Probe { static void Main(string[] args) {
File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"pid.txt"), System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
File.WriteAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"arguments.txt"), args);
File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"cwd.txt"), Environment.CurrentDirectory);
} }''', encoding='utf-8')
            subprocess.run([find_csc(), '/nologo', '/target:winexe', '/out:' + str(binary / 'SuzuEmojy.exe'), str(source)], check=True, capture_output=True)
            expected = ['--autostart', '中文 空格', '', 'quoted"text', 'C:\\目录 空格\\', '\\\\server\\share\\']
            subprocess.run([launcher] + expected, cwd=os.environ.get('WINDIR', r'C:\Windows'), check=True, timeout=15)
            result = binary / 'arguments.txt'
            for _ in range(200):
                if (binary / 'cwd.txt').is_file():
                    break
                time.sleep(.01)
            self.assertEqual(result.read_text(encoding='utf-8-sig').splitlines(), expected)
            self.assertEqual(Path((binary / 'cwd.txt').read_text(encoding='utf-8-sig')), root)
            # The launcher exits before its child. Wait for that child before
            # removing an executable that Windows still has mapped in memory.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            child = kernel.OpenProcess(0x00100000, False, int((binary / 'pid.txt').read_text(encoding='utf-8-sig')))
            if child:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(child, 10000), 0)
                finally:
                    kernel.CloseHandle(child)


if __name__ == '__main__':
    unittest.main()
