import os
import json
import sys
from PySide6.QtCore import QObject, Signal

class _I18nEngine(QObject):
    """
    多语言翻译引擎
    """
    # 当语言切换时发射此信号，UI组件可连接此信号进行局部刷新
    # 机制说明：已经实例化的控件，需要在其 __init__ 中将此信号连接到一个 update_texts() 方法，
    # 在该方法中重新调用 t() 并 setText()。这与主题切换时的重绘机制类似。
    language_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.current_lang = "zh"
        self.translations = {}
        self.config = None
        
        # 确定 translations 目录路径
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
        self.translations_dir = os.path.join(self.base_dir, "translations")
        os.makedirs(self.translations_dir, exist_ok=True)

    def init(self, config_service):
        """初始化引擎并读取配置中的语言设置"""
        self.config = config_service
        lang = self.config.get("language", "zh")
        self.load_language(lang)

    def load_language(self, lang):
        """加载指定语言的 JSON 字典"""
        self.current_lang = lang
        file_path = os.path.join(self.translations_dir, f"{lang}.json")
        
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.translations = json.load(f)
            except Exception as e:
                print(f"[I18N] 读取语言文件 {lang}.json 失败: {e}")
                self.translations = {}
        else:
            self.translations = {}
            
        self.language_changed.emit(lang)

    def set_language(self, lang):
        """切换语言并持久化到配置"""
        if self.current_lang != lang:
            if self.config:
                self.config.set("language", lang)
            self.load_language(lang)

    def t(self, text, context=None):
        """
        获取翻译文本。
        :param text: 中文原文
        :param context: 上下文后缀，用于解决一词多译。例如 t("打开", context="file")
        
        逃生机制说明：
        如果遇到中文相同但英文不同的情况，可以传入 context。
        JSON 字典中对应的 key 为 "原文::上下文"，例如 "打开::文件": "Open"。
        """
        # 如果是中文（基准语言），直接返回原文（忽略 context）
        if self.current_lang == "zh":
            return text
            
        # 构建带上下文的 key
        key = f"{text}::{context}" if context else text
        
        # 1. 尝试精确匹配带上下文的 key
        if key in self.translations:
            return self.translations[key]
            
        # 2. 如果带上下文的 key 没找到，尝试回退到无上下文的通用翻译
        if context and text in self.translations:
            return self.translations[text]
            
        # 3. 兜底机制：显示明显的缺失提示
        return f"[MISSING: {key}]"

# 支持的语言列表 (语言代码, 显示名称)
# 顺序：中文/繁中/英文/日文
SUPPORTED_LANGUAGES = [
    ("zh", "简体中文"),
    ("zh_TW", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語")
]

# 全局单例
i18n_engine = _I18nEngine()
# 导出简写函数
t = i18n_engine.t
