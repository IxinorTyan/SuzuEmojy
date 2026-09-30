import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, QEvent, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget, QAbstractScrollArea


def _native_window_under_cursor():
    """使用原生坐标命中窗口，避免混用 Qt 逻辑坐标和 Windows DPI 坐标。"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    hwnd = user32.WindowFromPoint(point)
    return user32.GetAncestor(hwnd, 2) if hwnd else None  # GA_ROOT


class HoverPreviewController(QObject):
    """统一管理缩略图悬停预览的生命周期、取消和可见区域校验。"""

    def __init__(self, owner, popup, viewport, parent=None):
        super().__init__(parent or owner)
        self.owner = owner
        self.popup = popup
        self.viewport = viewport

        self.current_hover_card = None
        self.current_hover_path = None
        self.hover_generation = 0
        self.delay = 500

        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._on_delay_timeout)

        self.validation_timer = QTimer(self)
        # 预览显示后持续确认鼠标位置，避免滚动或布局重排时 leaveEvent 丢失。
        self.validation_timer.setInterval(25)
        self.validation_timer.timeout.connect(self._validate_visible_preview)

        self.settle_timer = QTimer(self)
        self.settle_timer.setSingleShot(True)
        self.settle_timer.setInterval(150)
        self.settle_timer.timeout.connect(self._resume_under_cursor)
        # 应用级过滤器同时捕获卡片、viewport 及所属窗口的事件。
        QApplication.instance().installEventFilter(self)
        scroll_area = viewport.parentWidget()
        if isinstance(scroll_area, QAbstractScrollArea):
            scroll_area.verticalScrollBar().valueChanged.connect(self.suspend)
            scroll_area.horizontalScrollBar().valueChanged.connect(self.suspend)

    def suspend(self, *_):
        """滚动/布局稳定后重新命中卡片，再开始完整悬停延迟。"""
        self.cancel()
        self.settle_timer.start()

    def _resume_under_cursor(self):
        if not self._owner_is_visible():
            return
        card = self.viewport.childAt(self.viewport.mapFromGlobal(QCursor.pos()))
        while card is not None and card is not self.viewport:
            if hasattr(card, 'hover_started') and hasattr(card, 'image_path'):
                if self.is_thumbnail_under_cursor(card):
                    self.enter(card, card.image_path)
                return
            card = card.parentWidget()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in (
            QEvent.Hide, QEvent.Close, QEvent.WindowStateChange, QEvent.Leave,
            QEvent.Move, QEvent.Resize, QEvent.Wheel, QEvent.MouseButtonPress,
            QEvent.DragEnter, QEvent.ContextMenu,
        ):
            return False
        if not isinstance(obj, QWidget):
            return False
        if obj in (self.owner, self.owner.window(), self.viewport):
            if kind in (QEvent.Hide, QEvent.Close, QEvent.WindowStateChange, QEvent.Leave):
                self.cancel()
            elif kind in (QEvent.Move, QEvent.Resize):
                self.suspend()
        if obj is self.viewport or self.viewport.isAncestorOf(obj):
            if kind in (QEvent.Wheel, QEvent.Move, QEvent.Resize):
                self.suspend()
            elif kind in (QEvent.MouseButtonPress, QEvent.DragEnter, QEvent.ContextMenu):
                self.cancel()
        return False

    def set_delay(self, delay):
        self.delay = max(0, int(delay))

    def enter(self, card, path):
        if self.settle_timer.isActive():
            return
        if card is None or not self._owner_is_visible():
            self.cancel()
            return

        self._cancel_timers_and_popup()
        self.hover_generation += 1
        generation = self.hover_generation

        self.current_hover_card = card
        self.current_hover_path = path

        # destroyed 信号只影响当前这一代的当前卡片。
        card.destroyed.connect(
            lambda _obj=None, card=card, generation=generation:
            self._on_card_destroyed(card, generation)
        )
        self.hover_timer.start(self.delay)

    def leave(self, card):
        # A 的延迟 leave 不能清理后来进入的 B。
        if card is self.current_hover_card:
            self.cancel()

    def cancel(self):
        self.hover_generation += 1
        self.settle_timer.stop()
        self._cancel_timers_and_popup()
        self.current_hover_card = None
        self.current_hover_path = None

    def _cancel_timers_and_popup(self):
        self.hover_timer.stop()
        self.validation_timer.stop()
        self.popup.hide_preview()

    def _on_card_destroyed(self, card, generation):
        if card is self.current_hover_card and generation == self.hover_generation:
            self.cancel()

    @staticmethod
    def _is_valid_widget(widget):
        if widget is None:
            return False
        try:
            import shiboken6
            if not shiboken6.isValid(widget):
                return False
        except ImportError:
            pass
        try:
            return isinstance(widget, QWidget) and not widget.isHidden() and widget.isVisible()
        except RuntimeError:
            return False

    def is_thumbnail_under_cursor(self, card, global_pos=None):
        """验证鼠标位于缩略图内，且该缩略图实际处于所属 viewport 可见区域。"""
        if global_pos is None:
            global_pos = QCursor.pos()

        if not self._is_valid_widget(self.viewport):
            return False
        if not self._is_valid_widget(card):
            return False

        try:
            window = self.owner.window()
            if not window.rect().contains(window.mapFromGlobal(global_pos)):
                return False
            viewport_pos = self.viewport.mapFromGlobal(global_pos)
            if not self.viewport.rect().contains(viewport_pos):
                return False

            thumbnail_pos = card.mapFromGlobal(global_pos)
            if not card.rect().contains(thumbnail_pos):
                return False

            # visibleRegion() 是相对于 card 的区域，可排除被祖先裁剪的部分。
            if card.visibleRegion().isEmpty() or not card.visibleRegion().contains(thumbnail_pos):
                return False

            # 必须确认“鼠标当前点”同时位于卡片和 viewport 的可见交集内。
            # 仅判断两个矩形有交集会误把 viewport 外、瀑布流下方的卡片判定为悬停。
            card_global_rect = card.rect()
            card_global_rect.moveTo(card.mapToGlobal(card.rect().topLeft()))

            viewport_global_rect = self.viewport.rect()
            viewport_global_rect.moveTo(
                self.viewport.mapToGlobal(self.viewport.rect().topLeft())
            )
            visible_intersection = card_global_rect.intersected(viewport_global_rect)
            return (
                not visible_intersection.isEmpty()
                and visible_intersection.contains(global_pos)
                and self._window_is_under_cursor(card, global_pos)
            )
        except RuntimeError:
            return False

    def _window_is_under_cursor(self, card, global_pos):
        # widgetAt 排除同一窗口内的覆盖控件；原生命中排除其他应用遮挡。
        hit = QApplication.widgetAt(global_pos)
        if hit is None or not (hit is card or card.isAncestorOf(hit)):
            return False
        if sys.platform == 'win32' and QGuiApplication.platformName() == 'windows':
            return _native_window_under_cursor() == int(self.owner.window().winId())
        return True

    def _owner_is_visible(self):
        """只检查预览来源是否仍存在并可见，不要求主窗口拥有焦点。"""
        try:
            window = self.owner.window()
            return (
                self.owner.isVisible()
                and window.isVisible()
                and not window.isMinimized()
                and not self.viewport.isHidden()
                and not getattr(self.owner, 'is_selection_mode', False)
                and QApplication.activePopupWidget() is None
                and QApplication.activeModalWidget() is None
                and QApplication.mouseButtons() == Qt.NoButton
            )
        except RuntimeError:
            return False

    def _on_delay_timeout(self):
        generation = self.hover_generation
        card = self.current_hover_card
        path = self.current_hover_path

        if generation != self.hover_generation or not path:
            return
        if not self._owner_is_visible() or not self.is_thumbnail_under_cursor(card):
            self.cancel()
            return

        try:
            category_str, keyword_str = self.owner.get_preview_metadata(path)
            if generation != self.hover_generation:
                return
            if not self._owner_is_visible() or not self.is_thumbnail_under_cursor(card):
                self.cancel()
                return
            # 使用实际指针位置作为锚点，而不是卡片右上角。
            # 卡片边缘可能跨越多显示器边界，使用右上角会导致弹窗
            # 按另一块屏幕的可用区域计算位置。
            anchor_pos = QCursor.pos()
            self.popup.show_preview(
                path,
                anchor_pos,
                self.owner.get_preview_size(),
                category_str,
                keyword_str,
            )
        except RuntimeError:
            self.cancel()
            return

        # show_preview 可能因文件失效而不显示；只对当前代启动监测。
        if (
            generation == self.hover_generation
            and self.current_hover_card is card
            and self.current_hover_path == path
            and self.popup.isVisible()
        ):
            self._validate_visible_preview()
        if self.popup.isVisible() and generation == self.hover_generation:
            self.validation_timer.start()

    def _validate_visible_preview(self):
        if not self.popup.isVisible():
            self.validation_timer.stop()
            return

        card = self.current_hover_card
        if (
            not self._owner_is_visible()
            or not self.is_thumbnail_under_cursor(card)
        ):
            self.cancel()

    def shutdown(self):
        self.cancel()
        QApplication.instance().removeEventFilter(self)
