# src/gui/about_tab.py
import asyncio
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QCheckBox, QTextEdit, QMessageBox
# from i18n import translate, language_signals
from i18n import translate as _, language_signals

class AboutTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        
        layout = QVBoxLayout()
        
        # 版本信息
        self.version_layout = QVBoxLayout()
        self.version_layout_label = QLabel(_('about_tab.current_version') + ": " + self.main_window.update_handler.current_version)
        self.version_layout.addWidget(self.version_layout_label)
        
        # 检查更新按钮
        self.check_update_btn = QPushButton(_('about_tab.check_update'))
        self.check_update_btn.clicked.connect(self.check_update)
        self.version_layout.addWidget(self.check_update_btn)
        
        # 自动更新选项
        self.auto_check = QCheckBox(_('about_tab.automatic_update_check'))
        self.auto_check.setChecked(self.main_window.settings.get('auto_update', False))
        self.auto_check.stateChanged.connect(self.toggle_auto_update)
        self.version_layout.addWidget(self.auto_check)
        
        # 贡献信息
        contributors = QTextEdit()
        contributors.setReadOnly(True)
        contributors.setText(
            "开发者: ccvrc\n\n"
            "特别感谢:\n"
            "- ChrisFeline (ToNSaveManager)\n"
            "- VRChat OSC 社区\n\n"
            "开源项目地址: https://github.com/ccvrc/DG-LAB-VRCOSC\n\n"
            "使用开源项目:\n"
            "- PySide6 (LGPL)\n"
            "- websockets (BSD)\n"
            "- qasync (MIT)"
        )
        
        layout.addLayout(self.version_layout)
        layout.addWidget(contributors)
        self.setLayout(layout)
    
    def toggle_auto_update(self, state):
        self.main_window.settings['auto_update'] = state == 2  # Qt.Checked状态值为2
        self.main_window.save_settings()

    def check_update(self):
        # 防止多次点击
        if not self.check_update_btn.isEnabled():
            return
        self.check_update_btn.setEnabled(False)
        async def do_check():
            try:
                await self.main_window.check_update_manual()
            finally:
                self.check_update_btn.setEnabled(True)
        asyncio.create_task(do_check())

    def update_ui_texts(self):
        """更新UI上的所有文本为当前语言"""
        self.check_update_btn.setText(_('about_tab.check_update'))
        # 更新标签文本
        self.auto_check.setText(_('about_tab.automatic_update_check'))
        self.version_layout_label.setText(_('about_tab.current_version') + ": " + self.main_window.update_handler.current_version)
