from PySide6.QtWidgets import QLabel, QApplication, QMenu
from PySide6.QtGui import QCursor, QDrag, QPixmap, QImageReader, QPixmapCache
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint, QSize, QTimer
from qfluentwidgets import TransparentToolButton, FluentIcon

class ThumbnailCache:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._normal_limit_kb = 80 * 1024  # 正常操作时 80MB
            self._idle_limit_kb = 40 * 1024    # 空闲收缩时 40MB
            
            QPixmapCache.setCacheLimit(self._normal_limit_kb)
            
            # 空闲收缩定时器 (60秒)
            self._idle_timer = QTimer()
            self._idle_timer.setSingleShot(True)
            self._idle_timer.timeout.connect(self._on_idle_timeout)
            self._idle_timer.start(60000)
            
            self._initialized = True

    def reset_idle_timer(self):
        """由外部交互(滚动、切换分类等)调用，重置空闲状态"""
        if QPixmapCache.cacheLimit() != self._normal_limit_kb:
            QPixmapCache.setCacheLimit(self._normal_limit_kb)
        self._idle_timer.start(60000)

    def _on_idle_timeout(self):
        """空闲超时，收缩缓存上限以释放部分内存"""
        QPixmapCache.setCacheLimit(self._idle_limit_kb)

    def get_thumbnail(self, image_path, target_size):
        cache_key = f"{image_path}|{target_size}x{target_size}"
        
        pixmap = QPixmap()
        if QPixmapCache.find(cache_key, pixmap):
            return pixmap
            
        try:
            reader = QImageReader(image_path)
            orig_size = reader.size()
            
            if orig_size.isValid():
                # 计算保持宽高比的缩放尺寸
                orig_size.scale(target_size, target_size, Qt.KeepAspectRatio)
                # 让图片直接解码为目标大小，大幅降低内存峰值和 CPU 开销
                reader.setScaledSize(orig_size)
                
            img = reader.read()
            if img and not img.isNull():
                pixmap = QPixmap.fromImage(img)
                
                # 确保最终尺寸不超过 target_size，并应用平滑缩放以保证画质
                if pixmap.width() > target_size or pixmap.height() > target_size:
                    pixmap = pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    
                QPixmapCache.insert(cache_key, pixmap)
                return pixmap
        except Exception as e:
            print(f"[Error] ThumbnailCache failed to load image: {image_path}, error: {e}")
            
        return None

class EmojiCard(QLabel):
    """
    Fluent风格的图片组件，用于在网格中展示缩略图并支持点击事件和拖拽排序
    """
    clicked = Signal(str, Qt.KeyboardModifiers)  # 点击信号，传递图片路径和键盘修饰键状态
    delete_requested = Signal(object, str) # 请求删除的信号: (Widget实例, 路径)
    selection_changed = Signal(str, bool)  # 选中状态改变信号: (路径, 是否选中)
    
    hover_started = Signal(str) # 悬停进入
    hover_ended = Signal()      # 悬停离开

    def __init__(self, image_path, size=120, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self._drag_start_pos = None
        self._is_dragging = False
        self.current_size = size
        self._loaded_size = 0  # 记录当前实际加载的图片尺寸
        self._is_loaded = False
        
        self.is_selectable = False
        self.is_selected = False
        
        self.setFixedSize(self.current_size, self.current_size)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        
        self.setAlignment(Qt.AlignCenter)

        self.setObjectName("EmojiCard")
        self.update_style()
        self.update_size(self.current_size)

    def set_selectable(self, selectable):
        self.is_selectable = selectable
        if not selectable:
            self.set_selected(False)
            
    def set_selected(self, selected):
        if self.is_selected == selected:
            return
        self.is_selected = selected
        self.update_style()
        self.selection_changed.emit(self.image_path, selected)

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet("""
                QLabel#EmojiCard {
                    border-radius: 8px;
                    background-color: rgba(0, 120, 212, 0.15);
                    border: 2px solid #0078D4;
                }
            """)
        else:
            self.setStyleSheet("""
                QLabel#EmojiCard {
                    border-radius: 8px;
                    background-color: transparent;
                    border: 1px solid transparent;
                }
                QLabel#EmojiCard:hover {
                    background-color: rgba(200, 200, 200, 0.1);
                    border: 1px solid rgba(200, 200, 200, 0.2);
                }
            """)

    def paintEvent(self, event):
        super().paintEvent(event)
        # 如果被选中，在右上角画一个打勾的圆圈
        if self.is_selected:
            from PySide6.QtGui import QPainter, QColor, QPen
            from PySide6.QtCore import QRect, QPoint
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 画一个蓝色底色的圆
            painter.setBrush(QColor("#0078D4"))
            painter.setPen(Qt.NoPen)
            radius = 12
            center = QPoint(self.width() - radius - 6, radius + 6)
            painter.drawEllipse(center, radius, radius)
            
            # 画白色的对号
            pen = QPen(QColor("white"))
            pen.setWidth(2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            
            # 对号的坐标
            p1 = QPoint(center.x() - 4, center.y() + 1)
            p2 = QPoint(center.x() - 1, center.y() + 4)
            p3 = QPoint(center.x() + 5, center.y() - 3)
            
            painter.drawPolyline([p1, p2, p3])

    def needs_reload(self, target_size):
        return not self._is_loaded or self._loaded_size != target_size

    def clear_resources(self):
        self.clear()
        self._is_loaded = False
        self._loaded_size = 0

    def update_size(self, new_size, load_image=True):
        self.current_size = new_size
        self.setFixedSize(new_size, new_size)
        
        if not load_image:
            return
            
        target_img_size = max(10, new_size - 16)
        pixmap = ThumbnailCache().get_thumbnail(self.image_path, target_img_size)
        
        if pixmap and not pixmap.isNull():
            self.setPixmap(pixmap)
            self._is_loaded = True
            self._loaded_size = new_size
        else:
            self.clear()
            self._is_loaded = False
            self._loaded_size = 0

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
            
        # 多选模式下禁用拖拽
        if getattr(self, 'is_selectable', False):
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() > QApplication.startDragDistance():
            self._is_dragging = True 
            
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setData("application/x-emojy-reorder", self.image_path.encode('utf-8'))
            drag.setMimeData(mime_data)
            
            pixmap = self.grab()
            drag.setPixmap(pixmap.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            drag.setHotSpot(QPoint(30, 30))
            
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            drag.exec(Qt.MoveAction)
            self.setCursor(QCursor(Qt.PointingHandCursor))
            
            self._drag_start_pos = None
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self._is_dragging:
                modifiers = QApplication.keyboardModifiers()
                if self.is_selectable and not modifiers:
                    self.set_selected(not self.is_selected)
                else:
                    self.clicked.emit(self.image_path, modifiers)
                
        self._drag_start_pos = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.hover_started.emit(self.image_path)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_ended.emit()
        super().leaveEvent(event)