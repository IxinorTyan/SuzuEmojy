import os
import ctypes
import keyboard

from PySide6.QtCore import Qt, QObject, Signal, QSize, QEvent
from PySide6.QtGui import QIcon, QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    MSFluentWindow, NavigationItemPosition, FluentIcon as FIF,
    SearchLineEdit, MessageBox, TransparentToolButton, SplitTitleBar
)
from qframelesswindow import FramelessWindow, StandardTitleBar

from fluent_ui.views.gallery_view import GalleryInterface
from fluent_ui.views.setting_view import SettingInterface

user32 = ctypes.windll.user32

class HotkeySignal(QObject):
    """跨线程桥接信号"""
    activated = Signal()

class MainWindow(FramelessWindow):
    """
    无侧边栏的极简主窗口架构。
    使用 qframelesswindow 提供云母特效和自定义标题栏。
    """
    def __init__(self, storage_service, clipboard_service, config_service):
        super().__init__()
        self.storage = storage_service
        self.clipboard = clipboard_service
        self.config = config_service
        
        self.setTitleBar(StandardTitleBar(self))
        
        self._init_window()
        self._init_ui()
        self._init_global_hotkey()
        self._init_paste_shortcut()
        self._update_background()

    def _init_window(self):
        self.setWindowTitle("SuzuEmojy")
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hi.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        geometry = self.config.get("window_geometry", None)
        if geometry and len(geometry) == 4:
            self.setGeometry(*geometry)
        else:
            self.resize(860, 640)
            
        self.setMinimumSize(400, 300)

        font = self.font()
        if font.pointSize() <= 0:
            font.setPointSize(9)
            self.setFont(font)

    def _update_background(self):
        """处理深色模式背景"""
        from qfluentwidgets import isDarkTheme
        from PySide6.QtGui import QPalette, QColor
        
        palette = self.palette()
        is_dark = isDarkTheme()
        
        if is_dark:
            self.setStyleSheet("MainWindow { background-color: #202020; }")
            palette.setColor(QPalette.Window, QColor("#202020"))
        else:
            self.setStyleSheet("MainWindow { background-color: #F9F9F9; }")
            palette.setColor(QPalette.Window, QColor("#F9F9F9"))
            
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        
        if hasattr(self, 'windowEffect'):
            self.windowEffect.setMicaEffect(self.winId(), isDarkMode=is_dark)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'windowEffect'):
            self.windowEffect.addWindowAnimation(self.winId())
            
        self.apply_window_flags()
        self._update_background()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if hasattr(self, 'windowEffect'):
                self.windowEffect.addWindowAnimation(self.winId())

    def closeEvent(self, event):
        """保存窗口状态和布局比例"""
        geo = self.geometry()
        self.config.set("window_geometry", [geo.x(), geo.y(), geo.width(), geo.height()])
        super().closeEvent(event)

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        
        self.main_layout.setContentsMargins(0, 32, 0, 0)
        self.main_layout.setSpacing(0)
        
        self.titleBar.titleLabel.hide()
        self.titleBar.iconLabel.hide()
        
        self.gallery_interface = GalleryInterface(self.storage, self.clipboard, self.config, self)
        
        from PySide6.QtWidgets import QStackedWidget
        self.stacked_widget = QStackedWidget(self)
        self.stacked_widget.addWidget(self.gallery_interface)
        
        self.setting_interface = SettingInterface(self.config, self)
        self.setting_interface.settings_changed.connect(self.on_settings_changed)
        self.stacked_widget.addWidget(self.setting_interface)
        
        self.main_layout.addWidget(self.stacked_widget)
        
    def show_settings(self):
        """暴露给外部托盘图标调用的接口，用于切换到设置页"""
        self.stacked_widget.setCurrentWidget(self.setting_interface)
        self.showNormal()
        self.activateWindow()
        
    def show_gallery(self):
        """暴露给外部托盘图标调用的接口，用于切回主面板"""
        self.stacked_widget.setCurrentWidget(self.gallery_interface)
        self.showNormal()
        self.activateWindow()


    def _init_global_hotkey(self):
        self.hotkey_signal = HotkeySignal()
        self.hotkey_signal.activated.connect(self.toggle_window)
        self.current_hotkey = None
        self.bind_global_hotkey()

    def bind_global_hotkey(self):
        try:
            if self.current_hotkey:
                keyboard.remove_hotkey(self.current_hotkey)
        except Exception:
            pass
            
        hotkey_str = self.config.get("global_hotkey", "ctrl+shift+e")
        if hotkey_str:
            try:
                keyboard.add_hotkey(hotkey_str, self.hotkey_signal.activated.emit)
                self.current_hotkey = hotkey_str
            except Exception as e:
                print(f"Failed to bind hotkey: {e}")

    def toggle_window(self):
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self._update_background()
            self.show_gallery()
            self.showNormal()
            self.activateWindow()
            self.raise_()
            user32.SetForegroundWindow(ctypes.c_void_p(int(self.winId())))

    def apply_window_flags(self):
        """
        使用 user32.SetWindowPos 动态修改置顶状态，
        避免使用 self.setWindowFlag 导致重新创建窗口句柄而破坏无边框缩放特性
        """
        always_on_top = self.config.get("always_on_top", True)
        HWND_TOPMOST = ctypes.c_void_p(-1)
        HWND_NOTOPMOST = ctypes.c_void_p(-2)
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        
        insert_after = HWND_TOPMOST if always_on_top else HWND_NOTOPMOST
        user32.SetWindowPos(ctypes.c_void_p(int(self.winId())), insert_after, 0, 0, 0, 0, flags)

    def on_settings_changed(self, changed_key=""):
        """当设置界面修改了配置时被调用，按需局部刷新"""
        if changed_key == "always_on_top":
            self.apply_window_flags()
            
        elif changed_key == "global_hotkey":
            self.bind_global_hotkey()
            
        elif changed_key == "theme_mode":
            from qfluentwidgets import setTheme, Theme
            theme_mode = self.config.get("theme_mode", "system")
            if theme_mode == "dark":
                setTheme(Theme.DARK)
            elif theme_mode == "light":
                setTheme(Theme.LIGHT)
            else:
                setTheme(Theme.AUTO)
                
            self._update_background()
            
            self.gallery_interface.refresh_gallery()
            self.gallery_interface.sidebar.update_theme()
            
        elif changed_key in ["sidebar_icon_size", "show_sidebar_tooltip"]:
            self.gallery_interface.force_refresh_sidebar_icons()
            
        else:
            self.apply_window_flags()
            self.bind_global_hotkey()

    def _init_paste_shortcut(self):
        """全局 Ctrl+V 拦截"""
        paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        paste_shortcut.activated.connect(self.handle_global_paste)

    def handle_global_paste(self):
        self.gallery_interface.handle_global_paste()
