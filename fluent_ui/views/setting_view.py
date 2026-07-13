from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from qfluentwidgets import (
    SettingCard, SettingCardGroup, SwitchSettingCard, OptionsSettingCard, RangeSettingCard,
    ScrollArea, ExpandLayout, InfoBar, FluentIcon as FIF, LineEdit, Action, Theme, SpinBox
)
from PySide6.QtGui import QKeySequence

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
        self.hotkey_input.setPlaceholderText("点击输入框并按下快捷键")
        self.hotkey_input.setReadOnly(True)
        self.hotkey_input.setText(default_hotkey)
        self.hotkey_input.setFixedWidth(200)
        self.hotkey_input.installEventFilter(self)
        
        self.btn_clear = PushButton("清除", self)
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
        self.windowGroup = SettingCardGroup("窗口设置", self.scrollWidget)
        
        from qfluentwidgets import BoolValidator, qconfig, ConfigItem
        self.alwaysTopConfigItem = ConfigItem(
            "Window", "AlwaysOnTop", True,
            BoolValidator()
        )
        self.alwaysTopConfigItem.value = self.config.get("always_on_top", True)
        
        self.alwaysTopCard = SwitchSettingCard(
            FIF.PIN, "主窗口始终置顶", "让表情包管理器始终显示在其他窗口之上",
            configItem=self.alwaysTopConfigItem, parent=self.windowGroup
        )
        self.alwaysTopCard.setChecked(self.config.get("always_on_top", True))
        
        from qfluentwidgets import ConfigItem, OptionsConfigItem, OptionsValidator, Theme
        
        self.themeGroup = SettingCardGroup("个性化", self.scrollWidget)
        
        from qfluentwidgets import OptionsValidator, OptionsConfigItem, ComboBoxSettingCard
        
        self.themeConfigItem = OptionsConfigItem(
            "Theme", "Mode", "system",
            OptionsValidator(["system", "light", "dark"]),
            restart=True
        )
        self.themeConfigItem.value = self.config.get("theme_mode", "system")
        
        self.themeCard = ComboBoxSettingCard(
            self.themeConfigItem, FIF.BRUSH, "界面主题", "更改软件的外观主题",
            texts=["跟随系统", "浅色模式", "深色模式"],
            parent=self.themeGroup
        )
        
        from qfluentwidgets import BoolValidator, ConfigItem
        self.useSystemFontConfigItem = ConfigItem(
            "Theme", "UseSystemFont", False,
            BoolValidator()
        )
        self.useSystemFontConfigItem.value = self.config.get("use_system_font", False)
        
        self.useSystemFontCard = SwitchSettingCard(
            FIF.FONT, "使用系统默认字体", "关闭以使用组件库默认字体。开启后将跟随系统字体，但可能出现排版错位、文字被裁剪等显示问题。更改后需重启软件生效。",
            configItem=self.useSystemFontConfigItem, parent=self.themeGroup
        )
        self.useSystemFontCard.setChecked(self.config.get("use_system_font", False))
        
        self.advancedGroup = SettingCardGroup("高级设置", self.scrollWidget)
        
        from qfluentwidgets import RangeConfigItem, RangeValidator, RangeSettingCard
        self.previewDelayConfigItem = RangeConfigItem(
            "Advanced", "PreviewDelay", 500,
            RangeValidator(100, 3000)
        )
        self.previewDelayConfigItem.value = self.config.get("preview_delay", 500)
        
        self.previewDelayCard = SpinBoxRangeSettingCard(
            self.previewDelayConfigItem, FIF.HISTORY, "悬停预览延迟", "设置鼠标悬停多久后弹出大图预览",
            parent=self.advancedGroup
        )
        if hasattr(self.previewDelayCard, 'setValue'):
            self.previewDelayCard.setValue(self.config.get("preview_delay", 500))
        
        self.previewSizeConfigItem = RangeConfigItem(
            "Advanced", "PreviewSize", 320,
            RangeValidator(100, 800)
        )
        self.previewSizeConfigItem.value = self.config.get("preview_size", 320)
        
        self.previewSizeCard = SpinBoxRangeSettingCard(
            self.previewSizeConfigItem, FIF.ZOOM, "预览浮窗大小", "设置悬停时弹出的大图的像素尺寸",
            parent=self.advancedGroup
        )
        if hasattr(self.previewSizeCard, 'setValue'):
            self.previewSizeCard.setValue(self.config.get("preview_size", 320))

        self.sidebarIconSizeConfigItem = RangeConfigItem(
            "Advanced", "SidebarIconSize", 20,
            RangeValidator(16, 64)
        )
        self.sidebarIconSizeConfigItem.value = self.config.get("sidebar_icon_size", 20)
        
        self.sidebarIconSizeCard = SpinBoxRangeSettingCard(
            self.sidebarIconSizeConfigItem, FIF.FOLDER, "列表模式下侧边栏图标大小", "设置左侧分类列表图标的尺寸",
            parent=self.advancedGroup
        )
        if hasattr(self.sidebarIconSizeCard, 'setValue'):
            self.sidebarIconSizeCard.setValue(self.config.get("sidebar_icon_size", 20))

        from qfluentwidgets import BoolValidator, ConfigItem
        self.sidebarTooltipConfigItem = ConfigItem(
            "Advanced", "SidebarTooltip", True,
            BoolValidator()
        )
        self.sidebarTooltipConfigItem.value = self.config.get("show_sidebar_tooltip", True)
        
        self.batchSizeConfigItem = RangeConfigItem(
            "Advanced", "BatchSize", 50,
            RangeValidator(10, 200)
        )
        self.batchSizeConfigItem.value = self.config.get("render_batch_size", 50)
        
        self.batchSizeCard = SpinBoxRangeSettingCard(
            self.batchSizeConfigItem, FIF.SPEED_HIGH, "单次渲染上限", "设置每次加载图片的数量，数值越小越流畅但加载越久",
            parent=self.advancedGroup
        )
        if hasattr(self.batchSizeCard, 'setValue'):
            self.batchSizeCard.setValue(self.config.get("render_batch_size", 50))

        self.sidebarTooltipConfigItem = ConfigItem(
            "Advanced", "SidebarTooltip", True,
            BoolValidator()
        )
        self.sidebarTooltipConfigItem.value = self.config.get("show_sidebar_tooltip", True)
        
        self.sidebarTooltipCard = SwitchSettingCard(
            FIF.INFO, "图标模式悬浮提示", "在侧边栏折叠为仅图标模式时，鼠标悬停显示分类名称",
            configItem=self.sidebarTooltipConfigItem, parent=self.advancedGroup
        )
        self.sidebarTooltipCard.setChecked(self.config.get("show_sidebar_tooltip", True))
        
        self.hotkeyCard = CustomHotkeySettingCard(
            "唤醒快捷键", "设置全局唤醒和隐藏主面板的快捷键", FIF.COMMAND_PROMPT,
            self.config.get("global_hotkey", "ctrl+shift+e"),
            parent=self.advancedGroup
        )

        self.windowGroup.addSettingCard(self.alwaysTopCard)
        self.themeGroup.addSettingCard(self.themeCard)
        self.themeGroup.addSettingCard(self.useSystemFontCard)
        self.advancedGroup.addSettingCard(self.previewDelayCard)
        self.advancedGroup.addSettingCard(self.previewSizeCard)
        self.advancedGroup.addSettingCard(self.sidebarIconSizeCard)
        self.advancedGroup.addSettingCard(self.batchSizeCard)
        self.advancedGroup.addSettingCard(self.sidebarTooltipCard)
        self.advancedGroup.addSettingCard(self.hotkeyCard)
        
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        
        self.expandLayout.addWidget(self.windowGroup)
        self.expandLayout.addWidget(self.themeGroup)
        self.expandLayout.addWidget(self.advancedGroup)

    def _connect_signals(self):
        self.alwaysTopCard.checkedChanged.connect(self._on_always_top_changed)
        
        self.previewDelayCard.valueChanged.connect(lambda v: self._save_config("preview_delay", v))
        self.previewSizeCard.valueChanged.connect(lambda v: self._save_config("preview_size", v))
        self.sidebarIconSizeCard.valueChanged.connect(lambda v: self._save_config("sidebar_icon_size", v, True))
        self.sidebarTooltipCard.checkedChanged.connect(lambda v: self._save_config("show_sidebar_tooltip", v, True))
        self.batchSizeCard.valueChanged.connect(lambda v: self._save_config("render_batch_size", v))
        self.hotkeyCard.hotkey_changed.connect(lambda v: self._save_config("global_hotkey", v, True))
        
        def on_theme_changed(index):
            theme_keys = ["system", "light", "dark"]
            if 0 <= index < len(theme_keys):
                self._save_config("theme_mode", theme_keys[index], True)
                
        self.themeCard.comboBox.currentIndexChanged.connect(on_theme_changed)
        self.useSystemFontCard.checkedChanged.connect(lambda v: self._save_config("use_system_font", v, True))

    def _on_always_top_changed(self, is_checked):
        self._save_config("always_on_top", is_checked, True)
        
    def _save_config(self, key, value, emit_signal=False):
        if self.config.get(key) != value:
            self.config.set(key, value)
            if emit_signal:
                self.settings_changed.emit(key)
