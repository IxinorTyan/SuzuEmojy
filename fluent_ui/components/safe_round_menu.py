from PySide6.QtCore import QPoint, QRect
from qfluentwidgets import RoundMenu
from qfluentwidgets.common.screen import getCurrentScreenGeometry


class SafeRoundMenu(RoundMenu):
    """修复次级菜单在屏幕左侧展开时鼠标穿越导致的误收起。"""

    def _onShowMenuTimeOut(self):
        """定位次级菜单，避免左侧展开时与一级菜单产生异常间隔。"""
        if (
            self.lastHoverSubMenuItem is None
            or self.lastHoverItem is not self.lastHoverSubMenuItem
        ):
            return

        widget = self.view.itemWidget(self.lastHoverSubMenuItem)
        if widget is None or widget.menu.parentMenu.isHidden():
            return

        item_rect = QRect(
            widget.mapToGlobal(widget.rect().topLeft()),
            widget.size(),
        )
        submenu_size = widget.menu.sizeHint()
        screen_rect = getCurrentScreenGeometry()

        # 默认从右侧展开，并与一级菜单项保持很小的间距。
        x = item_rect.right() + 5
        y = item_rect.y() - 5

        # 屏幕右侧空间不足时改为从左侧展开。
        # 使用 +1 贴合父菜单项，避免默认的额外偏移在边缘修正后
        # 被放大成明显的空白区域。
        if x + submenu_size.width() > screen_rect.right():
            x = item_rect.left() - submenu_size.width() + 1

            # 只有真正超出屏幕左边界时才进行边界修正。
            if x < screen_rect.left():
                x = screen_rect.left()

        if y + submenu_size.height() > screen_rect.bottom():
            y = screen_rect.bottom() - submenu_size.height()

        y = max(y, screen_rect.top())
        widget.menu.exec(QPoint(x, y))

    def mouseMoveEvent(self, event):
        if not self.isSubMenu or self.parentMenu is None:
            return super().mouseMoveEvent(event)

        pos = event.globalPos()
        parent_menu = self.parentMenu
        view = parent_menu.view

        # 与 RoundMenu 原始实现保持一致，取得父菜单中的当前菜单项区域。
        margin = view.viewportMargins()
        item_rect = view.visualItemRect(self.menuItem).translated(
            view.mapToGlobal(QPoint())
        )
        item_rect = item_rect.translated(margin.left(), margin.top() + 2)

        # 鼠标位于当前菜单项、子菜单，或二者之间的横向间隙时，
        # 不执行原始的收起逻辑。该区域同时兼容右侧和左侧展开。
        submenu_rect = self.geometry()
        if item_rect.contains(pos) or submenu_rect.contains(pos):
            return

        gap_left = min(item_rect.right(), submenu_rect.right())
        gap_right = max(item_rect.left(), submenu_rect.left())
        gap_top = min(item_rect.top(), submenu_rect.top())
        gap_bottom = max(item_rect.bottom(), submenu_rect.bottom())
        bridge_rect = QRect(
            QPoint(gap_left, gap_top),
            QPoint(gap_right, gap_bottom),
        )
        if bridge_rect.contains(pos):
            return

        # 其他情况必须保留 RoundMenu 的默认行为，例如移动到父菜单的
        # 其他项目时收起当前子菜单。
        super().mouseMoveEvent(event)
