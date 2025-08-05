# src/gui/about_tab.py
import asyncio
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QCheckBox, QTextEdit, QMessageBox

class AboutTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        
        layout = QVBoxLayout()
        
        # 版本信息
        version_layout = QVBoxLayout()
        version_layout.addWidget(QLabel(f"当前版本: {self.main_window.update_handler.current_version}"))
        
        # 检查更新按钮
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.clicked.connect(self.check_update)
        version_layout.addWidget(self.check_update_btn)
        
        # 自动更新选项
        self.auto_check = QCheckBox("启用自动检查更新")
        self.auto_check.setChecked(self.main_window.settings.get('auto_update', False))
        self.auto_check.stateChanged.connect(self.toggle_auto_update)
        version_layout.addWidget(self.auto_check)
        
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
        
        layout.addLayout(version_layout)
        layout.addWidget(contributors)
        self.setLayout(layout)
    
    def toggle_auto_update(self, state):
        self.main_window.settings['auto_update'] = state == 2  # Qt.Checked状态值为2
        self.main_window.save_settings()

    def check_update(self):
        # 这里调用的是MainWindow的实例方法
        asyncio.create_task(self.main_window.check_update_manual())
