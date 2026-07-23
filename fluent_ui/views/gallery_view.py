import os
import ctypes
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QApplication, QHBoxLayout, QVBoxLayout, 
    QListWidget, QListWidgetItem, QInputDialog, QLineEdit
)
from PySide6.QtCore import Qt, QTimer, QSize, QThread, Signal
from PySide6.QtGui import QCursor, QIcon
from qfluentwidgets import (
    ScrollArea, InfoBar, InfoBarPosition, RoundMenu, Action, 
    PushButton, FluentIcon as FIF, TransparentToolButton, setFont, BodyLabel
)

from fluent_ui.components.emoji_card import EmojiCard
from fluent_ui.components.hover_preview import HoverPreviewPopup

user32 = ctypes.windll.user32

FAILED_RETENTION_DAYS = 7
CLEANUP_THROTTLE_HOURS = 1

def get_window_class_name(hwnd):
    """获取窗口的类名"""
    buff = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buff, 256)
    return buff.value

class DownloadThread(QThread):
    finished = Signal(bool, str, str, str) # success, temp_filepath, error_msg, url
    
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        
    def run(self):
        import urllib.request
        import urllib.error
        import tempfile
        import os
        try:
            req = urllib.request.Request(self.url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=10) as response:
                # 检查 Content-Type
                content_type = response.headers.get('Content-Type', '').lower()
                is_image = content_type.startswith('image/')
                
                # 如果 Content-Type 不明确，检查 URL 后缀
                if not is_image:
                    import urllib.parse
                    parsed_url = urllib.parse.urlparse(self.url)
                    _, ext = os.path.splitext(parsed_url.path)
                    if ext.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                        is_image = True
                        
                if not is_image:
                    self.finished.emit(False, "", "该链接不是有效的图片资源", self.url)
                    return
                    
                content = response.read()
                
                # 写入临时文件
                fd, temp_path = tempfile.mkstemp(suffix=".tmp")
                with os.fdopen(fd, 'wb') as f:
                    f.write(content)
                    
                self.finished.emit(True, temp_path, "", self.url)
        except Exception as e:
            self.finished.emit(False, "", str(e), self.url)

class ImportThread(QThread):
    """后台异步导入文件的线程，防止批量导入时主线程卡死"""
    progress = Signal(int, int) # current, total
    finished = Signal(int, int, int) # saved_count, skipped_count, failed_count
    
    def __init__(self, filepaths, storage, target_category, delete_after=False, parent=None):
        super().__init__(parent)
        self.filepaths = filepaths
        self.storage = storage
        self.target_category = target_category
        self.delete_after = delete_after
        
    def _handle_failed_import(self, filepath):
        import os
        import shutil
        from datetime import datetime
        if self.delete_after and os.path.exists(filepath):
            abs_filepath = os.path.normcase(os.path.abspath(filepath))
            abs_inbox = os.path.normcase(os.path.abspath(self.storage.inbox_dir))
            if abs_filepath.startswith(abs_inbox) and not abs_filepath.startswith(os.path.normcase(os.path.abspath(self.storage.inbox_failed_dir))):
                try:
                    filename = os.path.basename(filepath)
                    target_path = os.path.join(self.storage.inbox_failed_dir, filename)
                    if os.path.exists(target_path):
                        name, ext = os.path.splitext(filename)
                        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                        target_path = os.path.join(self.storage.inbox_failed_dir, f"{name}_{timestamp}{ext}")
                    shutil.move(filepath, target_path)
                except Exception as e:
                    print(f"[ERROR] 移动失败文件到 failed 目录失败: {e}")
            else:
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    def run(self):
        import os
        saved_count = 0
        skipped_count = 0
        failed_count = 0
        total = len(self.filepaths)
        
        from services.webm_converter import convert_video_to_gif
        import tempfile
        
        for i, filepath in enumerate(self.filepaths):
            is_webm = filepath.lower().endswith('.webm')
            is_webp = filepath.lower().endswith('.webp')
            
            needs_conversion = False
            if is_webm:
                needs_conversion = True
            elif is_webp:
                # 检查是否是动态 webp
                try:
                    from PIL import Image
                    with Image.open(filepath) as img:
                        if getattr(img, "is_animated", False):
                            needs_conversion = True
                except Exception:
                    pass
                    
            temp_gif_path = None
            
            if needs_conversion:
                try:
                    fd, temp_gif_path = tempfile.mkstemp(suffix=".gif")
                    os.close(fd)
                    convert_video_to_gif(filepath, temp_gif_path)
                    process_path = temp_gif_path
                except Exception as e:
                    print(f"[ERROR] Video to GIF conversion failed: {e}")
                    self._handle_failed_import(filepath)
                    failed_count += 1
                    self.progress.emit(i + 1, total)
                    continue
            else:
                process_path = filepath
                
            saved_path, is_duplicate = self.storage.save_file(process_path)
            
            if temp_gif_path and os.path.exists(temp_gif_path):
                try:
                    os.remove(temp_gif_path)
                except Exception:
                    pass
                    
            if saved_path:
                if is_duplicate:
                    skipped_count += 1
                else:
                    saved_count += 1
                    
                if self.target_category not in ("全部表情", "未分类"):
                    self.storage.add_image_to_category(saved_path, self.target_category)
                    
                if self.delete_after and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
            else:
                self._handle_failed_import(filepath)
                failed_count += 1
            
            # 每处理一个文件汇报一次进度
            self.progress.emit(i + 1, total)
            
        self.finished.emit(saved_count, skipped_count, failed_count)

class CategoryListWidget(QListWidget):
    """支持拖拽排序的分类列表，依赖原生 InternalMove 机制防止数据丢失"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 自定义拖拽自动滚动
        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.timeout.connect(self._do_auto_scroll)
        self.scroll_direction = 0
        
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder"):
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.auto_scroll_timer.stop()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder"):
            event.accept()
            
            # 处理自动滚动
            pos_y = event.pos().y()
            viewport_height = self.viewport().height()
            margin = 30
            
            if pos_y < margin:
                self.scroll_direction = -1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(16)
            elif pos_y > viewport_height - margin:
                self.scroll_direction = 1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(16)
            else:
                self.auto_scroll_timer.stop()
                
            super().dragMoveEvent(event)
        else:
            super().dragMoveEvent(event)

    def _do_auto_scroll(self):
        scrollbar = self.verticalScrollBar()
        current_val = scrollbar.value()
        step = 10 # 滚动速度
        scrollbar.setValue(current_val + (step * self.scroll_direction))
            
    def dropEvent(self, event):
        self.auto_scroll_timer.stop()
        if event.source() == self:
            current_item = self.currentItem()
            if not current_item:
                event.ignore()
                return
                
            current_row = self.row(current_item)
            
            if current_row == 0 or current_row == self.count() - 1:
                event.ignore()
                return
                
            drop_pos = event.pos()
            target_item = self.itemAt(drop_pos)
            target_row = self.row(target_item) if target_item else self.count() - 2
            
            if target_row <= 0:
                event.ignore()
                return
            if target_row >= self.count() - 1:
                event.ignore()
                return

            super().dropEvent(event)
            
            if hasattr(self.parent(), 'on_categories_reordered'):
                QTimer.singleShot(50, self.parent().on_categories_reordered)
        elif event.mimeData().hasFormat("application/x-emojy-reorder"):
            source_path = bytes(event.mimeData().data("application/x-emojy-reorder")).decode('utf-8')
            drop_pos = event.pos()
            target_item = self.itemAt(drop_pos)
            
            if target_item:
                target_cat = target_item.data(Qt.UserRole)
                if not target_cat: target_cat = target_item.text()
                
                if target_cat and target_cat not in ("全部表情", "新建分类"):
                    if hasattr(self.parent(), 'storage') and hasattr(self.parent(), 'gallery_view'):
                        if self.parent().storage.add_image_to_category(source_path, target_cat):
                            self.parent().gallery_view.show_success("添加成功", f"已快速添加到分类 '{target_cat}'")
                            # 如果当前在未分类视图，添加后需要刷新以移除该图片
                            if self.parent().gallery_view.filter_state.unclassified:
                                self.parent().gallery_view.remove_card_by_path(source_path)
            event.accept()
        else:
            event.ignore()

    def wheelEvent(self, event):
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            parent_sidebar = self.parent()
            if getattr(parent_sidebar, 'is_grid_mode', False):
                delta = event.angleDelta().y()
                step = 10 if delta > 0 else -10
                
                if hasattr(parent_sidebar, 'config') and parent_sidebar.config:
                    current_size = parent_sidebar.config.get("category_grid_icon_size", 64)
                    # 将下限降低到与列表模式默认图标大小相近（比如 20 或 24）
                    new_size = max(20, min(200, current_size + step))
                    
                    if new_size != current_size:
                        parent_sidebar.config.set("category_grid_icon_size", new_size)
                        parent_sidebar.refresh_list(self.currentItem().data(Qt.UserRole) if self.currentItem() else "全部表情")
                return
        super().wheelEvent(event)


class CategorySidebar(QWidget):
    """图库内部的左侧分类导航栏"""
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.gallery_view = parent # 引用父级 GalleryInterface
        self.config = parent.config if parent else None
        
        self.setMinimumWidth(60)
        self.setMaximumWidth(400)
        self.setObjectName("CategorySidebar")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)

        # 列表区域（使用支持拖拽的自定义类）
        self.list_widget = CategoryListWidget(self)
        
        icon_size = self.config.get("sidebar_icon_size", 20) if self.config else 20
        self.list_widget.setIconSize(QSize(icon_size, icon_size))
        
        # 开启右键菜单
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        
        layout.addWidget(self.list_widget)
        
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        
        self.is_grid_mode = self.config.get("sidebar_is_grid_mode", False) if self.config else False
        
        self.update_theme()
        self._apply_grid_mode(self.is_grid_mode, is_init=True)
        self.refresh_list()

    def _on_item_double_clicked(self, item):
        cat_name = item.data(Qt.UserRole)
        if cat_name == "全部表情":
            self.toggle_grid_mode()

    def toggle_grid_mode(self):
        self.is_grid_mode = not self.is_grid_mode
        if self.config:
            self.config.set("sidebar_is_grid_mode", self.is_grid_mode)
        self._apply_grid_mode(self.is_grid_mode, is_init=False)
        self.refresh_list(self.list_widget.currentItem().data(Qt.UserRole) if self.list_widget.currentItem() else "全部表情")

    def _apply_grid_mode(self, is_grid, is_init=False):
        """应用网格或列表模式的 UI 设置"""
        if is_grid:
            # 切换到网格模式
            self.setMaximumWidth(16777215) # 解除最大宽度限制，允许自由拖拽
            self.list_widget.setViewMode(QListWidget.ListMode)
            self.list_widget.setFlow(QListWidget.LeftToRight)
            self.list_widget.setWrapping(True)
            self.list_widget.setResizeMode(QListWidget.Adjust)
            self.list_widget.setDragDropMode(QListWidget.InternalMove)
            self.list_widget.setSpacing(10)
            self.list_widget.setWordWrap(True)
            
            # 恢复网格模式保存的宽度 (仅在非初始化时执行，初始化由 GalleryInterface._init_ui 统一处理)
            if not is_init and self.gallery_view and hasattr(self.gallery_view, 'splitter'):
                splitter = self.gallery_view.splitter
                total_width = sum(splitter.sizes())
                target_width = self.config.get("sidebar_width_grid", 300) if self.config else 300
                target_width = min(target_width, total_width - 100) # 保证右侧至少留 100px
                splitter.setSizes([target_width, total_width - target_width])
                if hasattr(self.gallery_view, '_trigger_responsive_layout'):
                    QTimer.singleShot(10, lambda: self.gallery_view._trigger_responsive_layout(force=True))
        else:
            # 切换回列表模式
            self.setMaximumWidth(400) # 恢复普通模式的最大宽度限制
            self.list_widget.setViewMode(QListWidget.ListMode)
            self.list_widget.setFlow(QListWidget.TopToBottom)
            self.list_widget.setWrapping(False)
            self.list_widget.setResizeMode(QListWidget.Fixed)
            self.list_widget.setDragDropMode(QListWidget.InternalMove)
            self.list_widget.setSpacing(0)
            self.list_widget.setWordWrap(False)
            
            # 恢复列表模式保存的宽度 (仅在非初始化时执行)
            if not is_init and self.gallery_view and hasattr(self.gallery_view, 'splitter'):
                splitter = self.gallery_view.splitter
                total_width = sum(splitter.sizes())
                target_width = self.config.get("sidebar_width_list", 140) if self.config else 140
                target_width = min(target_width, 400) # 列表模式最大 400px
                splitter.setSizes([target_width, total_width - target_width])
                if hasattr(self.gallery_view, '_trigger_responsive_layout'):
                    QTimer.singleShot(10, lambda: self.gallery_view._trigger_responsive_layout(force=True))

    def update_theme(self):
        from qfluentwidgets import isDarkTheme
        if isDarkTheme():
            self.setStyleSheet("""
                QWidget#CategorySidebar {
                    background-color: transparent;
                    border-right: 1px solid rgba(255, 255, 255, 0.1);
                }
                QListWidget {
                    border: none;
                    background-color: transparent;
                    outline: none;
                    color: white;
                }
                QListWidget::item {
                    border-radius: 6px;
                    padding: 8px;
                    margin: 2px 8px;
                }
                QListWidget::item:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                }
                QListWidget::item:selected {
                    background-color: rgba(0, 120, 212, 0.2);
                    color: #4CC2FF;
                    font-weight: bold;
                }
            """)
        else:
            self.setStyleSheet("""
                QWidget#CategorySidebar {
                    background-color: transparent;
                    border-right: 1px solid rgba(0, 0, 0, 0.1);
                }
                QListWidget {
                    border: none;
                    background-color: transparent;
                    outline: none;
                    color: black;
                }
                QListWidget::item {
                    border-radius: 6px;
                    padding: 8px;
                    margin: 2px 8px;
                }
                QListWidget::item:hover {
                    background-color: rgba(0, 0, 0, 0.05);
                }
                QListWidget::item:selected {
                    background-color: rgba(0, 120, 212, 0.1);
                    color: #0078D4;
                    font-weight: bold;
                }
            """)
            
        # 刷新列表以重新生成图标颜色
        if hasattr(self, 'list_widget') and self.list_widget.count() > 0:
            current_item = self.list_widget.currentItem()
            current_cat = current_item.data(Qt.UserRole) if current_item else "全部表情"
            self.refresh_list(current_cat)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 网格模式下不触发外观变化
        if getattr(self, 'is_grid_mode', False):
            return
            
        # 根据当前真实宽度自动切换模式
        if self.width() < 100:
            self.set_icon_only_mode(True)
        else:
            self.set_icon_only_mode(False)

    def on_categories_reordered(self):
        """当拖放导致顺序改变时被调用，同步给 storage"""
        categories = self.storage.get_all_categories()
        new_categories = {}
        for i in range(1, self.list_widget.count() - 1):
            cat_name = self.list_widget.item(i).data(Qt.UserRole)
            if cat_name and cat_name in categories:
                new_categories[cat_name] = categories[cat_name]
                
        for k, v in categories.items():
            if k not in new_categories:
                new_categories[k] = v
                
        self.storage.save_categories(new_categories)

    def refresh_list(self, select_category="全部表情"):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        
        # 根据模式决定图标大小
        if getattr(self, 'is_grid_mode', False):
            icon_size = self.config.get("category_grid_icon_size", 64) if self.config else 64
            self.list_widget.setIconSize(QSize(icon_size, icon_size))
            self.list_widget.setGridSize(QSize(icon_size + 20, icon_size + 20))
        else:
            icon_size = self.config.get("sidebar_icon_size", 20) if self.config else 20
            self.list_widget.setIconSize(QSize(icon_size, icon_size))
            self.list_widget.setGridSize(QSize()) # 清除网格大小限制
        
        item_all = QListWidgetItem(FIF.HOME.icon(), "全部表情")
        item_all.setData(Qt.UserRole, "全部表情")
        if getattr(self, 'is_grid_mode', False):
            item_all.setTextAlignment(Qt.AlignCenter)
        self.list_widget.addItem(item_all)
        
        categories = self.storage.get_all_categories()
        icons_dict = self.storage.get_all_category_icons()
        
        from PySide6.QtGui import QPixmap, QPainter, QFont
        
        for cat in categories.keys():
            if cat in ("全部表情", "未分类", "新建分类"): continue
            
            icon_val = icons_dict.get(cat)
            nav_icon = FIF.FOLDER.icon()
            
            if icon_val:
                # 判断是否是图片路径（通过后缀名判断，因为现在存的是文件名，可能不包含路径分隔符）
                is_image_path = any(icon_val.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp'])
                
                if is_image_path:
                    if os.path.exists(icon_val):
                        nav_icon = QIcon(icon_val)
                else:
                    # 认为是 Emoji 字符串（支持多个字符组合的复杂 Emoji）
                    # 根据当前模式动态调整渲染分辨率
                    render_size = icon_size * 2 if getattr(self, 'is_grid_mode', False) else 64
                    pixmap = QPixmap(render_size, render_size)
                    pixmap.fill(Qt.transparent)
                    painter = QPainter(pixmap)
                    font = painter.font()
                    # 使用 setPointSize 替代 setPixelSize，避免触发 Qt 底层的 -1 警告
                    # 1 point ≈ 1.33 pixels，所以乘以 0.75 进行换算
                    font.setPointSize(max(1, int(render_size * 0.75 * 0.75)))
                    font.setFamily("Segoe UI Emoji")
                    painter.setFont(font)
                    painter.drawText(pixmap.rect(), Qt.AlignCenter, icon_val)
                    painter.end()
                    nav_icon = QIcon(pixmap)
                
            item = QListWidgetItem(nav_icon, cat)
            item.setData(Qt.UserRole, cat)
            if getattr(self, 'is_grid_mode', False):
                item.setTextAlignment(Qt.AlignCenter)
            self.list_widget.addItem(item)
            
        item_add = QListWidgetItem(FIF.ADD.icon(), "新建分类")
        item_add.setData(Qt.UserRole, "新建分类")
        if getattr(self, 'is_grid_mode', False):
            item_add.setTextAlignment(Qt.AlignCenter)
        self.list_widget.addItem(item_add)
        
        # 应用 icon_only 状态（网格模式下强制隐藏文字）
        if getattr(self, 'is_grid_mode', False):
            self._apply_icon_only_state(True)
        else:
            is_icon_only = getattr(self, '_is_icon_only', False)
            self._apply_icon_only_state(is_icon_only)
        
        self.list_widget.blockSignals(False)
        self.set_active_category(select_category)

    def set_icon_only_mode(self, is_icon_only):
        if getattr(self, '_is_icon_only', False) == is_icon_only:
            return
        self._is_icon_only = is_icon_only
        self._apply_icon_only_state(is_icon_only)
        
    def _apply_icon_only_state(self, is_icon_only):
        show_tooltip = self.config.get("show_sidebar_tooltip", True) if self.config else True
        
        if is_icon_only:
            self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                real_name = item.data(Qt.UserRole)
                item.setText("")
                if real_name:
                    item.setToolTip(real_name if show_tooltip else "")
        else:
            self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                real_name = item.data(Qt.UserRole)
                if real_name:
                    item.setText(real_name)
                    item.setToolTip("")

    def set_active_category(self, category_name):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == category_name:
                self.list_widget.setCurrentItem(item)
                return
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_item_changed(self, current, previous):
        if current:
            cat_name = current.data(Qt.UserRole)
            if not cat_name:
                cat_name = current.text()
                
            if cat_name == "新建分类":
                self.list_widget.blockSignals(True)
                if previous:
                    self.list_widget.setCurrentItem(previous)
                else:
                    self.list_widget.setCurrentRow(0)
                self.list_widget.blockSignals(False)
                
                QTimer.singleShot(50, self._create_new_category)
                return

            if self.gallery_view and hasattr(self.gallery_view, 'gallery_layout'):
                self.gallery_view.set_category(cat_name)

    def _create_new_category(self):
        name, ok = QInputDialog.getText(self, "新建分类", "请输入分类名称：", QLineEdit.Normal, "")
        if ok and name.strip():
            name = name.strip()
            if name != "全部表情" and name != "新建分类":
                if self.storage.add_category(name):
                    # 保持当前视图不变，不自动跳转到新分类
                    current_cat = self.gallery_view.current_category if self.gallery_view else "全部表情"
                    self.refresh_list(select_category=current_cat)
                    if self.gallery_view:
                        self.gallery_view.show_success("分类已创建")
                else:
                    from qfluentwidgets import MessageBox
                    w = MessageBox("错误", "分类已存在或名称不合法", self.window())
                    w.exec()

    def _show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item: return
        cat_name = item.data(Qt.UserRole)
        if not cat_name: cat_name = item.text()
        
        if cat_name in ("全部表情", "未分类", "新建分类"): return

        menu = RoundMenu(title="设置", parent=self)
        
        action_rename = Action("重命名", parent=menu)
        action_rename.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._rename_category(cat_name)))
        menu.addAction(action_rename)
        
        action_icon = Action("自定义 Emoji 图标", parent=menu)
        action_icon.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._custom_emoji_icon(cat_name)))
        menu.addAction(action_icon)
        
        action_reset_icon = Action("恢复默认图标", parent=menu)
        action_reset_icon.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._reset_icon(cat_name)))
        menu.addAction(action_reset_icon)
        
        menu.addSeparator()
        
        action_export = Action("导出此分类...", parent=menu)
        action_export.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._export_category(cat_name)))
        menu.addAction(action_export)
        
        menu.addSeparator()
        
        action_del = Action("删除文件夹", parent=menu)
        action_del.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._delete_category_with_confirm(cat_name)))
        menu.addAction(action_del)
        
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))

    def _get_unique_export_path(self, target_dir, original_name):
        """生成防冲突的导出路径"""
        base_name, ext = os.path.splitext(original_name)
        target_path = os.path.join(target_dir, original_name)
        counter = 1
        while os.path.exists(target_path):
            target_path = os.path.join(target_dir, f"{base_name}({counter}){ext}")
            counter += 1
        return target_path

    def _export_category(self, cat_name):
        from PySide6.QtWidgets import QFileDialog
        import shutil
        
        dir_path = QFileDialog.getExistingDirectory(self, f"选择导出 '{cat_name}' 的目标文件夹", "")
        if not dir_path:
            return
            
        # 在目标目录下创建分类文件夹，防冲突
        export_dir = os.path.join(dir_path, cat_name)
        counter = 1
        while os.path.exists(export_dir):
            export_dir = os.path.join(dir_path, f"{cat_name}({counter})")
            counter += 1
            
        try:
            os.makedirs(export_dir)
            images = self.storage.get_images_by_category(cat_name)
            count = 0
            for img_path in images:
                if os.path.exists(img_path):
                    filename = os.path.basename(img_path)
                    target_path = self._get_unique_export_path(export_dir, filename)
                    shutil.copy2(img_path, target_path)
                    count += 1
            if self.gallery_view:
                self.gallery_view.show_success("导出成功", f"已将 {count} 个表情导出到\n{export_dir}")
        except Exception as e:
            if self.gallery_view:
                self.gallery_view.show_error("导出失败", str(e))

    def _rename_category(self, old_name):
        new_name, ok = QInputDialog.getText(self, "重命名", "请输入新的分类名称：", QLineEdit.Normal, old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            categories = self.storage.get_all_categories()
            if new_name in categories:
                from qfluentwidgets import MessageBox
                w = MessageBox("错误", "分类已存在", self.window())
                w.exec()
                return
                
            # 更新 categories
            new_categories = {}
            for k, v in categories.items():
                if k == old_name:
                    new_categories[new_name] = v
                else:
                    new_categories[k] = v
            self.storage.save_categories(new_categories)
            
            # 同步更新图标配置
            icons = self.storage.get_all_category_icons()
            if old_name in icons:
                icons[new_name] = icons.pop(old_name)
                self.storage.save_category_icons(icons)
                
            self.refresh_list(new_name)
            if self.gallery_view: self.gallery_view.show_success("重命名成功")

    def _custom_emoji_icon(self, cat_name):
        emoji, ok = QInputDialog.getText(self, "自定义图标", "请输入一个 Emoji 表情：", QLineEdit.Normal, "")
        if ok and emoji.strip():
            self.storage.set_category_icon(cat_name, emoji.strip())
            self.refresh_list(cat_name)

    def _reset_icon(self, cat_name):
        icons = self.storage.get_all_category_icons()
        if cat_name in icons:
            del icons[cat_name]
            self.storage.save_category_icons(icons)
            self.refresh_list(cat_name)

    def _delete_category_with_confirm(self, cat_name):
        from qfluentwidgets import MessageBoxBase, SubtitleLabel, CheckBox
        
        is_suzu = cat_name.lower() == "suzu"
        
        class DeleteConfirmBox(MessageBoxBase):
            def __init__(self, parent=None):
                super().__init__(parent)
                
                if is_suzu:
                    self.titleLabel = SubtitleLabel("你真的要删掉饺子醋吗(哭哭)")
                else:
                    self.titleLabel = SubtitleLabel("删除确认")
                    
                self.checkbox = CheckBox("是否同时彻底删除该文件夹内所含的表情？")
                
                self.viewLayout.addWidget(self.titleLabel)
                self.viewLayout.addWidget(BodyLabel(f"即将删除分类：'{cat_name}'\n此操作不可逆。"))
                self.viewLayout.addWidget(self.checkbox)
                
                self.widget.setMinimumWidth(300)

        w = DeleteConfirmBox(self.window())
        if w.exec():
            delete_files = w.checkbox.isChecked()
            
            if delete_files:
                images_to_delete = self.storage.get_images_by_category(cat_name)
                self.storage.remove_category(cat_name)
                for img in images_to_delete:
                    other_cats = self.storage.get_categories_by_image(img)
                    if not other_cats: 
                        self.storage.delete_image(img)
            else:
                self.storage.remove_category(cat_name)
                
            self.refresh_list("全部表情")
            if self.gallery_view: 
                msg = "分类已删除(哭哭)" if is_suzu else "分类已删除"
                self.gallery_view.show_success(msg)

class FilterState:
    """统一的图片过滤状态"""
    def __init__(self):
        self.no_tag = False
        self.unclassified = False
        self.is_gif = False
        self.is_static = False
        
    def is_active(self):
        return self.no_tag or self.unclassified or self.is_gif or self.is_static

class GalleryInterface(QWidget):
    """
    重构后的 Gallery 视图，包含左侧分类栏和右侧表情网格
    """
    setting_requested = Signal()

    def __init__(self, storage_service, clipboard_service, config_service, parent=None):
        super().__init__(parent=parent)
        self.storage = storage_service
        self.clipboard = clipboard_service
        self.config = config_service
        self.setObjectName("GalleryInterface")
        
        # 内部状态
        self.current_category = "全部表情"
        self.search_keyword = ""
        self.filter_state = FilterState()
        self.last_active_window = None
        self.is_selection_mode = False
        self._inbox_scanning = False
        self._last_cleanup_time = 0
        
        self._init_ui()
        
        # 悬停预览组件
        self.preview_popup = HoverPreviewPopup(self)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._show_preview_popup)
        self.current_hover_path = None
        
        # 焦点追踪器，用于复制后自动粘贴
        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self.track_active_window)
        self.focus_timer.start(500)
        
        self.download_threads = []
        self.import_thread = None
        self.setAcceptDrops(True)
        
        # 拖拽自动滚动定时器
        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.timeout.connect(self._do_auto_scroll)
        self.scroll_direction = 0
        
        # 分批加载状态
        self._all_current_images = []
        self._loaded_count = 0
        self._batch_size = 50
        self._is_loading = False
        
        # 懒加载状态
        self._all_card_widgets = []
        self._cleanup_threshold = 200
        
        # 布局常量
        self.LAYOUT_TOP_MARGIN = 8
        self.LAYOUT_SPACING = 10
        
        # 滚动节流定时器
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        self._lazy_load_timer.timeout.connect(self._apply_lazy_loading)
        
        # 实时导入显示状态
        self._pending_import_images = []
        
        # 多选状态优化
        self.selected_paths = set()
        self._last_clicked_path = None
        self._anchor_from_selection_click = False
        self._shift_selected_paths = set()
        
        # 首次强制刷新
        self.sidebar.refresh_list("全部表情")
        
        # 滚动位置记录
        self._saved_scroll_position = 0

    def save_scroll_position(self):
        if hasattr(self, 'scroll_area'):
            self._saved_scroll_position = self.scroll_area.verticalScrollBar().value()

    def restore_scroll_position(self):
        if hasattr(self, 'scroll_area'):
            self.scroll_area.verticalScrollBar().setValue(self._saved_scroll_position)

    def _init_ui(self):
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        from PySide6.QtWidgets import QSplitter
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background: transparent;
                width: 4px;
            }
            QSplitter::handle:hover {
                background: rgba(0, 120, 212, 0.2);
            }
            QSplitter::handle:pressed {
                background: rgba(0, 120, 212, 0.5);
            }
        """)
        
        # 左侧边栏
        self.sidebar = CategorySidebar(self.storage, self)
        self.splitter.addWidget(self.sidebar)
        
        # 右侧内容区（包含顶部的搜索框和下方的网格）
        self.right_container = QWidget(self)
        self.right_container.setStyleSheet("QWidget { background-color: transparent; }")
        self.right_layout = QVBoxLayout(self.right_container)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(0)
        
        # 右侧顶部：搜索框区域
        self.top_bar = QWidget(self.right_container)
        self.top_bar.setFixedHeight(48)
        self.top_bar_layout = QHBoxLayout(self.top_bar)
        self.top_bar_layout.setContentsMargins(16, 8, 16, 8)
        
        from qfluentwidgets import SearchLineEdit, TransparentToolButton
        
        self.btn_multi_select = TransparentToolButton(FIF.TILES, self.top_bar)
        self.btn_multi_select.setToolTip("批量选择")
        self.btn_multi_select.clicked.connect(lambda: self.set_selection_mode(True))
        
        self.search_box = SearchLineEdit(self.top_bar)
        self.search_box.setPlaceholderText("搜索表情关键词...")
        self.search_box.setFixedWidth(240)
        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._execute_search)
        
        self.search_box.textChanged.connect(self.set_search_keyword)
        
        # 筛选按钮
        self.btn_filter = TransparentToolButton(FIF.FILTER, self.top_bar)
        self.btn_filter.setToolTip("筛选")
        self.btn_filter.clicked.connect(self._show_filter_menu)
        
        # 设置按钮
        self.btn_setting = TransparentToolButton(FIF.SETTING, self.top_bar)
        self.btn_setting.setToolTip("设置")
        self.btn_setting.setVisible(self.config.get("show_setting_button", True))
        self.btn_setting.clicked.connect(self.setting_requested.emit)
        
        self.top_bar_layout.addStretch() # 把搜索框推到右边
        self.top_bar_layout.addWidget(self.btn_multi_select)
        self.top_bar_layout.addWidget(self.btn_filter)
        self.top_bar_layout.addWidget(self.btn_setting)
        self.top_bar_layout.addWidget(self.search_box)
        
        self.right_layout.addWidget(self.top_bar)
        
        # 右侧下部：滚动区 (承载网格)
        self.scroll_area = ScrollArea(self.right_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("QWidget { background-color: transparent; }")
        self.gallery_layout = QGridLayout(self.grid_container)
        self.gallery_layout.setSpacing(10)
        self.gallery_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.gallery_layout.setContentsMargins(16, 8, 16, 16)
        
        self.scroll_area.setWidget(self.grid_container)
        self.right_layout.addWidget(self.scroll_area, stretch=1)
        
        # 底部控制条 (多选模式下显示)
        self.command_bar = QWidget(self.right_container)
        self.command_bar.setFixedHeight(56)
        self.command_bar.setStyleSheet("""
            QWidget {
                background-color: rgba(0, 120, 212, 0.1);
                border-top: 1px solid rgba(0, 120, 212, 0.2);
            }
        """)
        self.command_bar_layout = QHBoxLayout(self.command_bar)
        self.command_bar_layout.setContentsMargins(24, 0, 24, 0)
        
        self.selected_count_label = BodyLabel("已选择 0 项")
        self.btn_select_all = PushButton("全选")
        self.btn_exit_selection = PushButton("退出多选")
        
        self.command_bar_layout.addWidget(self.selected_count_label)
        self.command_bar_layout.addStretch()
        self.command_bar_layout.addWidget(self.btn_select_all)
        self.command_bar_layout.addWidget(self.btn_exit_selection)
        
        self.btn_select_all.clicked.connect(self.select_all_cards)
        self.btn_exit_selection.clicked.connect(lambda: self.set_selection_mode(False))
        
        self.right_layout.addWidget(self.command_bar)
        self.command_bar.hide()
        
        self.splitter.addWidget(self.right_container)
        
        # 恢复上次保存的侧边栏宽度
        is_grid = getattr(self.sidebar, 'is_grid_mode', False)
        if is_grid:
            target_width = self.config.get("sidebar_width_grid", 300)
        else:
            target_width = self.config.get("sidebar_width_list", 140)
            
        # 初始设置一个合理的总宽度比例，后续 resizeEvent 会自动调整右侧
        self.splitter.setSizes([target_width, 800])
        self.splitter.setCollapsible(0, False)
        
        # 初始化检查是否应直接进入图标模式 (仅在列表模式下生效)
        if not is_grid and target_width < 100:
            self.sidebar.set_icon_only_mode(True)
            self.splitter.setSizes([60, 800])
            
        self.main_layout.addWidget(self.splitter, stretch=1)
        
        # 响应 Splitter 拖动
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # 安装事件过滤器以便处理 ctrl+滚轮以及把手释放吸附
        self.scroll_area.viewport().installEventFilter(self)
        self.splitter.handle(1).installEventFilter(self)
        
        # 监听滚动条实现懒加载
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _apply_lazy_loading(self):
        if not hasattr(self, '_all_card_widgets'):
            return
            
        scrollbar = self.scroll_area.verticalScrollBar()
        scroll_y = scrollbar.value()
        viewport_height = self.scroll_area.viewport().height()
        
        visible_top = scroll_y
        visible_bottom = scroll_y + viewport_height
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("thumbnail_size", 120) + self.LAYOUT_SPACING)))
        current_size = self.config.get("thumbnail_size", 120)
        
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
                    widget.update_size(current_size)

    def _on_scroll(self, value):
        if not self._is_loading:
            scrollbar = self.scroll_area.verticalScrollBar()
            # 当滚动到距离底部 100px 以内时，加载下一批
            if scrollbar.maximum() - value < 100:
                self._load_next_batch()
                
        # 滚动节流：延迟 32ms 执行懒加载，合并高频滚动事件
        self._lazy_load_timer.start(32)
        
        # 重置空闲定时器
        from fluent_ui.components.emoji_card import ThumbnailCache
        ThumbnailCache().reset_idle_timer()

    def _on_splitter_moved(self, pos, index):
        sizes = self.splitter.sizes()
        if sizes:
            sidebar_width = sizes[0]
            if getattr(self.sidebar, 'is_grid_mode', False):
                self.config.set("sidebar_width_grid", sidebar_width)
            else:
                self.config.set("sidebar_width_list", sidebar_width)
        self._trigger_responsive_layout()

    def eventFilter(self, obj, event):
        if hasattr(self, 'splitter') and obj == self.splitter.handle(1):
            if event.type() == event.Type.MouseButtonRelease:
                sizes = self.splitter.sizes()
                if not sizes: return False
                
                sidebar_width = sizes[0]
                # 如果左侧边栏处于网格模式，不执行吸附逻辑，允许自由调整宽度
                if not getattr(self.sidebar, 'is_grid_mode', False):
                    if sidebar_width < 100:
                        diff = sidebar_width - 60
                        self.splitter.setSizes([60, sizes[1] + diff])
                        sidebar_width = 60
                    elif sidebar_width >= 100 and sidebar_width < 140:
                        diff = sidebar_width - 140
                        self.splitter.setSizes([140, sizes[1] + diff])
                        sidebar_width = 140
                        
                    self.config.set("sidebar_width_list", sidebar_width)
                else:
                    self.config.set("sidebar_width_grid", sidebar_width)

        if obj == self.scroll_area.viewport() and event.type() == event.Type.Wheel:
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                delta = event.angleDelta().y()
                step = 10 if delta > 0 else -10
                
                current_size = self.config.get("thumbnail_size", 120)
                new_size = max(60, min(300, current_size + step))
                
                if new_size != current_size:
                    self.config.set("thumbnail_size", new_size)
                    self._apply_thumbnail_size(new_size)
                    
                return True
        return super().eventFilter(obj, event)

    def _show_filter_menu(self):
        menu = RoundMenu(parent=self)
        
        # 辅助函数：根据选中状态返回对应的图标（选中显示对号，未选中显示透明占位图标以保证文字对齐）
        def get_check_icon(is_checked):
            from PySide6.QtGui import QPixmap
            if is_checked:
                return FIF.ACCEPT.icon()
            else:
                # 创建一个透明的 QPixmap 作为占位符，尺寸与标准图标一致
                pixmap = QPixmap(16, 16)
                pixmap.fill(Qt.transparent)
                return QIcon(pixmap)
        
        action_no_tag = Action(get_check_icon(self.filter_state.no_tag), "无关键词", parent=menu)
        action_no_tag.triggered.connect(lambda: self._update_filter('no_tag', not self.filter_state.no_tag))
        menu.addAction(action_no_tag)
        
        menu.addSeparator()
        
        action_unclassified = Action(get_check_icon(self.filter_state.unclassified), "未分类", parent=menu)
        action_unclassified.triggered.connect(lambda: self._update_filter('unclassified', not self.filter_state.unclassified))
        menu.addAction(action_unclassified)
        
        menu.addSeparator()
        
        action_gif = Action(get_check_icon(self.filter_state.is_gif), "动图", parent=menu)
        action_gif.triggered.connect(lambda: self._update_filter('is_gif', not self.filter_state.is_gif))
        menu.addAction(action_gif)
        
        action_static = Action(get_check_icon(self.filter_state.is_static), "静态图片", parent=menu)
        action_static.triggered.connect(lambda: self._update_filter('is_static', not self.filter_state.is_static))
        menu.addAction(action_static)
        
        if self.filter_state.is_active():
            menu.addSeparator()
            action_clear = Action("清除筛选", parent=menu)
            action_clear.triggered.connect(self._clear_filter)
            menu.addAction(action_clear)
            
        from PySide6.QtCore import QPoint
        pos = self.btn_filter.mapToGlobal(QPoint(0, self.btn_filter.height()))
        menu.exec(pos)

    def _update_filter(self, key, value):
        setattr(self.filter_state, key, value)
        self.on_images_changed()

    def _clear_filter(self):
        self.filter_state = FilterState()
        self.on_images_changed()

    def set_category(self, category_name):
        """由左侧边栏调用，切换显示的数据"""
        if self.is_selection_mode:
            self.set_selection_mode(False)
        self.current_category = category_name
        self.on_images_changed()
        
    def set_search_keyword(self, keyword):
        """由顶栏调用，切换搜索词（带防抖）"""
        self.search_keyword = keyword
        # 延迟 300ms 执行搜索，避免连续输入时卡顿
        self._search_timer.start(300)
        
    def _execute_search(self):
        self.on_images_changed()

    def show_success(self, title, content=""):
        InfoBar.success(title, content, duration=2000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def show_error(self, title, content=""):
        InfoBar.error(title, content, duration=3000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def track_active_window(self):
        hwnd = user32.GetForegroundWindow()
        if hwnd and hwnd != int(self.window().winId()):
            self.last_active_window = hwnd
            
        self._check_inbox()

    def _check_inbox(self):
        if self._inbox_scanning:
            return
            
        import time
        # 自动清理 failed 目录
        current_time = time.time()
        if current_time - self._last_cleanup_time > CLEANUP_THROTTLE_HOURS * 3600:
            self._last_cleanup_time = current_time
            self._cleanup_failed_inbox()
            
        if not os.path.exists(self.storage.inbox_dir):
            return
            
        self._inbox_scanning = True
        
        pending_files = []
        from services.webm_converter import is_ffmpeg_available
        ffmpeg_ready = is_ffmpeg_available()
        
        for filename in os.listdir(self.storage.inbox_dir):
            filepath = os.path.join(self.storage.inbox_dir, filename)
            if not os.path.isfile(filepath):
                continue
                
            if not filename.lower().endswith(self.storage.SUPPORTED_FORMATS):
                continue
                
            # 第一层检测：文件锁
            try:
                with open(filepath, 'a'):
                    pass
            except PermissionError:
                continue
            except Exception:
                continue
                
            pending_files.append(filepath)
            
        if not pending_files:
            self._inbox_scanning = False
            return
            
        # 记录初始大小
        initial_sizes = {}
        for filepath in pending_files:
            try:
                initial_sizes[filepath] = os.path.getsize(filepath)
            except FileNotFoundError:
                pass
                
        # 延迟 1 秒进行二次检测
        QTimer.singleShot(1000, lambda: self._verify_and_import_inbox(initial_sizes))

    def _verify_and_import_inbox(self, initial_sizes):
        ready_files = []
        for filepath, size1 in initial_sizes.items():
            try:
                size2 = os.path.getsize(filepath)
                if size1 == size2 and size1 > 0:
                    ready_files.append(filepath)
            except FileNotFoundError:
                pass
                
        if ready_files:
            self._start_background_import(ready_files, delete_after=True, silent=True)
            
        self._inbox_scanning = False

    def _cleanup_failed_inbox(self):
        if not os.path.exists(self.storage.inbox_failed_dir):
            return
            
        import time
        from datetime import datetime
        current_time = time.time()
        retention_seconds = FAILED_RETENTION_DAYS * 24 * 3600
        
        for filename in os.listdir(self.storage.inbox_failed_dir):
            filepath = os.path.join(self.storage.inbox_failed_dir, filename)
            if not os.path.isfile(filepath):
                continue
                
            try:
                mtime = os.path.getmtime(filepath)
                if current_time - mtime > retention_seconds:
                    os.remove(filepath)
                    print(f"[INFO] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 已自动清理过期失败文件: {filename}")
            except Exception as e:
                print(f"[ERROR] 清理失败文件 {filename} 报错: {e}")


    def _apply_thumbnail_size(self, size):
        # 第一阶段：仅更新尺寸，不判断可见性
        for widget in getattr(self, '_all_card_widgets', []):
            widget.update_size(size, load_image=False)
                
        self._trigger_responsive_layout(force=True)
        
        # 第二阶段：等待 layout 完成后，异步刷新可见区域
        QTimer.singleShot(16, self._refresh_visible_thumbnails)
        
        # 重置空闲定时器
        from fluent_ui.components.emoji_card import ThumbnailCache
        ThumbnailCache().reset_idle_timer()

    def _refresh_visible_thumbnails(self):
        if not hasattr(self, '_all_card_widgets') or not self._all_card_widgets:
            return
            
        scrollbar = self.scroll_area.verticalScrollBar()
        scroll_y = scrollbar.value()
        viewport_height = self.scroll_area.viewport().height()
        
        visible_top = scroll_y - 200
        visible_bottom = scroll_y + viewport_height + 200
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("thumbnail_size", 120) + self.LAYOUT_SPACING)))
        current_size = self.config.get("thumbnail_size", 120)
        
        for index, widget in enumerate(self._all_card_widgets):
            row = index // columns
            card_y = self.LAYOUT_TOP_MARGIN + row * (current_size + self.LAYOUT_SPACING)
            card_bottom = card_y + current_size
            
            if card_bottom >= visible_top and card_y <= visible_bottom:
                if widget.needs_reload(current_size):
                    widget.update_size(current_size, load_image=True)

    def showEvent(self, event):
        super().showEvent(event)
        self._trigger_responsive_layout(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._trigger_responsive_layout()

    def _trigger_responsive_layout(self, force=False):
        if not hasattr(self, 'scroll_area'): return
        item_width = self.config.get("thumbnail_size", 120) + self.LAYOUT_SPACING
        area_width = self.scroll_area.viewport().width() - 32
        columns = max(1, area_width // item_width)
        
        if getattr(self, '_current_columns', 0) != columns or force:
            self._current_columns = columns
            self._rearrange_gallery(columns)

    def _rearrange_gallery(self, columns):
        self.grid_container.setUpdatesEnabled(False)
        
        while self.gallery_layout.count():
            self.gallery_layout.takeAt(0)
            
        from PySide6.QtCore import QSignalBlocker
        
        for index, widget in enumerate(getattr(self, '_all_card_widgets', [])):
            row = index // columns
            col = index % columns
            
            blocker = QSignalBlocker(widget)
            self.gallery_layout.addWidget(widget, row, col)
            widget.show()
            del blocker
            
        self.grid_container.setUpdatesEnabled(True)
        self.grid_container.update()
        
        # 恢复懒加载定时器，接管缩放结束后的剩余加载任务
        self._lazy_load_timer.start(32)

    def remove_card_by_path(self, image_path):
        """局部刷新：仅移除指定的卡片并重排，避免全局重绘卡顿"""
        widget_to_remove = None
        for widget in getattr(self, '_all_card_widgets', []):
            if getattr(widget, 'image_path', None) == image_path:
                widget_to_remove = widget
                break
                
        if widget_to_remove:
            self.grid_container.setUpdatesEnabled(False)
            
            self.gallery_layout.removeWidget(widget_to_remove)
            widget_to_remove.hide()
            widget_to_remove.setParent(None)
            widget_to_remove.deleteLater()
            
            self._all_card_widgets.remove(widget_to_remove)
            
            if image_path in self._all_current_images:
                self._all_current_images.remove(image_path)
                self._loaded_count -= 1
                
            if image_path in self.selected_paths:
                self.selected_paths.remove(image_path)
                self.update_selection_count()
                
            columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("thumbnail_size", 120) + 10)))
            self._rearrange_gallery(columns)
            
            self.grid_container.setUpdatesEnabled(True)
            self.grid_container.update()

    def clear_gallery(self):
        """清空所有卡片并强制垃圾回收，释放内存"""
        if hasattr(self, '_render_timer') and self._render_timer.isActive():
            self._render_timer.stop()
            
        self.grid_container.setUpdatesEnabled(False)
            
        while self.gallery_layout.count():
            self.gallery_layout.takeAt(0)
            
        for widget in getattr(self, '_all_card_widgets', []):
            try:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            except RuntimeError:
                pass
                
        self._loaded_count = 0
        self._pending_import_images.clear()
        
        self._all_card_widgets = []
        
        self.grid_container.setUpdatesEnabled(True)
        self.grid_container.update()
        
        # 强制垃圾回收
        import gc
        gc.collect()

    def _matches_category(self, image_path, category_name, categories_map):
        if category_name == "全部表情": return True
        return category_name in categories_map.get(image_path, [])

    def _matches_keyword(self, image_path, keyword, metadata):
        if not keyword: return True
        img_kw = metadata.get(image_path, "").lower()
        return keyword in img_kw or keyword in os.path.basename(image_path).lower()

    def _matches_filter(self, image_path, filter_state, metadata, categories_map):
        if not filter_state.is_active(): return True
        
        if filter_state.no_tag and metadata.get(image_path, "").strip(): return False
        if filter_state.unclassified and categories_map.get(image_path): return False
        
        is_anim = self.storage.is_animated(image_path)
        if filter_state.is_gif and not filter_state.is_static and not is_anim: return False
        if filter_state.is_static and not filter_state.is_gif and is_anim: return False
        
        return True

    def _filter_images(self):
        """统一的图片过滤管道"""
        all_images = self.storage.get_all_images()
        metadata = self.storage.get_all_metadata()
        categories_map = self.storage.get_image_to_categories_map()
        
        keyword = self.search_keyword.strip().lower()
        
        return [
            img for img in all_images
            if self._matches_category(img, self.current_category, categories_map)
            and self._matches_keyword(img, keyword, metadata)
            and self._matches_filter(img, self.filter_state, metadata, categories_map)
        ]

    def on_images_changed(self):
        """统一的图片变更刷新入口"""
        # 1. 重新执行统一过滤管道
        self._all_current_images = self._filter_images()
        
        # 2. 清理多选状态中已经被过滤掉的图片
        if self.is_selection_mode:
            valid_selected = {p for p in self.selected_paths if p in self._all_current_images}
            if len(valid_selected) != len(self.selected_paths):
                self.selected_paths = valid_selected
                self.update_selection_count()
                
        # 3. 触发 UI 重绘 (复用现有的懒加载重排逻辑)
        self._is_loading = True
        self.clear_gallery()
        self._is_loading = False
        self._load_next_batch()
        
        # 重置空闲定时器
        from fluent_ui.components.emoji_card import ThumbnailCache
        ThumbnailCache().reset_idle_timer()

    def refresh_gallery(self):
        """兼容旧接口，直接调用统一刷新入口"""
        self.on_images_changed()

    def _load_next_batch(self):
        if self._is_loading or self._loaded_count >= len(self._all_current_images):
            return
            
        self._is_loading = True
        
        # 动态计算填满一屏需要的图片数量
        current_size = self.config.get("thumbnail_size", 120)
        item_width = current_size + 10
        item_height = current_size + 10
        
        viewport_width = self.scroll_area.viewport().width()
        viewport_height = self.scroll_area.viewport().height()
        
        # 确保初始启动时视口大小正确计算
        if viewport_width < 100 or viewport_height < 100:
            geometry = self.config.get("window_geometry", None)
            if geometry and len(geometry) == 4:
                viewport_width = max(100, geometry[2] - 140) # 减去侧边栏估算宽度
                viewport_height = max(100, geometry[3] - 100) # 减去顶栏等估算高度
            else:
                viewport_width = 860 - 140
                viewport_height = 640 - 100
        
        columns = max(1, viewport_width // item_width)
        visible_rows = max(1, (viewport_height // item_height) + 2) # 多预加载2行防止滚动白屏
        
        target_count = columns * visible_rows
        
        # 确保至少加载一屏，且不超过总数
        self._target_load_count = min(self._loaded_count + target_count, len(self._all_current_images))
        
        # 开始分帧渲染
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_chunk)
        self._render_timer.start(0) # 0ms 延迟，让出主线程后立即执行

    def _render_chunk(self):
        """分帧渲染核心逻辑，每次只渲染一小批，防止主线程卡死"""
        if self._loaded_count >= self._target_load_count:
            self._render_timer.stop()
            self._is_loading = False
            self._apply_lazy_loading()
            return
            
        # 获取用户设置的单次渲染上限
        max_batch_size = self.config.get("render_batch_size", 50)
        
        end_idx = min(self._loaded_count + max_batch_size, self._target_load_count)
        batch_images = self._all_current_images[self._loaded_count:end_idx]
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("thumbnail_size", 120) + 10)))
        current_size = self.config.get("thumbnail_size", 120)
        
        self.grid_container.setUpdatesEnabled(False)
        from PySide6.QtCore import QSignalBlocker
        
        for i, image_path in enumerate(batch_images):
            index = self._loaded_count + i
            row = index // columns
            col = index % columns
            
            card = EmojiCard(image_path, size=current_size)
            card.customContextMenuRequested.connect(lambda pos, w=card: self.show_context_menu(w, pos))
            card.clicked.connect(self.on_image_clicked)
            card.delete_requested.connect(self.on_delete_requested)
            
            card.hover_started.connect(self.on_hover_started)
            card.hover_ended.connect(self.on_hover_ended)
            
            card.set_selectable(self.is_selection_mode)
            # 恢复选中状态
            if image_path in self.selected_paths:
                card.set_selected(True)
                
            card.selection_changed.connect(self.on_selection_changed)
            
            if not hasattr(self, '_all_card_widgets'):
                self._all_card_widgets = []
            card._is_loaded = True
            self._all_card_widgets.append(card)
            
            blocker = QSignalBlocker(card)
            self.gallery_layout.addWidget(card, row, col)
            del blocker
            
        self._loaded_count = end_idx
        self.grid_container.setUpdatesEnabled(True)
        self.grid_container.update()

    # ================== 批量选择与交互逻辑 ==================

    def set_selection_mode(self, enabled):
        self.is_selection_mode = enabled
        self.command_bar.setVisible(enabled)
        self.btn_multi_select.setVisible(not enabled)
        
        if not enabled:
            self.selected_paths.clear()
            self._last_clicked_path = None
            self._anchor_from_selection_click = False
            self._shift_selected_paths.clear()
            
        for widget in getattr(self, '_all_card_widgets', []):
            widget.set_selectable(enabled)
            if not enabled:
                widget.set_selected(False)
                    
        self.update_selection_count()
        
    def update_selection_count(self):
        count = len(self.selected_paths)
        self.selected_count_label.setText(f"已选择 {count} 项")
        
    def get_selected_paths(self):
        return list(self.selected_paths)

    def on_selection_changed(self, path, selected):
        if selected:
            self.selected_paths.add(path)
        else:
            self.selected_paths.discard(path)
        self.update_selection_count()
        
    def select_all_cards(self):
        # 全选时，直接将当前分类下的所有图片路径加入集合
        is_all_selected = len(self.selected_paths) == len(self._all_current_images)
        
        if is_all_selected:
            self.selected_paths.clear()
        else:
            self.selected_paths = set(self._all_current_images)
            
        # 更新已渲染的卡片 UI
        for widget in getattr(self, '_all_card_widgets', []):
            widget.set_selected(not is_all_selected)
                
        self.update_selection_count()

    def _execute_batch_delete(self, paths):
        if not paths: return
        from qfluentwidgets import MessageBox
        dialog = MessageBox("批量删除确认", f"确定要彻底删除选中的 {len(paths)} 个表情包吗？", self.window())
        if dialog.exec():
            for p in paths:
                self.storage.delete_image(p)
            self.on_images_changed()
            self.show_success("批量删除成功")
            self.set_selection_mode(False)

    def _execute_batch_add(self, paths, cat_name):
        if not paths: return
        success_count = 0
        exist_count = 0
        error_count = 0
        
        for p in paths:
            res = self.storage.add_image_to_category(p, cat_name)
            if res == "success":
                success_count += 1
            elif res == "already_exists":
                exist_count += 1
            else:
                error_count += 1
                
        msg = f"成功添加 {success_count} 项。"
        if exist_count > 0:
            msg += f"\n跳过 {exist_count} 项 (目标分类已存在)。"
        if error_count > 0:
            msg += f"\n异常 {error_count} 项 (添加失败)。"
            
        if success_count > 0:
            self.show_success("批量添加完成", msg)
        else:
            self.show_error("批量添加未执行", msg)
            
        self.set_selection_mode(False)
        self.on_images_changed()

    def _execute_batch_move(self, paths, target_cat):
        if not paths: return
        success_count = 0
        exist_count = 0
        error_count = 0
        
        for p in paths:
            res = self.storage.add_image_to_category(p, target_cat)
            if res in ("success", "already_exists"):
                if res == "already_exists":
                    exist_count += 1
                if self.storage.remove_image_from_category(p, self.current_category):
                    success_count += 1
                else:
                    error_count += 1
            else:
                error_count += 1
                
        msg = f"成功移动 {success_count} 项。"
        if exist_count > 0:
            msg += f"\n其中 {exist_count} 项在目标分类已存在。"
        if error_count > 0:
            msg += f"\n异常 {error_count} 项 (移动失败)。"
            
        if success_count > 0:
            self.show_success("批量移动完成", msg)
        else:
            self.show_error("批量移动未执行", msg)
            
        self.set_selection_mode(False)
        self.on_images_changed()

    def _execute_batch_remove(self, paths):
        if not paths: return
        count = 0
        for p in paths:
            if self.storage.remove_image_from_category(p, self.current_category):
                count += 1
                
        self.show_success("批量移出成功", f"已将 {count} 个表情从 '{self.current_category}' 移出")
        self.set_selection_mode(False)
        self.on_images_changed()

    def _execute_batch_export(self, paths):
        if not paths: return
        
        # 去重，防止在全部表情视图下选中了重复的路径
        unique_paths = list(set(paths))
        
        from PySide6.QtWidgets import QFileDialog
        import shutil
        
        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目标文件夹", "")
        if not dir_path:
            return
            
        count = 0
        try:
            for img_path in unique_paths:
                if os.path.exists(img_path):
                    filename = os.path.basename(img_path)
                    # 使用侧边栏中定义的防冲突函数
                    target_path = self.sidebar._get_unique_export_path(dir_path, filename)
                    shutil.copy2(img_path, target_path)
                    count += 1
            self.show_success("导出成功", f"已将 {count} 个表情导出到\n{dir_path}")
            self.set_selection_mode(False)
        except Exception as e:
            self.show_error("导出失败", str(e))

    def on_hover_started(self, image_path):
        if self.is_selection_mode: return
        self.current_hover_path = image_path
        self.hover_timer.start(self.config.get("preview_delay", 500))

    def on_hover_ended(self):
        self.hover_timer.stop()
        self.current_hover_path = None
        self.preview_popup.hide_preview()

    def _show_preview_popup(self):
        if not self.current_hover_path: return
        
        categories = self.storage.get_categories_by_image(self.current_hover_path)
        keywords = self.storage.get_image_keywords(self.current_hover_path)
        
        cat_str = ", ".join(categories) if categories else "无"
        kw_str = keywords if keywords else "无"
        
        self.preview_popup.show_preview(
            self.current_hover_path, 
            QCursor.pos(), 
            self.config.get("preview_size", 320),
            cat_str,
            kw_str
        )

    def on_image_clicked(self, image_path, modifiers=Qt.NoModifier):
        is_ctrl = bool(modifiers & Qt.ControlModifier)
        is_shift = bool(modifiers & Qt.ShiftModifier)

        if is_ctrl or is_shift:
            if not self.is_selection_mode:
                self.set_selection_mode(True)
            
            if is_shift and self._last_clicked_path and self._anchor_from_selection_click:
                self._execute_shift_selection(self._last_clicked_path, image_path)
            else:
                self._toggle_card_selection(image_path)
                if self._last_clicked_path != image_path:
                    self._shift_selected_paths.clear()
                self._last_clicked_path = image_path
                
            self._anchor_from_selection_click = True
            return

        if self.is_selection_mode:
            if self._last_clicked_path != image_path:
                self._shift_selected_paths.clear()
            self._last_clicked_path = image_path
            self._anchor_from_selection_click = True
            return

        if self._last_clicked_path != image_path:
            self._shift_selected_paths.clear()
        self._last_clicked_path = image_path
        self._anchor_from_selection_click = False

        if self.clipboard.copy_image_to_clipboard(image_path):
            # 记录到最近使用
            limit = self.config.get("recent_limit", 30)
            self.storage.add_recent_image(image_path, limit)
            
            # 再次检查 last_active_window 是否仍然有效且不是桌面/资源管理器等系统关键窗口
            if self.last_active_window and user32.IsWindow(self.last_active_window):
                class_name = get_window_class_name(self.last_active_window)
                system_classes = (
                    "Progman", "WorkerW", "Shell_TrayWnd", 
                    "CabinetWClass", "ExploreWClass", "Windows.UI.Core.CoreWindow"
                )
                if class_name not in system_classes:
                    user32.SetForegroundWindow(self.last_active_window)
                    QTimer.singleShot(100, self.simulate_paste)
                    return
            
            self.show_success("已复制到剪切板！")

    def _toggle_card_selection(self, image_path):
        for widget in getattr(self, '_all_card_widgets', []):
            if widget.image_path == image_path:
                widget.set_selected(not widget.is_selected)
                break

    def _execute_shift_selection(self, start_path, end_path):
        widgets = getattr(self, '_all_card_widgets', [])
        start_idx = -1
        end_idx = -1
        anchor_selected = True
        
        for i, widget in enumerate(widgets):
            if widget.image_path == start_path:
                start_idx = i
                anchor_selected = widget.is_selected
            if widget.image_path == end_path:
                end_idx = i
                
        if start_idx != -1 and end_idx != -1:
            min_idx = min(start_idx, end_idx)
            max_idx = max(start_idx, end_idx)
            
            new_shift_paths = set()
            for i in range(min_idx, max_idx + 1):
                new_shift_paths.add(widgets[i].image_path)
                
            # 恢复多出来的卡片状态
            for path in self._shift_selected_paths:
                if path not in new_shift_paths:
                    for w in widgets:
                        if w.image_path == path:
                            w.set_selected(not anchor_selected)
                            break
                            
            # 设置新范围内的卡片状态
            for i in range(min_idx, max_idx + 1):
                widgets[i].set_selected(anchor_selected)
                
            self._shift_selected_paths = new_shift_paths

    def simulate_paste(self):
        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    # ================== 拖拽逻辑 ==================

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder") or event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.auto_scroll_timer.stop()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder") or event.mimeData().hasUrls():
            event.accept()
            
            # 处理自动滚动
            pos_y = event.pos().y()
            viewport_height = self.scroll_area.viewport().height()
            margin = 40
            
            # 检查鼠标是否在 scroll_area 的垂直范围内
            scroll_y = self.scroll_area.mapFrom(self, event.pos()).y()
            
            if scroll_y < margin:
                self.scroll_direction = -1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(16) # ~60fps
            elif scroll_y > viewport_height - margin:
                self.scroll_direction = 1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(16)
            else:
                self.auto_scroll_timer.stop()
        else:
            event.ignore()

    def _do_auto_scroll(self):
        scrollbar = self.scroll_area.verticalScrollBar()
        current_val = scrollbar.value()
        step = 15 # 滚动速度
        scrollbar.setValue(current_val + (step * self.scroll_direction))

    def dropEvent(self, event):
        self.auto_scroll_timer.stop()
        mime_data = event.mimeData()
        
        if mime_data.hasFormat("application/x-emojy-reorder"):
            source_path = bytes(mime_data.data("application/x-emojy-reorder")).decode('utf-8')
            drop_pos = event.pos()
            target_path = None
            insert_after = False
            
            view_pos = self.grid_container.mapFrom(self, drop_pos)
            min_dist = float('inf')
            best_widget = None
            
            for i in range(self.gallery_layout.count()):
                item = self.gallery_layout.itemAt(i)
                if not item: continue
                widget = item.widget()
                if isinstance(widget, EmojiCard):
                    widget_center = widget.geometry().center()
                    dist = (view_pos - widget_center).manhattanLength()
                    if dist < min_dist:
                        min_dist = dist
                        best_widget = widget
                        insert_after = (view_pos.x() > widget_center.x())
            
            if best_widget:
                target_path = best_widget.image_path
                
            if source_path and target_path and source_path != target_path:
                images = self.storage.get_all_images()
                # 统一使用标准化绝对路径进行比对，防止路径格式不一致导致排序失效
                norm_source = self.storage._to_abspath(os.path.basename(source_path))
                norm_target = self.storage._to_abspath(os.path.basename(target_path))
                
                if norm_source in images and norm_target in images:
                    images.remove(norm_source)
                    target_idx = images.index(norm_target)
                    if insert_after:
                        target_idx += 1
                    images.insert(target_idx, norm_source)
                    self.storage.save_order(images)
                    self._reorder_widgets(images)
            event.accept()
            return
            
        if mime_data.hasUrls():
            local_files = []
            folder_to_import = None
            
            for url in mime_data.urls():
                if url.isLocalFile():
                    filepath = url.toLocalFile()
                    if os.path.isdir(filepath):
                        if not folder_to_import:
                            folder_to_import = filepath
                    else:
                        abs_filepath = os.path.normcase(os.path.abspath(filepath))
                        abs_storage = os.path.normcase(os.path.abspath(self.storage.images_dir))
                        if not abs_filepath.startswith(abs_storage):
                            local_files.append(filepath)
                elif url.scheme() in ("http", "https"):
                    self.show_success("正在下载", "正在从网络获取图片，请稍候...")
                    thread = DownloadThread(url.toString(), self)
                    self.download_threads.append(thread)
                    thread.finished.connect(self._on_download_finished)
                    thread.start()
            
            event.accept()
            
            if folder_to_import:
                if len(mime_data.urls()) > 1:
                    self.show_error("提示", "检测到文件夹，仅处理第一个文件夹，忽略其他文件")
                    
                folder_name = os.path.basename(folder_to_import).strip()
                if folder_name:
                    is_new = self.storage.add_category(folder_name)
                    if is_new:
                        self.sidebar.refresh_list(folder_name)
                    else:
                        self.sidebar.set_active_category(folder_name)
                    
                valid_images = []
                total_files = 0
                subdirs = 0
                non_images = 0
                
                try:
                    for entry in os.scandir(folder_to_import):
                        total_files += 1
                        if entry.is_dir():
                            subdirs += 1
                        elif entry.is_file():
                            if entry.name.lower().endswith(self.storage.SUPPORTED_FORMATS):
                                valid_images.append(entry.path)
                            else:
                                non_images += 1
                except Exception as e:
                    print(f"[ERROR] 扫描文件夹失败: {e}")
                    
                if valid_images:
                    folder_stats = {
                        'folder_name': folder_name,
                        'total_files': total_files,
                        'image_count': len(valid_images),
                        'non_images': non_images,
                        'subdirs': subdirs
                    }
                    self._start_background_import(valid_images, target_category=folder_name, folder_stats=folder_stats)
                else:
                    self.show_error("导入失败", f"文件夹 '{folder_name}' 中没有找到支持的图片文件")
            elif local_files:
                self._start_background_import(local_files)

    def _reorder_widgets(self, new_order_paths):
        widget_dict = {w.image_path: w for w in getattr(self, '_all_card_widgets', [])}
        
        sorted_widgets = []
        for path in new_order_paths:
            if path in widget_dict: sorted_widgets.append(widget_dict[path])
                
        for w in getattr(self, '_all_card_widgets', []):
            if w not in sorted_widgets: sorted_widgets.append(w)
                
        self._all_card_widgets = sorted_widgets
        
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // (self.config.get("thumbnail_size", 120) + 10)))
        self._rearrange_gallery(columns)

    def force_refresh_sidebar_icons(self):
        """外部调用以重新应用图标尺寸"""
        self.sidebar.refresh_list(self.current_category)

    # ================== 菜单与操作 ==================

    def on_delete_requested(self, widget, image_path):
        from qfluentwidgets import MessageBox
        dialog = MessageBox("删除确认", "确定要彻底删除这个表情包吗？", self.window())
        if dialog.exec():
            self.preview_popup.hide_preview()
            if hasattr(widget, 'clear_resources'):
                widget.clear_resources()
            QApplication.processEvents()
            
            if self.storage.delete_image(image_path):
                self.refresh_gallery()
                self.show_success("已删除")

    def show_context_menu(self, widget, position):
        if self.is_selection_mode and widget.is_selected:
            menu = self._build_batch_context_menu(self.get_selected_paths())
        else:
            menu = self._build_single_context_menu(widget)
            
        menu.exec(widget.mapToGlobal(position))

    def _build_single_context_menu(self, widget):
        image_path = widget.image_path
        menu = RoundMenu(parent=self)
        
        categories = self.storage.get_all_categories()
        is_sub_category = self.current_category in categories
        
        # 1. 添加到分类...
        add_to_cat_menu = RoundMenu(title="添加到分类...", parent=menu)
        menu.addMenu(add_to_cat_menu)
        
        has_valid_add_cat = False
        for cat_name in categories.keys():
            if cat_name != self.current_category:
                has_valid_add_cat = True
                action = Action(cat_name, parent=menu)
                action.triggered.connect(lambda checked=False, p=image_path, c=cat_name: self._add_to_cat(p, c))
                add_to_cat_menu.addAction(action)
                
        if not has_valid_add_cat:
            add_to_cat_menu.addAction(Action("(无可用分类)", parent=menu))
            
        # 2. 移动到分类... (仅在子分类下显示)
        if is_sub_category:
            move_to_cat_menu = RoundMenu(title="移动到分类...", parent=menu)
            menu.addMenu(move_to_cat_menu)
            
            has_valid_move_cat = False
            for cat_name in categories.keys():
                if cat_name != self.current_category:
                    has_valid_move_cat = True
                    action = Action(cat_name, parent=menu)
                    action.triggered.connect(lambda checked=False, p=image_path, c=cat_name: self._move_to_cat(p, c))
                    move_to_cat_menu.addAction(action)
                    
            if not has_valid_move_cat:
                move_to_cat_menu.addAction(Action("(无可用分类)", parent=menu))
                
        if is_sub_category:
            menu.addSeparator()
            
            set_icon_action = Action(f"设为 '{self.current_category}' 的分类图标", parent=menu)
            set_icon_action.triggered.connect(lambda: self._set_category_icon(image_path))
            menu.addAction(set_icon_action)
            
            remove_action = Action(f"从分类 '{self.current_category}' 移出", parent=menu)
            remove_action.triggered.connect(lambda: self._remove_from_cat(image_path))
            menu.addAction(remove_action)
            
        menu.addSeparator()
        
        set_kw_action = Action("设置/修改关键词...", parent=menu)
        set_kw_action.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._set_keywords(image_path)))
        menu.addAction(set_kw_action)
        
        menu.addSeparator()
        
        batch_action = Action("批量选择...", parent=menu)
        batch_action.triggered.connect(lambda: self._enter_batch_selection_from_menu(widget))
        menu.addAction(batch_action)
        
        menu.addSeparator()
        
        delete_action = Action("彻底删除此表情", parent=menu)
        delete_action.triggered.connect(lambda: QTimer.singleShot(50, lambda: self.on_delete_requested(widget, image_path)))
        menu.addAction(delete_action)
        
        return menu

    def _build_batch_context_menu(self, paths):
        menu = RoundMenu(parent=self)
        
        categories = self.storage.get_all_categories()
        is_sub_category = self.current_category in categories
        
        # 1. 添加到分类...
        add_to_cat_menu = RoundMenu(title="添加到分类...", parent=menu)
        menu.addMenu(add_to_cat_menu)
        
        has_valid_add_cat = False
        for cat_name in categories.keys():
            if cat_name != self.current_category:
                has_valid_add_cat = True
                action = Action(cat_name, parent=menu)
                action.triggered.connect(lambda checked=False, p=paths, c=cat_name: self._execute_batch_add(p, c))
                add_to_cat_menu.addAction(action)
                
        if not has_valid_add_cat:
            add_to_cat_menu.addAction(Action("(无可用分类)", parent=menu))
            
        # 2. 移动到分类... (仅在子分类下显示)
        if is_sub_category:
            move_to_cat_menu = RoundMenu(title="移动到分类...", parent=menu)
            menu.addMenu(move_to_cat_menu)
            
            has_valid_move_cat = False
            for cat_name in categories.keys():
                if cat_name != self.current_category:
                    has_valid_move_cat = True
                    action = Action(cat_name, parent=menu)
                    action.triggered.connect(lambda checked=False, p=paths, c=cat_name: self._execute_batch_move(p, c))
                    move_to_cat_menu.addAction(action)
                    
            if not has_valid_move_cat:
                move_to_cat_menu.addAction(Action("(无可用分类)", parent=menu))
                
        # 3. 从当前分类移出 (仅在子分类下显示)
        if is_sub_category:
            remove_action = Action(f"从分类 '{self.current_category}' 移出", parent=menu)
            remove_action.triggered.connect(lambda checked=False, p=paths: self._execute_batch_remove(p))
            menu.addAction(remove_action)
            
        menu.addSeparator()
        
        tags_menu = RoundMenu(title="标签", parent=menu)
        menu.addMenu(tags_menu)
        
        add_tags_action = Action("添加标签...", parent=tags_menu)
        add_tags_action.triggered.connect(lambda checked=False, p=paths: self._execute_batch_add_tags(p))
        tags_menu.addAction(add_tags_action)
        
        remove_tags_action = Action("删除标签...", parent=tags_menu)
        remove_tags_action.triggered.connect(lambda checked=False, p=paths: self._execute_batch_remove_tags(p))
        tags_menu.addAction(remove_tags_action)
        
        menu.addSeparator()
        
        export_action = Action("导出已选项...", parent=menu)
        export_action.triggered.connect(lambda checked=False, p=paths: self._execute_batch_export(p))
        menu.addAction(export_action)
        
        menu.addSeparator()
        
        delete_action = Action(f"彻底删除已选的 {len(paths)} 项", parent=menu)
        delete_action.triggered.connect(lambda checked=False, p=paths: self._execute_batch_delete(p))
        menu.addAction(delete_action)
        
        return menu

    def _enter_batch_selection_from_menu(self, widget):
        self.set_selection_mode(True)
        widget.set_selected(True)

    def _add_to_cat(self, image_path, cat_name):
        res = self.storage.add_image_to_category(image_path, cat_name)
        if res == "success":
            self.show_success("添加成功", f"已添加到分类 '{cat_name}'")
            self.on_images_changed()
        elif res == "already_exists":
            self.show_error("添加失败", f"该图片已存在于分类 '{cat_name}' 中")
        else:
            self.show_error("添加失败", "发生未知错误")

    def _move_to_cat(self, image_path, target_cat):
        res = self.storage.add_image_to_category(image_path, target_cat)
        if res in ("success", "already_exists"):
            if self.storage.remove_image_from_category(image_path, self.current_category):
                self.show_success("移动成功", f"已移动到分类 '{target_cat}'")
                self.on_images_changed()
            else:
                self.show_error("移动异常", "已添加到目标分类，但从当前分类移除失败")
        else:
            self.show_error("移动失败", "发生未知错误")

    def _set_category_icon(self, image_path):
        self.storage.set_category_icon(self.current_category, image_path)
        self.sidebar.refresh_list(self.current_category)
        self.show_success("设置成功", f"已将此图片设为 '{self.current_category}' 的图标")

    def _remove_from_cat(self, image_path):
        if self.storage.remove_image_from_category(image_path, self.current_category):
            self.on_images_changed()
            self.show_success("移除成功")

    def _delete_current_category(self):
        from qfluentwidgets import MessageBox
        dialog = MessageBox("删除分类", f"确定要删除分类 '{self.current_category}' 吗？\n里面的表情不会被删除。", self.window())
        if dialog.exec():
            if self.storage.remove_category(self.current_category):
                self.sidebar.refresh_list("全部表情")
                self.show_success("分类已删除")

    def _set_keywords(self, image_path):
        current_kw = self.storage.get_image_keywords(image_path)
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        text, ok = QInputDialog.getText(
            self.window(), "设置关键词", "输入关键词 (多个词用空格隔开):", 
            QLineEdit.Normal, current_kw
        )
        if ok:
            # 单项编辑依然保持覆盖逻辑，但使用统一的序列化方法保证格式规范
            tags_list = self.storage.parse_tags(text)
            final_str = self.storage.serialize_tags(tags_list)
            self.storage.set_image_keywords(image_path, final_str)
            self.on_images_changed()
            self.show_success("关键词已保存")

    def _execute_batch_add_tags(self, paths):
        if not paths: return
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        text, ok = QInputDialog.getText(
            self.window(), "批量添加标签", "输入要追加的标签 (多个词用空格隔开):", 
            QLineEdit.Normal, ""
        )
        if ok and text.strip():
            new_tags_str = text.strip()
            count = 0
            for p in paths:
                existing_tags = self.storage.get_image_keywords(p)
                merged_tags = self.storage.merge_tags(existing_tags, new_tags_str)
                if merged_tags != existing_tags:
                    self.storage.set_image_keywords(p, merged_tags)
                    count += 1
            self.on_images_changed()
            self.show_success("批量添加标签成功", f"已为 {count} 个表情追加了标签")
            self.set_selection_mode(False)

    def _execute_batch_remove_tags(self, paths):
        if not paths: return
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        text, ok = QInputDialog.getText(
            self.window(), "批量删除标签", "输入要删除的标签 (多个词用空格隔开):", 
            QLineEdit.Normal, ""
        )
        if ok and text.strip():
            remove_tags_str = text.strip()
            count = 0
            for p in paths:
                existing_tags = self.storage.get_image_keywords(p)
                final_tags = self.storage.remove_tags(existing_tags, remove_tags_str)
                if final_tags != existing_tags:
                    self.storage.set_image_keywords(p, final_tags)
                    count += 1
            self.on_images_changed()
            self.show_success("批量删除标签成功", f"已从 {count} 个表情中移除了标签")
            self.set_selection_mode(False)

    def _start_background_import(self, filepaths, delete_after=False, silent=False, target_category=None, folder_stats=None):
        if self.import_thread and self.import_thread.isRunning():
            if not silent:
                self.show_error("导入中", "当前有导入任务正在进行，请稍候...")
            return
            
        import_category = target_category if target_category else self.current_category
        self.import_thread = ImportThread(filepaths, self.storage, import_category, delete_after, self)
        self.import_thread.progress.connect(self._on_import_progress)
        self.import_thread.finished.connect(lambda s, k, f: self._on_import_finished(s, k, f, silent, folder_stats))
        
        if not silent:
            self._import_info_bar = InfoBar.info(
                title="正在导入",
                content=f"准备导入 {len(filepaths)} 个文件...",
                orient=Qt.Horizontal,
                isClosable=False,
                position=InfoBarPosition.TOP_RIGHT,
                duration=-1,
                parent=self
            )
        else:
            self._import_info_bar = None
            
        self.import_thread.start()

    def _on_import_progress(self, current, total):
        if hasattr(self, '_import_info_bar') and self._import_info_bar:
            if hasattr(self._import_info_bar, 'contentLabel'):
                self._import_info_bar.contentLabel.setText(f"正在处理: {current} / {total}")

    def _on_import_finished(self, saved_count, skipped_count, failed_count, silent=False, folder_stats=None):
        if hasattr(self, '_import_info_bar') and self._import_info_bar:
            self._import_info_bar.close()
            self._import_info_bar = None
            
        self.on_images_changed()
            
        if folder_stats:
            # 文件夹导入的详细统计弹窗
            msg = (
                f"文件夹：{folder_stats['folder_name']}\n"
                f"第一层文件总数：{folder_stats['total_files']}\n"
                f"其中图片文件：{folder_stats['image_count']}\n"
                f"成功导入：{saved_count}\n"
                f"重复跳过：{skipped_count}\n"
                f"解析失败：{failed_count}\n"
                f"忽略非图片文件：{folder_stats['non_images']}\n"
                f"忽略子文件夹：{folder_stats['subdirs']}"
            )
            from qfluentwidgets import MessageBox
            w = MessageBox("文件夹导入完成", msg, self.window())
            w.exec()
        elif saved_count > 0 or skipped_count > 0 or failed_count > 0:
            if silent:
                if saved_count > 0:
                    self.show_success("收件箱自动导入完成", f"成功添加 {saved_count} 个表情包")
            else:
                msg = []
                if saved_count > 0: msg.append(f"成功添加了 {saved_count} 个表情包")
                if skipped_count > 0: msg.append(f"跳过了 {skipped_count} 个重复表情")
                if failed_count > 0: msg.append(f"解析失败 {failed_count} 个")
                self.show_success("导入完成", "，".join(msg))

    def handle_global_paste(self):
        data_type, data = self.clipboard.get_data_from_clipboard()
        
        if data_type == 'file':
            self._start_background_import(data)
        elif data_type == 'image':
            saved_path, is_duplicate = self.storage.save_image(data)
            if saved_path:
                if self.current_category not in ("全部表情", "未分类"):
                    self.storage.add_image_to_category(saved_path, self.current_category)
                self.on_images_changed()
                if is_duplicate:
                    self.show_success("导入完成", "该图片已存在，已跳过保存")
                else:
                    self.show_success("保存成功", "静态图片已保存")
        elif data_type == 'network_url':
            url = data
            self.show_success("正在下载", "正在从网络获取图片，请稍候...")
            thread = DownloadThread(url, self)
            self.download_threads.append(thread)
            thread.finished.connect(self._on_download_finished)
            thread.start()

    def _on_download_finished(self, success, temp_filepath, error_msg, url):
        for t in self.download_threads[:]:
            if t.url == url:
                self.download_threads.remove(t)
                t.deleteLater()
                
        if not success:
            self.show_error("下载失败", f"无法获取网络图片: {error_msg}")
            return
            
        # 将下载好的临时文件交给统一的导入流程，并要求导入后删除临时文件
        self._start_background_import([temp_filepath], delete_after=True)
