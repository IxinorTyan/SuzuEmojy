from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme

from services.appearance import THEMES

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
