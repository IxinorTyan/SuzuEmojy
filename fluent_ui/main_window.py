import os
import ctypes
import ctypes.wintypes

from PySide6.QtCore import Qt, QObject, Signal, QSize, QEvent, QAbstractNativeEventFilter, QCoreApplication
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

class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    """拦截 Windows 原生消息以处理 RegisterHotKey"""
    def __init__(self, hotkey_id, callback):
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG" or eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(message.__int__())
            # WM_HOTKEY = 0x0312
            if msg.message == 0x0312 and msg.wParam == self.hotkey_id:
                self.callback()
                return True, 0
        return False, 0

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
        self.hotkey_id = 0x1A2B # 自定义一个唯一的快捷键 ID
        self.native_filter = GlobalHotkeyFilter(self.hotkey_id, self.toggle_window)
        QCoreApplication.instance().installNativeEventFilter(self.native_filter)
        self.bind_global_hotkey()

    def _parse_hotkey_string(self, hotkey_str):
        """将类似 'ctrl+shift+e' 的字符串解析为 Windows API 需要的 modifiers 和 vk"""
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        
        modifiers = 0
        vk = 0
        
        parts = hotkey_str.lower().split('+')
        for part in parts:
            part = part.strip()
            if part == 'ctrl':
                modifiers |= MOD_CONTROL
            elif part == 'shift':
                modifiers |= MOD_SHIFT
            elif part == 'alt':
                modifiers |= MOD_ALT
            elif part == 'win':
                modifiers |= MOD_WIN
            else:
                # 假设最后一个部分是普通按键
                if len(part) == 1:
                    vk = ord(part.upper())
                else:
                    # 处理特殊按键，这里仅做简单映射，可根据需要扩展
                    key_map = {
                        'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
                        'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
                        'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
                        'space': 0x20, 'enter': 0x0D, 'esc': 0x1B, 'tab': 0x09
                    }
                    vk = key_map.get(part, 0)
                    
        return modifiers, vk

    def bind_global_hotkey(self):
        # 先注销旧的快捷键
        user32.UnregisterHotKey(ctypes.c_void_p(int(self.winId())), self.hotkey_id)
            
        hotkey_str = self.config.get("global_hotkey", "ctrl+shift+e")
        if hotkey_str:
            modifiers, vk = self._parse_hotkey_string(hotkey_str)
            if vk != 0:
                # 注册新的快捷键
                success = user32.RegisterHotKey(ctypes.c_void_p(int(self.winId())), self.hotkey_id, modifiers, vk)
                if not success:
                    print(f"Failed to bind native hotkey: {hotkey_str}")

    def toggle_window(self):
        if self.isVisible() and self.isActiveWindow():
            # 修复：在隐藏前强制重置标题栏按钮状态
            if hasattr(self, 'titleBar') and hasattr(self.titleBar, 'closeBtn'):
                self.titleBar.closeBtn.setState(0) # 0 通常代表 Normal 状态
                leave_event = QEvent(QEvent.Leave)
                QApplication.sendEvent(self.titleBar.closeBtn, leave_event)
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
        """监听 Windows 底层消息"""
        try:
            msg = message.contents
            # WM_POWERBROADCAST = 0x0218
            if msg.message == 0x0218:
                # PBT_APMRESUMEAUTOMATIC = 0x0012 (系统自动唤醒)
                # PBT_APMRESUMESUSPEND = 0x0007 (系统唤醒并恢复交互)
                if msg.wParam == 0x0012 or msg.wParam == 0x0007:
                    # 唤醒后重新注册一次原生快捷键以防万一
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(2000, self.bind_global_hotkey)
        except Exception:
            pass
        return super().nativeEvent(eventType, message)
