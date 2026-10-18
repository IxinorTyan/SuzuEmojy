from math import trunc

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication
from qfluentwidgets import ScrollArea
from fluent_ui.components.middle_button_scroll import MiddleButtonScroll


class ResponsiveScrollArea(ScrollArea):
    """保留 Fluent 滚动条，触控板直接跟随，鼠标滚轮使用短动画。"""

    scrollingChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewport().removeEventFilter(self.scrollDelagate)
        self._scroll_animations = {}
        self._wheel_remainders = {Qt.Vertical: 0.0, Qt.Horizontal: 0.0}
        self.is_scrolling = False
        self._slider_pressed = False
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._finish_scrolling)
        for orientation, bar, overlay in (
            (Qt.Vertical, self.verticalScrollBar(), self.scrollDelagate.vScrollBar),
            (Qt.Horizontal, self.horizontalScrollBar(), self.scrollDelagate.hScrollBar),
        ):
            animation = QPropertyAnimation(bar, b'value', self)
            animation.setDuration(100)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self._scroll_animations[orientation] = animation
            bar.sliderPressed.connect(self.stop_scrolling)
            overlay.sliderPressed.connect(self.stop_scrolling)
            bar.sliderPressed.connect(self._slider_start)
            overlay.sliderPressed.connect(self._slider_start)
            bar.sliderReleased.connect(self._slider_end)
            overlay.sliderReleased.connect(self._slider_end)
            bar.valueChanged.connect(self._mark_scrolling)
        self.viewport().installEventFilter(self)
        self._middle_scroller = MiddleButtonScroll(self)
        for bar in (self.verticalScrollBar(), self.scrollDelagate.vScrollBar):
            bar.sliderPressed.connect(self._middle_scroller.stop)

    def stop_scrolling(self):
        for animation in self._scroll_animations.values():
            animation.stop()

    def _mark_scrolling(self, *_):
        if not self.is_scrolling:
            self.is_scrolling = True
            self.scrollingChanged.emit(True)
        self._settle_timer.start(140)

    def _slider_start(self):
        self._slider_pressed = True
        self._mark_scrolling()

    def _slider_end(self):
        self._slider_pressed = False
        self._mark_scrolling()

    def _finish_scrolling(self):
        if self._slider_pressed or self._middle_scroller.active or any(a.state() == QAbstractAnimation.Running for a in self._scroll_animations.values()):
            self._settle_timer.start(140)
            return
        self.is_scrolling = False
        self.scrollingChanged.emit(False)

    def hideEvent(self, event):
        self._middle_scroller.stop(settle=False)
        self.stop_scrolling()
        self._settle_timer.stop()
        self._slider_pressed = False
        self.is_scrolling = False
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if obj is not self.viewport() or event.type() != QEvent.Wheel:
            return super().eventFilter(obj, event)
        if event.modifiers() & Qt.ControlModifier:
            return False

        self._mark_scrolling()

        pixels = event.pixelDelta()
        if not pixels.isNull():
            # 触控板已经提供逐帧位移（包括系统惯性），不再叠加第二层动画。
            self.stop_scrolling()
            self._wheel_remainders = {Qt.Vertical: 0.0, Qt.Horizontal: 0.0}
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - pixels.y())
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - pixels.x())
            event.accept()
            return True

        delta = event.angleDelta()
        orientation = Qt.Vertical if delta.y() else Qt.Horizontal
        angle = delta.y() if delta.y() else delta.x()
        if not angle:
            return False
        if event.modifiers() & Qt.ShiftModifier:
            orientation = Qt.Horizontal
        bar = self.verticalScrollBar() if orientation == Qt.Vertical else self.horizontalScrollBar()
        animation = self._scroll_animations[orientation]
        distance = -angle / 120 * QApplication.wheelScrollLines() * bar.singleStep()
        remainder = self._wheel_remainders[orientation]
        if remainder * distance < 0:
            remainder = 0
        total = distance + remainder
        distance = trunc(total)
        self._wheel_remainders[orientation] = total - distance
        if not distance:
            event.accept()
            return True
        current = bar.value()
        target = current
        if animation.state() == QAbstractAnimation.Running:
            pending = animation.endValue() - current
            # 反向滚动立即丢弃旧方向的余量，连续同向输入只保留有限的待滚距离。
            if pending * distance > 0:
                target = animation.endValue()
        animation.stop()
        pending = max(-bar.pageStep(), min(bar.pageStep(), target + distance - current))
        target = max(bar.minimum(), min(bar.maximum(), round(current + pending)))
        if target == current:
            return False
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.start()
        event.accept()
        return True
