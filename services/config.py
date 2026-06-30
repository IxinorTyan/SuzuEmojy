import os
import json

class ConfigService:
    def __init__(self):
        import sys
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.config_path = os.path.join(self.base_dir, "data", "config.json")
        
        self.default_config = {
            "always_on_top": True,   # 默认置顶
            "preview_delay": 500,    # 悬停预览延迟 (ms)
            "preview_size": 320      # 悬停预览浮窗大小 (px)
        }
        self.config = self._load_config()

    def _load_config(self):
        if not os.path.exists(self.config_path):
            self._save_config(self.default_config)
            return self.default_config
            
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # 合并默认配置，防止新增字段时报错
                config = self.default_config.copy()
                config.update(loaded)
                return config
        except Exception as e:
            print(f"读取配置失败，使用默认配置: {e}")
            return self.default_config.copy()

    def _save_config(self, config_data):
        try:
            # 确保 data 目录存在
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def get_actual_theme_is_dark(self):
        """获取当前软件应该采用的真实明暗状态"""
        import darkdetect
        theme_mode = self.get("theme_mode", "system")
        if theme_mode == "dark":
            return True
        elif theme_mode == "light":
            return False
        else:
            return darkdetect.isDark()

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self._save_config(self.config)
