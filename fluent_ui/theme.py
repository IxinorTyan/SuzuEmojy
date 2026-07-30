from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme

THEMES = {
    "white":    {"name": "白色",   "light": {"accent": "#5C5C5C", "bg": "#FFFFFF"}, "dark": {"accent": "#A6A6A6", "bg": "#000000"}},
    "black":    {"name": "黑色",   "light": {"accent": "#000000", "bg": "#FFFFFF"}, "dark": {"accent": "#A6A6A6", "bg": "#000000"}},
    "rouge":    {"name": "胭脂红", "light": {"accent": "#C97C86", "bg": "#FCF1EF"}, "dark": {"accent": "#E2897F", "bg": "#201A1F"}},
    "sky":      {"name": "天空蓝", "light": {"accent": "#6FA0D9", "bg": "#EFF5FB"}, "dark": {"accent": "#6FA0E0", "bg": "#161B2A"}},
    "lavender": {"name": "薰衣草紫", "light": {"accent": "#A594D9", "bg": "#F4F1FB"}, "dark": {"accent": "#A594E8", "bg": "#1C1830"}},
    "sage":     {"name": "鼠尾草绿", "light": {"accent": "#82AD8E", "bg": "#F0F5EE"}, "dark": {"accent": "#7FB08F", "bg": "#161F19"}},
    "apricot":  {"name": "暮霞杏橙", "light": {"accent": "#D9A05A", "bg": "#FBF3E7"}, "dark": {"accent": "#D6A15E", "bg": "#241C14"}},
}

def get_current_theme_key(config, is_dark=None):
    if is_dark is None:
        is_dark = isDarkTheme()
    if is_dark:
        return config.get("dark_theme_key", "white")
    else:
        return config.get("light_theme_key", "white")

def get_current_accent_color(config, is_dark=None):
    if is_dark is None:
        is_dark = isDarkTheme()
    
    theme_key = get_current_theme_key(config, is_dark)
    theme_data = THEMES.get(theme_key, THEMES["white"])
    
    mode_key = "dark" if is_dark else "light"
    color_hex = theme_data[mode_key]["accent"]
    return QColor(color_hex)

def get_current_background_color(config, is_dark=None):
    if is_dark is None:
        is_dark = isDarkTheme()
        
    theme_key = get_current_theme_key(config, is_dark)
    theme_data = THEMES.get(theme_key, THEMES["white"])
    
    mode_key = "dark" if is_dark else "light"
    color_hex = theme_data[mode_key]["bg"]
    return QColor(color_hex)