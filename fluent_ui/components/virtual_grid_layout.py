from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QLayout, QWidgetItem


class VirtualGridLayout(QLayout):
    """Reserve all rows, but position only the few cards that actually exist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._positions = {}
        self._total = 0
        self._columns = 1
        self._size = 120
        self.setContentsMargins(16, 8, 16, 16)
        self.setSpacing(10)

    def configure(self, total, columns, size):
        self._total, self._columns, self._size = total, max(1, columns), size
        # The scroll range depends on data, never on thumbnail completion or batches.
        height = self.minimumSize().height()
        if self.parentWidget().minimumHeight() != height:
            self.parentWidget().setMinimumHeight(height)
        self.invalidate()

    def addItem(self, item):
        self._items.append(item)
        self._positions[item.widget()] = (0, 0)
        self.invalidate()

    def addWidget(self, widget, row=0, column=0):
        if widget not in self._positions:
            self.addChildWidget(widget)
            self._items.append(QWidgetItem(widget))
        self._positions[widget] = (row, column)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def itemAtPosition(self, row, column):
        return next((item for item in self._items if self._positions[item.widget()] == (row, column)), None)

    def takeAt(self, index):
        if not 0 <= index < len(self._items):
            return None
        item = self._items.pop(index)
        self._positions.pop(item.widget(), None)
        self.invalidate()
        return item

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        margins = self.contentsMargins()
        rows = (self._total + self._columns - 1) // self._columns
        height = margins.top() + margins.bottom() + max(0, rows * (self._size + self.spacing()) - self.spacing())
        return QSize(self._size + margins.left() + margins.right(), height)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        margins = self.contentsMargins()
        stride = self._size + self.spacing()
        width = self._columns * stride - self.spacing()
        left = max(margins.left(), (rect.width() - width) // 2)
        for item in self._items:
            row, column = self._positions[item.widget()]
            item.setGeometry(QRect(rect.x() + left + column * stride,
                                   rect.y() + margins.top() + row * stride,
                                   self._size, self._size))
