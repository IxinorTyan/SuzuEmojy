import os
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication, QHBoxLayout
from PySide6.QtCore import Qt, QSize, QByteArray, QBuffer
from PySide6.QtGui import QPixmap, QMovie, QImageReader
from qfluentwidgets import BodyLabel, CaptionLabel
import darkdetect
from services.i18n import t, i18n_engine

class HoverPreviewPopup(QWidget):
    """
    独立且无焦点的悬停预览浮窗组件，包含元数据展示
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 使用无焦点的工具窗口，避免 Qt.ToolTip 在应用失去激活状态时自动隐藏。
        # 预览不接收鼠标事件，因此不会阻挡指针离开缩略图。
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
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
        
        self.category_label = CaptionLabel(f"🗂️ {t('无分类')}")
        
        self.keyword_label = CaptionLabel(f"🏷️ {t('无关键词')}")
        
        self.meta_layout.addWidget(self.category_label)
        self.meta_layout.addWidget(self.keyword_label)
        
        self.layout.addLayout(self.meta_layout)
        
        self.current_movie = None
        self.setObjectName("PreviewPopup")
        
        # 绑定语言切换信号
        i18n_engine.language_changed.connect(self.update_texts)

    def update_texts(self, lang):
        """动态刷新界面文本"""
        # 只有在没有实际数据时才刷新默认文本
        if not hasattr(self, 'current_category_str') or not self.current_category_str:
            self.category_label.setText(f"🗂️ {t('无分类')}")
            self.keyword_label.setText(f"🏷️ {t('无关键词')}")
        else:
            # 如果有数据，重新翻译并显示
            self.category_label.setText(f"🗂️ {t('分类')}: {self.current_category_str}")
            self.keyword_label.setText(f"🏷️ {t('关键词')}: {self.current_keyword_str}")

    def update_theme(self):
        """动态同步当前 qfluentwidgets 的主题风格"""
        from qfluentwidgets import isDarkTheme
        from qfluentwidgets import themeColor
        color = themeColor().name()
        
        if isDarkTheme():
            self.category_label.setStyleSheet("color: white;")
            self.keyword_label.setStyleSheet("color: white;")
            self.setStyleSheet(f"""
                QWidget#PreviewPopup {{
                    background-color: rgba(30, 30, 30, 0.95);
                    border: 1px solid {color};
                    border-radius: 8px;
                }}
            """)
        else:
            self.category_label.setStyleSheet("color: #222;")
            self.keyword_label.setStyleSheet("color: #222;")
            self.setStyleSheet(f"""
                QWidget#PreviewPopup {{
                    background-color: rgba(255, 255, 255, 0.95);
                    border: 1px solid {color};
                    border-radius: 8px;
                }}
            """)

    def show_preview(self, image_path, global_pos, size_config=320, category_str="无", keyword_str="无"):
        """
        显示预览窗口，并更新元数据
        """
        if not os.path.exists(image_path):
            return

        self.update_theme()
        
        # 保存当前状态以便语言切换时刷新
        self.current_category_str = category_str
        self.current_keyword_str = keyword_str
        
        self.category_label.setText(f"🗂️ {t('分类')}: {category_str}")
        self.keyword_label.setText(f"🏷️ {t('关键词')}: {keyword_str}")

        # 1. 停止清理上一次的动图
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.setDevice(None)
            self.current_movie.deleteLater() 
            self.current_movie = None
            
        self.image_label.clear()
        
        # 根据当前屏幕可用空间限制预览尺寸，避免边缘位置放不下时窗口
        # 超出屏幕或覆盖指针，继而被悬停逻辑立即隐藏。
        screen = QApplication.screenAt(global_pos)
        if screen is None:
            screen = QApplication.primaryScreen()
        screen_rect = screen.availableGeometry()

        gap = 15
        metadata_height = 40
        max_popup_width = max(1, screen_rect.width() - gap * 2)
        max_image_size = max(
            1,
            screen_rect.height() - gap * 2 - metadata_height,
        )
        display_size = min(
            int(size_config),
            max_popup_width,
            max_image_size,
        )
        total_height = display_size + metadata_height
        self.setFixedSize(display_size, total_height)
        
        # 只读取图片尺寸，不载入原图像素数据。
        reader = QImageReader(image_path)
        original_size = reader.size()
        
        target_size = self.calculate_scaled_size(original_size, display_size)
        
        self.image_label.setFixedSize(
            max(1, display_size - 16),
            max(1, display_size - 16),
        )
        self.image_label.setScaledContents(False)

        # 2. 智能计算位置，防止预览窗口落到屏幕外
        popup_width = self.width()
        popup_height = self.height()

        # 优先放在指针右下方；空间不足时分别翻转到左侧/上方。
        candidate_positions = (
            (global_pos.x() + gap, global_pos.y() + gap),
            (global_pos.x() - popup_width - gap, global_pos.y() + gap),
            (global_pos.x() + gap, global_pos.y() - popup_height - gap),
            (global_pos.x() - popup_width - gap, global_pos.y() - popup_height - gap),
        )

        target_x, target_y = next(
            (
                (x, y)
                for x, y in candidate_positions
                if (
                    x >= screen_rect.left()
                    and y >= screen_rect.top()
                    and x + popup_width <= screen_rect.right() + 1
                    and y + popup_height <= screen_rect.bottom() + 1
                )
            ),
            (
                # 四个方向都放不下时，夹紧到当前屏幕可用区域。
                max(
                    screen_rect.left(),
                    min(global_pos.x() + gap, screen_rect.right() - popup_width + 1),
                ),
                max(
                    screen_rect.top(),
                    min(global_pos.y() + gap, screen_rect.bottom() - popup_height + 1),
                ),
            ),
        )

        # 3. 移动并无焦点显示窗口
        self.move(target_x, target_y)
        self.show()

        # 4. 根据文件格式渲染
        if image_path.lower().endswith(('.gif', '.webp')):
            self.current_movie = QMovie(image_path, parent=self)
            # 预览动图时只保留当前/必要帧，避免 CacheAll 将整个动画
            # 解码后常驻内存。显示尺寸和动画行为保持不变。
            self.current_movie.setCacheMode(QMovie.CacheNone)
            
            self.current_movie.setScaledSize(target_size)
            
            self.image_label.setMovie(self.current_movie)
            self.current_movie.start()
        else:
            # 直接按预览尺寸解码，避免 QPixmap(image_path) 先把超大原图
            # 完整载入内存后再缩放。target_size 已按原图比例计算。
            reader = QImageReader(image_path)
            reader.setScaledSize(target_size)
            image = reader.read()

            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                self.image_label.setPixmap(pixmap)

    def hide_preview(self):
        """隐藏预览并清理资源释放文件锁"""
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.setDevice(None)
            self.current_movie.deleteLater()
            self.current_movie = None
            
        self.image_label.clear()
        # 显式替换 QLabel 内部的 pixmap 引用，避免静态预览在隐藏后
        # 继续占用一块较大的图像内存。
        self.image_label.setPixmap(QPixmap())
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
