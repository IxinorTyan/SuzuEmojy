from pathlib import Path
import ast
import textwrap

path = Path("fluent_ui/views/tg_sticker_view.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
target_class = next(
    node for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "TGStickerInterface"
)
target_method = next(
    node for node in target_class.body
    if isinstance(node, ast.FunctionDef) and node.name == "_init_ui"
)

new_method = r'''
    def _init_ui(self):
        """构建与 QQ 扫描页一致的现代化上下结构。"""
        self.log_history = []
        self._config_anim = None
        self._busy = False

        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(32, 10, 32, 12)
        self.mainLayout.setSpacing(10)

        # 1. 顶栏
        self.topBar = QWidget(self)
        self.topBar.setFixedHeight(self.TOP_BAR_HEIGHT)
        self.topBar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(10)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回主面板"))
        self.btnBack.clicked.connect(self._on_back_clicked)
        self.titleLabel = TitleLabel(t("下载TG贴纸"), self.topBar)
        self.btnLog = TransparentToolButton(FIF.DOCUMENT, self.topBar)
        self.btnLog.setFixedSize(32, 32)
        self.btnLog.setToolTip(t("操作日志"))
        self.btnLog.clicked.connect(self.show_log_dialog)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()
        self.topBarLayout.addWidget(self.btnLog)
        self.mainLayout.addWidget(self.topBar)

        # 2. 配置折叠后的摘要
        self.summaryRow = QWidget(self)
        summary_layout = QHBoxLayout(self.summaryRow)
        summary_layout.setContentsMargins(12, 4, 12, 4)
        summary_layout.setSpacing(8)
        self.summaryLabel = BodyLabel("", self.summaryRow)
        self.summaryLabel.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.expandConfigButton = PushButton(t("展开配置"), self.summaryRow)
        self.expandConfigButton.setIcon(FIF.CHEVRON_DOWN_MED)
        self.expandConfigButton.clicked.connect(self.expand_config)
        summary_layout.addWidget(self.summaryLabel)
        summary_layout.addStretch()
        summary_layout.addWidget(self.expandConfigButton)
        self.summaryRow.hide()
        self.mainLayout.addWidget(self.summaryRow)

        # 3. 全宽可折叠配置卡
        self.configCard = CardWidget(self)
        config_layout = QVBoxLayout(self.configCard)
        config_layout.setContentsMargins(20, 14, 20, 14)
        config_layout.setSpacing(10)

        config_header = QHBoxLayout()
        self.configTitle = StrongBodyLabel(t("Telegram 贴纸配置"), self.configCard)
        self.collapseConfigButton = RotatingChevronButton(self.configCard)
        self.collapseConfigButton.set_direction(180, animated=False)
        self.collapseConfigButton.setToolTip(t("收起配置面板"))
        self.collapseConfigButton.clicked.connect(self.toggle_config)
        config_header.addWidget(self.configTitle)
        config_header.addStretch()
        config_header.addWidget(self.collapseConfigButton)
        config_layout.addLayout(config_header)

        def add_form_row(label_text, control, trailing=None):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = BodyLabel(t(label_text), self.configCard)
            label.setFixedWidth(76)
            row.addWidget(label)
            row.addWidget(control, 1)
            if trailing is not None:
                row.addWidget(trailing)
            config_layout.addLayout(row)
            return label

        self.urlInputEdit = LineEdit(self.configCard)
        self.urlInputEdit.setPlaceholderText(
            t("贴纸链接或包名，如: animals 或 https://t.me/addstickers/xxx")
        )
        self.urlInputEdit.returnPressed.connect(self.startParsePack)
        self.helpButton = TransparentToolButton(FIF.HELP, self.configCard)
        self.helpButton.setFixedSize(32, 32)
        self.helpButton.setToolTip(t("使用帮助与教程"))
        self.helpButton.clicked.connect(self.showHelpDialog)
        self.urlLabel = add_form_row("贴纸链接:", self.urlInputEdit, self.helpButton)

        self.savePathEdit = LineEdit(self.configCard)
        self.savePathEdit.setText(self.save_path)
        self.savePathEdit.setPlaceholderText(t("请选择贴纸保存路径..."))
        self.selectDirButton = PushButton(t("浏览..."), self.configCard)
        self.selectDirButton.setFixedWidth(90)
        self.selectDirButton.clicked.connect(self.selectSavePath)
        self.savePathLabel = add_form_row("保存路径:", self.savePathEdit, self.selectDirButton)

        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        self.formatLabel = BodyLabel(t("导出格式:"), self.configCard)
        self.formatLabel.setFixedWidth(76)
        self.formatComboBox = ComboBox(self.configCard)
        self.formatComboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.formatComboBox.addItem(t("PNG (静态图片 / 动图首帧)"), userData="png")
        self.formatComboBox.addItem(t("GIF (动图 / 视频贴纸)"), userData="gif")
        self.formatComboBox.addItem(t("原始格式 (WebP / TGS / WebM)"), userData="original")
        self.formatComboBox.addItem(t("智能适配 (自动匹配最佳格式)"), userData="auto")
        self.zipCheckBox = CheckBox(t("导出完成后打包为 ZIP 压缩文件"), self.configCard)
        self.zipCheckBox.setChecked(True)
        format_row.addWidget(self.formatLabel)
        format_row.addWidget(self.formatComboBox, 1)
        format_row.addWidget(self.zipCheckBox)
        config_layout.addLayout(format_row)

        # 高级设置独立折叠
        advanced_header = QHBoxLayout()
        self.advancedTitle = BodyLabel(t("高级设置 (Token / 网络代理)"), self.configCard)
        self.toggleAdvBtn = RotatingChevronButton(self.configCard)
        self.toggleAdvBtn.set_direction(0, animated=False)
        self.toggleAdvBtn.setToolTip(t("展开高级设置"))
        self.toggleAdvBtn.clicked.connect(self.toggleAdvancedSettings)
        advanced_header.addWidget(self.advancedTitle)
        advanced_header.addStretch()
        advanced_header.addWidget(self.toggleAdvBtn)
        config_layout.addLayout(advanced_header)

        self.advWidget = QWidget(self.configCard)
        adv_layout = QVBoxLayout(self.advWidget)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(8)

        def add_advanced_row(label_text, control, trailing=None):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = BodyLabel(t(label_text), self.advWidget)
            label.setFixedWidth(76)
            row.addWidget(label)
            row.addWidget(control, 1)
            if trailing is not None:
                row.addWidget(trailing)
            adv_layout.addLayout(row)
            return label

        self.tokenEdit = LineEdit(self.advWidget)
        self.tokenEdit.setEchoMode(LineEdit.Password)
        self.tokenEdit.setPlaceholderText(t("内置默认 Token，可填自定义 Token"))
        self.toggleTokenBtn = PushButton(t("👁️ 显示"), self.advWidget)
        self.toggleTokenBtn.setFixedWidth(76)
        self.toggleTokenBtn.clicked.connect(self.toggleTokenVisibility)
        self.tokenLabel = add_advanced_row("Bot Token:", self.tokenEdit, self.toggleTokenBtn)

        self.cfProxyEdit = LineEdit(self.advWidget)
        self.cfProxyEdit.setPlaceholderText(t("如: {proxy}").format(proxy=DEF_CF_PROXY))
        self.cfProxyLabel = add_advanced_row("CF 代理 URL:", self.cfProxyEdit)

        self.proxyEdit = LineEdit(self.advWidget)
        self.proxyEdit.setPlaceholderText(
            t("如 http://127.0.0.1:7890 (留空为官方直连/CF路由)")
        )
        self.proxyLabel = add_advanced_row("本地代理:", self.proxyEdit)

        advanced_buttons = QHBoxLayout()
        advanced_buttons.addStretch()
        self.testNetButton = PushButton(FIF.IOT, t("测试连通性"), self.advWidget)
        self.resetCfgButton = PushButton(FIF.SYNC, t("恢复默认"), self.advWidget)
        self.testNetButton.clicked.connect(self.testNetworkConnection)
        self.resetCfgButton.clicked.connect(self.resetConfig)
        advanced_buttons.addWidget(self.testNetButton)
        advanced_buttons.addWidget(self.resetCfgButton)
        adv_layout.addLayout(advanced_buttons)
        self.advWidget.hide()
        config_layout.addWidget(self.advWidget)

        self.parseButton = PrimaryPushButton(
            FIF.SEARCH, t("解析贴纸包预览"), self.configCard
        )
        self.parseButton.setFixedHeight(36)
        self.parseButton.clicked.connect(self.startParsePack)
        config_layout.addWidget(self.parseButton)
        self.mainLayout.addWidget(self.configCard)

        # 4. 常驻操作栏
        self.actionBar = QWidget(self)
        action_layout = QHBoxLayout(self.actionBar)
        action_layout.setContentsMargins(4, 2, 4, 2)
        action_layout.setSpacing(8)

        self.importSelectedButton = PrimaryPushButton(
            FIF.SAVE, t("入库选中"), self.actionBar
        )
        self.importAllButton = PushButton(FIF.APPLICATION, t("入库全部"), self.actionBar)
        self.exportSelectedButton = PushButton(
            FIF.DOWNLOAD, t("导出选中"), self.actionBar
        )
        self.exportAllButton = PushButton(FIF.FOLDER, t("导出全部"), self.actionBar)
        self.selectAllButton = PushButton(t("全选已加载"), self.actionBar)
        self.clearSelectionButton = PushButton(t("清空选择"), self.actionBar)
        self.invertSelectionButton = PushButton(t("反选"), self.actionBar)

        for button in (
            self.importSelectedButton, self.importAllButton,
            self.exportSelectedButton, self.exportAllButton,
            self.selectAllButton, self.clearSelectionButton,
            self.invertSelectionButton
        ):
            button.setFixedHeight(32)

        self.importSelectedButton.clicked.connect(self.importSelected)
        self.importAllButton.clicked.connect(self.importAll)
        self.exportSelectedButton.clicked.connect(self.exportSelected)
        self.exportAllButton.clicked.connect(self.exportAll)
        self.selectAllButton.clicked.connect(self.selectAllLoaded)
        self.clearSelectionButton.clicked.connect(self.clearSelection)
        self.invertSelectionButton.clicked.connect(self.invertSelection)

        action_layout.addWidget(self.importSelectedButton)
        action_layout.addWidget(self.importAllButton)
        action_layout.addWidget(self.exportSelectedButton)
        action_layout.addWidget(self.exportAllButton)
        action_layout.addStretch()
        action_layout.addWidget(self.selectAllButton)
        action_layout.addWidget(self.clearSelectionButton)
        action_layout.addWidget(self.invertSelectionButton)
        self.mainLayout.addWidget(self.actionBar)

        # 5. 贴纸包轻量标题
        self.previewTitleLabel = SubtitleLabel(t("贴纸预览区 (未加载)"), self)
        self.mainLayout.addWidget(self.previewTitleLabel)

        # 6. 主内容区：预览网格 + 详情卡
        self.contentLayout = QHBoxLayout()
        self.contentLayout.setSpacing(14)

        self.previewListWidget = QListWidget(self)
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
                padding: 0;
            }
            QListWidget::item:hover { background-color: rgba(0, 0, 0, 10); }
            QListWidget::item:selected {
                background-color: rgba(0, 120, 212, 30);
                border: 2px solid #0078d4;
            }
        """)
        self.previewListWidget.verticalScrollBar().valueChanged.connect(
            self.onScrollBarMoved
        )
        self.previewListWidget.itemSelectionChanged.connect(
            self.onItemSelectionChanged
        )
        self.contentLayout.addWidget(self.previewListWidget, 1)

        self.detailWidget = QWidget(self)
        self.detailWidget.setObjectName("detailWidget")
        self.detailWidget.setFixedWidth(280)
        self.detailWidget.setStyleSheet("""
            QWidget#detailWidget {
                background-color: rgba(255, 255, 255, 15);
                border: 1px solid rgba(0, 0, 0, 15);
                border-radius: 8px;
            }
        """)
        self.detailLayout = QVBoxLayout(self.detailWidget)
        self.detailLayout.setContentsMargins(14, 14, 14, 14)
        self.detailLayout.setSpacing(10)

        self.detailTitle = SubtitleLabel(t("贴纸详细预览"), self.detailWidget)
        self.detailPreviewLabel = QLabel(self.detailWidget)
        self.detailPreviewLabel.setAlignment(Qt.AlignCenter)
        self.detailPreviewLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.detailPreviewLabel.setFixedSize(250, 250)
        self.detailPreviewLabel.setStyleSheet(
            "background-color: rgba(0, 0, 0, 5); "
            "border: 1px solid rgba(0, 0, 0, 15); border-radius: 8px;"
        )
        self.detailInfoLabel = BodyLabel(t("未选中贴纸"), self.detailWidget)
        self.detailInfoLabel.setWordWrap(True)
        self.detailInfoLabel.setFixedWidth(250)
        self.detailInfoLabel.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.thanksLabel = BodyLabel(self.detailWidget)
        self.thanksLabel.setText(
            t('致谢：基于 <a href="https://github.com/Kiowx" '
              'style="color: #0078d4; text-decoration: underline;">Kiowx</a> '
              '的项目二次开发')
        )
        self.thanksLabel.setOpenExternalLinks(True)
        self.thanksLabel.setStyleSheet("color: #888888; font-size: 11px;")

        self.detailLayout.addWidget(self.detailTitle)
        self.detailLayout.addWidget(self.detailPreviewLabel, alignment=Qt.AlignCenter)
        self.detailLayout.addWidget(self.detailInfoLabel)
        self.detailLayout.addStretch()
        self.detailLayout.addWidget(self.thanksLabel)
        self.contentLayout.addWidget(self.detailWidget)
        self.mainLayout.addLayout(self.contentLayout, 1)

        self.tooltip = StateToolTipManager(self)
        self.tooltip.closed.connect(self._on_tooltip_closed)

        self._set_pack_actions_enabled(False)
        self._update_summary_label()
        self._update_detail_preview_visibility()

        self.log(t("💬 Telegram 贴纸包批量下载工具已就绪"))
        self.log(t("💡 支持官方直连与智能路由回退，在上方输入贴纸包链接即可开始解析。"))

        self._i18n_widgets = []
        self._register_i18n_widgets()
        i18n_engine.language_changed.connect(self.update_texts)
        self.update_texts()
        disable_wheel_scroll_adjustment(self)
'''

lines = source.splitlines(keepends=True)
replacement = new_method.lstrip("\n")
updated = "".join(lines[:target_method.lineno - 1]) + replacement + "\n" + "".join(lines[target_method.end_lineno:])
path.write_text(updated, encoding="utf-8")
print(f"Replaced _init_ui lines {target_method.lineno}-{target_method.end_lineno}")
