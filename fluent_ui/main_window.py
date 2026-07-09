import os
import ctypes

from PySide6.QtCore import Qt, QObject, Signal, QSize, QEvent, QTimer
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
        self.hotkey_listener = None
        self.bind_global_hotkey()

    def _parse_pynput_hotkey(self, hotkey_str):
        """将 'ctrl+shift+e' 转换为 pynput 格式 '<ctrl>+<shift>+e'"""
        parts = hotkey_str.lower().split('+')
        pynput_parts = []
        for part in parts:
            part = part.strip()
            if part in ('ctrl', 'shift', 'alt', 'win'):
                pynput_parts.append(f"<{part}>")
            else:
                pynput_parts.append(part)
        return "+".join(pynput_parts)

    def bind_global_hotkey(self):
        try:
            if self.hotkey_listener:
                self.hotkey_listener.stop()
                self.hotkey_listener = None
        except Exception:
            pass
            
        hotkey_str = self.config.get("global_hotkey", "ctrl+shift+e")
        if hotkey_str:
            try:
                from pynput import keyboard
                pynput_hotkey = self._parse_pynput_hotkey(hotkey_str)
                
                def on_activate():
                    self.hotkey_signal.activated.emit()
                    
                self.hotkey_listener = keyboard.GlobalHotKeys({
                    pynput_hotkey: on_activate
                })
                self.hotkey_listener.start()
            except Exception as e:
                print(f"Failed to bind pynput hotkey: {e}")

    def toggle_window(self):
        if self.isVisible() and self.isActiveWindow():
            # 修复：在隐藏前强制重置标题栏按钮状态
            if hasattr(self, 'titleBar') and hasattr(self.titleBar, 'closeBtn'):
                self.titleBar.closeBtn.setState(0) # 0 通常代表 Normal 状态
                leave_event = QEvent(QEvent.Leave)
                QApplication.sendEvent(self.titleBar.closeBtn, leave_event)
                
            # 记录滚动位置
            if hasattr(self, 'gallery_interface'):
                self.gallery_interface.save_scroll_position()
                
            self.hide()
        else:
            self._update_background()
            self.show_gallery()
            
            # 唤醒时也重置一次，双重保险
            if hasattr(self, 'titleBar') and hasattr(self.titleBar, 'closeBtn'):
                self.titleBar.closeBtn.setState(0)
                leave_event = QEvent(QEvent.Leave)
                QApplication.sendEvent(self.titleBar.closeBtn, leave_event)
            
            self.showNormal()
            self.activateWindow()
            self.raise_()
            user32.SetForegroundWindow(ctypes.c_void_p(int(self.winId())))
            
            # 在下一帧恢复滚动位置
            if hasattr(self, 'gallery_interface'):
                QTimer.singleShot(0, self.gallery_interface.restore_scroll_position)

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

    def nativeEvent(self, eventType, message):
        """监听 Windows 底层消息，处理睡眠唤醒后快捷键失效的问题"""
        try:
            msg = message.contents
            # WM_POWERBROADCAST = 0x0218
            if msg.message == 0x0218:
                # PBT_APMRESUMEAUTOMATIC = 0x0012 (系统自动唤醒)
                # PBT_APMRESUMESUSPEND = 0x0007 (系统唤醒并恢复交互)
                if msg.wParam == 0x0012 or msg.wParam == 0x0007:
                    # 延迟重新绑定快捷键，确保系统钩子机制已完全恢复
                    QTimer.singleShot(2000, self.bind_global_hotkey)
        except Exception:
            pass
        return super().nativeEvent(eventType, message)
