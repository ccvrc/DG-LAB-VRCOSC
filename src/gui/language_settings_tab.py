from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QMessageBox
from PySide6.QtCore import Qt
import logging

from i18n import LANGUAGES, get_current_language, set_language, translate as _, language_signals

logger = logging.getLogger(__name__)

class LanguageSettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        
        # 当前设置
        self.settings = main_window.settings
        self.current_language = self.settings.get('language') or get_current_language()
        
        # 设置布局
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        
        # 语言选择区域
        language_layout = QHBoxLayout()
        self.language_label = QLabel(_("main.settings.language"))
        self.language_label.setMinimumWidth(100)
        
        self.language_combo = QComboBox()
        for lang_code, lang_name in LANGUAGES.items():
            self.language_combo.addItem(lang_name, lang_code)
            
        # 设置当前语言
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == self.current_language:
                self.language_combo.setCurrentIndex(i)
                break
                
        language_layout.addWidget(self.language_label)
        language_layout.addWidget(self.language_combo)
        language_layout.addStretch(1)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        self.save_button = QPushButton(_("main.settings.save"))
        self.cancel_button = QPushButton(_("main.settings.cancel"))
        
        self.save_button.clicked.connect(self.save_settings)
        self.cancel_button.clicked.connect(self.reset_settings)
        
        button_layout.addStretch(1)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        
        # 添加布局
        layout.addLayout(language_layout)
        layout.addStretch(1)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # 监听语言变更信号
        language_signals.language_changed.connect(self.update_ui_texts)
        
    def save_settings(self):
        """保存语言设置"""
        selected_language = self.language_combo.currentData()
        
        if selected_language != self.current_language:
            # 更新设置
            self.settings['language'] = selected_language
            self.current_language = selected_language
            
            # 保存设置到文件
            from config import save_settings
            save_settings(self.settings)
            
            # 设置当前语言 - 这将触发语言变更信号
            set_language(selected_language)
            
            # 显示提示信息（新语言）
            language_name = LANGUAGES.get(selected_language, selected_language)
            QMessageBox.information(
                self,
                _("main.settings.language"),
                f"{_('main.settings.language_changed').format(language=language_name)}"
            )
            
            logger.info(f"Language changed to {language_name} ({selected_language})")
            
    def reset_settings(self):
        """重置为当前设置"""
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == self.current_language:
                self.language_combo.setCurrentIndex(i)
                break
                
    def update_ui_texts(self):
        """更新UI上的文本为当前选择的语言"""
        self.language_label.setText(_("main.settings.language"))
        self.save_button.setText(_("main.settings.save"))
        self.cancel_button.setText(_("main.settings.cancel")) 