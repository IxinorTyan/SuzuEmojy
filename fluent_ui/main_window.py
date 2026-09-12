import os
import ctypes
import time

from PySide6.QtCore import Qt, QObject, Signal, QSize, QEvent, QTimer
from PySide6.QtGui import QIcon, QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication

from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import MessageBox
from qframelesswindow import FramelessWindow, StandardTitleBar

from ctypes import wintypes

from fluent_ui.views.gallery_view import GalleryInterface
from fluent_ui.views.setting_view import SettingInterface, AboutInterface
from fluent_ui.views.exchange_view import ExchangeInterface
from fluent_ui.views.qq_scan_view import QQScanInterface
from fluent_ui.views.tg_sticker_view import TGStickerInterface

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

# 显式声明 Win32 API 的参数类型，避免 64 位冻结程序中 HWND 被截断。
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    wintypes.LPCVOID,
    wintypes.DWORD,
]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

class HotkeySignal(QObject):
    """跨线程桥接信号"""
    activated = Signal()
    quick_activated = Signal()

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
        
        self._last_window_animation_time = 0.0
        self._animation_cooldown = 0.25

        # 合并短时间内重复的全局快捷键事件，避免窗口动画和状态切换堆积
        self._toggle_window_requests = 0
        self._toggle_window_timer = QTimer(self)
        self._toggle_window_timer.setSingleShot(True)
        self._toggle_window_timer.timeout.connect(self._flush_toggle_window)
        self._toggle_quick_panel_requests = 0
        self._toggle_quick_panel_timer = QTimer(self)
        self._toggle_quick_panel_timer.setSingleShot(True)
        self._toggle_quick_panel_timer.timeout.connect(self._flush_toggle_quick_panel)

        self._init_window()
        self._init_ui()
        self._init_global_hotkey()
        self._init_paste_shortcut()
        self._update_background()
        self.apply_rounded_corners()
        self.apply_window_flags()

    def _init_window(self):
        self.setWindowTitle("SuzuEmojy")
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ico.ico")
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
            
        self.ensure_window_visible()

    def ensure_window_visible(self):
        """
        检查窗口是否完全位于所有屏幕的可用区域之外。
        如果是，则将其移动到主屏幕中央，防止高DPI或多显示器配置改变导致窗口丢失。
        """
        from PySide6.QtGui import QGuiApplication
        
        frame_geo = self.frameGeometry()
        is_visible = False
        
        for screen in QGuiApplication.screens():
            if frame_geo.intersects(screen.availableGeometry()):
                is_visible = True
                break
                
        if not is_visible:
            primary_screen = QGuiApplication.primaryScreen()
            if primary_screen:
                available_geo = primary_screen.availableGeometry()
                new_x = available_geo.x() + (available_geo.width() - frame_geo.width()) // 2
                new_y = available_geo.y() + (available_geo.height() - frame_geo.height()) // 2
                self.move(new_x, new_y)

    def _update_background(self):
        """处理深色模式背景"""
        from qfluentwidgets import isDarkTheme, setThemeColor
        from PySide6.QtGui import QPalette, QColor
        from fluent_ui.theme import get_current_background_color, get_current_accent_color
        
        palette = self.palette()
        is_dark = isDarkTheme()
        
        # 更新强调色
        accent_color = get_current_accent_color(self.config, is_dark)
        setThemeColor(accent_color)
        
        # 更新背景色
        bg_color = get_current_background_color(self.config, is_dark)
        self.setStyleSheet(f"MainWindow {{ background-color: {bg_color.name()}; }}")
        palette.setColor(QPalette.Window, bg_color)
        
        icon_color = QColor(255, 255, 255) if is_dark else QColor(0, 0, 0)
            
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        
        if hasattr(self, 'windowEffect'):
            self.windowEffect.setMicaEffect(self.winId(), isDarkMode=is_dark)

        # 更新标题栏按钮颜色
        if hasattr(self, 'titleBar'):
            for btn in [self.titleBar.minBtn, self.titleBar.maxBtn]:
                if btn:
                    btn.setNormalColor(icon_color)
                    btn.setHoverColor(icon_color)
                    btn.setPressedColor(icon_color)
                    btn.update()
                    
            if self.titleBar.closeBtn:
                self.titleBar.closeBtn.setNormalColor(icon_color)
                self.titleBar.closeBtn.setHoverColor(QColor(255, 255, 255))
                self.titleBar.closeBtn.setPressedColor(QColor(255, 255, 255))
                self.titleBar.closeBtn.update()

    def _play_window_animation(self):
        """限制窗口动画频率，避免快速切换时动画请求堆积。"""
        now = time.monotonic()
        if now - self._last_window_animation_time < self._animation_cooldown:
            return
        self._last_window_animation_time = now
        if hasattr(self, 'windowEffect'):
            self.windowEffect.addWindowAnimation(self.winId())

    def showEvent(self, event):
        super().showEvent(event)
        self._play_window_animation()

        self.apply_rounded_corners()
        # qframelesswindow/Windows 可能在显示阶段重新调整 Z 序，
        # 因此在窗口真正显示后再次应用置顶状态。
        self.apply_window_flags()
        QTimer.singleShot(0, self.apply_rounded_corners)
        QTimer.singleShot(0, self.apply_window_flags)
        QTimer.singleShot(100, self.apply_rounded_corners)
        QTimer.singleShot(100, self.apply_window_flags)

    def _reset_close_button_state(self):
        """窗口隐藏前清除关闭按钮残留的 hover/pressed 绘制状态。"""
        close_btn = getattr(getattr(self, "titleBar", None), "closeBtn", None)
        if close_btn is None:
            return

        # TitleBarButton 的 NORMAL 状态值为 0。窗口隐藏时可能收不到
        # mouseRelease/leave，必须无条件清理，而不能只判断 isPressed()。
        close_btn.setState(0)
        QApplication.sendEvent(close_btn, QEvent(QEvent.Leave))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._play_window_animation()
        elif event.type() == QEvent.ActivationChange:
            # 激活切换只重新确认置顶状态，不修改关闭按钮的正常 hover 状态。
            QTimer.singleShot(0, self.apply_window_flags)

    def closeEvent(self, event):
        """保存窗口状态和布局比例"""
        geo = self.geometry()
        self.config.set("window_geometry", [geo.x(), geo.y(), geo.width(), geo.height()])
        if hasattr(self, 'qq_scan_interface') and hasattr(self.qq_scan_interface, 'shutdown'):
            self.qq_scan_interface.shutdown()
        if hasattr(self, 'tg_sticker_interface') and hasattr(self.tg_sticker_interface, 'shutdown'):
            self.tg_sticker_interface.shutdown()
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
        self.setting_interface.back_requested.connect(self.show_gallery)
        self.setting_interface.about_requested.connect(self.show_about)
        self.stacked_widget.addWidget(self.setting_interface)

        # 关于软件详情页面
        self.about_interface = AboutInterface(self)
        self.about_interface.back_requested.connect(self.show_settings)
        self.stacked_widget.addWidget(self.about_interface)

        # 导出导入页面
        self.exchange_interface = ExchangeInterface(self)
        self.exchange_interface.back_requested.connect(self.show_gallery)
        self.exchange_interface.import_requested.connect(self.gallery_interface._import_exchange_package)
        self.exchange_interface.export_all_requested.connect(self.gallery_interface._export_all_exchange_package)
        self.exchange_interface.export_selected_requested.connect(self.gallery_interface._export_selected_categories_exchange_package)
        self.exchange_interface.qq_scan_requested.connect(self.show_qq_scan)
        self.exchange_interface.tg_sticker_requested.connect(self.show_tg_sticker)
        self.stacked_widget.addWidget(self.exchange_interface)

        # QQ扫描页面
        self.qq_scan_interface = QQScanInterface(self, self.config)
        self.qq_scan_interface.back_requested.connect(self.show_exchange)
        self.stacked_widget.addWidget(self.qq_scan_interface)

        # TG贴纸下载页面
        self.tg_sticker_interface = TGStickerInterface(self)
        self.tg_sticker_interface.back_requested.connect(self.show_exchange)
        self.stacked_widget.addWidget(self.tg_sticker_interface)
        
        self.gallery_interface.setting_requested.connect(self.show_settings)
        self.gallery_interface.exchange_requested.connect(self.show_exchange)
        
        self.main_layout.addWidget(self.stacked_widget)
        
        from fluent_ui.components.quick_panel import QuickPanel
        self.quick_panel = QuickPanel(self.storage, self.clipboard, self.config)
        
    def show_settings(self):
        """暴露给外部托盘图标调用的接口，用于切换到设置页"""
        self.stacked_widget.setCurrentWidget(self.setting_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()
        
    def show_gallery(self, refresh=True):
        """切回主面板；快捷键唤醒时应使用 refresh=False 的轻量路径。"""
        # 通过标题栏关闭后，按钮可能保留 pressed/hover 状态；
        # 每次重新唤醒主面板时统一恢复为正常状态。
        self._reset_close_button_state()
        
        if hasattr(self, 'gallery_interface') and refresh:
            self.gallery_interface.sidebar.refresh_list(self.gallery_interface.current_category)
            self.gallery_interface.on_images_changed()
            
        self.stacked_widget.setCurrentWidget(self.gallery_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()

    def show_about(self):
        """切换到关于软件详情页"""
        self.stacked_widget.setCurrentWidget(self.about_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()

    def show_exchange(self):
        """切换到导出导入界面"""
        self.stacked_widget.setCurrentWidget(self.exchange_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()

    def show_qq_scan(self):
        """切换到QQ扫描界面"""
        self.stacked_widget.setCurrentWidget(self.qq_scan_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()

    def show_tg_sticker(self):
        """切换到TG贴纸下载界面"""
        self.stacked_widget.setCurrentWidget(self.tg_sticker_interface)
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self._reapply_window_flags_after_show()


    def _reapply_window_flags_after_show(self):
        """在窗口显示或被唤醒后重新应用 Windows 置顶状态及圆角。"""
        self.apply_rounded_corners()
        self.apply_window_flags()
        # 分多个时间点重试，覆盖 Windows 激活、无边框窗口调整 Z 序的异步过程。
        for delay in (0, 100, 500, 1000):
            QTimer.singleShot(delay, self.apply_rounded_corners)
            QTimer.singleShot(delay, self.apply_window_flags)


    def _init_global_hotkey(self):
        self.hotkey_signal = HotkeySignal()
        self.hotkey_signal.activated.connect(self._request_toggle_window)
        self.hotkey_signal.quick_activated.connect(self._request_toggle_quick_panel)
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
        quick_hotkey_str = self.config.get("quick_panel_hotkey", "alt+2")
        
        hotkeys_dict = {}
        
        try:
            from pynput import keyboard
            
            if hotkey_str:
                pynput_hotkey = self._parse_pynput_hotkey(hotkey_str)
                def on_activate():
                    self.hotkey_signal.activated.emit()
                hotkeys_dict[pynput_hotkey] = on_activate
                
            if quick_hotkey_str:
                pynput_quick_hotkey = self._parse_pynput_hotkey(quick_hotkey_str)
                def on_quick_activate():
                    self.hotkey_signal.quick_activated.emit()
                hotkeys_dict[pynput_quick_hotkey] = on_quick_activate
                
            if hotkeys_dict:
                self.hotkey_listener = keyboard.GlobalHotKeys(hotkeys_dict)
                self.hotkey_listener.start()
        except Exception as e:
            print(f"Failed to bind pynput hotkey: {e}")

    def _request_toggle_window(self):
        self._toggle_window_requests += 1
        self._toggle_window_timer.start(40)

    def _flush_toggle_window(self):
        requests = self._toggle_window_requests
        self._toggle_window_requests = 0
        if requests % 2:
            self.toggle_window()

    def _request_toggle_quick_panel(self):
        self._toggle_quick_panel_requests += 1
        self._toggle_quick_panel_timer.start(40)

    def _flush_toggle_quick_panel(self):
        requests = self._toggle_quick_panel_requests
        self._toggle_quick_panel_requests = 0
        if requests % 2:
            self.toggle_quick_panel()

    def toggle_quick_panel(self):
        if self.quick_panel.isVisible():
            self.quick_panel.hide_panel()
        else:
            # 在面板显示前，记录当前的前台窗口句柄
            hwnd = user32.GetForegroundWindow()
            if hwnd and hwnd != int(self.winId()) and hwnd != int(self.quick_panel.winId()):
                self.quick_panel.set_target_hwnd(hwnd)
            self.quick_panel.show_at_cursor()

    def toggle_window(self):
        if self.isVisible() and self.isActiveWindow():
            # 在隐藏前强制重置标题栏按钮状态
            self._reset_close_button_state()
                
            # 记录滚动位置
            if hasattr(self, 'gallery_interface'):
                self.gallery_interface.save_scroll_position()
                
            self.hide()
        else:
            self.show_gallery(refresh=False)
            
            self.showNormal()
            self.activateWindow()
            self.raise_()
            user32.SetForegroundWindow(ctypes.c_void_p(int(self.winId())))
            
            # 在下一帧恢复滚动位置
            if hasattr(self, 'gallery_interface'):
                # 卡片采用分帧懒加载，延迟恢复可避免布局尚未完成时被截断。
                QTimer.singleShot(0, self.gallery_interface.restore_scroll_position)
                QTimer.singleShot(100, self.gallery_interface.restore_scroll_position)
                QTimer.singleShot(300, self.gallery_interface.restore_scroll_position)

    def apply_rounded_corners(self):
        """
        强制指定 Windows 11 DWM 窗口圆角。
        避免 qframelesswindow 动态添加 WS_THICKFRAME/WS_CAPTION 等原生样式时，
        Windows 11 将无边框窗口回退为直角矩形。
        """
        try:
            hwnd = int(self.winId())
            if not hwnd or not user32.IsWindow(hwnd):
                return False

            preference = wintypes.DWORD(DWMWCP_ROUND)
            hr = dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
            if hr != 0:
                return False

            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags)
            return True
        except (AttributeError, TypeError, ValueError, OSError):
            return False

    def apply_window_flags(self):
        """
        使用 user32.SetWindowPos 动态修改置顶状态，
        避免使用 self.setWindowFlag 导致重新创建窗口句柄而破坏无边框缩放特性。
        """
        try:
            hwnd = int(self.winId())
            if not hwnd or not user32.IsWindow(hwnd):
                return False

            always_on_top = bool(self.config.get("always_on_top", True))
            HWND_TOPMOST = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

            insert_after = HWND_TOPMOST if always_on_top else HWND_NOTOPMOST
            result = user32.SetWindowPos(
                hwnd, insert_after, 0, 0, 0, 0, flags
            )
            if not result:
                print(f"[WARNING] SetWindowPos failed, error={ctypes.get_last_error()}")
            return bool(result)
        except (AttributeError, TypeError, ValueError, OSError) as error:
            print(f"[WARNING] Failed to apply window topmost state: {error}")
            return False

    def on_settings_changed(self, changed_key=""):
        """当设置界面修改了配置时被调用，按需局部刷新"""
        if changed_key == "always_on_top":
            self.apply_window_flags()
            
        elif changed_key == "show_setting_button":
            is_show = self.config.get("show_setting_button", True)
            self.gallery_interface.btn_setting.setVisible(is_show)
            
        elif changed_key in ["global_hotkey", "quick_panel_hotkey"]:
            self.bind_global_hotkey()
            
        elif changed_key in ["appearance_mode", "light_theme_key", "dark_theme_key"]:
            from qfluentwidgets import setTheme, Theme
            appearance_mode = self.config.get("appearance_mode", self.config.get("theme_mode", "system"))
            if appearance_mode == "dark":
                setTheme(Theme.DARK)
            elif appearance_mode == "light":
                setTheme(Theme.LIGHT)
            else:
                setTheme(Theme.AUTO)
                
            self._update_background()
            
            # 通知色块选择器更新颜色
            if hasattr(self.setting_interface, 'themeColorCard'):
                self.setting_interface.themeColorCard.update_colors()
            
            self.gallery_interface.refresh_gallery()
            self.gallery_interface.sidebar.update_theme()
            self.quick_panel.update_theme()
            
        elif changed_key in ["sidebar_icon_size", "show_sidebar_tooltip"]:
            self.gallery_interface.force_refresh_sidebar_icons()
            
        elif changed_key == "use_system_font":
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.warning(
                title="需要重启",
                content="字体设置已更改，请重启软件以应用更改。",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self
            )
            
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
        """监听 Windows 底层消息，处理睡眠唤醒后快捷键失效的问题，以及系统主题切换"""
        try:
            msg = message.contents
            # WM_POWERBROADCAST = 0x0218
            if msg.message == 0x0218:
                # PBT_APMRESUMEAUTOMATIC = 0x0012 (系统自动唤醒)
                # PBT_APMRESUMESUSPEND = 0x0007 (系统唤醒并恢复交互)
                if msg.wParam == 0x0012 or msg.wParam == 0x0007:
                    # 延迟重新绑定快捷键，确保系统钩子机制已完全恢复
                    QTimer.singleShot(2000, self.bind_global_hotkey)
                    QTimer.singleShot(500, self._reapply_window_flags_after_show)
            # WM_DWMCOMPOSITIONCHANGED = 0x031E
            elif msg.message == 0x031E:
                QTimer.singleShot(50, self.apply_rounded_corners)
            # WM_SETTINGCHANGE = 0x001A
            elif msg.message == 0x001A:
                # 系统设置改变（包括深浅色模式切换）
                appearance_mode = self.config.get("appearance_mode", self.config.get("theme_mode", "system"))
                if appearance_mode == "system":
                    # 延迟一点点执行，确保 qfluentwidgets 已经处理完系统主题切换
                    QTimer.singleShot(100, lambda: self.on_settings_changed("appearance_mode"))
        except Exception:
            pass
        return super().nativeEvent(eventType, message)
