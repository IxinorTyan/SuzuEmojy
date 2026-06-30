from PySide6.QtWidgets import QLabel, QApplication, QMenu
from PySide6.QtGui import QCursor, QDrag, QPixmap, QImageReader
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint, QSize
from qfluentwidgets import TransparentToolButton, FluentIcon

class EmojiCard(QLabel):
    """
    Fluent风格的图片组件，用于在网格中展示缩略图并支持点击事件和拖拽排序
    """
    clicked = Signal(str)  # 点击信号，传递图片路径
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
        
        self.is_selectable = False
        self.is_selected = False
        
        self.setFixedSize(self.current_size, self.current_size)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        
        self.setAlignment(Qt.AlignCenter)
        self._load_image()

        self.setObjectName("EmojiCard")
        self.update_style()

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

    def clear_resources(self):
        self.clear()

    def _load_image(self):
        try:
            # 安全加载图片
            reader = QImageReader(self.image_path)
            
            # 智能缓存策略：获取图片原始大小
            orig_size = reader.size()
            max_cache_size = 500
            
            # 只有当原图大得离谱(超过 500x500)时，才进行源头裁剪以防内存 OOM
            # 如果原图较小，则保留原图的高清画质
            if orig_size.isValid() and (orig_size.width() > max_cache_size or orig_size.height() > max_cache_size):
                orig_size.scale(max_cache_size, max_cache_size, Qt.KeepAspectRatio)
                reader.setScaledSize(orig_size)
                
            img = reader.read()
            if img and not img.isNull():
                self._original_pixmap = QPixmap.fromImage(img)
                self.update_size(self.current_size)
            else:
                print(f"[Warning] Failed to read image: {self.image_path}")
        except Exception as e:
            print(f"[Error] Loading image crashed: {self.image_path}, error: {e}")
            
    def update_size(self, new_size):
        self.current_size = new_size
        self.setFixedSize(new_size, new_size)
        if hasattr(self, '_original_pixmap') and self._original_pixmap and not self._original_pixmap.isNull():
            # 图片缩放为控件大小减去 padding
            target_img_size = max(10, new_size - 16)
            # 画质优化：放弃粗糙的 FastTransformation，改用 SmoothTransformation(双线性插值)
            # 配合上面保存的高清基准图，这样缩小显示的缩略图边缘会非常平滑和锐利
            self.setPixmap(self._original_pixmap.scaled(target_img_size, target_img_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

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
                if self.is_selectable:
                    self.set_selected(not self.is_selected)
                else:
                    self.clicked.emit(self.image_path)
                
        self._drag_start_pos = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.hover_started.emit(self.image_path)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_ended.emit()
        super().leaveEvent(event)