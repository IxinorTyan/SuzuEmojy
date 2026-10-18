"""Opt-in real Windows clipboard/focus/input tests, isolated from user windows.

Run separately: SUZU_TEST_NATIVE_PASTE=1 python -B tests/test_auto_paste_windows.py
Only the temporary receiver window is allowed to receive Ctrl+V. The sender
retains the previous OLE clipboard object and restores it on exit.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def receiver(folder):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QTextEdit

    app = QApplication([])

    class Receiver(QTextEdit):
        def insertFromMimeData(self, source):
            (folder / 'received.json').write_text(json.dumps({
                'image': source.hasImage(),
                'files': [url.toLocalFile() for url in source.urls()],
            }), encoding='utf-8')

    window = Receiver()
    window.setWindowTitle('SuzuEmojy temporary paste receiver')
    window.resize(320, 160)
    window.show()
    window.setFocus()
    (folder / 'receiver.json').write_text(json.dumps({'hwnd': int(window.winId())}), encoding='utf-8')
    QTimer.singleShot(12000, app.quit)
    app.exec()


def sender(folder, hide):
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QPushButton
    from services.auto_paste import user32
    from services.clipboard import ClipboardService

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    ole = ctypes.OleDLL('ole32')
    ole.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    ole.OleSetClipboard.argtypes = [ctypes.c_void_p]
    previous = ctypes.c_void_p()
    ole.OleGetClipboard(ctypes.byref(previous))
    service = ClipboardService({'convert_static_to_gif': False})
    target = json.loads((folder / 'receiver.json').read_text())['hwnd']
    original_foreground = user32.GetForegroundWindow()
    window = QPushButton('Temporary image selection')
    window.setWindowTitle('SuzuEmojy temporary paste source')
    window.resize(320, 120)
    result = {'failed': False, 'notifications': [], 'target': target}
    service.auto_paste.failed.connect(lambda: result.update(
        failed=True, failure_foreground=user32.GetForegroundWindow(),
        failure_sequence=user32.GetClipboardSequenceNumber(),
        modifiers=[key for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)
                   if user32.GetAsyncKeyState(key) & 0x8000]))
    service.clipboard.dataChanged.connect(lambda: result['notifications'].append({
        'owned': service.clipboard.ownsClipboard(),
        'sequence': user32.GetClipboardSequenceNumber(),
    }))
    image_path = folder / '测试 图片.png'
    image = QImage(16, 16, QImage.Format_ARGB32)
    image.fill(0xff1e90ff)
    assert image.save(str(image_path))

    def copy_and_paste():
        result['source_was_foreground'] = user32.GetForegroundWindow() == int(window.winId())
        result['foreground_before_copy'] = user32.GetForegroundWindow()
        result['copied'] = service.copy_image_to_clipboard(str(image_path))
        result['copied_sequence'] = service._copied_clipboard_sequence
        if hide:
            window.hide()
        result['started'] = service.paste_to_window(target)
        result['foreground_after_start'] = user32.GetForegroundWindow()

    def done():
        result['received'] = (folder / 'received.json').is_file()
        result['foreground_is_target'] = user32.GetForegroundWindow() == target
        (folder / 'sender.json').write_text(json.dumps(result), encoding='utf-8')
        app.quit()

    window.clicked.connect(copy_and_paste)
    try:
        window.show()
        window.activateWindow()
        # The harness has no physical click. Establish foreground ownership
        # for this temporary source before exercising the normal paste code.
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(original_foreground, None)
        user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
        attached = (foreground_thread != current_thread
                    and user32.AttachThreadInput(current_thread, foreground_thread, True))
        try:
            user32.SetForegroundWindow(int(window.winId()))
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        QTimer.singleShot(250, window.click)
        QTimer.singleShot(1250, done)
        app.exec()
    finally:
        service.auto_paste.cancel()
        if previous.value:
            ole.OleSetClipboard(previous)
            ole.OleFlushClipboard()
            vtable = ctypes.cast(previous, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(previous)
        else:
            service.clipboard.clear()
        if original_foreground:
            user32.SetForegroundWindow(original_foreground)


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('SUZU_TEST_NATIVE_PASTE') == '1',
                     'Requires an interactive Windows desktop; opt in with SUZU_TEST_NATIVE_PASTE=1')
class NativeAutoPasteTests(unittest.TestCase):
    def run_paste(self, hide):
        env = os.environ.copy()
        env['QT_QPA_PLATFORM'] = 'windows'
        with tempfile.TemporaryDirectory(prefix='Suzu 粘贴 ') as name:
            folder = Path(name)
            process = subprocess.Popen([sys.executable, '-B', __file__, '--receiver', name],
                                       env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 5
                while not (folder / 'receiver.json').exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('Temporary receiver did not start')
                    time.sleep(.02)
                subprocess.run([sys.executable, '-B', __file__, '--sender', name, str(int(hide))],
                               env=env, check=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
                result = json.loads((folder / 'sender.json').read_text(encoding='utf-8'))
                self.assertTrue(result['copied'], result)
                self.assertTrue(result['started'], result)
                self.assertFalse(result['failed'], result)
                self.assertTrue(result['received'], result)
                self.assertTrue(result['foreground_is_target'], result)
                self.assertTrue(any(n['owned'] and n['sequence'] == result['copied_sequence']
                                    for n in result['notifications']), result)
                received = json.loads((folder / 'received.json').read_text(encoding='utf-8'))
                self.assertTrue(received['image'], received)
                self.assertEqual([os.path.normpath(path) for path in received['files']],
                                 [str(folder / '测试 图片.png')])
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)

    def test_visible_panel_restores_target_and_pastes_image(self):
        self.run_paste(hide=False)

    def test_hidden_panel_restores_target_and_pastes_image(self):
        self.run_paste(hide=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--receiver':
        receiver(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == '--sender':
        sender(Path(sys.argv[2]), bool(int(sys.argv[3])))
    else:
        unittest.main()
