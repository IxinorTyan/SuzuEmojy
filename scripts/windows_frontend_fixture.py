"""Isolated, repeatable input for comparing native and legacy galleries."""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

from PIL import Image, ImageDraw


def create(root, count, dark):
    data = root / "data"
    images = data / "images"
    images.mkdir(parents=True)
    colors = ["#C97C86", "#6FA0D9", "#A594D9", "#82AD8E", "#D9A05A"]
    for index in range(count):
        frames = []
        for step in range(2 if index % 5 == 0 else 1):
            frame = Image.new("RGBA", (128, 128))
            draw = ImageDraw.Draw(frame)
            inset = 12 + 4 * (index % 4) + step * 10
            draw.rounded_rectangle((inset, inset, 127 - inset, 127 - inset), 14,
                                   fill=colors[index % len(colors)])
            draw.ellipse((43, 48, 53, 58), fill="#222222")
            draw.ellipse((73, 48, 83, 58), fill="#222222")
            draw.arc((48, 54, 78, 85), 10, 170, fill="#222222", width=2)
            frames.append(frame)
        path = images / (f"{index:04d}.gif" if len(frames) > 1 else f"{index:04d}.png")
        if len(frames) > 1:
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=180,
                           loop=0, disposal=2)
        else:
            frames[0].save(path)
    groups = {"收藏": [], "静态": [], "动图": []}
    keywords = {}
    for index, path in enumerate(sorted(images.iterdir())):
        if index % 3 == 0:
            groups["收藏"].append(path.name)
        groups["动图" if path.suffix == ".gif" else "静态"].append(path.name)
        if index % 2 == 0:
            keywords[path.name] = "可爱 happy"
    for file, contents in {
        "categories": groups, "metadata": keywords,
        "recent": [path.name for path in sorted(images.iterdir())[:8]],
        "config": {"appearance_mode": "dark" if dark else "light", "always_on_top": False},
    }.items():
        (data / f"{file}.json").write_text(json.dumps(contents, ensure_ascii=False), encoding="utf-8")


def _memory():
    """Same Windows counters as Process.WorkingSet64/PrivateMemorySize64."""
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (field, ctypes.c_size_t) for field in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                "PrivateUsage",
            )]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    process_handle = ctypes.windll.kernel32.GetCurrentProcess
    process_handle.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    if not get_memory(process_handle(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return [counters.WorkingSetSize, counters.PrivateUsage]


def legacy(root, output=None):
    if output is not None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QInputDialog
    from qfluentwidgets import RoundMenu, setTheme, Theme
    from services.clipboard import ClipboardService
    from services.config import ConfigService
    from services.storage import StorageService
    from fluent_ui.main_window import MainWindow

    app = QApplication([])
    if output is not None and app.platformName() != "offscreen":
        raise RuntimeError("Benchmark must run on Qt's offscreen platform.")
    if output is not None:
        # The Qt offscreen platform does not inherit the Windows font database.
        fonts = Path(os.environ["WINDIR"]) / "Fonts"
        for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / name))
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei"])
        font.setPointSize(9)
        app.setFont(font)
    with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(root / "fixture.exe")):
        config = ConfigService()
        storage = StorageService()
        clipboard = ClipboardService(config)
    setTheme(Theme.DARK if config.get("appearance_mode") == "dark" else Theme.LIGHT)
    # Don't register system-wide shortcuts as part of a visual comparison.
    with patch.object(MainWindow, "_init_global_hotkey"):
        window = MainWindow(storage, clipboard, config)
    window.show()
    if output is not None:
        started = monotonic()
        samples = []
        timer = QTimer()

        def capture():
            samples.append(_memory())
            if len(samples) < 5:
                return
            timer.stop()
            output.mkdir(parents=True, exist_ok=True)
            screenshot = output / "qt-gallery.png"
            if not window.grab().save(str(screenshot), "PNG"):
                app.exit(1)
                return
            gallery = window.gallery_interface
            images = storage.get_all_images()
            if images:
                path = next((path for path in images if str(path).endswith('.png')), images[0])
                preview = gallery.preview_popup
                preview.show_preview(path, QPoint(32, 32), gallery.get_preview_size(), *gallery.get_preview_metadata(path))
                if not preview.grab().save(str(output / "qt-sticker-preview.png"), "PNG"):
                    app.exit(1)
                    return
                preview.hide_preview()
            # Render the real menu without entering its popup event loop.
            menu_saved = False

            def save_menu(menu, *_args, **_kwargs):
                nonlocal menu_saved
                menu_saved = menu.grab().save(str(output / name), "PNG")

            for active, name in [(False, "qt-filter.png"), (True, "qt-filter-active.png")]:
                menu_saved = False
                with patch.object(RoundMenu, "exec", save_menu), patch.object(gallery.filter_state, "no_tag", active):
                    gallery._show_filter_menu()
                if not menu_saved:
                    app.exit(1)
                    return
            window.stacked_widget.setCurrentWidget(window.setting_interface)
            window.stacked_widget.layout().activate()
            if not window.grab().save(str(output / "qt-settings.png"), "PNG"):
                app.exit(1)
                return
            window.stacked_widget.setCurrentWidget(gallery)

            def save_input(parent, title, label, *_args):
                dialog = QInputDialog(parent)
                dialog.setWindowTitle(title)
                dialog.setLabelText(label)
                dialog.adjustSize()
                if not dialog.grab().save(str(output / "qt-new-category.png"), "PNG"):
                    raise RuntimeError("Cannot render category prompt")
                return "", False

            with patch.object(QInputDialog, "getText", save_input):
                gallery.sidebar._create_new_category()
            print(json.dumps({"frontend": "Qt", "fixtureImages": len(storage.get_all_images()),
                              "samples": samples, "measuredMillis": round((monotonic() - started) * 1000),
                              "theme": config.get("appearance_mode")}))
            quick = window.quick_panel
            quick.setAttribute(Qt.WA_DontShowOnScreen)
            quick._activation_guard = True
            quick._perform_search(force=True)
            quick.show()  # Qt's offscreen backend only; never call show_at_cursor here.
            quick._trigger_responsive_layout(force=True)

            def capture_quick():
                if not quick.grab().save(str(output / "qt-quick-panel.png"), "PNG"):
                    app.exit(1)
                else:
                    app.quit()

            QTimer.singleShot(500, capture_quick)

        timer.timeout.connect(capture)
        timer.start(1000)
    result = app.exec()
    if output is not None:
        window.quick_panel.hide_panel()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--offscreen-output", type=Path, help="Render the legacy gallery without showing a desktop window.")
    args = parser.parse_args()
    if args.legacy or args.offscreen_output:
        return legacy(args.root.resolve(), args.offscreen_output)
    create(args.root, args.count, args.dark)


if __name__ == "__main__":
    sys.exit(main())
