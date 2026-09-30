"""Current-user Windows login startup; importing this module never changes the OS."""
import os
import subprocess
import sys
from pathlib import Path


class AutostartService:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "SuzuEmojy"

    def __init__(self):
        self.supported = sys.platform == "win32"

    def command(self):
        executable = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False) or globals().get("__compiled__"):
            args = [str(executable)]
        else:
            # Reuse the working runtime, including the lightweight launcher's runtime.
            pythonw = executable.with_name("pythonw.exe")
            if pythonw.is_file():
                executable = pythonw
            script = Path(__file__).resolve().parent.parent / "main.py"
            if not script.is_file():
                raise FileNotFoundError(str(script))
            args = [str(executable), str(script)]
        return subprocess.list2cmdline(args + ["--autostart"])

    def is_enabled(self):
        if not self.supported:
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                value, kind = winreg.QueryValueEx(key, self.VALUE_NAME)
            return kind == winreg.REG_SZ and os.path.normcase(value) == os.path.normcase(self.command())
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if not self.supported:
            raise OSError("Windows login startup is unavailable on this platform")
        import winreg
        if enabled:
            command = self.command()
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.VALUE_NAME, 0, winreg.REG_SZ, command)
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
