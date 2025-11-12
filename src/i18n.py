import os
import sys
import yaml
import logging
from typing import Dict, Any
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

def resource_path(relative_path):
    """获取资源的绝对路径，确保开发和打包后都能正常使用"""
    if hasattr(sys, '_MEIPASS'):  # PyInstaller 打包后的路径
        return os.path.join(sys._MEIPASS, relative_path)
    # 开发环境下的路径
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

# 语言变更信号类
class LanguageSignals(QObject):
    language_changed = Signal(str)  # 参数是新的语言代码

# 创建信号实例
language_signals = LanguageSignals()

# 支持的语言
LANGUAGES = {
    'zh': '中文',
    'en': 'English',
    'ja': '日本語',  # 添加日语支持
}

# 默认语言
DEFAULT_LANGUAGE = 'zh'
_current_language = DEFAULT_LANGUAGE
_translations: Dict[str, Dict[str, Any]] = {}

def load_translations():
    """加载所有可用语言的翻译文件"""
    global _translations

    logger.info(f"当前工作目录: {os.getcwd()}")
    logger.info(f"当前文件目录: {os.path.dirname(os.path.abspath(__file__))}")

    for lang_code in LANGUAGES.keys():
        # 使用 resource_path 函数来获取翻译文件路径
        translation_file = resource_path(f'locales/{lang_code}.yml')

        # 输出调试信息
        logger.info(f"尝试加载翻译文件: {translation_file}")
        logger.info(f"文件是否存在: {os.path.exists(translation_file)}")

        if not os.path.exists(translation_file):
            logger.warning(f"Translation file {translation_file} not found, creating default one")
            _translations[lang_code] = {}
            continue
            
        try:
            with open(translation_file, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info(f"文件内容前100个字符: {content[:100]}")
                _translations[lang_code] = yaml.safe_load(content) or {}
                logger.info(f"Loaded {len(_translations[lang_code])} translations for {lang_code}")
                # 输出加载的所有键
                if _translations[lang_code]:
                    logger.info(f"加载的顶级键: {list(_translations[lang_code].keys())}")
                    # 如果包含 main 键，输出其子键
                    if 'main' in _translations[lang_code]:
                        logger.info(f"main 子键: {list(_translations[lang_code]['main'].keys())}")
                        if 'settings' in _translations[lang_code]['main']:
                            logger.info(f"main.settings 子键: {list(_translations[lang_code]['main']['settings'].keys())}")
        except Exception as e:
            logger.error(f"Error loading translation file {translation_file}: {e}")
            _translations[lang_code] = {}

def get_current_language():
    """获取当前使用的语言代码"""
    return _current_language

def set_language(lang_code):
    """设置当前使用的语言"""
    global _current_language
    if lang_code in LANGUAGES:
        _current_language = lang_code
        logger.info(f"Language set to {LANGUAGES[lang_code]} ({lang_code})")
        # 发送语言变更信号
        language_signals.language_changed.emit(lang_code)
        return True
    else:
        logger.warning(f"Language {lang_code} not supported")
        return False

def translate(text_id, lang=None):
    """
    根据文本ID获取翻译
    如果指定语言没有找到翻译，则使用默认语言
    如果默认语言也没有翻译，则返回文本ID本身
    """
    lang = lang or _current_language
    
    # 如果翻译文件还没有加载
    if not _translations:
        load_translations()
    
    # 处理嵌套的键（使用点分隔）
    parts = text_id.split('.')
    
    # 遍历翻译字典，尝试获取嵌套值
    def get_nested_value(d, keys):
        if not keys:
            return d
        if not isinstance(d, dict):
            return None
        key = keys[0]
        if key not in d:
            return None
        if len(keys) == 1:
            return d[key]
        return get_nested_value(d[key], keys[1:])
    
    # 尝试获取翻译
    if lang in _translations:
        value = get_nested_value(_translations[lang], parts)
        if value is not None:
            return value
    
    # 如果没有找到翻译，尝试使用默认语言
    if lang != DEFAULT_LANGUAGE and DEFAULT_LANGUAGE in _translations:
        value = get_nested_value(_translations[DEFAULT_LANGUAGE], parts)
        if value is not None:
            return value
    
    # 如果都找不到，返回文本ID本身
    logger.debug(f"未找到翻译: {text_id}")
    return text_id

# 初始化时加载翻译
load_translations() 