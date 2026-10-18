"""Restore a captured target and paste only while it still owns the foreground."""

import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QObject, QElapsedTimer, QTimer, Signal


user32 = ctypes.windll.user32
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
kernel32 = ctypes.windll.kernel32
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
        ('dwExtraInfo', wintypes.WPARAM),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG), ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD), ('dwExtraInfo', wintypes.WPARAM),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]


class _InputData(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('data', _InputData)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


SYSTEM_WINDOW_CLASSES = {
    'Progman', 'WorkerW', 'Shell_TrayWnd', 'CabinetWClass',
    'ExploreWClass', 'Windows.UI.Core.CoreWindow',
}


class AutoPasteController(QObject):
    failed = Signal()
    TIMEOUT_MS = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._check_focus_and_paste)
        self._elapsed = QElapsedTimer()
        self._target = None
        self._identity = None
        self._clipboard_sequence = None

    def cancel(self):
        self._timer.stop()
        self._target = None
        self._identity = None
        self._clipboard_sequence = None

    @staticmethod
    def _window_identity(hwnd):
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        process_id = wintypes.DWORD()
        thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not thread_id or not process_id.value or process_id.value == os.getpid():
            return None
        return thread_id, process_id.value

    def start(self, target, clipboard_sequence=None):
        """Capture the target once; later focus tracking must not change this request."""
        self.cancel()
        identity = self._window_identity(target)
        if identity is None:
            self._fail()
            return False
        class_name = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(target, class_name, len(class_name)):
            self._fail()
            return False
        if class_name.value in SYSTEM_WINDOW_CLASSES:
            self._fail()
            return False

        self._target = target
        self._identity = identity
        current_sequence = user32.GetClipboardSequenceNumber()
        self._clipboard_sequence = current_sequence if clipboard_sequence is None else clipboard_sequence
        if not self._clipboard_sequence or current_sequence != self._clipboard_sequence:
            self._fail()
            return False
        self._elapsed.start()
        if user32.IsIconic(target):
            user32.ShowWindow(target, 9)  # SW_RESTORE
        self._restore_target_foreground()
        self._timer.start(100)
        return True

    def _restore_target_foreground(self):
        if user32.SetForegroundWindow(self._target):
            return
        # Windows can refuse the switch after a tool panel hides. Temporarily
        # share the input queues, then detach before checking focus or sending
        # any keys. Never force activation again from a delayed timer callback.
        current_thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        foreground_thread = (user32.GetWindowThreadProcessId(foreground, None)
                             if foreground else 0)
        threads = dict.fromkeys((self._identity[0], foreground_thread))
        attached = []
        try:
            for thread in threads:
                if thread and thread != current_thread and user32.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
            user32.SetForegroundWindow(self._target)
        finally:
            for thread in reversed(attached):
                user32.AttachThreadInput(current_thread, thread, False)

    def _fail(self):
        self.cancel()
        self.failed.emit()

    def _request_is_valid(self):
        return (
            self._window_identity(self._target) == self._identity
            and user32.GetClipboardSequenceNumber() == self._clipboard_sequence
        )

    def _check_focus_and_paste(self):
        if self._target is None:
            return
        # A delayed event (sleep, busy UI) must not paste unexpectedly much later.
        if self._elapsed.hasExpired(self.TIMEOUT_MS) or not self._request_is_valid():
            self._fail()
            return
        modifiers_down = any(
            user32.GetAsyncKeyState(key) & 0x8000
            for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, Win
        )
        if user32.GetForegroundWindow() != self._target or modifiers_down:
            self._timer.start(20)
            return

        inputs = (INPUT * 4)()
        for item, key, flags in zip(inputs, (0x11, 0x56, 0x56, 0x11), (0, 0, 2, 2)):
            item.type = 1  # INPUT_KEYBOARD
            item.data.ki = KEYBDINPUT(key, 0, flags, 0, 0)

        # Check again immediately before inserting one uninterrupted input batch.
        if not self._request_is_valid() or user32.GetForegroundWindow() != self._target:
            self._fail()
            return
        self.cancel()
        inserted = user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
        if inserted != len(inputs):
            if inserted:
                # Release keys if Windows accepted only part of the batch.
                releases = (INPUT * 2)(inputs[2], inputs[3])
                user32.SendInput(2, releases, ctypes.sizeof(INPUT))
            self.failed.emit()
