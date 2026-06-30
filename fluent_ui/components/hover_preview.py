import os
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication, QHBoxLayout
from PySide6.QtCore import Qt, QSize, QByteArray, QBuffer
from PySide6.QtGui import QPixmap, QMovie
from qfluentwidgets import BodyLabel, CaptionLabel
import darkdetect

class HoverPreviewPopup(QWidget):
    """
    独立且无焦点的悬停预览浮窗组件，包含元数据展示
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 简化标志，ToolTip 本身就是悬浮提示框，不会抢占输入焦点，并且默认置顶
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        
        # 核心布局
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(4)
        
        # 图像显示区域
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("border: none; background: transparent;")
        self.layout.addWidget(self.image_label, stretch=1)
        
        # 元数据区域 (分类和关键词)
        self.meta_layout = QVBoxLayout()
        self.meta_layout.setSpacing(2)
        
        self.category_label = CaptionLabel("🗂️ 无分类")
        
        self.keyword_label = CaptionLabel("🏷️ 无关键词")
        
        self.meta_layout.addWidget(self.category_label)
        self.meta_layout.addWidget(self.keyword_label)
        
        self.layout.addLayout(self.meta_layout)
        
        self.current_movie = None
        self.setObjectName("PreviewPopup")

    def update_theme(self):
        """动态同步当前 qfluentwidgets 的主题风格"""
        from qfluentwidgets import isDarkTheme
        if isDarkTheme():
            self.category_label.setStyleSheet("color: white;")
            self.keyword_label.setStyleSheet("color: white;")
            self.setStyleSheet("""
                QWidget#PreviewPopup {
                    background-color: rgba(30, 30, 30, 0.95);
                    border: 1px solid #444;
                    border-radius: 8px;
                }
            """)
        else:
            self.category_label.setStyleSheet("color: #222;")
            self.keyword_label.setStyleSheet("color: #222;")
            self.setStyleSheet("""
                QWidget#PreviewPopup {
                    background-color: rgba(255, 255, 255, 0.95);
                    border: 1px solid #ccc;
                    border-radius: 8px;
                }
            """)

    def show_preview(self, image_path, global_pos, size_config=320, category_str="无", keyword_str="无"):
        """
        显示预览窗口，并更新元数据
        """
        if not os.path.exists(image_path):
            return

        self.update_theme()
        
        self.category_label.setText(f"🗂️ 分类: {category_str}")
        self.keyword_label.setText(f"🏷️ 关键词: {keyword_str}")

        # 1. 停止清理上一次的动图
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.setDevice(None)
            self.current_movie.deleteLater() 
            self.current_movie = None
            
        self.image_label.clear()
        
        # 根据元数据计算底部高度，大约需要 40px 留给文字
        total_height = size_config + 40
        self.setFixedSize(size_config, total_height)
        
        # 预读图片获取原始尺寸
        from PySide6.QtGui import QImageReader
        reader = QImageReader(image_path)
        original_size = reader.size()
        
        target_size = self.calculate_scaled_size(original_size, size_config)
        
        self.image_label.setFixedSize(size_config - 16, size_config - 16)
        self.image_label.setScaledContents(False)

        # 2. 智能计算位置，防止超出屏幕
        screen_rect = QApplication.primaryScreen().availableGeometry()
        
        # 默认放在鼠标右下方偏移一定像素
        target_x = global_pos.x() + 15
        target_y = global_pos.y() + 15
        
        # 如果超出右边界，往左翻转
        if target_x + size_config > screen_rect.right():
            target_x = global_pos.x() - size_config - 15
            
        # 如果超出下边界，往上翻转
        if target_y + total_height > screen_rect.bottom():
            target_y = global_pos.y() - total_height - 15

        # 3. 移动并无焦点显示窗口
        self.move(target_x, target_y)
        self.show()

        # 4. 根据文件格式渲染
        if image_path.lower().endswith(('.gif', '.webp')):
            self.current_movie = QMovie(image_path, parent=self)
            self.current_movie.setCacheMode(QMovie.CacheAll)
            
            self.current_movie.setScaledSize(target_size)
            
            self.image_label.setMovie(self.current_movie)
            self.current_movie.start()
        else:
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.image_label.setPixmap(scaled_pixmap)

    def hide_preview(self):
        """隐藏预览并清理资源释放文件锁"""
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.setDevice(None)
            self.current_movie.deleteLater()
            self.current_movie = None
            
        self.image_label.clear()
        self.hide()
        
    def calculate_scaled_size(self, original_size, target_square_size):
        """计算保持宽高比的 QSize"""
        if original_size.isEmpty():
            return QSize(target_square_size - 16, target_square_size - 16)
            
        width = original_size.width()
        height = original_size.height()
        target = target_square_size - 16 

        if width > height:
            return QSize(target, int(height * (target / width)))
        else:
            return QSize(int(width * (target / height)), target)