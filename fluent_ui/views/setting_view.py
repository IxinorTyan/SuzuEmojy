from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    SettingCard, SettingCardGroup, SwitchSettingCard, OptionsSettingCard, RangeSettingCard,
    ScrollArea, ExpandLayout, InfoBar, FluentIcon as FIF, LineEdit, Action, Theme, SpinBox,
    ComboBoxSettingCard, TransparentToolButton, TitleLabel
)
from PySide6.QtGui import QKeySequence
from services.i18n import t, i18n_engine, SUPPORTED_LANGUAGES

class SpinBoxRangeSettingCard(RangeSettingCard):
    """
    带 SpinBox 的范围设置卡片，既能拖动滑块也能直接输入数字
    """
    def __init__(self, configItem, icon, title, content=None, parent=None):
        super().__init__(configItem, icon, title, content, parent)
        
        if hasattr(self, 'valueLabel'):
            self.valueLabel.hide()
            
        self.spinBox = SpinBox(self)
        self.spinBox.setRange(configItem.validator.min, configItem.validator.max)
        self.spinBox.setValue(configItem.value)
        self.spinBox.setFixedWidth(200)
        
        self.hBoxLayout.insertWidget(self.hBoxLayout.count() - 2, self.spinBox, 0, Qt.AlignRight)
        
        self.slider.valueChanged.connect(self.spinBox.setValue)
        self.spinBox.valueChanged.connect(self.slider.setValue)

    def setValue(self, value):
        super().setValue(value)
        if hasattr(self, 'valueLabel'):
            self.valueLabel.hide()
        self.spinBox.setValue(value)

class CustomHotkeySettingCard(SettingCard):
    """
    用于拦截和显示快捷键的自定义设置卡片，完美融入 Fluent 风格
    """
    hotkey_changed = Signal(str)

    def __init__(self, title, content, icon, default_hotkey, parent=None):
        super().__init__(icon, title, content, parent)
        
        from qfluentwidgets import PushButton
        
        self.hotkey_input = LineEdit(self)
        self.hotkey_input.setPlaceholderText(t("点击输入框并按下快捷键"))
        self.hotkey_input.setReadOnly(True)
        self.hotkey_input.setText(default_hotkey)
        self.hotkey_input.setFixedWidth(200)
        self.hotkey_input.installEventFilter(self)
        
        self.btn_clear = PushButton(t("清除"), self)
        self.btn_clear.clicked.connect(lambda: self._update_hotkey(""))
        
        self.hBoxLayout.addWidget(self.hotkey_input, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addWidget(self.btn_clear, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _update_hotkey(self, hotkey_str):
        self.hotkey_input.setText(hotkey_str)
        self.hotkey_changed.emit(hotkey_str)

    def eventFilter(self, obj, event):
        if obj == self.hotkey_input and event.type() == event.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            
            if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R):
                return True

            parts = []
            if modifiers & Qt.ControlModifier:
                parts.append("ctrl")
            if modifiers & Qt.AltModifier:
                parts.append("alt")
            if modifiers & Qt.ShiftModifier:
                parts.append("shift")
            if modifiers & Qt.MetaModifier:
                parts.append("win")

            key_str = QKeySequence(key).toString().lower()
            
            if key_str:
                parts.append(key_str)
                hotkey_str = "+".join(parts)
                self._update_hotkey(hotkey_str)

            return True
            
        return super().eventFilter(obj, event)


class SettingInterface(ScrollArea):
    """设置界面 (View)"""
    
    settings_changed = Signal(str)
    back_requested = Signal()

    def __init__(self, config_service, parent=None):
        super().__init__(parent=parent)
        self.config = config_service
        self.setObjectName("SettingInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.scrollWidget.setStyleSheet("QWidget { background-color: transparent; }")
        
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        # 顶部返回工具栏
        self.topBar = QWidget(self.scrollWidget)
        self.topBarLayout = QHBoxLayout(self.topBar)
        self.topBarLayout.setContentsMargins(0, 0, 0, 0)
        self.topBarLayout.setSpacing(12)

        self.btnBack = TransparentToolButton(FIF.LEFT_ARROW, self.topBar)
        self.btnBack.setToolTip(t("返回主面板"))
        self.btnBack.clicked.connect(self.back_requested.emit)

        self.titleLabel = TitleLabel(t("设置"), self.topBar)

        self.topBarLayout.addWidget(self.btnBack)
        self.topBarLayout.addWidget(self.titleLabel)
        self.topBarLayout.addStretch()

        # =================== 1. 窗口设置 ===================
        self.windowGroup = SettingCardGroup(t("窗口设置"), self.scrollWidget)
        
        from qfluentwidgets import BoolValidator, qconfig, ConfigItem
        self.alwaysTopConfigItem = ConfigItem(
            "Window", "AlwaysOnTop", True,
            BoolValidator()
        )
        self.alwaysTopConfigItem.value = self.config.get("always_on_top", True)
        
        self.alwaysTopCard = SwitchSettingCard(
            FIF.PIN, t("主窗口始终置顶"), t("让表情包管理器始终显示在其他窗口之上"),
            configItem=self.alwaysTopConfigItem, parent=self.windowGroup
        )
        self.alwaysTopCard.setChecked(self.config.get("always_on_top", True))
        
        from qfluentwidgets import RangeConfigItem, RangeValidator
        
        self.previewSizeConfigItem = RangeConfigItem(
            "Advanced", "PreviewSize", 320,
            RangeValidator(100, 800)
        )
        self.previewSizeConfigItem.value = self.config.get("preview_size", 320)
        
        self.previewSizeCard = SpinBoxRangeSettingCard(
            self.previewSizeConfigItem, FIF.ZOOM, t("预览浮窗大小"), t("设置悬停时弹出的大图的像素尺寸"),
            parent=self.windowGroup
        )
        if hasattr(self.previewSizeCard, 'setValue'):
            self.previewSizeCard.setValue(self.config.get("preview_size", 320))
            
        self.quickPanelWidthConfigItem = RangeConfigItem(
            "Advanced", "QuickPanelWidth", 360,
            RangeValidator(250, 800)
        )
        self.quickPanelWidthConfigItem.value = self.config.get("quick_panel_width", 360)
        
        self.quickPanelWidthCard = SpinBoxRangeSettingCard(
            self.quickPanelWidthConfigItem, FIF.FIT_PAGE, t("快速面板宽度"), t("设置快速表情调用面板的宽度（像素）"),
            parent=self.windowGroup
        )
        if hasattr(self.quickPanelWidthCard, 'setValue'):
            self.quickPanelWidthCard.setValue(self.config.get("quick_panel_width", 360))
            
        self.quickPanelHeightConfigItem = RangeConfigItem(
            "Advanced", "QuickPanelHeight", 480,
            RangeValidator(150, 1000)
        )
        self.quickPanelHeightConfigItem.value = self.config.get("quick_panel_height", 480)
        
        self.quickPanelHeightCard = SpinBoxRangeSettingCard(
            self.quickPanelHeightConfigItem, FIF.FIT_PAGE, t("快速面板高度"), t("设置快速表情调用面板的高度（像素）"),
            parent=self.windowGroup
        )
        if hasattr(self.quickPanelHeightCard, 'setValue'):
            self.quickPanelHeightCard.setValue(self.config.get("quick_panel_height", 480))

        # =================== 2. 个性化 ===================
        self.themeGroup = SettingCardGroup(t("个性化"), self.scrollWidget)
        
        from qfluentwidgets import OptionsValidator, OptionsConfigItem
        
        # 语言设置
        self.languageConfigItem = OptionsConfigItem(
            "Theme", "Language", "zh",
            OptionsValidator([lang[0] for lang in SUPPORTED_LANGUAGES]),
            restart=False
        )
        self.languageConfigItem.value = self.config.get("language", "zh")
        
        self.languageCard = ComboBoxSettingCard(
            self.languageConfigItem, FIF.LANGUAGE, t("语言 (Language)"), t("更改软件的显示语言"),
            texts=[lang[1] for lang in SUPPORTED_LANGUAGES],
            parent=self.themeGroup
        )
        
        self.themeConfigItem = OptionsConfigItem(
            "Theme", "Mode", "system",
            OptionsValidator(["system", "light", "dark"]),
            restart=True
        )
        # 兼容旧配置名 theme_mode，新配置名 appearance_mode
        appearance_mode = self.config.get("appearance_mode", self.config.get("theme_mode", "system"))
        self.themeConfigItem.value = appearance_mode
        
        self.themeCard = ComboBoxSettingCard(
            self.themeConfigItem, FIF.BRUSH, t("外观模式"), t("更改软件的深浅色模式"),
            texts=[t("跟随系统"), t("浅色模式"), t("深色模式")],
            parent=self.themeGroup
        )
        
        from fluent_ui.components.theme_color_card import ThemeColorSettingCard
        
        # 兼容旧配置名 theme_color
        old_theme_color = self.config.get("theme_color", "white")
        light_key = self.config.get("light_theme_key", old_theme_color)
        dark_key = self.config.get("dark_theme_key", old_theme_color)
        
        self.themeColorCard = ThemeColorSettingCard(
            t("主题颜色"), t("为浅色和深色模式分别设置强调色和背景色"), FIF.PALETTE,
            light_key, dark_key,
            parent=self.themeGroup
        )
        
        self.useSystemFontConfigItem = ConfigItem(
            "Theme", "UseSystemFont", False,
            BoolValidator()
        )
        self.useSystemFontConfigItem.value = self.config.get("use_system_font", False)
        
        self.useSystemFontCard = SwitchSettingCard(
            FIF.FONT, t("使用系统默认字体"), t("关闭以使用组件库默认字体。开启后将跟随系统字体，但可能出现排版错位、文字被裁剪等显示问题。更改后需重启软件生效。"),
            configItem=self.useSystemFontConfigItem, parent=self.themeGroup
        )
        self.useSystemFontCard.setChecked(self.config.get("use_system_font", False))
        
        self.sidebarIconSizeConfigItem = RangeConfigItem(
            "Advanced", "SidebarIconSize", 20,
            RangeValidator(16, 64)
        )
        self.sidebarIconSizeConfigItem.value = self.config.get("sidebar_icon_size", 20)
        
        self.sidebarIconSizeCard = SpinBoxRangeSettingCard(
            self.sidebarIconSizeConfigItem, FIF.FOLDER, t("列表模式下侧边栏图标大小"), t("设置左侧分类列表图标的尺寸"),
            parent=self.themeGroup
        )
        if hasattr(self.sidebarIconSizeCard, 'setValue'):
            self.sidebarIconSizeCard.setValue(self.config.get("sidebar_icon_size", 20))
            
        self.showSettingBtnConfigItem = ConfigItem(
            "Theme", "ShowSettingButton", True,
            BoolValidator()
        )
        self.showSettingBtnConfigItem.value = self.config.get("show_setting_button", True)
        
        self.showSettingBtnCard = SwitchSettingCard(
            FIF.SETTING, t("显示设置入口"), t("在主面板右上角显示快速进入设置的按钮"),
            configItem=self.showSettingBtnConfigItem, parent=self.themeGroup
        )
        self.showSettingBtnCard.setChecked(self.config.get("show_setting_button", True))
        
        # =================== 3. 高级设置 ===================
        self.advancedGroup = SettingCardGroup(t("高级设置"), self.scrollWidget)
        
        # 3.1 发送时静态图转 GIF 开关
        self.sendAsGifConfigItem = ConfigItem(
            "Advanced", "ConvertStaticToGif", True,
            BoolValidator()
        )
        self.sendAsGifConfigItem.value = self.config.get("convert_static_to_gif", True)
        
        self.sendAsGifCard = SwitchSettingCard(
            FIF.SEND, t("发送时将静态图转为GIF"), t("复制或发送 PNG/JPG/WEBP 等静态图时自动转为 1 帧 GIF 格式，避免在 QQ 等聊天软件中显示为超大原图"),
            configItem=self.sendAsGifConfigItem, parent=self.advancedGroup
        )
        self.sendAsGifCard.setChecked(self.config.get("convert_static_to_gif", True))

        # 3.2 悬停预览延迟
        self.previewDelayConfigItem = RangeConfigItem(
            "Advanced", "PreviewDelay", 500,
            RangeValidator(100, 3000)
        )
        self.previewDelayConfigItem.value = self.config.get("preview_delay", 500)
        
        self.previewDelayCard = SpinBoxRangeSettingCard(
            self.previewDelayConfigItem, FIF.HISTORY, t("悬停预览延迟"), t("设置鼠标悬停多久后弹出大图预览"),
            parent=self.advancedGroup
        )
        if hasattr(self.previewDelayCard, 'setValue'):
            self.previewDelayCard.setValue(self.config.get("preview_delay", 500))
        
        # 3.3 单次渲染上限
        self.batchSizeConfigItem = RangeConfigItem(
            "Advanced", "BatchSize", 50,
            RangeValidator(10, 200)
        )
        self.batchSizeConfigItem.value = self.config.get("render_batch_size", 50)
        
        self.batchSizeCard = SpinBoxRangeSettingCard(
            self.batchSizeConfigItem, FIF.SPEED_HIGH, t("单次渲染上限"), t("设置每次加载图片的数量，数值越小越流畅但加载越久"),
            parent=self.advancedGroup
        )
        if hasattr(self.batchSizeCard, 'setValue'):
            self.batchSizeCard.setValue(self.config.get("render_batch_size", 50))

        # 3.4 最近使用记录上限
        self.recentLimitConfigItem = RangeConfigItem(
            "Advanced", "RecentLimit", 30,
            RangeValidator(1, 999)
        )
        self.recentLimitConfigItem.value = self.config.get("recent_limit", 30)
        
        self.recentLimitCard = SpinBoxRangeSettingCard(
            self.recentLimitConfigItem, FIF.HISTORY, t("最近使用记录上限"), t("设置快速面板中显示的最近使用表情数量"),
            parent=self.advancedGroup
        )

        # 3.5 侧边栏悬浮提示
        self.sidebarTooltipConfigItem = ConfigItem(
            "Advanced", "SidebarTooltip", True,
            BoolValidator()
        )
        self.sidebarTooltipConfigItem.value = self.config.get("show_sidebar_tooltip", True)
        
        self.sidebarTooltipCard = SwitchSettingCard(
            FIF.INFO, t("图标模式悬浮提示"), t("在侧边栏折叠为仅图标模式时，鼠标悬停显示分类名称"),
            configItem=self.sidebarTooltipConfigItem, parent=self.advancedGroup
        )
        self.sidebarTooltipCard.setChecked(self.config.get("show_sidebar_tooltip", True))
        
        # 3.6 主唤醒快捷键
        self.hotkeyCard = CustomHotkeySettingCard(
            t("唤醒快捷键"), t("设置全局唤醒和隐藏主面板的快捷键"), FIF.COMMAND_PROMPT,
            self.config.get("global_hotkey", "ctrl+shift+e"),
            parent=self.advancedGroup
        )
        
        # 3.7 快速面板快捷键
        self.quickHotkeyCard = CustomHotkeySettingCard(
            t("快速面板快捷键"), t("设置全局唤醒快速表情调用面板的快捷键"), FIF.COMMAND_PROMPT,
            self.config.get("quick_panel_hotkey", "alt+2"),
            parent=self.advancedGroup
        )

        # 将卡片加入窗口设置组
        self.windowGroup.addSettingCard(self.alwaysTopCard)
        self.windowGroup.addSettingCard(self.previewSizeCard)
        self.windowGroup.addSettingCard(self.quickPanelWidthCard)
        self.windowGroup.addSettingCard(self.quickPanelHeightCard)
        
        # 将卡片加入个性化组
        self.themeGroup.addSettingCard(self.languageCard)
        self.themeGroup.addSettingCard(self.themeCard)
        self.themeGroup.addSettingCard(self.themeColorCard)
        self.themeGroup.addSettingCard(self.sidebarIconSizeCard)
        self.themeGroup.addSettingCard(self.showSettingBtnCard)
        self.themeGroup.addSettingCard(self.useSystemFontCard)
        
        # 将卡片加入高级设置组
        self.advancedGroup.addSettingCard(self.sendAsGifCard)
        self.advancedGroup.addSettingCard(self.previewDelayCard)
        self.advancedGroup.addSettingCard(self.batchSizeCard)
        self.advancedGroup.addSettingCard(self.recentLimitCard)
        self.advancedGroup.addSettingCard(self.sidebarTooltipCard)
        self.advancedGroup.addSettingCard(self.hotkeyCard)
        self.advancedGroup.addSettingCard(self.quickHotkeyCard)
        
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        
        self.expandLayout.addWidget(self.topBar)
        self.expandLayout.addWidget(self.windowGroup)
        self.expandLayout.addWidget(self.themeGroup)
        self.expandLayout.addWidget(self.advancedGroup)
        
        # 绑定语言切换信号
        i18n_engine.language_changed.connect(self.update_texts)

    def update_texts(self, lang):
        """动态刷新界面文本"""
        self.titleLabel.setText(t("设置"))
        self.btnBack.setToolTip(t("返回主面板"))
        self.windowGroup.titleLabel.setText(t("窗口设置"))
        self.alwaysTopCard.setTitle(t("主窗口始终置顶"))
        self.alwaysTopCard.setContent(t("让表情包管理器始终显示在其他窗口之上"))
        self.previewSizeCard.setTitle(t("预览浮窗大小"))
        self.previewSizeCard.setContent(t("设置悬停时弹出的大图的像素尺寸"))
        self.quickPanelWidthCard.setTitle(t("快速面板宽度"))
        self.quickPanelWidthCard.setContent(t("设置快速表情调用面板的宽度（像素）"))
        self.quickPanelHeightCard.setTitle(t("快速面板高度"))
        self.quickPanelHeightCard.setContent(t("设置快速表情调用面板的高度（像素）"))
        
        self.themeGroup.titleLabel.setText(t("个性化"))
        self.languageCard.setTitle(t("语言 (Language)"))
        self.languageCard.setContent(t("更改软件的显示语言"))
        self.themeCard.setTitle(t("外观模式"))
        self.themeCard.setContent(t("更改软件的深浅色模式"))
        # 更新 ComboBox 的选项文本
        self.themeCard.comboBox.blockSignals(True)
        try:
            self.themeCard.comboBox.clear()
            self.themeCard.comboBox.addItems([t("跟随系统"), t("浅色模式"), t("深色模式")])
            self.themeCard.comboBox.setCurrentIndex(self.themeConfigItem.options.index(self.themeConfigItem.value))
        finally:
            self.themeCard.comboBox.blockSignals(False)
        
        self.themeColorCard.setTitle(t("主题颜色"))
        self.themeColorCard.setContent(t("为浅色和深色模式分别设置强调色和背景色"))
        self.useSystemFontCard.setTitle(t("使用系统默认字体"))
        self.useSystemFontCard.setContent(t("关闭以使用组件库默认字体。开启后将跟随系统字体，但可能出现排版错位、文字被裁剪等显示问题。更改后需重启软件生效。"))
        self.sidebarIconSizeCard.setTitle(t("列表模式下侧边栏图标大小"))
        self.sidebarIconSizeCard.setContent(t("设置左侧分类列表图标的尺寸"))
        self.showSettingBtnCard.setTitle(t("显示设置入口"))
        self.showSettingBtnCard.setContent(t("在主面板右上角显示快速进入设置的按钮"))
        
        self.advancedGroup.titleLabel.setText(t("高级设置"))
        self.sendAsGifCard.setTitle(t("发送时将静态图转为GIF"))
        self.sendAsGifCard.setContent(t("复制或发送 PNG/JPG/WEBP 等静态图时自动转为 1 帧 GIF 格式，避免在 QQ 等聊天软件中显示为超大原图"))
        self.previewDelayCard.setTitle(t("悬停预览延迟"))
        self.previewDelayCard.setContent(t("设置鼠标悬停多久后弹出大图预览"))
        self.batchSizeCard.setTitle(t("单次渲染上限"))
        self.batchSizeCard.setContent(t("设置每次加载图片的数量，数值越小越流畅但加载越久"))
        self.recentLimitCard.setTitle(t("最近使用记录上限"))
        self.recentLimitCard.setContent(t("设置快速面板中显示的最近使用表情数量"))
        self.sidebarTooltipCard.setTitle(t("图标模式悬浮提示"))
        self.sidebarTooltipCard.setContent(t("在侧边栏折叠为仅图标模式时，鼠标悬停显示分类名称"))
        self.hotkeyCard.setTitle(t("唤醒快捷键"))
        self.hotkeyCard.setContent(t("设置全局唤醒和隐藏主面板的快捷键"))
        self.hotkeyCard.hotkey_input.setPlaceholderText(t("点击输入框并按下快捷键"))
        self.hotkeyCard.btn_clear.setText(t("清除"))
        self.quickHotkeyCard.setTitle(t("快速面板快捷键"))
        self.quickHotkeyCard.setContent(t("设置全局唤醒快速表情调用面板的快捷键"))
        self.quickHotkeyCard.hotkey_input.setPlaceholderText(t("点击输入框并按下快捷键"))
        self.quickHotkeyCard.btn_clear.setText(t("清除"))

    def _connect_signals(self):
        self.alwaysTopCard.checkedChanged.connect(self._on_always_top_changed)
        
        self.previewDelayCard.valueChanged.connect(lambda v: self._save_config("preview_delay", v))
        self.previewSizeCard.valueChanged.connect(lambda v: self._save_config("preview_size", v))
        self.sidebarIconSizeCard.valueChanged.connect(lambda v: self._save_config("sidebar_icon_size", v, True))
        self.showSettingBtnCard.checkedChanged.connect(lambda v: self._save_config("show_setting_button", v, True))
        self.sendAsGifCard.checkedChanged.connect(lambda v: self._save_config("convert_static_to_gif", v, True))
        self.sidebarTooltipCard.checkedChanged.connect(lambda v: self._save_config("show_sidebar_tooltip", v, True))
        self.batchSizeCard.valueChanged.connect(lambda v: self._save_config("render_batch_size", v))
        self.recentLimitCard.valueChanged.connect(lambda v: self._save_config("recent_limit", v))
        self.quickPanelWidthCard.valueChanged.connect(lambda v: self._save_config("quick_panel_width", v))
        self.quickPanelHeightCard.valueChanged.connect(lambda v: self._save_config("quick_panel_height", v))
        self.hotkeyCard.hotkey_changed.connect(lambda v: self._on_hotkey_changed("global_hotkey", v, self.hotkeyCard))
        self.quickHotkeyCard.hotkey_changed.connect(lambda v: self._on_hotkey_changed("quick_panel_hotkey", v, self.quickHotkeyCard))
        
        def on_language_changed(index):
            if 0 <= index < len(SUPPORTED_LANGUAGES):
                lang_code = SUPPORTED_LANGUAGES[index][0]
                i18n_engine.set_language(lang_code)
                
        self.languageCard.comboBox.currentIndexChanged.connect(on_language_changed)
        
        def on_theme_changed(index):
            theme_keys = ["system", "light", "dark"]
            if 0 <= index < len(theme_keys):
                self._save_config("appearance_mode", theme_keys[index], True)
                
        self.themeCard.comboBox.currentIndexChanged.connect(on_theme_changed)
        
        def on_color_changed(mode, theme_key):
            config_key = "dark_theme_key" if mode == "dark" else "light_theme_key"
            self._save_config(config_key, theme_key, True)
            
        self.themeColorCard.color_changed.connect(on_color_changed)
        self.useSystemFontCard.checkedChanged.connect(lambda v: self._save_config("use_system_font", v, True))

    def _on_hotkey_changed(self, key, value, card):
        other_key = "quick_panel_hotkey" if key == "global_hotkey" else "global_hotkey"
        other_value = self.config.get(other_key, "alt+2" if other_key == "quick_panel_hotkey" else "ctrl+shift+e")
        
        if value and value == other_value:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error(
                title=t("快捷键冲突"),
                content=t("该快捷键已被其他功能占用，请重新设置。"),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
            # 恢复原来的值
            old_value = self.config.get(key, "ctrl+shift+e" if key == "global_hotkey" else "alt+2")
            card.hotkey_input.setText(old_value)
            return
            
        self._save_config(key, value, True)

    def _on_always_top_changed(self, is_checked):
        self._save_config("always_on_top", is_checked, True)
        
    def _save_config(self, key, value, emit_signal=False):
        if self.config.get(key) != value:
            self.config.set(key, value)
            if emit_signal:
                self.settings_changed.emit(key)
