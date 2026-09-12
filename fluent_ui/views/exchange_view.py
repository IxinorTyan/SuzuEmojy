from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout
from PySide6.QtGui import QMouseEvent
from qfluentwidgets import (
    SettingCard, SettingCardGroup, ScrollArea, ExpandLayout,
    FluentIcon as FIF, TransparentToolButton, TitleLabel,
    PushButton
)
from services.i18n import t, i18n_engine
from fluent_ui.views.setting_view import disable_wheel_scroll_adjustment


class ActionSettingCard(SettingCard):
    """
    带右侧操作按钮且整行可点击的 Fluent 风格卡片
    """
    clicked = Signal()

    def __init__(self, icon, title, content=None, btn_text="", parent=None):
        super().__init__(icon, title, content, parent)
        self.setCursor(Qt.PointingHandCursor)

        self.button = PushButton(btn_text, self)
        self.button.setFixedWidth(110)
        self.button.clicked.connect(self.clicked.emit)

        # 插入到 SettingCard 的右侧布局中
        self.hBoxLayout.addWidget(self.button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def mouseReleaseEvent(self, e: QMouseEvent):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class ExchangeInterface(QWidget):
    """导出导入数据交换界面 (View)"""
    back_requested = Signal()
    import_requested = Signal()
    export_all_requested = Signal()
    export_selected_requested = Signal()
    qq_scan_requested = Signal()
    tg_sticker_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("ExchangeInterface")
        
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(0, 0, 0, 0)
        self.mainLayout.setSpacing(0)

        # 顶部返回工具栏（固定在页面顶端）
        self.topBar = QWidget(self)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(36, 10, 36, 12)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回主面板"))
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel(t("导入导出"), self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        self.mainLayout.addWidget(self.topBar)

        # 独立滚动区域
        self.scrollArea = ScrollArea(self)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.enableTransparentBackground()
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.scrollWidget.setStyleSheet("QWidget { background-color: transparent; }")
        
        # =================== 1. 表情资源包卡片组 ===================
        self.resourceGroup = SettingCardGroup(t("表情资源包"), self.scrollWidget)

        # 1.1 导入资源包
        self.importCard = ActionSettingCard(
            FIF.FOLDER_ADD,
            t("导入资源包"),
            t("导入外部表情包文件（.zip），自动识别表情分类并去重"),
            btn_text=t("导入..."),
            parent=self.resourceGroup
        )
        self.importCard.clicked.connect(self.import_requested.emit)

        # 1.2 导出全部表情包
        self.exportAllCard = ActionSettingCard(
            FIF.SAVE,
            t("导出全部资源包"),
            t("将本地表情库的所有分类及表情完整导出为一个资源包"),
            btn_text=t("导出全部"),
            parent=self.resourceGroup
        )
        self.exportAllCard.clicked.connect(self.export_all_requested.emit)

        # 1.3 导出选中分类
        self.exportSelectedCard = ActionSettingCard(
            FIF.TAG,
            t("导出指定分类"),
            t("自由勾选需要导出的表情分类，单独打包生成资源包"),
            btn_text=t("挑选导出..."),
            parent=self.resourceGroup
        )
        self.exportSelectedCard.clicked.connect(self.export_selected_requested.emit)

        self.resourceGroup.addSettingCard(self.importCard)
        self.resourceGroup.addSettingCard(self.exportAllCard)
        self.resourceGroup.addSettingCard(self.exportSelectedCard)

        # =================== 2. 第三方平台导入卡片组 ===================
        self.thirdPartyGroup = SettingCardGroup(t("第三方导入"), self.scrollWidget)

        # 2.1 扫描 QQ 文件
        self.qqScanCard = ActionSettingCard(
            FIF.PEOPLE,
            t("扫描 QQ 聊天表情"),
            t("扫描本地 QQ 缓存目录，提取接收到的表情与个人表情"),
            btn_text=t("开始扫描"),
            parent=self.thirdPartyGroup
        )
        self.qqScanCard.clicked.connect(self.qq_scan_requested.emit)

        # 2.2 下载 TG 贴纸
        self.tgStickerCard = ActionSettingCard(
            FIF.SEND,
            t("下载 TG 贴纸"),
            t("批量解析并下载 Telegram 贴纸包/表情包，支持格式转换与入库"),
            btn_text=t("前往下载"),
            parent=self.thirdPartyGroup
        )
        self.tgStickerCard.clicked.connect(self.tg_sticker_requested.emit)

        self.thirdPartyGroup.addSettingCard(self.qqScanCard)
        self.thirdPartyGroup.addSettingCard(self.tgStickerCard)

        # 布局排列
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 0, 36, 20)
        
        self.expandLayout.addWidget(self.resourceGroup)
        self.expandLayout.addWidget(self.thirdPartyGroup)

        self.mainLayout.addWidget(self.scrollArea)
        disable_wheel_scroll_adjustment(self)

    def _connect_signals(self):
        # 绑定多语言切换
        i18n_engine.language_changed.connect(self.update_texts)

    def update_texts(self, lang):
        """动态刷新界面文本"""
        self.titleLabel.setText(t("导入导出"))
        self.btnBack.setToolTip(t("返回主面板"))

        self.resourceGroup.titleLabel.setText(t("表情资源包"))
        self.importCard.setTitle(t("导入资源包"))
        self.importCard.setContent(t("导入外部表情包文件（.zip），自动识别表情分类并去重"))
        self.importCard.button.setText(t("导入..."))

        self.exportAllCard.setTitle(t("导出全部资源包"))
        self.exportAllCard.setContent(t("将本地表情库的所有分类及表情完整导出为一个资源包"))
        self.exportAllCard.button.setText(t("导出全部"))

        self.exportSelectedCard.setTitle(t("导出指定分类"))
        self.exportSelectedCard.setContent(t("自由勾选需要导出的表情分类，单独打包生成资源包"))
        self.exportSelectedCard.button.setText(t("挑选导出..."))

        self.thirdPartyGroup.titleLabel.setText(t("第三方导入"))
        self.qqScanCard.setTitle(t("扫描 QQ 聊天表情"))
        self.qqScanCard.setContent(t("扫描本地 QQ 缓存目录，提取接收到的表情与个人表情"))
        self.qqScanCard.button.setText(t("开始扫描"))

        self.tgStickerCard.setTitle(t("下载 TG 贴纸"))
        self.tgStickerCard.setContent(t("批量解析并下载 Telegram 贴纸包/表情包，支持格式转换与入库"))
        self.tgStickerCard.button.setText(t("前往下载"))
