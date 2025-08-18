# src/gui/about_tab.py
import asyncio
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QTextEdit
from PySide6.QtCore import QLocale
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
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
        
        # 按钮布局 - 使用水平布局让两个按钮并排
        self.buttons_layout = QHBoxLayout()
        
        # 检查更新按钮
        self.check_update_btn = QPushButton(_('about_tab.check_update'))
        self.check_update_btn.clicked.connect(self.check_update)
        self.buttons_layout.addWidget(self.check_update_btn)
        
        # 问题反馈按钮
        self.feedback_btn = QPushButton(_('about_tab.feedback'))
        self.feedback_btn.clicked.connect(self.open_feedback)
        self.buttons_layout.addWidget(self.feedback_btn)
        
        # 将按钮布局添加到版本布局中
        self.version_layout.addLayout(self.buttons_layout)
        
        # 自动更新选项
        self.auto_check = QCheckBox(_('about_tab.automatic_update_check'))
        self.auto_check.setChecked(self.main_window.settings.get('auto_update', False))
        self.auto_check.stateChanged.connect(self.toggle_auto_update)
        self.version_layout.addWidget(self.auto_check)
        
        # 贡献信息
        contributors = QTextEdit()
        # 强制使用英文区域设置，避免数字显示为繁体中文
        contributors.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        contributors.setReadOnly(True)
        contributors.setText(
            "开发组织: ccvrc\n\n"
            "贡献者: \n"
            "- icrazt\n"
            "- 光水\n"
            "- icelly_QAQ\n\n"
            "特别感谢:\n"
            "- ChrisFeline (ToNSaveManager)\n"
            "- VRChat OSC 社区\n"
            "- VRSuya SoundPad\n"
            "- WastingMisaka(鱼板)\n"
            "- Wanlin\n"
            "- 所有参与测试、使用本项目及贡献问题反馈的用户\n\n"
            "项目地址: https://github.com/ccvrc/DG-LAB-VRCOSC\n\n"
            "使用的开源项目:\n"
            "- PySide6 (LGPL)\n"
            "- websockets (BSD)\n"
            "- qasync (MIT)\n"
            "- pydglab-ws (BSD)\n"
            "- qrcode (LGPL)\n"
            "- python-osc (MIT)\n"
            "- colorlog (MIT)\n"
            "- pillow (HPND)\n"
            "- pyyaml (MIT)\n"
            "- psutil (BSD)\n"
            "- aiohttp (Apache 2.0)\n"
            "- requests (Apache 2.0)"
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

    def open_feedback(self):
        url = QUrl("https://qiz80xlgzfj.feishu.cn/share/base/form/shrcn5tv1swXYDkg8HZ99BwOWfh")
        QDesktopServices.openUrl(url)

    def update_ui_texts(self):
        """更新UI上的所有文本为当前语言"""
        self.check_update_btn.setText(_('about_tab.check_update'))
        self.feedback_btn.setText(_('about_tab.feedback'))
        # 更新标签文本
        self.auto_check.setText(_('about_tab.automatic_update_check'))
        self.version_layout_label.setText(_('about_tab.current_version') + ": " + self.main_window.update_handler.current_version)
