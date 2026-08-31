from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtGui import QMouseEvent
from qfluentwidgets import (
    SettingCard, SettingCardGroup, ScrollArea, ExpandLayout,
    FluentIcon as FIF, TransparentToolButton, TitleLabel,
    RoundMenu, Action
)

class ClickableSettingCard(SettingCard):
    """可点击的设置卡片"""
    clicked = Signal()

    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e: QMouseEvent):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

class ExchangeInterface(ScrollArea):
    """导出导入界面 (View)"""
    back_requested = Signal()
    import_requested = Signal()
    export_all_requested = Signal()
    export_selected_requested = Signal()
    qq_scan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("ExchangeInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.scrollWidget.setStyleSheet("QWidget { background-color: transparent; }")
        
        self._init_ui()

    def _init_ui(self):
        # 顶部返回工具栏
        self.topBar = QWidget(self.scrollWidget)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip("返回主面板")
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel("导出导入", self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        # =================== 导出导入选项 ===================
        self.exchangeGroup = SettingCardGroup("数据交换", self.scrollWidget)

        # 1. 资源包
        self.resourceCard = ClickableSettingCard(
            FIF.SAVE, "资源包", "导入外部表情包，或将本地表情及分类打包导出",
            parent=self.exchangeGroup
        )
        self.resourceCard.clicked.connect(self._show_resource_menu)

        # 2. 扫描 QQ 文件
        self.qqScanCard = ClickableSettingCard(
            FIF.PEOPLE, "扫描 QQ 文件", "扫描本地 QQ 缓存目录，提取商店表情与个人表情",
            parent=self.exchangeGroup
        )
        self.qqScanCard.clicked.connect(self.qq_scan_requested.emit)

        # 3. 扫描微信文件
        self.wechatScanCard = ClickableSettingCard(
            FIF.MESSAGE, "扫描微信文件 (暂未开放)", "后续将支持导入微信表情缓存数据",
            parent=self.exchangeGroup
        )
        self.wechatScanCard.setEnabled(False)

        # 将卡片加入组中
        self.exchangeGroup.addSettingCard(self.resourceCard)
        self.exchangeGroup.addSettingCard(self.qqScanCard)
        self.exchangeGroup.addSettingCard(self.wechatScanCard)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        
        self.expandLayout.addWidget(self.topBar)
        self.expandLayout.addWidget(self.exchangeGroup)

    def _show_resource_menu(self):
        menu = RoundMenu(parent=self)

        action_import = Action("导入资源包...", parent=menu)
        action_import.triggered.connect(self.import_requested.emit)
        menu.addAction(action_import)

        menu.addSeparator()

        action_export_all = Action("导出全部资源包", parent=menu)
        action_export_all.triggered.connect(self.export_all_requested.emit)
        menu.addAction(action_export_all)

        action_export_selected = Action("导出选中的收藏夹资源包", parent=menu)
        action_export_selected.triggered.connect(self.export_selected_requested.emit)
        menu.addAction(action_export_selected)

        # 弹在卡片下方
        pos = self.resourceCard.mapToGlobal(QPoint(0, self.resourceCard.height()))
        menu.exec(pos)
