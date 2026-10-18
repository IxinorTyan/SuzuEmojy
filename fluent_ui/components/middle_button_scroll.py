"""Middle-button panning and browser-style click-to-scroll for a scroll area."""
from math import trunc
from time import perf_counter

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget


class MiddleButtonScroll(QObject):
    def __init__(self, area):
        super().__init__(area)
        self.area = area
        self.origin = None
        self._last_position = None
        self._pressed = False
        self._moved = False
        self._cancel_button = Qt.NoButton
        self._cursor_owned = False
        self._remainder = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        area.destroyed.connect(self._restore_cursor)
        QApplication.instance().installEventFilter(self)

    @property
    def active(self):
        return self.origin is not None

    def _cursor(self, shape):
        if self._cursor_owned:
            QApplication.changeOverrideCursor(QCursor(shape))
        else:
            QApplication.setOverrideCursor(QCursor(shape))
            self._cursor_owned = True

    def stop(self, *, settle=True):
        was_active = self.active
        self.timer.stop()
        self.origin = None
        self._pressed = False
        self._remainder = 0.0
        if QWidget.mouseGrabber() is self.area.viewport():
            self.area.viewport().releaseMouse()
        self._restore_cursor()
        if was_active and settle:
            self.area._mark_scrolling()

    def _restore_cursor(self):
        if self._cursor_owned:
            QApplication.restoreOverrideCursor()
            self._cursor_owned = False

    def _start(self, position):
        self.area.stop_scrolling()
        self.origin = position
        self._last_position = position
        self._pressed = True
        self._moved = False
        self._remainder = 0.0
        self._cursor(Qt.ClosedHandCursor)
        self.area.viewport().grabMouse()
        self.area._mark_scrolling()

    def _advance(self, position, elapsed):
        """Only change the scrollbar here; never load or lay out images."""
        if not self.active or self._pressed:
            return
        offset = position.y() - self.origin.y()
        distance = max(0, abs(offset) - 10)  # Neutral zone around the click.
        speed = min(2400, distance * 12) * (1 if offset > 0 else -1)
        if speed * self._remainder < 0 or not speed:
            self._remainder = 0.0
        movement = speed * max(0, min(.05, elapsed)) + self._remainder
        pixels = trunc(movement)
        self._remainder = movement - pixels
        bar = self.area.verticalScrollBar()
        bar.setValue(bar.value() + pixels)

    def _tick(self):
        now = perf_counter()
        self._advance(QCursor.pos(), now - self._last_tick)
        self._last_tick = now

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.MouseButtonRelease and event.button() == self._cancel_button:
            self._cancel_button = Qt.NoButton
            return True
        if self.active:
            if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                self._cancel_button = event.button()
                self.stop()
                return True  # The cancelling click must not paste/select a card.
            if kind == QEvent.MouseMove and self._pressed:
                position = event.globalPosition().toPoint()
                if self._moved or (position - self.origin).manhattanLength() >= QApplication.startDragDistance():
                    self._moved = True
                    bar = self.area.verticalScrollBar()
                    bar.setValue(bar.value() - (position.y() - self._last_position.y()))
                    self._last_position = position
                return True
            if kind == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
                # A drag ends on release. A click latches automatic scrolling.
                moved = self._moved or (event.globalPosition().toPoint() - self.origin).manhattanLength() >= QApplication.startDragDistance()
                if moved:
                    self.stop()
                else:
                    self._pressed = False
                    if QWidget.mouseGrabber() is self.area.viewport():
                        self.area.viewport().releaseMouse()
                    self._cursor(Qt.SizeVerCursor)
                    self._last_tick = perf_counter()
                    self.timer.start()
                return True
            if kind == QEvent.KeyPress:
                self.stop()
                return event.key() == Qt.Key_Escape
            if kind == QEvent.Wheel:
                self.stop()  # Normal wheel handling continues.
            elif kind == QEvent.ApplicationDeactivate or (kind == QEvent.WindowDeactivate and obj is self.area.window()):
                self.stop()
        elif kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) and event.button() == Qt.MiddleButton:
            viewport = self.area.viewport()
            if self.area.isVisible() and isinstance(obj, QWidget) and (obj is viewport or viewport.isAncestorOf(obj)):
                if self.area.verticalScrollBar().maximum() > self.area.verticalScrollBar().minimum():
                    self._start(event.globalPosition().toPoint())
                    return True
        return False
