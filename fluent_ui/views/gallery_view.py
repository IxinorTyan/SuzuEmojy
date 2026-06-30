import os
import ctypes
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QApplication, QHBoxLayout, QVBoxLayout, 
    QListWidget, QListWidgetItem, QInputDialog, QLineEdit
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QCursor, QIcon
from qfluentwidgets import (
    ScrollArea, InfoBar, InfoBarPosition, RoundMenu, Action, 
    PushButton, FluentIcon as FIF, TransparentToolButton, setFont, BodyLabel
)

from fluent_ui.components.emoji_card import EmojiCard
from fluent_ui.components.hover_preview import HoverPreviewPopup

user32 = ctypes.windll.user32

def get_window_class_name(hwnd):
    """获取窗口的类名"""
    buff = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buff, 256)
    return buff.value

class CategoryListWidget(QListWidget):
    """支持拖拽排序的分类列表，依赖原生 InternalMove 机制防止数据丢失"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder"):
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-emojy-reorder"):
            event.accept()
        else:
            super().dragMoveEvent(event)
            
    def dropEvent(self, event):
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
                            if self.parent().gallery_view.current_category == "未分类":
                                self.parent().gallery_view.refresh_gallery()
            event.accept()
        else:
            event.ignore()


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
        
        self.update_theme()
        self.refresh_list()

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
        
        icon_size = self.config.get("sidebar_icon_size", 20) if self.config else 20
        self.list_widget.setIconSize(QSize(icon_size, icon_size))
        
        item_all = QListWidgetItem(FIF.HOME.icon(), "全部表情")
        item_all.setData(Qt.UserRole, "全部表情")
        self.list_widget.addItem(item_all)
        
        categories = self.storage.get_all_categories()
        icons_dict = self.storage.get_all_category_icons()
        
        from PySide6.QtGui import QPixmap, QPainter, QFont
        
        for cat in categories.keys():
            if cat in ("全部表情", "未分类", "新建分类"): continue
            
            icon_val = icons_dict.get(cat)
            nav_icon = FIF.FOLDER.icon()
            
            if icon_val:
                if len(icon_val) <= 2:
                    pixmap = QPixmap(64, 64)
                    pixmap.fill(Qt.transparent)
                    painter = QPainter(pixmap)
                    font = painter.font()
                    font.setPixelSize(48)
                    font.setFamily("Segoe UI Emoji")
                    painter.setFont(font)
                    painter.drawText(pixmap.rect(), Qt.AlignCenter, icon_val)
                    painter.end()
                    nav_icon = QIcon(pixmap)
                elif os.path.exists(icon_val):
                    nav_icon = QIcon(icon_val)
                
            item = QListWidgetItem(nav_icon, cat)
            item.setData(Qt.UserRole, cat)
            self.list_widget.addItem(item)
            
        item_unclassified = QListWidgetItem(FIF.HELP.icon(), "未分类")
        item_unclassified.setData(Qt.UserRole, "未分类")
        font = item_unclassified.font()
        font.setItalic(True)
        item_unclassified.setFont(font)
        self.list_widget.addItem(item_unclassified)
        
        item_add = QListWidgetItem(FIF.ADD.icon(), "新建分类")
        item_add.setData(Qt.UserRole, "新建分类")
        font = item_add.font()
        font.setItalic(True)
        item_add.setFont(font)
        self.list_widget.addItem(item_add)
        
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
                
                self._create_new_category()
                return

            if self.gallery_view and hasattr(self.gallery_view, 'gallery_layout'):
                self.gallery_view.set_category(cat_name)

    def _create_new_category(self):
        name, ok = QInputDialog.getText(self, "新建分类", "请输入分类名称：", QLineEdit.Normal, "")
        if ok and name.strip():
            name = name.strip()
            if name != "全部表情" and name != "新建分类":
                if self.storage.add_category(name):
                    self.refresh_list(select_category=name)
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
        
        action_del = Action("删除文件夹", parent=menu)
        action_del.triggered.connect(lambda: QTimer.singleShot(50, lambda: self._delete_category_with_confirm(cat_name)))
        menu.addAction(action_del)
        
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))

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

class GalleryInterface(QWidget):
    """
    重构后的 Gallery 视图，包含左侧分类栏和右侧表情网格
    """
    def __init__(self, storage_service, clipboard_service, config_service, parent=None):
        super().__init__(parent=parent)
        self.storage = storage_service
        self.clipboard = clipboard_service
        self.config = config_service
        self.setObjectName("GalleryInterface")
        
        # 内部状态
        self.current_category = "全部表情"
        self.search_keyword = ""
        self.last_active_window = None
        self.is_selection_mode = False
        
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
        
        self.setAcceptDrops(True)
        
        # 首次强制刷新
        self.sidebar.refresh_list("全部表情")

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
        self.search_box.textChanged.connect(self.set_search_keyword)
        
        self.top_bar_layout.addStretch() # 把搜索框推到右边
        self.top_bar_layout.addWidget(self.btn_multi_select)
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
        self.btn_batch_add = PushButton(FIF.FOLDER_ADD.icon(), "移动到分类...")
        self.btn_batch_remove = PushButton(FIF.REMOVE.icon(), "移出分类")
        self.btn_batch_delete = PushButton(FIF.DELETE.icon(), "批量删除")
        self.btn_exit_selection = PushButton("退出多选")
        
        self.command_bar_layout.addWidget(self.selected_count_label)
        self.command_bar_layout.addStretch()
        self.command_bar_layout.addWidget(self.btn_select_all)
        self.command_bar_layout.addWidget(self.btn_batch_add)
        self.command_bar_layout.addWidget(self.btn_batch_remove)
        self.command_bar_layout.addWidget(self.btn_batch_delete)
        self.command_bar_layout.addWidget(self.btn_exit_selection)
        
        self.btn_select_all.clicked.connect(self.select_all_cards)
        self.btn_batch_add.clicked.connect(self.batch_add_to_category)
        self.btn_batch_remove.clicked.connect(self.batch_remove_from_category)
        self.btn_batch_delete.clicked.connect(self.batch_delete)
        self.btn_exit_selection.clicked.connect(lambda: self.set_selection_mode(False))
        
        self.right_layout.addWidget(self.command_bar)
        self.command_bar.hide()
        
        self.splitter.addWidget(self.right_container)
        
        # 恢复上次保存的比例
        saved_sizes = self.config.get("splitter_sizes", [200, 800])
        self.splitter.setSizes(saved_sizes)
        self.splitter.setCollapsible(0, False)
        
        # 初始化检查是否应直接进入图标模式
        if saved_sizes[0] < 100:
            self.sidebar.set_icon_only_mode(True)
            self.splitter.setSizes([60, sum(saved_sizes) - 60])
            
        self.main_layout.addWidget(self.splitter, stretch=1)
        
        # 响应 Splitter 拖动
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # 安装事件过滤器以便处理 ctrl+滚轮以及把手释放吸附
        self.scroll_area.viewport().installEventFilter(self)
        self.splitter.handle(1).installEventFilter(self)

    def _on_splitter_moved(self, pos, index):
        self.config.set("splitter_sizes", self.splitter.sizes())

    def eventFilter(self, obj, event):
        if hasattr(self, 'splitter') and obj == self.splitter.handle(1):
            if event.type() == event.Type.MouseButtonRelease:
                sizes = self.splitter.sizes()
                if sizes[0] < 100:
                    diff = sizes[0] - 60
                    self.splitter.setSizes([60, sizes[1] + diff])
                elif sizes[0] >= 100 and sizes[0] < 140:
                    diff = sizes[0] - 140
                    self.splitter.setSizes([140, sizes[1] + diff])
                self.config.set("splitter_sizes", self.splitter.sizes())

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

    def set_category(self, category_name):
        """由左侧边栏调用，切换显示的数据"""
        if self.is_selection_mode:
            self.set_selection_mode(False)
        self.current_category = category_name
        self.refresh_gallery()
        
    def set_search_keyword(self, keyword):
        """由顶栏调用，切换搜索词"""
        self.search_keyword = keyword
        self.refresh_gallery()

    def show_success(self, title, content=""):
        InfoBar.success(title, content, duration=2000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def show_error(self, title, content=""):
        InfoBar.error(title, content, duration=3000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def track_active_window(self):
        hwnd = user32.GetForegroundWindow()
        if hwnd and hwnd != int(self.window().winId()):
            class_name = get_window_class_name(hwnd)
            # 过滤掉桌面和任务栏等系统窗口，防止误粘贴文件到桌面
            if class_name not in ("Progman", "WorkerW", "Shell_TrayWnd"):
                self.last_active_window = hwnd


    def _apply_thumbnail_size(self, size):
        for i in range(self.gallery_layout.count()):
            item = self.gallery_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, EmojiCard):
                    widget.update_size(size)
        self._trigger_responsive_layout(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        self._trigger_responsive_layout(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._trigger_responsive_layout()

    def _trigger_responsive_layout(self, force=False):
        if not hasattr(self, 'scroll_area'): return
        item_width = self.config.get("thumbnail_size", 120) + 10
        area_width = self.scroll_area.viewport().width() - 32
        columns = max(1, area_width // item_width)
        
        if getattr(self, '_current_columns', 0) != columns or force:
            self._current_columns = columns
            self._rearrange_gallery(columns)

    def _rearrange_gallery(self, columns):
        widgets = []
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            if item.widget(): widgets.append(item.widget())
                
        for index, widget in enumerate(widgets):
            self.gallery_layout.addWidget(widget, index // columns, index % columns)

    def refresh_gallery(self):
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()

        images = self.storage.search_images(self.search_keyword, self.current_category)
        
        columns = getattr(self, '_current_columns', 4)
        current_size = self.config.get("thumbnail_size", 120)
        
        for index, image_path in enumerate(images):
            row = index // columns
            col = index % columns
            
            card = EmojiCard(image_path, size=current_size)
            card.customContextMenuRequested.connect(lambda pos, w=card: self.show_context_menu(w, pos))
            card.clicked.connect(self.on_image_clicked)
            card.delete_requested.connect(self.on_delete_requested)
            
            card.hover_started.connect(self.on_hover_started)
            card.hover_ended.connect(self.on_hover_ended)
            
            card.set_selectable(self.is_selection_mode)
            card.selection_changed.connect(self.on_selection_changed)
            
            self.gallery_layout.addWidget(card, row, col)

    # ================== 批量选择与交互逻辑 ==================

    def set_selection_mode(self, enabled):
        self.is_selection_mode = enabled
        self.command_bar.setVisible(enabled)
        self.btn_multi_select.setVisible(not enabled)
        
        # 动态控制“移出分类”按钮的显示
        if enabled:
            can_remove = self.current_category not in ("全部表情", "未分类")
            self.btn_batch_remove.setVisible(can_remove)
        
        for i in range(self.gallery_layout.count()):
            item = self.gallery_layout.itemAt(i)
            if item and item.widget():
                item.widget().set_selectable(enabled)
                if not enabled:
                    item.widget().set_selected(False)
                    
        self.update_selection_count()
        
    def update_selection_count(self):
        count = len(self.get_selected_paths())
        self.selected_count_label.setText(f"已选择 {count} 项")
        
    def get_selected_paths(self):
        paths = []
        for i in range(self.gallery_layout.count()):
            item = self.gallery_layout.itemAt(i)
            if item and item.widget() and getattr(item.widget(), 'is_selected', False):
                paths.append(item.widget().image_path)
        return paths

    def on_selection_changed(self, path, selected):
        self.update_selection_count()
        
    def select_all_cards(self):
        paths = self.get_selected_paths()
        total = self.gallery_layout.count()
        select = len(paths) < total
        for i in range(total):
            item = self.gallery_layout.itemAt(i)
            if item and item.widget():
                item.widget().set_selected(select)

    def batch_delete(self):
        paths = self.get_selected_paths()
        if not paths: return
        from qfluentwidgets import MessageBox
        dialog = MessageBox("批量删除确认", f"确定要彻底删除选中的 {len(paths)} 个表情包吗？", self.window())
        if dialog.exec():
            for p in paths:
                self.storage.delete_image(p)
            self.refresh_gallery()
            self.show_success("批量删除成功")
            self.set_selection_mode(False)

    def batch_add_to_category(self):
        paths = self.get_selected_paths()
        if not paths: return
        
        menu = RoundMenu(parent=self)
        categories = self.storage.get_all_categories()
        
        has_valid_cat = False
        for cat_name in categories.keys():
            if cat_name in ("全部表情", "未分类", "新建分类"): continue
            has_valid_cat = True
            action = Action(cat_name, parent=menu)
            action.triggered.connect(lambda checked=False, c=cat_name: self._execute_batch_add(paths, c))
            menu.addAction(action)
            
        if not has_valid_cat:
            menu.addAction(Action("(无可用分类)", parent=menu))
            
        from PySide6.QtCore import QPoint
        pos = self.btn_batch_add.mapToGlobal(QPoint(0, -menu.sizeHint().height() or -150))
        menu.exec(pos)

    def _execute_batch_add(self, paths, cat_name):
        count = 0
        for p in paths:
            if self.storage.add_image_to_category(p, cat_name):
                count += 1
        self.show_success("批量添加成功", f"已将 {count} 个表情添加到 '{cat_name}'")
        self.set_selection_mode(False)
        if self.current_category == "未分类":
            self.refresh_gallery()

    def batch_remove_from_category(self):
        paths = self.get_selected_paths()
        if not paths: return
        
        count = 0
        for p in paths:
            if self.storage.remove_image_from_category(p, self.current_category):
                count += 1
                
        self.show_success("批量移出成功", f"已将 {count} 个表情从 '{self.current_category}' 移出")
        self.set_selection_mode(False)
        self.refresh_gallery()

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

    def on_image_clicked(self, image_path):
        if self.clipboard.copy_image_to_clipboard(image_path):
            # 再次检查 last_active_window 是否仍然有效且不是桌面
            if self.last_active_window and user32.IsWindow(self.last_active_window):
                class_name = get_window_class_name(self.last_active_window)
                if class_name not in ("Progman", "WorkerW", "Shell_TrayWnd"):
                    user32.SetForegroundWindow(self.last_active_window)
                    QTimer.singleShot(100, self.simulate_paste)
                    return
            
            self.show_success("已复制到剪切板！")

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

    def dropEvent(self, event):
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
                if source_path in images and target_path in images:
                    images.remove(source_path)
                    target_idx = images.index(target_path)
                    if insert_after:
                        target_idx += 1
                    images.insert(target_idx, source_path)
                    self.storage.save_order(images)
                    self._reorder_widgets(images)
            event.accept()
            return
            
        if mime_data.hasUrls():
            saved_count = 0
            skipped_count = 0
            for url in mime_data.urls():
                if url.isLocalFile():
                    filepath = url.toLocalFile()
                    abs_filepath = os.path.normcase(os.path.abspath(filepath))
                    abs_storage = os.path.normcase(os.path.abspath(self.storage.images_dir))
                    if not abs_filepath.startswith(abs_storage):
                        saved_path, is_duplicate = self.storage.save_file(filepath)
                        if saved_path:
                            if is_duplicate:
                                skipped_count += 1
                            else:
                                saved_count += 1
                            if self.current_category not in ("全部表情", "未分类"):
                                self.storage.add_image_to_category(saved_path, self.current_category)
            
            event.accept()
            
            if saved_count > 0 or skipped_count > 0:
                def delayed_refresh():
                    self.refresh_gallery()
                    msg = []
                    if saved_count > 0: msg.append(f"成功添加了 {saved_count} 个表情包")
                    if skipped_count > 0: msg.append(f"跳过了 {skipped_count} 个重复表情")
                    self.show_success("导入完成", "，".join(msg))
                QTimer.singleShot(50, delayed_refresh)

    def _reorder_widgets(self, new_order_paths):
        widgets = []
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            if item.widget(): widgets.append(item.widget())
                
        widget_dict = {w.image_path: w for w in widgets if isinstance(w, EmojiCard)}
        
        sorted_widgets = []
        for path in new_order_paths:
            if path in widget_dict: sorted_widgets.append(widget_dict[path])
                
        for w in widgets:
            if w not in sorted_widgets: sorted_widgets.append(w)
                
        columns = getattr(self, '_current_columns', max(1, self.scroll_area.viewport().width() // 130))
        for index, widget in enumerate(sorted_widgets):
            self.gallery_layout.addWidget(widget, index // columns, index % columns)

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
        image_path = widget.image_path
        menu = RoundMenu(parent=self)
        
        add_to_cat_menu = RoundMenu(title="添加到分类...", parent=menu)
        categories = self.storage.get_all_categories()
        menu.addMenu(add_to_cat_menu)
        
        if not categories:
            add_to_cat_menu.addAction(Action("(无可用分类)", parent=menu))
        else:
            for cat_name in categories.keys():
                action = Action(cat_name, parent=menu)
                action.triggered.connect(lambda checked=False, p=image_path, c=cat_name: self._add_to_cat(p, c))
                add_to_cat_menu.addAction(action)
                    
        if self.current_category != "全部表情":
            menu.addSeparator()
            
            set_icon_action = Action(f"设为 '{self.current_category}' 的分类图标", parent=menu)
            set_icon_action.triggered.connect(lambda: self._set_category_icon(image_path))
            menu.addAction(set_icon_action)
            
            remove_action = Action(f"从分类 '{self.current_category}' 移除", parent=menu)
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
        
        menu.exec(widget.mapToGlobal(position))

    def _enter_batch_selection_from_menu(self, widget):
        self.set_selection_mode(True)
        widget.set_selected(True)

    def _add_to_cat(self, image_path, cat_name):
        if self.storage.add_image_to_category(image_path, cat_name):
            self.show_success("添加成功", f"已添加到分类 '{cat_name}'")
            if self.current_category == "未分类":
                self.refresh_gallery()

    def _set_category_icon(self, image_path):
        self.storage.set_category_icon(self.current_category, image_path)
        self.sidebar.refresh_list(self.current_category)
        self.show_success("设置成功", f"已将此图片设为 '{self.current_category}' 的图标")

    def _remove_from_cat(self, image_path):
        if self.storage.remove_image_from_category(image_path, self.current_category):
            self.refresh_gallery()
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
            text = text.strip()
            self.storage.set_image_keywords(image_path, text)
            self.show_success("关键词已保存")

    def handle_global_paste(self):
        data_type, data = self.clipboard.get_data_from_clipboard()
        
        if data_type == 'file':
            saved_count = 0
            skipped_count = 0
            for filepath in data:
                saved_path, is_duplicate = self.storage.save_file(filepath)
                if saved_path:
                    if is_duplicate:
                        skipped_count += 1
                    else:
                        saved_count += 1
                    if self.current_category not in ("全部表情", "未分类"):
                        self.storage.add_image_to_category(saved_path, self.current_category)
            if saved_count > 0 or skipped_count > 0:
                self.refresh_gallery()
                msg = []
                if saved_count > 0: msg.append(f"保存了 {saved_count} 个文件")
                if skipped_count > 0: msg.append(f"跳过了 {skipped_count} 个重复文件")
                self.show_success("剪贴板导入完成", "，".join(msg))
        elif data_type == 'image':
            saved_path, is_duplicate = self.storage.save_image(data)
            if saved_path:
                if self.current_category not in ("全部表情", "未分类"):
                    self.storage.add_image_to_category(saved_path, self.current_category)
                self.refresh_gallery()
                if is_duplicate:
                    self.show_success("导入完成", "该图片已存在，已跳过保存")
                else:
                    self.show_success("保存成功", "静态图片已保存")
