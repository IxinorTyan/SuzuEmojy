"""Current-user Windows login startup; importing this module never changes the OS."""
import os
import subprocess
import sys
from pathlib import Path


def _process_executable():
    """Read the actual Windows image path; Nuitka sys.executable may be python.exe."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    get_module_filename = kernel32.GetModuleFileNameW
    get_module_filename.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    get_module_filename.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_module_filename(None, buffer, len(buffer))
    if not length:
        raise ctypes.WinError(ctypes.get_last_error())
    if length >= len(buffer):
        raise OSError('The application executable path is too long')
    return Path(buffer.value).resolve()


class AutostartService:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APPROVAL_KEYS = (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32",
    )
    VALUE_NAME = "SuzuEmojy"

    def __init__(self):
        self.supported = sys.platform == "win32"

    def command(self):
        if getattr(sys, "frozen", False) or globals().get("__compiled__"):
            executable = _process_executable()
            # Use the public launcher in a standalone release so local update
            # recovery and the working directory match a normal double-click.
            launcher = executable.parent.parent / "SuzuEmojy.exe"
            if executable.parent.name.lower() == "bin" and launcher.is_file():
                executable = launcher
            args = [str(executable)]
        else:
            # Reuse the working runtime, including the lightweight launcher's runtime.
            executable = Path(sys.executable).resolve()
            pythonw = executable.with_name("pythonw.exe")
            if pythonw.is_file():
                executable = pythonw
            script = Path(__file__).resolve().parent.parent / "main.py"
            if not script.is_file():
                raise FileNotFoundError(str(script))
            args = [str(executable), str(script)]
        return subprocess.list2cmdline(args + ["--autostart"])

    def repair_registration(self):
        """Repair this installation's old compiled command only if already enabled."""
        if not self.supported or not (getattr(sys, 'frozen', False) or globals().get('__compiled__')):
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                value, kind = winreg.QueryValueEx(key, self.VALUE_NAME)
        except FileNotFoundError:
            return False

        # Only migrate commands belonging to this installation. Automatic
        # migration must preserve a user's disabled state in Task Manager.
        executable = _process_executable()
        old_commands = {
            os.path.normcase(subprocess.list2cmdline([str(path), '--autostart']))
            for path in (executable.with_name('python.exe'), executable)
        }
        if kind != winreg.REG_SZ or os.path.normcase(value) not in old_commands:
            return False
        if os.path.normcase(value) == os.path.normcase(self.command()):
            return False
        self._write_command(self.command())
        return True

    def _disabled_approval_keys(self):
        import winreg
        disabled = []
        for path in self.APPROVAL_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    value, kind = winreg.QueryValueEx(key, self.VALUE_NAME)
            except FileNotFoundError:
                continue
            # Windows records per-user Startup Apps disabling separately from
            # Run. Both 3 and 7 are disabled states; 2 and 6 are enabled.
            if kind == winreg.REG_BINARY and isinstance(value, bytes) and value and value[0] in (3, 7):
                disabled.append(path)
        return disabled

    def _write_command(self, command):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.RUN_KEY,
                                0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.VALUE_NAME, 0, winreg.REG_SZ, command)

    def is_enabled(self):
        if not self.supported:
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                value, kind = winreg.QueryValueEx(key, self.VALUE_NAME)
            return (kind == winreg.REG_SZ
                    and os.path.normcase(value) == os.path.normcase(self.command())
                    and not self._disabled_approval_keys())
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if not self.supported:
            raise OSError("Windows login startup is unavailable on this platform")
        import winreg
        if enabled:
            command = self.command()
            self._write_command(command)
            # Only an explicit enable action clears this app's old disabling
            # record. Do not fabricate undocumented StartupApproved binary data.
            for path in self._disabled_approval_keys():
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, self.VALUE_NAME)
                except FileNotFoundError:
                    pass
            if not self.is_enabled():
                raise OSError("Windows 未能启用自启动，请检查系统的启动应用设置")
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.VALUE_NAME)
            except FileNotFoundError:
                pass


def should_start_hidden(argv, tray_available):
    """Never leave the app inaccessible when no system tray is available."""
    return "--autostart" in argv and tray_available
