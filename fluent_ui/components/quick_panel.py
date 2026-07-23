import os
import ctypes
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QApplication
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint
from PySide6.QtGui import QCursor, QGuiApplication
from qfluentwidgets import SearchLineEdit, ScrollArea, isDarkTheme, BodyLabel

from fluent_ui.components.emoji_card import EmojiCard
from fluent_ui.components.hover_preview import HoverPreviewPopup

user32 = ctypes.windll.user32

def get_window_class_name(hwnd):
    buff = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buff, 256)
    return buff.value

class QuickPanel(QWidget):
    def __init__(self, storage_service, clipboard_service, config_service, parent=None):
        super().__init__(parent)
        self.storage = storage_service
        self.clipboard = clipboard_service
        self.config = config_service
        
        # 设置为 Tool 窗口，无边框，置顶
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 初始化时读取一次尺寸（后续 show_at_cursor 会再次读取最新值）
        w = self.config.get("quick_panel_width", 360)
        h = self.config.get("quick_panel_height", 480)
        self.resize(w, h)
            
        # 目标窗口句柄（用于粘贴）
        self._target_hwnd = None
        
        self._all_card_widgets = []
        self._current_columns = 0
        
        # 布局常量
        self.LAYOUT_TOP_MARGIN = 0
        self.LAYOUT_SPACING = 8
        
        self._init_ui()
        
        # 悬停预览组件
        self.preview_popup = HoverPreviewPopup(self)
        # 实例级修改 flag，避免抢占焦点导致 QuickPanel 隐藏
        self.preview_popup.setWindowFlags(
            self.preview_popup.windowFlags() | 
            Qt.WindowTransparentForInput | 
            Qt.WindowDoesNotAcceptFocus
        )
        
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._show_preview_popup)
        self.current_hover_path = None
        
        # 搜索防抖
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search)
        
        self.update_theme()

    def _init_ui(self):
        self.layout = QVBoxLayout(self)
        # 恢复纯粹的视觉留白
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(0)
        
        # 背景容器
        self.bg_widget = QWidget(self)
        self.bg_widget.setObjectName("bg_widget")
        self.bg_layout = QVBoxLayout(self.bg_widget)
        self.bg_layout.setContentsMargins(10, 10, 10, 10)
        self.bg_layout.setSpacing(10)
        self.layout.addWidget(self.bg_widget)
        
        # 搜索框
        self.search_box = SearchLineEdit(self.bg_widget)
        self.search_box.setObjectName("quickPanelSearchBox")
        self.search_box.setPlaceholderText("搜索关键词...")
        self.search_box.textChanged.connect(self._on_search_text_changed)
        
        # 隐藏 qfluentwidgets 默认的聚焦底部粗边框 (由 paintEvent 绘制)
        self.search_box.setCustomFocusedBorderColor(Qt.transparent, Qt.transparent)
        
        # 使用 setCustomStyleSheet 追加样式，保留原有的 padding、圆角和字体颜色
        # 使用 ID 选择器提高优先级，确保覆盖默认的 LineEdit:focus[transparent=true] 样式
        from qfluentwidgets import setCustomStyleSheet
        light_qss = """
            SearchLineEdit#quickPanelSearchBox {
                border: 1px solid rgba(0, 0, 0, 0.08);
                background-color: rgba(255, 255, 255, 0.7);
            }
            SearchLineEdit#quickPanelSearchBox:hover {
                background-color: rgba(249, 249, 249, 0.5);
            }
            SearchLineEdit#quickPanelSearchBox:focus {
                border: 1px solid rgba(0, 0, 0, 0.08);
                background-color: rgba(255, 255, 255, 0.9);
            }
        """
        dark_qss = """
            SearchLineEdit#quickPanelSearchBox {
                border: 1px solid rgba(255, 255, 255, 0.08);
                background-color: rgba(255, 255, 255, 0.05);
            }
            SearchLineEdit#quickPanelSearchBox:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            SearchLineEdit#quickPanelSearchBox:focus {
                border: 1px solid rgba(255, 255, 255, 0.08);
                background-color: rgba(255, 255, 255, 0.05);
            }
        """
        setCustomStyleSheet(self.search_box, light_qss, dark_qss)
        
        self.bg_layout.addWidget(self.search_box)
        
        # 滚动区域
        self.scroll_area = ScrollArea(self.bg_widget)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("QWidget { background-color: transparent; }")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(8)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll_area.setWidget(self.grid_container)
        self.bg_layout.addWidget(self.scroll_area)
        
        # 空状态提示
        self.empty_label = BodyLabel("暂无最近使用记录", self.grid_container)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: gray;")
        self.empty_label.hide()
        
        # 安装事件过滤器以支持滚轮缩放
        self.scroll_area.viewport().installEventFilter(self)
        
        # 监听滚动条实现懒加载
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
        # 滚动节流定时器
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        self._lazy_load_timer.timeout.connect(self._apply_lazy_loading)

    def _on_scroll(self, value):
        self._lazy_load_timer.start(32)
        
        from fluent_ui.components.emoji_card import ThumbnailCache
        ThumbnailCache().reset_idle_timer()

    def _apply_lazy_loading(self):
        if not hasattr(self, '_all_card_widgets') or not self._all_card_widgets:
            return
            
        scrollbar = self.scroll_area.verticalScrollBar()
        scroll_y = scrollbar.value()
        viewport_height = self.scroll_area.viewport().height()
        
        visible_top = scroll_y
        visible_bottom = scroll_y + viewport_height
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("quick_panel_thumbnail_size", 72) + self.LAYOUT_SPACING)))
        current_size = self.config.get("quick_panel_thumbnail_size", 72)
        
        for index, widget in enumerate(self._all_card_widgets):
            row = index // columns
            
            card_y = self.LAYOUT_TOP_MARGIN + row * (current_size + self.LAYOUT_SPACING)
            card_bottom = card_y + current_size
            
            is_far_above = card_bottom < visible_top - 500
            is_far_below = card_y > visible_bottom + 500
            is_near = (card_bottom >= visible_top - 200) and (card_y <= visible_bottom + 200)
            
            if is_far_above or is_far_below:
                if getattr(widget, '_is_loaded', True):
                    widget.clear_resources()
            elif is_near:
                if widget.needs_reload(current_size):
                    widget.update_size(current_size, load_image=True)

    def set_target_hwnd(self, hwnd):
        self._target_hwnd = hwnd

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QPainterPath
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        path = QPainterPath()
        # 留出 1px 边距防止裁剪
        path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, 8, 8)
        
        if isDarkTheme():
            painter.fillPath(path, QColor(32, 32, 32, 240))
            painter.setPen(QColor(68, 68, 68))
        else:
            painter.fillPath(path, QColor(249, 249, 249, 240))
            painter.setPen(QColor(204, 204, 204))
            
        painter.drawPath(path)

    def update_theme(self):
        # 背景绘制已移至 paintEvent，这里只需触发重绘
        self.update()

    def _on_search_text_changed(self, text):
        self.search_timer.start(150)

    def _perform_search(self):
        keyword = self.search_box.text().strip()
        limit = self.config.get("recent_limit", 30)
        
        if not keyword:
            # 初始状态或清空搜索框时，显示最近使用
            results = self.storage.get_recent_images()
            results = results[:limit]
        else:
            results = self.storage.search_images(keyword, "全部表情")
            # 限制结果数量，保证性能
            results = results[:30]
        
        # 清理旧结果
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget and widget != self.empty_label:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                
        self._all_card_widgets.clear()
                
        if not results:
            self.empty_label.setText("没有找到匹配的表情" if keyword else "暂无最近使用记录")
            self.empty_label.show()
            self.grid_layout.addWidget(self.empty_label, 0, 0)
            return
            
        self.empty_label.hide()
                
        card_size = self.config.get("quick_panel_thumbnail_size", 72)
        
        # 填充新结果
        for path in results:
            # 初始化时不加载图片，交给懒加载处理
            card = EmojiCard(path, size=card_size, parent=self.grid_container)
            card.update_size(card_size, load_image=False)
            card._is_loaded = False
            
            card.clicked.connect(self._on_card_clicked)
            card.hover_started.connect(self._on_card_hover_started)
            card.hover_ended.connect(self._on_card_hover_ended)
            self._all_card_widgets.append(card)
            
        # 如果窗口已经可见（用户在搜索框中输入），则立即触发布局
        # 如果窗口不可见（刚唤醒），则等待 show_at_cursor 中的延迟触发
        if self.isVisible():
            self._trigger_responsive_layout(force=True)

    def _trigger_responsive_layout(self, force=False):
        if not self._all_card_widgets:
            return
            
        card_size = self.config.get("quick_panel_thumbnail_size", 72)
        available_width = self.scroll_area.viewport().width() - 16
        columns = max(1, available_width // (card_size + self.LAYOUT_SPACING))
        
        if self._current_columns != columns or force:
            self._current_columns = columns
            self._rearrange_gallery(columns)

    def _rearrange_gallery(self, columns):
        self.grid_container.setUpdatesEnabled(False)
        
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            # 不删除 widget，只是从 layout 中移除
            
        for index, widget in enumerate(self._all_card_widgets):
            row = index // columns
            col = index % columns
            self.grid_layout.addWidget(widget, row, col)
            widget.show()
            
        self.grid_container.setUpdatesEnabled(True)
        self.grid_container.update()
        
        # 重排后触发懒加载
        self._apply_lazy_loading()

    def _apply_thumbnail_size(self, size):
        # 第一阶段：仅更新尺寸，不判断可见性
        for widget in self._all_card_widgets:
            widget.update_size(size, load_image=False)
                
        self._trigger_responsive_layout(force=True)
        
        # 第二阶段：等待 layout 完成后，异步刷新可见区域
        QTimer.singleShot(0, self._refresh_visible_thumbnails)

    def _refresh_visible_thumbnails(self):
        if not hasattr(self, '_all_card_widgets') or not self._all_card_widgets:
            return
            
        scrollbar = self.scroll_area.verticalScrollBar()
        scroll_y = scrollbar.value()
        viewport_height = self.scroll_area.viewport().height()
        
        visible_top = scroll_y - 100
        visible_bottom = scroll_y + viewport_height + 100
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("quick_panel_thumbnail_size", 72) + self.LAYOUT_SPACING)))
        current_size = self.config.get("quick_panel_thumbnail_size", 72)
        
        for index, widget in enumerate(self._all_card_widgets):
            row = index // columns
            card_y = self.LAYOUT_TOP_MARGIN + row * (current_size + self.LAYOUT_SPACING)
            card_bottom = card_y + current_size
            
            if card_bottom >= visible_top and card_y <= visible_bottom:
                if widget.needs_reload(current_size):
                    widget.update_size(current_size, load_image=True)

    def eventFilter(self, obj, event):
        if obj == self.scroll_area.viewport() and event.type() == event.Type.Wheel:
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                delta = event.angleDelta().y()
                step = 6 if delta > 0 else -6
                
                current_size = self.config.get("quick_panel_thumbnail_size", 72)
                new_size = max(40, min(200, current_size + step))
                
                if new_size != current_size:
                    self.config.set("quick_panel_thumbnail_size", new_size)
                    self._apply_thumbnail_size(new_size)
                    
                return True
        return super().eventFilter(obj, event)

    def _on_card_clicked(self, path, modifiers=None):
        self.clipboard.copy_image_to_clipboard(path)
        limit = self.config.get("recent_limit", 30)
        self.storage.add_recent_image(path, limit)
        self.hide_panel()
        
        # 尝试恢复焦点并粘贴
        if self._target_hwnd and user32.IsWindow(self._target_hwnd):
            class_name = get_window_class_name(self._target_hwnd)
            system_classes = (
                "Progman", "WorkerW", "Shell_TrayWnd", 
                "CabinetWClass", "ExploreWClass", "Windows.UI.Core.CoreWindow"
            )
            if class_name not in system_classes:
                # 如果窗口被最小化，先恢复
                if user32.IsIconic(self._target_hwnd):
                    user32.ShowWindow(self._target_hwnd, 9) # SW_RESTORE
                    
                # 强制恢复焦点
                user32.SetForegroundWindow(self._target_hwnd)
                # 轮询确认焦点切换成功，带超时兜底
                self._paste_attempts = 0
                self._check_focus_and_paste()

    def _check_focus_and_paste(self):
        current_hwnd = user32.GetForegroundWindow()
        if current_hwnd == self._target_hwnd or self._paste_attempts >= 10:
            # 焦点已切换成功，或者已经尝试了 10 次（约 100ms），执行粘贴
            self._simulate_paste()
        else:
            self._paste_attempts += 1
            QTimer.singleShot(10, self._check_focus_and_paste)

    def _simulate_paste(self):
        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    def _on_card_hover_started(self, path):
        self.current_hover_path = path
        delay = self.config.get("preview_delay", 500)
        self.hover_timer.start(delay)

    def _on_card_hover_ended(self):
        self.hover_timer.stop()
        self.current_hover_path = None
        self.preview_popup.hide_preview()

    def _show_preview_popup(self):
        if not self.current_hover_path or not self.isVisible():
            return
            
        global_pos = QCursor.pos()
        size_config = self.config.get("preview_size", 320)
        
        cats = self.storage.get_categories_by_image(self.current_hover_path)
        cat_str = ", ".join(cats) if cats else "无"
        kw_str = self.storage.get_image_keywords(self.current_hover_path) or "无"
        
        self.preview_popup.show_preview(
            self.current_hover_path, 
            global_pos, 
            size_config, 
            cat_str, 
            kw_str
        )

    def show_at_cursor(self):
        # 每次唤醒时读取最新的宽高配置
        w = self.config.get("quick_panel_width", 360)
        h = self.config.get("quick_panel_height", 480)
        if self.width() != w or self.height() != h:
            self.resize(w, h)
            
        self.update_theme()
        self.search_box.clear()
        self._perform_search()
        
        # 计算位置
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
            
        avail_geo = screen.availableGeometry()
        
        x = cursor_pos.x() + 10
        y = cursor_pos.y() + 10
        
        if x + self.width() > avail_geo.right():
            x = cursor_pos.x() - self.width() - 10
            
        if y + self.height() > avail_geo.bottom():
            y = cursor_pos.y() - self.height() - 10
            
        self.move(x, y)
        self.show()
        
        # 延迟触发布局计算，确保窗口已经显示且 viewport 宽度准确
        QTimer.singleShot(0, lambda: self._trigger_responsive_layout(force=True))
        
        # 恢复强制系统级前台和聚焦
        user32.SetForegroundWindow(int(self.winId()))
        self.activateWindow()
        self.search_box.setFocus()

    def hide_panel(self):
        self.hover_timer.stop()
        self.preview_popup.hide_preview()
        self.hide()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小改变时，重新计算列数并重排
        if self.isVisible():
            self._trigger_responsive_layout()

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                self.hide_panel()
        super().changeEvent(event)
