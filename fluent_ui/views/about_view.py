import os
import sys
from PySide6.QtCore import Qt, Signal, QThread, QUrl, QRectF, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QSizePolicy, QApplication
)
from PySide6.QtGui import (
    QDesktopServices, QPixmap, QPainter, QPainterPath,
    QColor, QPen, QFont, QIcon
)
from qfluentwidgets import (
    ScrollArea, InfoBar, InfoBarPosition,
    FluentIcon as FIF, TransparentToolButton, TitleLabel, SubtitleLabel,
    BodyLabel, CaptionLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    CardWidget, SimpleCardWidget, isDarkTheme, IconWidget
)
from services.i18n import t, i18n_engine


def disable_wheel_scroll_adjustment(widget: QWidget):
    """递归禁用输入组件的滚轮事件，保证 ScrollArea 滚动顺畅。"""
    from PySide6.QtWidgets import QAbstractSpinBox, QAbstractSlider, QComboBox, QScrollBar
    from qfluentwidgets import SpinBox, Slider, ComboBox

    target_types = (QAbstractSpinBox, QAbstractSlider, QComboBox, SpinBox, Slider, ComboBox)
    if isinstance(widget, target_types) and not isinstance(widget, QScrollBar):
        widget.wheelEvent = lambda event: event.ignore()

    for child in widget.findChildren(QWidget):
        if isinstance(child, target_types) and not isinstance(child, QScrollBar):
            child.wheelEvent = lambda event: event.ignore()


class AvatarLoader(QThread):
    """在后台异步加载 GitHub 头像并缓存到本地，避免阻塞 UI。"""

    avatar_loaded = Signal(QPixmap)

    def __init__(self, avatar_url="https://github.com/IxinorTyan.png", parent=None):
        super().__init__(parent=parent)
        self.avatar_url = avatar_url
        
        # 缓存文件路径
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.cache_dir = os.path.join(base_dir, "data", "cache")
        self.cache_path = os.path.join(self.cache_dir, "avatar_IxinorTyan.png")

    def run(self):
        # 1. 优先读取本地已缓存头像
        if os.path.exists(self.cache_path):
            try:
                pixmap = QPixmap(self.cache_path)
                if not pixmap.isNull():
                    self.avatar_loaded.emit(pixmap)
            except Exception as e:
                print(f"[About] 读取本地头像缓存失败: {e}")

        # 2. 网络异步下载更新
        try:
            import requests

            os.makedirs(self.cache_dir, exist_ok=True)
            response = requests.get(
                self.avatar_url,
                timeout=8,
                headers={"User-Agent": "SuzuEmojy-Client"}
            )
            if response.status_code == 200 and response.content:
                pixmap = QPixmap()
                if pixmap.loadFromData(response.content):
                    # 保存本地缓存
                    with open(self.cache_path, "wb") as f:
                        f.write(response.content)
                    self.avatar_loaded.emit(pixmap)
        except Exception as e:
            # 网络不通或超时，静默失败，使用本地缓存或备用头像
            pass


class CircularAvatarWidget(QWidget):
    """带抗锯齿圆角裁剪与高光外圈的主题自适应头像控件。"""

    def __init__(self, size=72, parent=None):
        super().__init__(parent=parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = None

    def set_pixmap(self, pixmap: QPixmap):
        if pixmap and not pixmap.isNull():
            self._pixmap = pixmap
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        w = self.width()
        h = self.height()
        radius = min(w, h) / 2.0 - 2.0

        center_x = w / 2.0
        center_y = h / 2.0

        clip_path = QPainterPath()
        clip_path.addEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)

        if self._pixmap and not self._pixmap.isNull():
            painter.save()
            painter.setClipPath(clip_path)
            # 等比例平铺填充圆形
            scaled_pixmap = self._pixmap.scaled(
                int(radius * 2), int(radius * 2),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation
            )
            offset_x = int(center_x - scaled_pixmap.width() / 2.0)
            offset_y = int(center_y - scaled_pixmap.height() / 2.0)
            painter.drawPixmap(offset_x, offset_y, scaled_pixmap)
            painter.restore()
        else:
            # 备用默认头像底色：微渐变
            painter.save()
            painter.setClipPath(clip_path)
            dark = isDarkTheme()
            bg_color = QColor(60, 64, 75) if dark else QColor(220, 226, 235)
            painter.setBrush(bg_color)
            painter.setPen(Qt.NoPen)
            painter.drawRect(0, 0, w, h)

            # 绘制字母缩写 "IT"
            painter.setPen(QColor(255, 255, 255) if dark else QColor(70, 75, 85))
            font = QFont(self.font())
            font.setPointSize(int(radius * 0.45))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, "IT")
            painter.restore()

        # 外圈光晕/边框
        dark = isDarkTheme()
        ring_color = QColor(255, 255, 255, 45) if dark else QColor(0, 0, 0, 25)
        painter.setPen(QPen(ring_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)


class PillBadge(QLabel):
    """现代化胶囊标签组件，适应深浅色主题。"""

    def __init__(self, text, is_accent=False, parent=None):
        super().__init__(text, parent=parent)
        self.is_accent = is_accent
        self.setFixedHeight(24)
        self.setAlignment(Qt.AlignCenter)
        self.update_style()

    def update_style(self):
        dark = isDarkTheme()
        if self.is_accent:
            if dark:
                bg = "rgba(0, 153, 255, 0.18)"
                color = "#60CDFF"
                border = "rgba(0, 153, 255, 0.35)"
            else:
                bg = "rgba(0, 103, 192, 0.10)"
                color = "#005FB8"
                border = "rgba(0, 103, 192, 0.25)"
        else:
            if dark:
                bg = "rgba(255, 255, 255, 0.08)"
                color = "#D8D8D8"
                border = "rgba(255, 255, 255, 0.12)"
            else:
                bg = "rgba(0, 0, 0, 0.05)"
                color = "#4D4D4D"
                border = "rgba(0, 0, 0, 0.08)"

        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 12px;
                padding-left: 10px;
                padding-right: 10px;
                font-size: 11px;
                font-weight: 500;
            }}
        """)


class SettingCardGroup(QWidget):
    """带标题的卡片分组组件，使用标准垂直布局保证动态卡片高度自适应，杜绝错位与重叠。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent=parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.titleLabel = QLabel(title, self)
        self.titleLabel.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.vBoxLayout.addWidget(self.titleLabel)

    def addSettingCard(self, card: QWidget):
        card.setParent(self)
        self.vBoxLayout.addWidget(card)


class FeatureGridCard(SimpleCardWidget):
    """网格中的单个特性展示卡片。"""

    def __init__(self, icon, title, desc, parent=None):
        super().__init__(parent=parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(16, 14, 16, 14)
        self.layout.setSpacing(14)

        # 图标容器
        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(28, 28)

        self.textLayout = QVBoxLayout()
        self.textLayout.setContentsMargins(0, 0, 0, 0)
        self.textLayout.setSpacing(4)

        self.titleLabel = StrongBodyLabel(title, self)
        self.descLabel = CaptionLabel(desc, self)
        self.descLabel.setWordWrap(True)
        self.descLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.textLayout.addWidget(self.titleLabel)
        self.textLayout.addWidget(self.descLabel)

        self.layout.addWidget(self.iconWidget, 0, Qt.AlignTop)
        self.layout.addLayout(self.textLayout, 1)

    def set_texts(self, title, desc):
        self.titleLabel.setText(title)
        self.descLabel.setText(desc)


class AboutInterface(QWidget):
    """关于软件与开发者个人介绍详情页 (Fluent Design)。"""

    AUTHOR_URL = "https://github.com/IxinorTyan"
    PROJECT_URL = "https://github.com/IxinorTyan/SuzuEmojy"
    RELEASES_URL = "https://github.com/IxinorTyan/SuzuEmojy/releases"
    QQ_GROUP_NUM = "834586488"

    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AboutInterface")

        self._init_ui()
        self._load_avatar()
        i18n_engine.language_changed.connect(self.update_texts)

    def _init_ui(self):
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(0, 0, 0, 0)
        self.mainLayout.setSpacing(0)

        # ----------------------------------------------------
        # 1. 顶部固定导航栏
        # ----------------------------------------------------
        self.topBar = QWidget(self)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(36, 10, 36, 12)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回设置"))
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel(t("关于软件"), self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        self.mainLayout.addWidget(self.topBar)

        # ----------------------------------------------------
        # 2. 滚动内容区
        # ----------------------------------------------------
        self.scrollArea = ScrollArea(self)
        self.scrollWidget = QWidget()
        self.scrollLayout = QVBoxLayout(self.scrollWidget)

        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.enableTransparentBackground()
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.scrollWidget.setStyleSheet("QWidget { background-color: transparent; }")

        self.scrollLayout.setSpacing(24)
        self.scrollLayout.setContentsMargins(36, 0, 36, 32)
        self.scrollLayout.setAlignment(Qt.AlignTop)

        # ==================== 卡片 1: 软件品牌横幅 (Hero Card) ====================
        self.heroCard = CardWidget(self.scrollWidget)
        self.heroLayout = QHBoxLayout(self.heroCard)
        self.heroLayout.setContentsMargins(24, 22, 24, 22)
        self.heroLayout.setSpacing(20)

        # 软件图标容器
        self.logoLabel = QLabel(self.heroCard)
        self.logoLabel.setFixedSize(64, 64)
        self.logoLabel.setAlignment(Qt.AlignCenter)
        self._setup_app_logo()

        # 软件文本信息
        self.heroTextLayout = QVBoxLayout()
        self.heroTextLayout.setContentsMargins(0, 0, 0, 0)
        self.heroTextLayout.setSpacing(6)

        self.appNameLabel = TitleLabel("SuzuEmojy", self.heroCard)
        self.appSloganLabel = BodyLabel(t("轻量、快速、随心所欲的 Windows 本地表情包管理利器"), self.heroCard)

        # 胶囊徽章栏
        self.badgesLayout = QHBoxLayout()
        self.badgesLayout.setContentsMargins(0, 0, 0, 0)
        self.badgesLayout.setSpacing(8)

        self.badgeVersion = PillBadge("v1.2.0", is_accent=True, parent=self.heroCard)
        self.badgeLicense = PillBadge("GPL-3.0 License", parent=self.heroCard)
        self.badgePlatform = PillBadge("Windows 10 / 11", parent=self.heroCard)
        self.badgeTech = PillBadge("PySide6 · Fluent UI", parent=self.heroCard)

        self.badgesLayout.addWidget(self.badgeVersion)
        self.badgesLayout.addWidget(self.badgeLicense)
        self.badgesLayout.addWidget(self.badgePlatform)
        self.badgesLayout.addWidget(self.badgeTech)
        self.badgesLayout.addStretch()

        self.heroTextLayout.addWidget(self.appNameLabel)
        self.heroTextLayout.addWidget(self.appSloganLabel)
        self.heroTextLayout.addSpacing(2)
        self.heroTextLayout.addLayout(self.badgesLayout)

        # 快速入口按钮
        self.heroActionsLayout = QVBoxLayout()
        self.heroActionsLayout.setContentsMargins(0, 0, 0, 0)
        self.heroActionsLayout.setSpacing(10)

        self.btnRepo = PrimaryPushButton(FIF.GITHUB, t("项目主页"), self.heroCard)
        self.btnRepo.setFixedWidth(130)
        self.btnRepo.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.PROJECT_URL)))

        self.btnReleases = PushButton(FIF.CLOUD_DOWNLOAD, t("发布与更新"), self.heroCard)
        self.btnReleases.setFixedWidth(130)
        self.btnReleases.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.RELEASES_URL)))

        self.heroActionsLayout.addWidget(self.btnRepo)
        self.heroActionsLayout.addWidget(self.btnReleases)

        self.heroLayout.addWidget(self.logoLabel, 0, Qt.AlignVCenter)
        self.heroLayout.addLayout(self.heroTextLayout, 1)
        self.heroLayout.addLayout(self.heroActionsLayout, 0)

        self.scrollLayout.addWidget(self.heroCard)

        # ==================== 卡片 2: 开发者名片 (个人介绍) ====================
        self.authorGroup = SettingCardGroup(t("关于作者与个人介绍"), self.scrollWidget)

        self.authorCard = CardWidget(self.authorGroup)
        self.authorCardLayout = QVBoxLayout(self.authorCard)
        self.authorCardLayout.setContentsMargins(24, 20, 24, 20)
        self.authorCardLayout.setSpacing(16)

        # 头像 + 昵称 + 身份
        self.authorHeaderLayout = QHBoxLayout()
        self.authorHeaderLayout.setContentsMargins(0, 0, 0, 0)
        self.authorHeaderLayout.setSpacing(18)

        self.avatarWidget = CircularAvatarWidget(size=68, parent=self.authorCard)

        self.authorInfoLayout = QVBoxLayout()
        self.authorInfoLayout.setContentsMargins(0, 0, 0, 0)
        self.authorInfoLayout.setSpacing(4)

        self.authorNameRow = QHBoxLayout()
        self.authorNameRow.setContentsMargins(0, 0, 0, 0)
        self.authorNameRow.setSpacing(10)

        self.authorNameLabel = SubtitleLabel("IxinorTyan", self.authorCard)
        self.authorHandleLabel = CaptionLabel("@IxinorTyan", self.authorCard)
        self.badgeRole = PillBadge(t("独立开发者 · Creator"), is_accent=True, parent=self.authorCard)

        self.authorNameRow.addWidget(self.authorNameLabel)
        self.authorNameRow.addWidget(self.authorHandleLabel)
        self.authorNameRow.addWidget(self.badgeRole)
        self.authorNameRow.addStretch()

        self.authorBioLabel = BodyLabel(
            t("常年多账号切换导致表情包互不相通，四处翻找极为繁琐，于是从零写下了 SuzuEmojy。\n"
              "致力于打造极致顺手、轻量快速的本地斗图与表情管理体验。希望这款小工具能陪伴你的每一次日常聊天与斗图！✨"),
            self.authorCard
        )
        self.authorBioLabel.setWordWrap(True)
        self.authorBioLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.authorInfoLayout.addLayout(self.authorNameRow)
        self.authorInfoLayout.addSpacing(2)
        self.authorInfoLayout.addWidget(self.authorBioLabel)

        self.authorHeaderLayout.addWidget(self.avatarWidget, 0, Qt.AlignTop)
        self.authorHeaderLayout.addLayout(self.authorInfoLayout, 1)

        # 社交入口与群聊互动条
        self.authorFooterLayout = QHBoxLayout()
        self.authorFooterLayout.setContentsMargins(0, 4, 0, 0)
        self.authorFooterLayout.setSpacing(12)

        self.btnAuthorGithub = PushButton(FIF.PEOPLE, t("作者主页"), self.authorCard)
        self.btnAuthorGithub.setFixedWidth(120)
        self.btnAuthorGithub.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.AUTHOR_URL)))

        self.btnCopyQQ = PushButton(FIF.CHAT, f"{t('QQ交流群')}: {self.QQ_GROUP_NUM}", self.authorCard)
        self.btnCopyQQ.setToolTip(t("点击复制 QQ 群号"))
        self.btnCopyQQ.clicked.connect(self._copy_qq_group)

        self.btnCopyAction = PushButton(FIF.COPY, t("复制群号"), self.authorCard)
        self.btnCopyAction.setFixedWidth(100)
        self.btnCopyAction.clicked.connect(self._copy_qq_group)

        self.authorFooterLayout.addWidget(self.btnAuthorGithub)
        self.authorFooterLayout.addWidget(self.btnCopyQQ)
        self.authorFooterLayout.addWidget(self.btnCopyAction)
        self.authorFooterLayout.addStretch()

        self.authorCardLayout.addLayout(self.authorHeaderLayout)
        self.authorCardLayout.addLayout(self.authorFooterLayout)

        self.authorGroup.addSettingCard(self.authorCard)
        self.scrollLayout.addWidget(self.authorGroup)

        # ==================== 卡片 3: 核心功能矩阵 (Feature Matrix) ====================
        self.featureGroup = SettingCardGroup(t("核心功能与特性"), self.scrollWidget)

        self.featureGridWidget = QWidget(self.featureGroup)
        self.featureGridLayout = QGridLayout(self.featureGridWidget)
        self.featureGridLayout.setContentsMargins(0, 0, 0, 0)
        self.featureGridLayout.setSpacing(12)

        # 6 项核心功能
        self.feat1 = FeatureGridCard(
            FIF.COMMAND_PROMPT,
            t("全局瞬时呼出"),
            t("全局快捷键（Ctrl+Shift+E / Alt+2）秒级唤出面板，选中即发并自动回切聊天窗口。"),
            self.featureGridWidget
        )
        self.feat2 = FeatureGridCard(
            FIF.SEARCH,
            t("瞬时拼音检索"),
            t("支持标签关键词与拼音首字母极速模糊过滤，数千张表情包毫秒级精准命中。"),
            self.featureGridWidget
        )
        self.feat3 = FeatureGridCard(
            FIF.FOLDER,
            t("智能归类整理"),
            t("分类文件夹拖拽一键映射入库、支持自由拖拽排序、批量跨分类迁移与自动去重。"),
            self.featureGridWidget
        )
        self.feat4 = FeatureGridCard(
            FIF.CHAT,
            t("QQ 聊天表情提取"),
            t("深度扫描提取本地 QQNT 缓存数据库，批量一键收纳历史接收表情与个人表情。"),
            self.featureGridWidget
        )
        self.feat5 = FeatureGridCard(
            FIF.CLOUD_DOWNLOAD,
            t("TG 贴纸快速解析"),
            t("批量解析并下载 Telegram 贴纸包与表情包，全自动格式转码与去重入库。"),
            self.featureGridWidget
        )
        self.feat6 = FeatureGridCard(
            FIF.ZOOM_IN,
            t("高清悬停与防文件化"),
            t("鼠标悬停高清浮动大图预览；复制发送静态图时智能重编码，防止被聊天软件转为文件。"),
            self.featureGridWidget
        )

        self.featureGridLayout.addWidget(self.feat1, 0, 0)
        self.featureGridLayout.addWidget(self.feat2, 0, 1)
        self.featureGridLayout.addWidget(self.feat3, 1, 0)
        self.featureGridLayout.addWidget(self.feat4, 1, 1)
        self.featureGridLayout.addWidget(self.feat5, 2, 0)
        self.featureGridLayout.addWidget(self.feat6, 2, 1)

        self.featureGroup.addSettingCard(self.featureGridWidget)
        self.scrollLayout.addWidget(self.featureGroup)

        # ==================== 卡片 4: 关于吉祥物 Suzu ====================
        self.mascotGroup = SettingCardGroup(t("关于默认表情包"), self.scrollWidget)
        self.mascotCard = CardWidget(self.mascotGroup)
        self.mascotLayout = QHBoxLayout(self.mascotCard)
        self.mascotLayout.setContentsMargins(20, 16, 20, 16)
        self.mascotLayout.setSpacing(16)

        self.mascotIcon = IconWidget(FIF.HEART, self.mascotCard)
        self.mascotIcon.setFixedSize(28, 28)

        self.mascotTextLayout = QVBoxLayout()
        self.mascotTextLayout.setContentsMargins(0, 0, 0, 0)
        self.mascotTextLayout.setSpacing(4)

        self.mascotTitle = StrongBodyLabel(t("关于内置吉祥物 · 🥟 Suzu"), self.mascotCard)
        self.mascotDesc = BodyLabel(
            t("软件初次安装时默认内置了一套 Suzu 表情包，方便直接上手体验。"
              "如果你更喜欢自己的表情收藏，完全可以自由删除或替换它们（当然如果能喜欢她就太好啦 QAQ）~"),
            self.mascotCard
        )
        self.mascotDesc.setWordWrap(True)

        self.mascotTextLayout.addWidget(self.mascotTitle)
        self.mascotTextLayout.addWidget(self.mascotDesc)

        self.mascotLayout.addWidget(self.mascotIcon, 0, Qt.AlignTop)
        self.mascotLayout.addLayout(self.mascotTextLayout, 1)

        self.mascotGroup.addSettingCard(self.mascotCard)
        self.scrollLayout.addWidget(self.mascotGroup)

        # ==================== 卡片 5: 致谢与鸣谢 (Acknowledgements) ====================
        self.creditsGroup = SettingCardGroup(t("致谢与鸣谢"), self.scrollWidget)
        self.creditsCard = CardWidget(self.creditsGroup)
        self.creditsLayout = QVBoxLayout(self.creditsCard)
        self.creditsLayout.setContentsMargins(20, 16, 20, 16)
        self.creditsLayout.setSpacing(12)

        self.creditsDesc = CaptionLabel(
            t("本项目在开发过程中借鉴与参考了以下开源先驱的技术实现与思路，衷心感谢各位原作者的卓越贡献："),
            self.creditsCard
        )
        self.creditsDesc.setWordWrap(True)
        self.creditsLayout.addWidget(self.creditsDesc)

        # 鸣谢列表项
        self.creditsListLayout = QVBoxLayout()
        self.creditsListLayout.setSpacing(8)

        credits_data = [
            ("PyQt-Fluent-Widgets", "zhiyiYo", "https://github.com/zhiyiYo/PyQt-Fluent-Widgets", t("提供优雅现代的 Windows 11 Fluent Design 风格组件库与设计哲学")),
            ("QQFavoriteExtract", "VanillaNahida", "https://github.com/VanillaNahida/QQFavoriteExtract", t("为 QQNT 本地表情扫描、数据解析与提取导出提供了宝贵的实现思路")),
            ("tg_sticker_downloader", "Kiowx", "https://github.com/Kiowx/tg_sticker_downloader", t("为 Telegram 贴纸包解析、下载与格式转码处理提供了技术借鉴")),
        ]

        self.credit_widgets = []
        for name, author, url, note in credits_data:
            row = QWidget(self.creditsCard)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)
            row_layout.setSpacing(12)

            info_col = QVBoxLayout()
            info_col.setContentsMargins(0, 0, 0, 0)
            info_col.setSpacing(2)

            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            project_lbl = StrongBodyLabel(name, row)
            by_lbl = CaptionLabel(f"by {author}", row)
            title_row.addWidget(project_lbl)
            title_row.addWidget(by_lbl)
            title_row.addStretch()

            desc_lbl = CaptionLabel(note, row)
            desc_lbl.setWordWrap(True)

            info_col.addLayout(title_row)
            info_col.addWidget(desc_lbl)

            btn = PushButton(FIF.LINK, t("访问仓库"), row)
            btn.setFixedWidth(100)
            target_url = url
            btn.clicked.connect(lambda checked=False, u=target_url: QDesktopServices.openUrl(QUrl(u)))

            row_layout.addLayout(info_col, 1)
            row_layout.addWidget(btn, 0, Qt.AlignVCenter)

            self.creditsListLayout.addWidget(row)
            self.credit_widgets.append((project_lbl, by_lbl, desc_lbl, btn, note))

        self.creditsLayout.addLayout(self.creditsListLayout)
        self.creditsGroup.addSettingCard(self.creditsCard)
        self.scrollLayout.addWidget(self.creditsGroup)

        # ==================== 卡片 6: 开源协议与版权声明 ====================
        self.licenseGroup = SettingCardGroup(t("开源协议与声明"), self.scrollWidget)
        self.licenseCard = CardWidget(self.licenseGroup)
        self.licenseLayout = QVBoxLayout(self.licenseCard)
        self.licenseLayout.setContentsMargins(20, 16, 20, 16)
        self.licenseLayout.setSpacing(8)

        self.licenseTitle = StrongBodyLabel("GNU General Public License v3.0 (GPL-3.0)", self.licenseCard)
        self.licenseDesc = CaptionLabel(
            t("1. 仅供个人学习与备份：本软件提供的表情扫描与贴纸下载等功能，仅用于用户对合法拥有资源的本地备份与整理。\n"
              "2. 遵守平台与法规规范：请在遵守相关法律法规及对应平台服务条款的前提下使用本软件。\n"
              "3. 尊重原创版权：表情包与贴纸资源著作权归原作者所有，请勿用于任何未经授权的商业用途或侵权传播。\n\n"
              "Copyright © 2024-2026 IxinorTyan. All rights reserved."),
            self.licenseCard
        )
        self.licenseDesc.setWordWrap(True)

        self.licenseLayout.addWidget(self.licenseTitle)
        self.licenseLayout.addWidget(self.licenseDesc)

        self.licenseGroup.addSettingCard(self.licenseCard)
        self.scrollLayout.addWidget(self.licenseGroup)

        self.mainLayout.addWidget(self.scrollArea)
        disable_wheel_scroll_adjustment(self)

    def _setup_app_logo(self):
        """加载本地程序图标到品牌横幅中"""
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_path = os.path.join(base_dir, "ico.ico")

        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                self.logoLabel.setPixmap(
                    pixmap.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return

        # 备用图标
        self.logoLabel.setText("🥟")
        self.logoLabel.setStyleSheet("font-size: 40px;")

    def _load_avatar(self):
        """启动后台线程拉取 GitHub 头像"""
        self.avatar_thread = AvatarLoader(parent=self)
        self.avatar_thread.avatar_loaded.connect(self.avatarWidget.set_pixmap)
        self.avatar_thread.start()

    def _copy_qq_group(self):
        """一键复制 QQ 群号到剪贴板并弹出 Fluent 提示条"""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.QQ_GROUP_NUM)
        InfoBar.success(
            title=t("复制成功"),
            content=t("QQ 交流群号 (834586488) 已复制到剪贴板，欢迎加入！"),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self
        )

    def update_texts(self, lang=None):
        """语言切换时动态刷新所有界面文本与徽章。"""
        self.btnBack.setToolTip(t("返回设置"))
        self.titleLabel.setText(t("关于软件"))

        # Hero
        self.appSloganLabel.setText(t("轻量、快速、随心所欲的 Windows 本地表情包管理利器"))
        self.btnRepo.setText(t("项目主页"))
        self.btnReleases.setText(t("发布与更新"))

        # 徽章
        self.badgeVersion.update_style()
        self.badgeLicense.update_style()
        self.badgePlatform.update_style()
        self.badgeTech.update_style()
        self.badgeRole.setText(t("独立开发者 · Creator"))
        self.badgeRole.update_style()

        # 开发者名片
        self.authorGroup.titleLabel.setText(t("关于作者与个人介绍"))
        self.authorBioLabel.setText(
            t("常年多账号切换导致表情包互不相通，四处翻找极为繁琐，于是从零写下了 SuzuEmojy。\n"
              "致力于打造极致顺手、轻量快速的本地斗图与表情管理体验。希望这款小工具能陪伴你的每一次日常聊天与斗图！✨")
        )
        self.btnAuthorGithub.setText(t("作者主页"))
        self.btnCopyQQ.setText(f"{t('QQ交流群')}: {self.QQ_GROUP_NUM}")
        self.btnCopyAction.setText(t("复制群号"))

        # 核心功能
        self.featureGroup.titleLabel.setText(t("核心功能与特性"))
        self.feat1.set_texts(
            t("全局瞬时呼出"),
            t("全局快捷键（Ctrl+Shift+E / Alt+2）秒级唤出面板，选中即发并自动回切聊天窗口。")
        )
        self.feat2.set_texts(
            t("瞬时拼音检索"),
            t("支持标签关键词与拼音首字母极速模糊过滤，数千张表情包毫秒级精准命中。")
        )
        self.feat3.set_texts(
            t("智能归类整理"),
            t("分类文件夹拖拽一键映射入库、支持自由拖拽排序、批量跨分类迁移与自动去重。")
        )
        self.feat4.set_texts(
            t("QQ 聊天表情提取"),
            t("深度扫描提取本地 QQNT 缓存数据库，批量一键收纳历史接收表情与个人表情。")
        )
        self.feat5.set_texts(
            t("TG 贴纸快速解析"),
            t("批量解析并下载 Telegram 贴纸包与表情包，全自动格式转码与去重入库。")
        )
        self.feat6.set_texts(
            t("高清悬停与防文件化"),
            t("鼠标悬停高清浮动大图预览；复制发送静态图时智能重编码，防止被聊天软件转为文件。")
        )

        # 吉祥物
        self.mascotGroup.titleLabel.setText(t("关于默认表情包"))
        self.mascotTitle.setText(t("关于内置吉祥物 · 🥟 Suzu"))
        self.mascotDesc.setText(
            t("软件初次安装时默认内置了一套 Suzu 表情包，方便直接上手体验。"
              "如果你更喜欢自己的表情收藏，完全可以自由删除或替换它们（当然如果能喜欢她就太好啦 QAQ）~")
        )

        # 致谢
        self.creditsGroup.titleLabel.setText(t("致谢与鸣谢"))
        self.creditsDesc.setText(
            t("本项目在开发过程中借鉴与参考了以下开源先驱的技术实现与思路，衷心感谢各位原作者的卓越贡献：")
        )
        for project_lbl, by_lbl, desc_lbl, btn, note in self.credit_widgets:
            desc_lbl.setText(t(note))
            btn.setText(t("访问仓库"))

        # 开源协议与版权
        self.licenseGroup.titleLabel.setText(t("开源协议与声明"))
        self.licenseDesc.setText(
            t("1. 仅供个人学习与备份：本软件提供的表情扫描与贴纸下载等功能，仅用于用户对合法拥有资源的本地备份与整理。\n"
              "2. 遵守平台与法规规范：请在遵守相关法律法规及对应平台服务条款的前提下使用本软件。\n"
              "3. 尊重原创版权：表情包与贴纸资源著作权归原作者所有，请勿用于任何未经授权的商业用途或侵权传播。\n\n"
              "Copyright © 2024-2026 IxinorTyan. All rights reserved.")
        )
