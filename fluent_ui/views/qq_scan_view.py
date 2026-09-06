from PySide6.QtCore import Qt, QSize, Signal, QCoreApplication, QRect
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter, QFrame,
    QFileDialog, QMessageBox, QLabel, QListWidget, QSizePolicy
)
from PySide6.QtGui import (
    QIcon, QFont, QPixmap, QPainter, QColor, QMovie, QImageReader
)
from qfluentwidgets import (
    LineEdit, PushButton, PrimaryPushButton, ComboBox, ProgressBar,
    TextEdit, FluentIcon as FIF, TransparentToolButton,
    TitleLabel, BodyLabel, SubtitleLabel, ScrollArea, CardWidget, StrongBodyLabel
)

import os
import shutil
import subprocess
from pathlib import Path

from services.qq_extractor import QQExtractor
from services.i18n import t, i18n_engine


def _tf(text, **kwargs):
    """翻译并格式化动态文本。"""
    return t(text).format(**kwargs)


class QQScanInterface(QWidget):
    """QQNT表情包批量提取工具界面 (View)"""
    back_requested = Signal()
    DETAIL_PREVIEW_HIDE_WIDTH = 1000
    CONTENT_STACK_WIDTH = 760
    TOP_BAR_HEIGHT = 40

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("QQScanInterface")
        
        self.savePath = None
        self.default_ini_path = r'C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini'
        self.userdata_save_path_cache = None
        
        # 懒加载相关的状态变量
        self.emoji_file_paths = []     # 存放当前分类下所有待加载表情包文件的完整路径
        self.loaded_emoji_count = 0    # 已渲染到列表中的表情包数量
        self.batch_size = 100          # 每次懒加载的表情包数量
        self.is_loading = False        # 是否正在加载，防止重复触发
        self.active_movies = {}        # 存放当前正在播放动图的项目 {item: (label, movie)}
        self.detail_movie = None

        self._init_ui()
        i18n_engine.language_changed.connect(self.update_texts)

    def _init_ui(self):
        # 主布局：垂直布局，顶栏 + 内容区
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 10, 36, 12)
        self.mainLayout.setSpacing(12)

        # 顶部返回工具栏
        self.topBar = QWidget(self)
        self.topBar.setFixedHeight(self.TOP_BAR_HEIGHT)
        self.topBar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回主面板"))
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel(t("扫描QQ文件"), self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        self.mainLayout.addWidget(self.topBar)

        # 内容分割器 (左控制面板 + 右预览面板)
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        
        # ====== 左侧控制面板 ======
        self.leftScrollArea = ScrollArea(self.splitter)
        self.leftScrollArea.setWidgetResizable(True)
        self.leftScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.leftScrollArea.enableTransparentBackground()
        self.leftScrollArea.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.leftScrollArea.setMinimumWidth(330)

        self.leftWidget = QWidget()
        self.leftWidget.setStyleSheet("QWidget { background-color: transparent; }")
        self.leftWidget.setMinimumWidth(320)
        self.leftLayout = QVBoxLayout(self.leftWidget)
        self.leftLayout.setContentsMargins(0, 0, 8, 0)
        self.leftLayout.setSpacing(12)

        # 1. 路径与配置卡片
        self.configCard = CardWidget(self.leftWidget)
        config_layout = QVBoxLayout(self.configCard)
        config_layout.setContentsMargins(14, 12, 14, 12)
        config_layout.setSpacing(10)

        self.configTitle = StrongBodyLabel(t("QQ 数据配置"), self.configCard)
        config_layout.addWidget(self.configTitle)

        self.formLayout = QFormLayout()
        self.formLayout.setSpacing(8)
        self.formLayout.setLabelAlignment(Qt.AlignLeft)

        # 数据读取路径选择
        read_path_layout = QHBoxLayout()
        read_path_layout.setSpacing(6)
        self.readPathEdit = LineEdit(self.configCard)
        self.readPathEdit.setReadOnly(True)
        self.readPathEdit.setPlaceholderText(t("自动定位中，或手动选择..."))
        self.selectReadDirButton = PushButton(t("定位目录"), self.configCard)
        self.selectReadDirButton.setFixedWidth(80)
        self.selectReadDirButton.clicked.connect(self.selectReadPath)
        read_path_layout.addWidget(self.readPathEdit)
        read_path_layout.addWidget(self.selectReadDirButton)
        
        read_path_label = BodyLabel(t('数据路径:'), self.configCard)
        self.readPathLabel = read_path_label
        self.formLayout.addRow(read_path_label, read_path_layout)

        # 保存路径选择
        save_path_layout = QHBoxLayout()
        save_path_layout.setSpacing(6)
        self.savePathEdit = LineEdit(self.configCard)
        self.savePathEdit.setPlaceholderText(t("请选择表情包保存路径..."))
        self.selectDirButton = PushButton(t('浏览...'), self.configCard)
        self.selectDirButton.setFixedWidth(80)
        self.selectDirButton.clicked.connect(self.selectSavePath)
        save_path_layout.addWidget(self.savePathEdit)
        save_path_layout.addWidget(self.selectDirButton)
        
        save_path_label = BodyLabel(t('保存路径:'), self.configCard)
        self.savePathLabel = save_path_label
        self.formLayout.addRow(save_path_label, save_path_layout)

        # 选择用户下拉框
        user_layout = QHBoxLayout()
        user_layout.setSpacing(6)
        self.userComboBox = ComboBox(self.configCard)
        self.userComboBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.helpButton = TransparentToolButton(FIF.HELP, self.configCard)
        self.helpButton.setFixedSize(28, 28)
        self.helpButton.setToolTip(t("使用帮助"))
        self.helpButton.clicked.connect(self.showHelp)
        user_layout.addWidget(self.userComboBox, 1)
        user_layout.addWidget(self.helpButton)
        
        user_label = BodyLabel(t('选择账号:'), self.configCard)
        self.userLabel = user_label
        self.formLayout.addRow(user_label, user_layout)

        # 选择分类下拉框
        self.emojiFolderComboBox = ComboBox(self.configCard)
        self.emojiFolderComboBox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        emoji_folder_label = BodyLabel(t('选择分类:'), self.configCard)
        self.emojiFolderLabel = emoji_folder_label
        self.formLayout.addRow(emoji_folder_label, self.emojiFolderComboBox)

        config_layout.addLayout(self.formLayout)
        self.leftLayout.addWidget(self.configCard)

        # 2. 操作卡片
        self.actionsCard = CardWidget(self.leftWidget)
        actions_layout = QVBoxLayout(self.actionsCard)
        actions_layout.setContentsMargins(14, 12, 14, 12)
        actions_layout.setSpacing(10)

        self.actionsTitle = StrongBodyLabel(t("操作与提取"), self.actionsCard)
        actions_layout.addWidget(self.actionsTitle)

        # 核心扫描按钮
        self.scanButton = PrimaryPushButton(FIF.SEARCH, t('扫描表情包预览'), self.actionsCard)
        self.scanButton.setFixedHeight(34)
        self.scanButton.clicked.connect(self.scanEmojis)
        actions_layout.addWidget(self.scanButton)

        # 导出操作 (双列并排)
        export_btn_layout = QHBoxLayout()
        export_btn_layout.setSpacing(8)
        self.exportSelectedButton = PushButton(FIF.DOWNLOAD, t('导出选中'), self.actionsCard)
        self.exportSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportSelectedButton.clicked.connect(self.exportSelected)
        self.exportAllButton = PushButton(FIF.FOLDER, t('导出全部'), self.actionsCard)
        self.exportAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exportAllButton.clicked.connect(self.exportAll)
        export_btn_layout.addWidget(self.exportSelectedButton, 1)
        export_btn_layout.addWidget(self.exportAllButton, 1)
        actions_layout.addLayout(export_btn_layout)

        # 导入操作 (双列并排)
        import_btn_layout = QHBoxLayout()
        import_btn_layout.setSpacing(8)
        self.importSelectedButton = PushButton(FIF.SAVE, t('入库选中'), self.actionsCard)
        self.importSelectedButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.importSelectedButton.clicked.connect(self.importSelected)
        self.importAllButton = PushButton(FIF.APPLICATION, t('入库全部'), self.actionsCard)
        self.importAllButton.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.importAllButton.clicked.connect(self.importAll)
        import_btn_layout.addWidget(self.importSelectedButton, 1)
        import_btn_layout.addWidget(self.importAllButton, 1)
        actions_layout.addLayout(import_btn_layout)

        self.leftLayout.addWidget(self.actionsCard)

        # 3. 进度条与状态
        self.progressBar = ProgressBar(self.leftWidget)
        self.leftLayout.addWidget(self.progressBar)

        # 4. 日志输出框
        self.logTextEdit = TextEdit(self.leftWidget)
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMinimumHeight(110)
        self.logTextEdit.setStyleSheet("""
            TextEdit {
                font-family: 'Segoe UI', 'Microsoft YaHei', Consolas;
                font-size: 12px;
                border-radius: 6px;
            }
        """)
        self.leftLayout.addWidget(self.logTextEdit)

        # 状态及致谢声明
        self.statusLabel = BodyLabel('', self.leftWidget)
        self.statusLabel.setStyleSheet("color: #666666; font-size: 11px;")
        self.leftLayout.addWidget(self.statusLabel)

        self.thanksLabel = BodyLabel(self.leftWidget)
        self.thanksLabel.setText(t('致谢：基于 <a href="https://github.com/VanillaNahida" style="color: #0078d4; text-decoration: underline;">VanillaNahida</a> 的项目二次开发'))
        self.thanksLabel.setOpenExternalLinks(True)
        self.thanksLabel.setStyleSheet("color: #888888; font-size: 11px;")
        self.leftLayout.addWidget(self.thanksLabel)

        self.leftScrollArea.setWidget(self.leftWidget)

        # ====== 右侧表情包预览区域 ======
        self.rightWidget = QWidget(self.splitter)
        self.rightWidget.setMinimumWidth(320)
        self.rightLayout = QVBoxLayout(self.rightWidget)
        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.rightLayout.setSpacing(10)

        preview_header_layout = QHBoxLayout()
        self.previewLabel = SubtitleLabel(t('表情包预览区'), self.rightWidget)
        preview_header_layout.addWidget(self.previewLabel)
        preview_header_layout.addStretch()
        
        self.selectAllButton = PushButton(t('全选已加载'), self.rightWidget)
        self.selectAllButton.clicked.connect(self.selectAllLoaded)
        preview_header_layout.addWidget(self.selectAllButton)
        
        self.clearSelectionButton = PushButton(t('清空选择'), self.rightWidget)
        self.clearSelectionButton.clicked.connect(self.clearSelection)
        preview_header_layout.addWidget(self.clearSelectionButton)
        
        self.rightLayout.addLayout(preview_header_layout)

        self.previewListWidget = QListWidget(self.rightWidget)
        self.previewListWidget.setViewMode(QListWidget.IconMode)
        self.previewListWidget.setResizeMode(QListWidget.Adjust)
        self.previewListWidget.setIconSize(QSize(100, 100))
        self.previewListWidget.setGridSize(QSize(110, 110))
        self.previewListWidget.setSelectionMode(QListWidget.ExtendedSelection)
        self.previewListWidget.setDragEnabled(False)
        self.previewListWidget.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: 1px solid rgba(0, 0, 0, 15);
                border-radius: 8px;
            }
            QListWidget::item {
                width: 100px;
                height: 100px;
                border: 2px solid transparent;
                border-radius: 6px;
                margin: 4px;
                padding: 0px;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 0, 0, 10);
            }
            QListWidget::item:selected {
                background-color: rgba(0, 120, 212, 30);
                border: 2px solid #0078d4;
            }
        """)
        self.rightLayout.addWidget(self.previewListWidget)

        # ====== 最右侧单个表情大图预览区域 ======
        self.detailWidget = QWidget(self)
        self.detailLayout = QVBoxLayout(self.detailWidget)
        self.detailLayout.setContentsMargins(10, 0, 0, 0)
        self.detailLayout.setSpacing(10)
        self.detailWidget.setFixedWidth(280)

        self.detailTitle = SubtitleLabel(t('表情详细预览'), self.detailWidget)
        self.detailLayout.addWidget(self.detailTitle)

        self.detailPreviewLabel = QLabel(self.detailWidget)
        self.detailPreviewLabel.setAlignment(Qt.AlignCenter)
        self.detailPreviewLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.detailPreviewLabel.setFixedSize(250, 250)
        self.detailPreviewLabel.setStyleSheet("background-color: rgba(0, 0, 0, 5); border: 1px solid rgba(0, 0, 0, 15); border-radius: 5px;")
        self.detailLayout.addWidget(self.detailPreviewLabel, alignment=Qt.AlignCenter)

        self.detailInfoLabel = BodyLabel(t("未选中表情"), self.detailWidget)
        self.detailInfoLabel.setWordWrap(True)
        self.detailInfoLabel.setFixedWidth(250)
        self.detailInfoLabel.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detailLayout.addWidget(self.detailInfoLabel)
        
        self.detailLayout.addStretch()

        # 分割器大小配置
        self.splitter.addWidget(self.leftScrollArea)
        self.splitter.addWidget(self.rightWidget)
        self.splitter.setSizes([360, 740])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        # 合并到主水平内容布局
        self.contentLayout = QHBoxLayout()
        self.contentLayout.addWidget(self.splitter, stretch=1)
        self.contentLayout.addWidget(self.detailWidget)

        self.mainLayout.addLayout(self.contentLayout, 1)
        self._update_responsive_layout()

        # 槽函数绑定
        self.userComboBox.currentIndexChanged.connect(self.onUserChanged)
        self.previewListWidget.verticalScrollBar().valueChanged.connect(self.onScrollBarMoved)
        self.previewListWidget.itemSelectionChanged.connect(self.onItemSelectionChanged)

        # 初始化定位
        self.log(t("💬 QQNT表情包批量提取工具启动成功"))
        self.log(t("💡建议在使用前提前打开要提取表情包的账户，随便选择一个聊天窗口，将表情全部加载出来，这样提取的表情包更齐全。"))
        self.populateUserComboBox()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    def _update_responsive_layout(self):
        stacked = self.width() < self.CONTENT_STACK_WIDTH
        orientation = Qt.Vertical if stacked else Qt.Horizontal

        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([420, 520] if stacked else [360, 740])

        self._update_detail_preview_visibility()

    def _update_detail_preview_visibility(self):
        should_show = self.width() >= self.DETAIL_PREVIEW_HIDE_WIDTH
        if self.detailWidget.isVisible() == should_show:
            return

        self.detailWidget.setVisible(should_show)

        if not should_show and self.detail_movie:
            self.detail_movie.stop()
            self.detail_movie = None
            self.detailPreviewLabel.clear()
        elif should_show:
            self.onItemSelectionChanged()

    def _category_display_name(self, folder_name):
        mapping = {
            "personal_emoji": t("个人表情 (personal_emoji)"),
            "emoji-recv": t("接收到表情[谨慎加载,内含巨量表情] (emoji-recv)"),
            "marketface": t("商店表情 (marketface)"),
            "BaseEmojiSyastems": t("系统表情[已支持APNG动图转GIF导出] (BaseEmojiSyastems)"),
            "emoji-related": t("候选表情[打字时系统推荐] (emoji-related)")
        }
        return mapping.get(folder_name, _tf("{folder} (其他分类)", folder=folder_name))

    def update_texts(self, lang):
        """刷新 QQ 扫描页面的多语言文本。"""
        self.btnBack.setToolTip(t("返回主面板"))
        self.titleLabel.setText(t("扫描QQ文件"))
        self.configTitle.setText(t("QQ 数据配置"))
        self.readPathEdit.setPlaceholderText(t("自动定位中，或手动选择..."))
        self.selectReadDirButton.setText(t("定位目录"))
        self.readPathLabel.setText(t("数据路径:"))
        self.savePathEdit.setPlaceholderText(t("请选择表情包保存路径..."))
        self.selectDirButton.setText(t("浏览..."))
        self.savePathLabel.setText(t("保存路径:"))
        self.helpButton.setToolTip(t("使用帮助"))
        self.userLabel.setText(t("选择账号:"))
        self.emojiFolderLabel.setText(t("选择分类:"))
        self.actionsTitle.setText(t("操作与提取"))
        self.scanButton.setText(t("扫描表情包预览"))
        self.exportSelectedButton.setText(t("导出选中"))
        self.exportAllButton.setText(t("导出全部"))
        self.importSelectedButton.setText(t("入库选中"))
        self.importAllButton.setText(t("入库全部"))
        self.previewLabel.setText(t("表情包预览区"))
        self.selectAllButton.setText(t("全选已加载"))
        self.clearSelectionButton.setText(t("清空选择"))
        self.detailTitle.setText(t("表情详细预览"))
        self.detailInfoLabel.setText(t("未选中表情"))
        self.thanksLabel.setText(t(
            '致谢：基于 <a href="https://github.com/VanillaNahida" '
            'style="color: #0078d4; text-decoration: underline;">VanillaNahida</a> 的项目二次开发'
        ))

        current_folder = self.getSelectedFolder()
        if current_folder:
            index = self.emojiFolderComboBox.findData(current_folder)
            if index >= 0:
                self.emojiFolderComboBox.setItemText(
                    index, self._category_display_name(current_folder)
                )

    def set_font(self, widget):
        pass

    def selectSavePath(self):
        directory = QFileDialog.getExistingDirectory(self, t("💬 请选择表情包保存路径"))
        if directory:
            self.savePathEdit.setText(directory)
            self.savePath = directory
            self.log(_tf("✅ 已将保存路径设置为: {path}", path=directory))

    def selectReadPath(self):
        directory = QFileDialog.getExistingDirectory(
            self, 
            t("选择QQ聊天记录所在目录（即包含QQ号数字文件夹的 Tencent Files 目录）")
        )
        if directory:
            self.log(_tf("✅ 已选择数据目录: {path}", path=directory))
            self.userdata_save_path_cache = directory
            self.populateUserComboBox()
        else:
            self.log(t("💬 取消选择数据目录"))

    def get_selected_qq(self):
        data = self.userComboBox.currentData()
        if data:
            return str(data)
        txt = self.userComboBox.currentText()
        if '（' in txt and '）' in txt:
            return txt.split('（')[-1].split('）')[0].strip()
        elif '(' in txt and ')' in txt:
            return txt.split('(')[-1].split(')')[0].strip()
        return txt.strip()

    def getSelectedFolder(self):
        data = self.emojiFolderComboBox.currentData()
        if data:
            return str(data)
        selected_folder_text = self.emojiFolderComboBox.currentText()
        if not selected_folder_text:
            return None
        for key in ("personal_emoji", "emoji-recv", "marketface",
                    "BaseEmojiSyastems", "emoji-related"):
            if selected_folder_text == self._category_display_name(key):
                return key
        suffix = t(" (其他分类)")
        if selected_folder_text.endswith(suffix):
            return selected_folder_text[:-len(suffix)].strip()
        return selected_folder_text

    def populateUserComboBox(self):
        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)

        if userdata_save_path and os.path.exists(userdata_save_path):
            self.readPathEdit.setText(userdata_save_path)
            self.userdata_save_path_cache = userdata_save_path
            
            numeric_subdirs = QQExtractor.get_numeric_subdirectories(userdata_save_path)
            self.userComboBox.clear()
            if numeric_subdirs:
                for subdir in numeric_subdirs:
                    nickname = QQExtractor.get_user_nickname(subdir)
                    if nickname:
                        display_name = f"{nickname}（{subdir}）"
                        self.userComboBox.addItem(display_name, userData=subdir)
                    else:
                        self.userComboBox.addItem(subdir, userData=subdir)
                self.log(_tf("✅ 成功加载了 {count} 个QQ用户文件夹", count=len(numeric_subdirs)))
            else:
                self.log(_tf(
                    "⚠️ 在目录 [{path}] 下未找到任何QQ号数据文件夹（纯数字命名且含有nt_qq）",
                    path=userdata_save_path
                ))
        else:
            self.readPathEdit.setText("")
            self.userComboBox.clear()
            self.log(t("⚠️ 未能自动定位到QQ聊天数据文件夹，请手动点击按钮 [选择数据目录] 指定！"))
        
        self.onUserChanged()

    def sanitize_filename(self, name):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        return name.strip()

    def _is_marketface(self):
        folder = self.getSelectedFolder()
        return bool(folder and folder.lower() == "marketface")

    def _get_marketface_data(self, file_path):
        """读取 marketface 恢复后的 GIF 数据及临时可读路径。"""
        if not self._is_marketface():
            return None, None
        data = QQExtractor.read_marketface_data(file_path)
        if data is None:
            return None, None
        return data, QQExtractor.get_marketface_gif_path(file_path)

    def onUserChanged(self):
        selected_qq = self.get_selected_qq()
        self.emojiFolderComboBox.clear()
        if not selected_qq:
            return

        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
        if not userdata_save_path:
            return

        file_path = Path(os.path.join(userdata_save_path, selected_qq))
        emoji_root = file_path / "nt_qq" / "nt_data" / "Emoji"

        if emoji_root.exists() and emoji_root.is_dir():
            try:
                subdirs = [d for d in os.listdir(emoji_root) if os.path.isdir(emoji_root / d)]
                for subdir in subdirs:
                    display_name = self._category_display_name(subdir)
                    self.emojiFolderComboBox.addItem(display_name, userData=subdir)
            except Exception as e:
                self.log(_tf("⚠️ 读取表情分类出错: {error}", error=e))
        else:
            self.log(_tf("⚠️ 未找到该账户的 Emoji 目录: {path}", path=emoji_root))

    def onScrollBarMoved(self, value):
        scroll_bar = self.previewListWidget.verticalScrollBar()
        max_val = scroll_bar.maximum()
        if max_val > 0 and value > max_val * 0.9:
            if self.loaded_emoji_count < len(self.emoji_file_paths) and not self.is_loading:
                self.loadMoreEmojis()

    def loadMoreEmojis(self):
        if self.is_loading:
            return
        self.is_loading = True
        
        start_idx = self.loaded_emoji_count
        end_idx = min(start_idx + self.batch_size, len(self.emoji_file_paths))
        
        if start_idx >= end_idx:
            self.is_loading = False
            return

        self.log(_tf(
            "💬 正在加载预览图 {start} - {end} ...",
            start=start_idx + 1, end=end_idx
        ))
        self.progressBar.setMaximum(len(self.emoji_file_paths))
        
        batch_paths = self.emoji_file_paths[start_idx:end_idx]
        
        for idx, file_path_str in enumerate(batch_paths):
            current_count = start_idx + idx + 1
            self.progressBar.setValue(current_count)
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            marketface_data = None
            marketface_gif_path = None
            marketface_frames = 0
            if self._is_marketface():
                marketface_info = QQExtractor.get_marketface_info(file_path_str)
                if marketface_info is not None:
                    marketface_data, marketface_frames = marketface_info
                    marketface_gif_path = QQExtractor.get_marketface_gif_path(file_path_str)
                    actual_ext = "gif"

            if actual_ext:
                try:
                    if marketface_data is not None:
                        file_data = marketface_data
                    else:
                        with open(file_path_str, 'rb') as f:
                            file_data = f.read()
                    
                    pixmap = QPixmap()
                    if pixmap.loadFromData(file_data):
                        # 检测是否为动图（GIF 或 APNG）
                        is_animated = False
                        badge_text = "GIF"
                        
                        # 1. marketface 已在内存中恢复为 GIF
                        if marketface_data is not None:
                            # marketface 可能是单帧 GIF；只有多帧时才标记为动图并显示 GIF 角标
                            is_animated = marketface_frames > 1
                            badge_text = "GIF" if is_animated else ""
                        # 2. 检查是否为 APNG
                        elif actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                            is_animated = True
                            badge_text = "APNG"
                        else:
                            # 2. 检查标准动画格式（如 GIF）
                            try:
                                reader = QImageReader(file_path_str)
                                if reader.supportsAnimation():
                                    is_animated = reader.imageCount() > 1
                            except Exception:
                                pass

                        # 创建 100x100 的透明背景画布，保证预览图大小绝对 1:1
                        canvas = QPixmap(100, 100)
                        canvas.fill(Qt.transparent)

                        scaled_pixmap = pixmap.scaled(
                            100, 100, 
                            Qt.KeepAspectRatio, 
                            Qt.SmoothTransformation
                        )

                        # 将缩放后的表情包居中绘制到画布上
                        painter = QPainter(canvas)
                        x = (100 - scaled_pixmap.width()) // 2
                        y = (100 - scaled_pixmap.height()) // 2
                        painter.drawPixmap(x, y, scaled_pixmap)

                        if is_animated:
                            rect = QRect(55, 84, 45, 16)
                            painter.fillRect(rect, QColor(0, 0, 0, 160))
                            painter.setPen(QColor(255, 255, 255))
                            font = QFont("Arial", 8, QFont.Bold)
                            painter.setFont(font)
                            painter.drawText(rect, Qt.AlignCenter, badge_text)
                            
                        painter.end()

                        icon = QIcon(canvas)
                        from PySide6.QtWidgets import QListWidgetItem
                        item = QListWidgetItem(icon, "")
                        item.setData(Qt.UserRole, file_path_str)
                        item.setData(Qt.UserRole + 1, is_animated)
                        item.setData(Qt.UserRole + 2, icon)
                        display_ext = "GIF" if marketface_data is not None else (
                            "APNG" if badge_text == "APNG" else actual_ext.upper()
                        )
                        item.setToolTip(_tf(
                            "格式: {format}\n路径: {path}",
                            format=display_ext,
                            path=os.path.basename(file_path_str)
                        ))
                        self.previewListWidget.addItem(item)
                except Exception:
                    pass
            
            if idx % 10 == 0 or idx == len(batch_paths) - 1:
                QCoreApplication.processEvents()
                
        self.loaded_emoji_count = end_idx
        self.log(_tf(
            "✅ 已加载表情预览：{loaded}/{total}",
            loaded=self.loaded_emoji_count, total=len(self.emoji_file_paths)
        ))
        self.is_loading = False

    def onItemSelectionChanged(self):
        current_item = self.previewListWidget.currentItem()
        
        if hasattr(self, 'detail_movie') and self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None
            
        self.detailPreviewLabel.clear()

        if not self.detailWidget.isVisible():
            return

        if not current_item or not current_item.isSelected():
            self.detailInfoLabel.setText(t("未选中表情"))
            return
            
        file_path_str = current_item.data(Qt.UserRole)
        is_animated = current_item.data(Qt.UserRole + 1)
        
        if not file_path_str or not os.path.exists(file_path_str):
            self.detailInfoLabel.setText(t("文件不存在"))
            return
            
        marketface_data, marketface_gif_path = self._get_marketface_data(file_path_str)
        try:
            file_size_kb = os.path.getsize(file_path_str) / 1024
            actual_ext = QQExtractor.get_actual_extension(file_path_str)
            file_name = os.path.basename(file_path_str)
            
            format_display = actual_ext.upper() if actual_ext else t("未知")
            if marketface_data is not None:
                format_display = "GIF"
            elif actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(file_path_str):
                format_display = t("APNG (动态图片)")

            info_text = _tf(
                "<b>文件名:</b><br/>{name}<br/><br/>"
                "<b>格式:</b> {format}<br/>"
                "<b>大小:</b> {size:.2f} KB<br/><br/>"
                "<b>保存路径:</b><br/>{path}",
                name=file_name, format=format_display, size=file_size_kb,
                path=file_path_str
            )
            self.detailInfoLabel.setText(info_text)
        except Exception as e:
            self.detailInfoLabel.setText(_tf("获取信息失败: {error}", error=e))
            
        try:
            play_path = file_path_str
            if marketface_gif_path:
                play_path = marketface_gif_path
            elif is_animated:
                # 若为 APNG 格式，转换为临时 GIF 播放
                if QQExtractor.is_apng_file(file_path_str):
                    converted_gif = QQExtractor.convert_apng_to_gif(file_path_str)
                    if converted_gif:
                        play_path = converted_gif

            if marketface_gif_path or is_animated:
                self.detail_movie = QMovie(play_path)
                reader = QImageReader(play_path)
                orig_size = reader.size()
                if orig_size.isValid():
                    scaled_size = orig_size.scaled(240, 240, Qt.KeepAspectRatio)
                    self.detail_movie.setScaledSize(scaled_size)
                else:
                    self.detail_movie.setScaledSize(QSize(240, 240))
                
                self.detailPreviewLabel.setMovie(self.detail_movie)
                self.detail_movie.start()
            else:
                pixmap = QPixmap()
                if pixmap.load(file_path_str):
                    scaled_pixmap = pixmap.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.detailPreviewLabel.setPixmap(scaled_pixmap)
                else:
                    self.detailPreviewLabel.setText(t("图片加载失败"))
        except Exception as e:
            self.detailPreviewLabel.setText(_tf("预览失败: {error}", error=e))

    def scanEmojis(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
        if not userdata_save_path:
            self.log(t("❌ 未找到QQ数据路径"))
            return

        file_path = Path(os.path.join(userdata_save_path, selected_qq))
        emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
        
        if not emoji_path.exists():
            self.log(_tf("❌ 未找到该用户的表情分类目录: {path}", path=emoji_path))
            QMessageBox.warning(
                self, t("警告"),
                t("未找到该分类的本地目录，可能是该账号在本地未生成对应分类，或者路径不正确。")
            )
            return

        if hasattr(self, 'detail_movie') and self.detail_movie:
            try:
                self.detail_movie.stop()
            except Exception:
                pass
            self.detail_movie = None
        self.detailPreviewLabel.clear()
        self.detailInfoLabel.setText(t("未选中表情"))

        self.previewListWidget.clear()
        self.emoji_file_paths = []
        self.loaded_emoji_count = 0
        self.log(_tf("💬 开始智能扫描分类 [{folder}] 表情包路径...", folder=selected_folder))

        self.progressBar.setMaximum(100)
        self.progressBar.setValue(30)
        QCoreApplication.processEvents()

        self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)
        total_valid = len(self.emoji_file_paths)
        
        if total_valid == 0:
            self.log(t("❌ 未筛选出任何有效的表情包图片"))
            self.progressBar.setValue(100)
            return

        self.log(_tf("✅ 扫描并筛选完毕，共发现 {count} 个有效表情图片。", count=total_valid))
        self.progressBar.setValue(100)
        self.loadMoreEmojis()

    def copy_files_with_progress(self, file_paths, dst_dir):
        try:
            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir)

            total_files = len(file_paths)
            self.progressBar.setMaximum(total_files)
            self.progressBar.setValue(0)

            copied_count = 0
            for idx, src_file in enumerate(file_paths):
                if not src_file or not os.path.exists(src_file):
                    continue
                
                actual_ext = QQExtractor.get_actual_extension(src_file)
                marketface_data, marketface_gif_path = self._get_marketface_data(src_file)
                filename_no_ext = os.path.splitext(os.path.basename(src_file))[0]
                
                # marketface 原始文件无扩展名且内容经过保护，导出恢复后的 GIF
                if marketface_gif_path:
                    dest_file = os.path.join(dst_dir, f"{filename_no_ext}.gif")
                    shutil.copy2(marketface_gif_path, dest_file)
                    copied_count += 1
                    self.progressBar.setValue(copied_count)
                    self.log(_tf(
                        "导出(marketface恢复GIF) [{done}/{total}]: {source} -> {destination}",
                        done=copied_count, total=total_files,
                        source=os.path.basename(src_file),
                        destination=os.path.basename(dest_file)
                    ))
                # 如果检测到是 APNG 格式的表情，将其转码为通用动图 GIF 导出
                elif actual_ext and actual_ext.lower() == 'png' and QQExtractor.is_apng_file(src_file):
                    dest_file = os.path.join(dst_dir, f"{filename_no_ext}.gif")
                    converted_path = QQExtractor.convert_apng_to_gif(src_file, dest_file)
                    if converted_path:
                        copied_count += 1
                        self.progressBar.setValue(copied_count)
                        self.log(_tf(
                            "导出(APNG转GIF) [{done}/{total}]: {source} -> {destination}",
                            done=copied_count, total=total_files,
                            source=os.path.basename(src_file),
                            destination=os.path.basename(dest_file)
                        ))
                    else:
                        dest_file = os.path.join(dst_dir, f"{filename_no_ext}.png")
                        shutil.copy2(src_file, dest_file)
                        copied_count += 1
                        self.progressBar.setValue(copied_count)
                        self.log(_tf(
                            "导出(回退PNG) [{done}/{total}]: {source} -> {destination}",
                            done=copied_count, total=total_files,
                            source=os.path.basename(src_file),
                            destination=os.path.basename(dest_file)
                        ))
                else:
                    filename = os.path.basename(src_file)
                    if actual_ext:
                        filename_lower = filename.lower()
                        # QQ personal_emoji/Ori 中部分 JPG 原图命名为 *.jpg.gif。
                        # 文件内容虽识别为 JPG，但应保留这个原始文件名，
                        # 避免导出为 *.jpg.gif.jpg。
                        has_original_extension = (
                            filename_lower.endswith(f".{actual_ext}") or
                            filename_lower.endswith(f".{actual_ext}.gif")
                        )
                        if not has_original_extension:
                            dest_file = os.path.join(dst_dir, f"{filename}.{actual_ext}")
                        else:
                            dest_file = os.path.join(dst_dir, filename)
                    else:
                        dest_file = os.path.join(dst_dir, filename)

                    shutil.copy2(src_file, dest_file)
                    copied_count += 1
                    self.progressBar.setValue(copied_count)
                    self.log(_tf(
                        "导出 [{done}/{total}]: {source} -> {destination}",
                        done=copied_count, total=total_files,
                        source=os.path.basename(src_file),
                        destination=os.path.basename(dest_file)
                    ))
                
                if idx % 5 == 0 or idx == total_files - 1:
                    QCoreApplication.processEvents()
            self.log(_tf("✅ 成功导出 {count} 个表情文件！", count=copied_count))
        except Exception as e:
            self.log(_tf("❌ 导出文件时出错: {error}", error=e))

    def selectAllLoaded(self):
        for i in range(self.previewListWidget.count()):
            item = self.previewListWidget.item(i)
            item.setSelected(True)
        self.log(_tf(
            "✅ 已全选当前加载的 {count} 个表情",
            count=self.previewListWidget.count()
        ))

    def clearSelection(self):
        self.previewListWidget.clearSelection()
        self.log(t("✅ 已清空当前的选择"))

    def exportSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if not self.savePath:
            message = t("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            message = t("❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导出。")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("请先在右侧预览区选中表情后再导出！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导出选中"),
            _tf("确定导出当前选中的 {count} 个表情？", count=len(selected_items)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导出操作"))
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的选中表情"
        self.log(_tf("✅ 正在复制选中的表情文件到: {path}", path=output_dir))
        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.copy_files_with_progress(selected_paths, output_dir)
        self.log(t("✅ 完成！正在打开输出文件夹……"))
        try:
            subprocess.Popen(['explorer', os.path.abspath(output_dir)])
            QMessageBox.information(self, t("完成"), t("选中表情提取成功！"))
        except Exception as e:
            self.log(_tf("❌ 无法打开资源管理器: {error}", error=e))

    def exportAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if not self.savePath:
            message = t("❌ 你还没有选择保存路径呢，请先选择保存路径！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                file_path = Path(os.path.join(userdata_save_path, selected_qq))
                emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
                self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            message = t("❌ 该表情分类下未发现任何有效的图片文件，无法导出！")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("该分类下未发现任何有效的表情图片文件！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导出全部"),
            _tf(
                "当前不管界面是否完全加载，将直接导出扫描到的该分类下所有 {count} 个表情？",
                count=len(self.emoji_file_paths)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导出操作"))
            return

        display_name = QQExtractor.get_display_name(selected_qq)
        safe_name = self.sanitize_filename(display_name)
        output_dir = f"{self.savePath}/{safe_name}_{selected_folder}_提取的全部表情"
        self.log(_tf("✅ 正在复制所有表情文件到: {path}", path=output_dir))
        self.copy_files_with_progress(self.emoji_file_paths, output_dir)
        self.log(t("✅ 完成！正在打开输出文件夹……"))
        try:
            subprocess.Popen(['explorer', os.path.abspath(output_dir)])
            QMessageBox.information(self, t("完成"), t("全部提取成功！"))
        except Exception as e:
            self.log(_tf("❌ 无法打开资源管理器: {error}", error=e))

    def import_files_with_progress(self, file_paths):
        try:
            main_win = self.window()
            if not hasattr(main_win, 'storage') or not main_win.storage:
                message = t("❌ 导入失败，无法获取表情包资源库存储服务！")
                self.log(message)
                QMessageBox.warning(self, t("错误"), t("无法获取表情包资源库存储服务！"))
                return

            storage = main_win.storage

            selected_qq = self.get_selected_qq()
            selected_folder = self.getSelectedFolder()
            
            # 建立一个资源库分类名称，例如 QQ_123456_personal_emoji
            category_name = f"QQ_{selected_qq}_{selected_folder}"
            
            storage.add_category(category_name)

            total_files = len(file_paths)
            self.progressBar.setMaximum(total_files)
            self.progressBar.setValue(0)

            imported_count = 0
            dup_count = 0
            fail_count = 0
            
            self.log(_tf(
                "💬 开始导入表情到资源库，分类: [{category}]...",
                category=category_name
            ))

            for idx, src_file in enumerate(file_paths):
                if not src_file or not os.path.exists(src_file):
                    fail_count += 1
                    continue
                
                # marketface 先恢复为 GIF；APNG 也转为 GIF，确保入库内容可正常读取
                target_file_to_save = src_file
                _, marketface_gif_path = self._get_marketface_data(src_file)
                if marketface_gif_path:
                    target_file_to_save = marketface_gif_path
                elif QQExtractor.is_apng_file(src_file):
                    temp_gif = QQExtractor.convert_apng_to_gif(src_file)
                    if temp_gif:
                        target_file_to_save = temp_gif

                # 经过存储的清洗保存程序（自动转码 PNG/GIF 并提取 MD5/Pixel 感知哈希进行全局去重）
                dest_path, is_duplicated = storage.save_file(target_file_to_save)
                if dest_path:
                    # 将清洗去重后的表情加入到这个分类下
                    storage.add_image_to_category(dest_path, category_name)
                    if is_duplicated:
                        dup_count += 1
                    imported_count += 1
                else:
                    fail_count += 1
                
                self.progressBar.setValue(idx + 1)
                filename = os.path.basename(src_file)
                duplicate_text = t("(重复已被合并)") if is_duplicated else ""
                self.log(_tf(
                    "导入 [{done}/{total}]: {filename} -> {category} {duplicate}",
                    done=idx + 1, total=total_files, filename=filename,
                    category=category_name, duplicate=duplicate_text
                ))
                
                if idx % 5 == 0 or idx == total_files - 1:
                    QCoreApplication.processEvents()
            
            self.log(_tf(
                "✅ 导入完成！成功导入并分类 {imported} 个表情，其中 {duplicated} 个重复已被合并过滤，失败 {failed} 个。",
                imported=imported_count, duplicated=dup_count, failed=fail_count
            ))
            QMessageBox.information(
                self, t("完成"),
                _tf(
                    "表情导入成功！\n分类: {category}\n共导入并去重处理: {count} 个",
                    category=category_name, count=imported_count
                )
            )
        except Exception as e:
            self.log(_tf("❌ 导入到资源库时出错: {error}", error=e))
            QMessageBox.critical(self, t("错误"), _tf("导入出错: {error}", error=e))

    def importSelected(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_items = self.previewListWidget.selectedItems()
        if len(selected_items) == 0:
            message = t("❌ 您尚未选择任何表情！请先在右侧预览区选中表情后再导入。")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("请先在右侧预览区选中表情后再导入！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导入选中"),
            _tf(
                "确定将当前选中的 {count} 个表情导入到资源库？（会经过自动清洗和去重过滤）",
                count=len(selected_items)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导入操作"))
            return

        selected_paths = [item.data(Qt.UserRole) for item in selected_items if item.data(Qt.UserRole)]
        self.import_files_with_progress(selected_paths)

    def importAll(self):
        selected_qq = self.get_selected_qq()
        if not selected_qq:
            message = t("❌ 你还没有选择QQ号呢，请先选择一个QQ号！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        selected_folder = self.getSelectedFolder()
        if not selected_folder:
            message = t("❌ 你还没有选择表情分类呢，请先选择一个分类！")
            self.log(message)
            QMessageBox.information(self, t("提示"), message)
            return

        if len(self.emoji_file_paths) == 0:
            userdata_save_path = QQExtractor.get_userdata_save_path(self.default_ini_path, self.userdata_save_path_cache)
            if userdata_save_path:
                file_path = Path(os.path.join(userdata_save_path, selected_qq))
                emoji_path = file_path / "nt_qq" / "nt_data" / "Emoji" / selected_folder
                self.emoji_file_paths = QQExtractor.scan_emojis(emoji_path, selected_folder)

        if len(self.emoji_file_paths) == 0:
            message = t("❌ 该表情分类下未发现任何有效的图片文件，无法导入！")
            self.log(message)
            QMessageBox.warning(self, t("提示"), t("该分类下未发现任何有效的表情图片文件！"))
            return

        reply = QMessageBox.question(
            self,
            t("确认导入全部"),
            _tf(
                "当前不管界面是否完全加载，将直接导入扫描到的该分类下所有 {count} 个表情到资源库？（自动清洗和去重）",
                count=len(self.emoji_file_paths)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self.log(t("💬 用户取消了导入操作"))
            return

        self.import_files_with_progress(self.emoji_file_paths)

    def log(self, message):
        self.logTextEdit.append(message)
        self.statusLabel.setText(message)
        scrollbar = self.logTextEdit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.logTextEdit.ensureCursorVisible()

    def showHelp(self):
        help_text = t(
            "使用帮助：\n\n"
            "1. 使用时请确保已登录过QQ并加载过全部表情包\n"
            "2. 提取的时候会自动创建以QQ号开头的文件夹\n"
            "3. 选择一个账号后，点击'扫描表情包预览'获取表情包，然后选择表情，最后'导出选中表情'或'导出全部表情'。\n"
            "4. 导出的表情包比账号内实际的表情包要多属正常现象，因为QQ会缓存一些表情包\n\n"
            "  注意：如果没有找到任何用户，请确保QQ已经在本地登录过。并确保路径正确\n"
            "  可以尝试手动指定聊天数据文件夹的所在位置"
        )

        QMessageBox.information(
            self,
            t("使用帮助"),
            help_text,
            QMessageBox.Ok
        )
