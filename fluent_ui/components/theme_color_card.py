from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QCursor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QLabel
from qfluentwidgets import SettingCard, FluentIcon as FIF, isDarkTheme

from fluent_ui.theme import THEMES
from services.i18n import t, i18n_engine

class ColorButton(QToolButton):
    """圆形的颜色选择按钮"""
    def __init__(self, theme_key, is_dark_mode_btn, parent=None):
        super().__init__(parent)
        self.theme_key = theme_key
        self.is_dark_mode_btn = is_dark_mode_btn
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.is_selected = False
        
    def set_selected(self, selected):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()
            
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 强制获取该按钮对应模式下的颜色，不随当前系统深浅色变化
        theme_data = THEMES.get(self.theme_key, THEMES["white"])
        mode_key = "dark" if self.is_dark_mode_btn else "light"
        color = QColor(theme_data[mode_key]["accent"])
        
        # 绘制圆形色块
        rect = QRect(4, 4, 24, 24)
        painter.setBrush(color)
        
        # 如果是浅色模式下的白色主题，或者深色模式下的黑色主题，加一个细边框防止看不清
        if (self.theme_key == "white" and not self.is_dark_mode_btn) or (self.theme_key == "black" and self.is_dark_mode_btn):
            painter.setPen(QPen(QColor(150, 150, 150, 100), 1))
        else:
            painter.setPen(Qt.NoPen)
            
        painter.drawEllipse(rect)
        
        # 绘制选中态（外圈边框）
        if self.is_selected:
            pen = QPen(color, 2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(1, 1, 30, 30)

class ThemeColorSettingCard(SettingCard):
    """双行主题颜色选择卡片"""
    color_changed = Signal(str, str) # 发送 (模式, theme_key)
    
    def __init__(self, title, content, icon, light_theme_key, dark_theme_key, parent=None):
        super().__init__(icon, title, content, parent)
        self.light_theme_key = light_theme_key
        self.dark_theme_key = dark_theme_key
        
        self.light_buttons = {}
        self.dark_buttons = {}
        
        # 创建垂直容器
        self.v_container = QWidget(self)
        self.v_layout = QVBoxLayout(self.v_container)
        self.v_layout.setContentsMargins(0, 12, 0, 12)
        self.v_layout.setSpacing(12)
        
        # 浅色模式行
        self.light_row = QWidget()
        self.light_layout = QHBoxLayout(self.light_row)
        self.light_layout.setContentsMargins(0, 0, 0, 0)
        self.light_layout.setSpacing(8)
        
        self.light_label = QLabel(t("浅色模式主题"))
        self.light_label.setStyleSheet("color: gray; font-size: 12px;")
        self.light_layout.addWidget(self.light_label)
        self.light_layout.addSpacing(8)
        
        for key in THEMES.keys():
            btn = ColorButton(key, is_dark_mode_btn=False, parent=self.light_row)
            btn.setToolTip(t(THEMES[key]["name"]))
            btn.clicked.connect(lambda checked=False, k=key: self._on_light_button_clicked(k))
            self.light_layout.addWidget(btn)
            self.light_buttons[key] = btn
            
        # 深色模式行
        self.dark_row = QWidget()
        self.dark_layout = QHBoxLayout(self.dark_row)
        self.dark_layout.setContentsMargins(0, 0, 0, 0)
        self.dark_layout.setSpacing(8)
        
        self.dark_label = QLabel(t("深色模式主题"))
        self.dark_label.setStyleSheet("color: gray; font-size: 12px;")
        self.dark_layout.addWidget(self.dark_label)
        self.dark_layout.addSpacing(8)
        
        for key in THEMES.keys():
            btn = ColorButton(key, is_dark_mode_btn=True, parent=self.dark_row)
            btn.setToolTip(t(THEMES[key]["name"]))
            btn.clicked.connect(lambda checked=False, k=key: self._on_dark_button_clicked(k))
            self.dark_layout.addWidget(btn)
            self.dark_buttons[key] = btn
            
        self.v_layout.addWidget(self.light_row)
        self.v_layout.addWidget(self.dark_row)
            
        self.hBoxLayout.addWidget(self.v_container, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)
        
        self._update_selection()
            
        # 绑定语言切换信号
        i18n_engine.language_changed.connect(self.update_texts)

    def update_texts(self, lang):
        """动态刷新界面文本"""
        self.light_label.setText(t("浅色模式主题"))
        self.dark_label.setText(t("深色模式主题"))
        for key, btn in self.light_buttons.items():
            btn.setToolTip(t(THEMES[key]["name"]))
        for key, btn in self.dark_buttons.items():
            btn.setToolTip(t(THEMES[key]["name"]))
        
    def _on_light_button_clicked(self, theme_key):
        if self.light_theme_key != theme_key:
            self.light_theme_key = theme_key
            self._update_selection()
            self.color_changed.emit("light", theme_key)
            
    def _on_dark_button_clicked(self, theme_key):
        if self.dark_theme_key != theme_key:
            self.dark_theme_key = theme_key
            self._update_selection()
            self.color_changed.emit("dark", theme_key)
            
    def _update_selection(self):
        for key, btn in self.light_buttons.items():
            btn.set_selected(key == self.light_theme_key)
        for key, btn in self.dark_buttons.items():
            btn.set_selected(key == self.dark_theme_key)
            
    def update_colors(self):
        """当系统深浅色模式改变时，触发按钮重绘以更新颜色"""
        for btn in self.light_buttons.values():
            btn.update()
        for btn in self.dark_buttons.values():
            btn.update()
