from PySide6.QtWidgets import QLabel, QApplication, QMenu
from PySide6.QtGui import QCursor, QDrag, QPixmap, QImageReader, QPixmapCache
from PySide6.QtCore import (
    Qt, Signal, QMimeData, QPoint, QSize, QTimer, QEasingCurve,
    QPropertyAnimation, QObject, QRunnable, QThreadPool
)
from PySide6.QtWidgets import QGraphicsOpacityEffect
from qfluentwidgets import TransparentToolButton, FluentIcon

try:
    from shiboken6 import isValid as _is_qt_object_valid
except ImportError:
    def _is_qt_object_valid(obj):
        return True


class _ThumbnailLoadSignals(QObject):
    finished = Signal(str, object, int, int)


class _ThumbnailLoadTask(QRunnable):
    """在线程池中解码缩略图，只传递 QImage，不在线程中创建 QPixmap。"""

    def __init__(self, image_path, target_size, request_id):
        super().__init__()
        self.image_path = image_path
        self.target_size = target_size
        self.request_id = request_id
        self._cancelled = False
        self.signals = _ThumbnailLoadSignals()

    def run(self):
        image = None
        try:
            if self._cancelled:
                return

            reader = QImageReader(self.image_path)
            original_size = reader.size()

            if original_size.isValid():
                original_size.scale(
                    self.target_size,
                    self.target_size,
                    Qt.KeepAspectRatio
                )
                reader.setScaledSize(original_size)

            loaded = reader.read()
            if self._cancelled:
                return
            if loaded and not loaded.isNull():
                image = loaded
        except Exception as e:
            print(
                f"[Error] Async thumbnail failed: "
                f"{self.image_path}, error: {e}"
            )

        if self._cancelled:
            return

        self.signals.finished.emit(
            self.image_path,
            image,
            self.request_id,
            self.target_size
        )


class ThumbnailCache:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._normal_limit_kb = 16 * 1024  # 正常操作时 16MB
            self._idle_limit_kb = 4 * 1024     # 空闲时 4MB

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
        """空闲超时，清空未被控件引用的缓存图片并降低缓存上限。"""
        QPixmapCache.setCacheLimit(self._idle_limit_kb)
        QPixmapCache.clear()

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

_thumbnail_pool = QThreadPool()
# 缩略图解码通常受磁盘和图片解码器限制；降低并发可减少快速滚动时
# 同时存在的 QImage 峰值，避免线程池积压造成内存突增。
_thumbnail_pool.setMaxThreadCount(2)


class EmojiCard(QLabel):
    """
    Fluent风格的图片组件，用于在网格中展示缩略图并支持点击事件和拖拽排序
    """
    clicked = Signal(str, Qt.KeyboardModifiers)  # 点击信号，传递图片路径和键盘修饰键状态
    delete_requested = Signal(object, str) # 请求删除的信号: (Widget实例, 路径)
    selection_changed = Signal(str, bool)  # 选中状态改变信号: (路径, 是否选中)

    hover_started = Signal(str) # 悬停进入
    hover_ended = Signal()      # 悬停离开

    def __init__(self, image_path, size=120, parent=None, load_image=True):
        super().__init__(parent)
        self.image_path = image_path
        self._drag_start_pos = None
        self._is_dragging = False
        self.current_size = size
        self._loaded_size = 0  # 记录当前实际加载的图片尺寸
        self._is_loaded = False
        self._is_loading = False
        self._loading_size = 0
        self._load_request_id = 0
        self._load_task = None

        # 入场动画资源。动画结束后会移除 opacity effect，避免长期增加绘制开销。
        self._appear_timer = None
        self._opacity_effect = None
        self._appear_animation = None
        self._animation_generation = 0

        self.is_selectable = False
        self.is_selected = False

        self.setFixedSize(self.current_size, self.current_size)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setContextMenuPolicy(Qt.CustomContextMenu)

        self.setAlignment(Qt.AlignCenter)

        self.setObjectName("EmojiCard")
        self.update_style()
        self.update_size(self.current_size, load_image=load_image)

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
            from qfluentwidgets import themeColor, isDarkTheme
            color = themeColor()
            r, g, b = color.red(), color.green(), color.blue()
            hex_color = color.name()

            if isDarkTheme():
                bg_color = f"rgba({r}, {g}, {b}, 0.28)"
            else:
                bg_color = f"rgba({r}, {g}, {b}, 0.12)"

            self.setStyleSheet(f"""
                QLabel#EmojiCard {{
                    border-radius: 8px;
                    background-color: {bg_color};
                    border: 2px solid {hex_color};
                }}
            """)
        else:
            from qfluentwidgets import isDarkTheme
            if isDarkTheme():
                hover_bg = "rgba(255, 255, 255, 0.08)"
                hover_border = "rgba(255, 255, 255, 0.15)"
            else:
                hover_bg = "rgba(0, 0, 0, 0.05)"
                hover_border = "rgba(0, 0, 0, 0.12)"

            self.setStyleSheet(f"""
                QLabel#EmojiCard {{
                    border-radius: 8px;
                    background-color: transparent;
                    border: 1px solid transparent;
                }}
                QLabel#EmojiCard:hover {{
                    background-color: {hover_bg};
                    border: 1px solid {hover_border};
                }}
            """)

    def paintEvent(self, event):
        super().paintEvent(event)
        # 如果被选中，在右上角画一个打勾的圆圈
        if self.is_selected:
            from PySide6.QtGui import QPainter, QColor, QPen
            from PySide6.QtCore import QRect, QPoint
            from qfluentwidgets import themeColor

            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            # 画一个主题色底色的圆
            painter.setBrush(themeColor())
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
        target_img_size = max(10, target_size - 16)
        if self._is_loading and self._loading_size == target_img_size:
            return False
        return not self._is_loaded or self._loaded_size != target_size

    def _stop_appear_animation(self):
        """停止入场动画并恢复正常绘制状态。"""
        self._animation_generation += 1

        timer = self._appear_timer
        self._appear_timer = None
        if timer is not None:
            try:
                if _is_qt_object_valid(timer):
                    timer.stop()
                    timer.deleteLater()
            except RuntimeError:
                pass

        animation = self._appear_animation
        self._appear_animation = None
        if animation is not None:
            try:
                if _is_qt_object_valid(animation):
                    animation.stop()
                    animation.deleteLater()
            except RuntimeError:
                pass

        effect = self._opacity_effect
        self._opacity_effect = None
        if effect is not None:
            try:
                if _is_qt_object_valid(effect) and self.graphicsEffect() is effect:
                    self.setGraphicsEffect(None)
            except RuntimeError:
                pass

    def _start_appear_animation(self, generation):
        if generation != self._animation_generation or not self.isVisible():
            return

        self._appear_timer = None
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self._opacity_effect = effect
        self.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._appear_animation = animation

        def finish():
            if self._appear_animation is not animation:
                return

            self._appear_animation = None
            self._opacity_effect = None
            try:
                if _is_qt_object_valid(effect):
                    effect.setOpacity(1.0)
                    if self.graphicsEffect() is effect:
                        self.setGraphicsEffect(None)
                if _is_qt_object_valid(animation):
                    animation.deleteLater()
            except RuntimeError:
                pass

        animation.finished.connect(finish)
        animation.start()

    def animate_appear(self, delay=0):
        """以轻微淡入显示卡片；delay 用于批次内错峰，避免同时闪现。"""
        self._stop_appear_animation()
        generation = self._animation_generation

        if delay <= 0:
            self._start_appear_animation(generation)
            return

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda: self._start_appear_animation(generation)
        )
        self._appear_timer = timer
        timer.start(delay)

    def _cancel_thumbnail_task(self):
        task = self._load_task
        self._load_task = None
        if task is not None:
            task._cancelled = True

    def clear_resources(self):
        """释放图片引用，但保留卡片控件和占位外观。"""
        self._stop_appear_animation()
        self._cancel_thumbnail_task()
        self._load_request_id += 1
        self._is_loading = False
        self._loading_size = 0
        self.clear()
        self._is_loaded = False
        self._loaded_size = 0
        self.setText("⋯")

    def _on_thumbnail_loaded(
        self, image_path, image, request_id, target_size
    ):
        if (
            request_id != self._load_request_id
            or image_path != self.image_path
            or target_size != max(10, self.current_size - 16)
        ):
            return

        self._load_task = None
        self._is_loading = False
        self._loading_size = 0
        if image is None or image.isNull():
            self._is_loaded = False
            self._loaded_size = 0
            self.setText(" ")
            return

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._is_loaded = False
            self._loaded_size = 0
            self.setText(" ")
            return

        target_img_size = max(10, self.current_size - 16)
        QPixmapCache.insert(
            f"{self.image_path}|{target_img_size}x{target_img_size}",
            pixmap
        )
        self.setText("")
        self.setPixmap(pixmap)
        self._is_loaded = True
        self._loaded_size = self.current_size

    def _load_thumbnail_async(self, target_size):
        if self._is_loading and self._loading_size == target_size:
            return

        cache_key = f"{self.image_path}|{target_size}x{target_size}"
        cached = QPixmap()
        if QPixmapCache.find(cache_key, cached):
            self.setText("")
            self.setPixmap(cached)
            self._is_loaded = True
            self._loaded_size = self.current_size
            return

        self._cancel_thumbnail_task()
        self._load_request_id += 1
        request_id = self._load_request_id
        self._is_loading = True
        self._loading_size = target_size

        task = _ThumbnailLoadTask(
            self.image_path,
            target_size,
            request_id
        )
        task.signals.finished.connect(self._on_thumbnail_loaded)
        self._load_task = task
        _thumbnail_pool.start(task)

    def update_size(self, new_size, load_image=True):
        target_img_size = max(10, new_size - 16)
        if (
            load_image
            and self._is_loading
            and self._loading_size == target_img_size
            and self.current_size == new_size
        ):
            return

        self.current_size = new_size
        self.setFixedSize(new_size, new_size)

        if not load_image:
            # 尺寸调整期间保留旧 pixmap，避免先清空造成闪烁；
            # 但新建卡片没有旧图时显示稳定的占位符。
            self._cancel_thumbnail_task()
            self._load_request_id += 1
            self._is_loading = False
            self._loading_size = 0
            if not self._is_loaded:
                pixmap = self.pixmap()
                if pixmap is None or pixmap.isNull():
                    self.setText("⋯")
            return

        target_img_size = max(10, new_size - 16)
        self._is_loaded = False
        self._loaded_size = 0
        self.setText("⋯")
        self._load_thumbnail_async(target_img_size)

    def closeEvent(self, event):
        """卡片销毁前取消异步任务并释放仍由 QLabel 持有的缩略图。"""
        self._stop_appear_animation()
        self._cancel_thumbnail_task()
        self._load_request_id += 1
        self.clear()
        super().closeEvent(event)

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
