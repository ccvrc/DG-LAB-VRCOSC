import sys
import asyncio
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QMessageBox
from PySide6.QtGui import QIcon
from qasync import QEventLoop
import logging

from functools import partial
from config import load_settings
from logger_config import setup_logging
from i18n import set_language, translate as _, language_signals
from update_handler import UpdateHandler, UpdateDialog
# Import the GUI modules
from gui.network_config_tab import NetworkConfigTab
from gui.controller_settings_tab import ControllerSettingsTab
from gui.ton_damage_system_tab import TonDamageSystemTab
from gui.log_viewer_tab import LogViewerTab
from gui.osc_parameters import OSCParametersTab
from gui.about_tab import AboutTab

from qasync import asyncSlot

setup_logging()
# Configure the logger
logger = logging.getLogger(__name__)

def resource_path(relative_path):
    """ 获取资源的绝对路径，确保开发和打包后都能正常使用。 """
    if hasattr(sys, '_MEIPASS'):  # PyInstaller 打包后的路径
        return os.path.join(sys._MEIPASS, relative_path)
    # 对于开发环境下，从 src 跳到项目根目录，再进入 docs/images
    return os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), relative_path)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Load settings from file or use defaults
        self.settings = load_settings()
        
        # 设置语言
        if 'language' in self.settings:
            set_language(self.settings['language'])
            
        self.setWindowTitle(_("main.title"))
        self.setGeometry(300, 300, 900, 470)

        # 设置窗口图标
        self.setWindowIcon(QIcon(resource_path('docs/images/fish-cake.ico')))

        default_settings = {
            'interface': '',
            'ip': '',
            'port': 5678,
            'osc_port': 9001,
            'auto_update': False
        }

        self.settings = load_settings() or {}
        for key, value in default_settings.items():
            self.settings.setdefault(key, value)
        # Load settings from file or use defaults

                # 初始化更新处理器
        self.update_handler = UpdateHandler(
            current_version="v0.1.0",  # 需要从配置读取
            config=self.settings
        )

        # Set initial controller to None
        self.controller = None
        self.app_status_online = False

        # Create the tab widget
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        # Create tabs and pass reference to MainWindow
        self.network_config_tab = NetworkConfigTab(self)
        self.controller_settings_tab = ControllerSettingsTab(self)
        self.ton_damage_system_tab = TonDamageSystemTab(self)
        self.log_viewer_tab = LogViewerTab(self)
        self.osc_parameters_tab = OSCParametersTab(self)
        self.about_tab = AboutTab(self)

        # Add tabs to the tab widget
        self.tab_widget.addTab(self.network_config_tab, _("main.tabs.network"))
        self.tab_widget.addTab(self.controller_settings_tab, _("main.tabs.controller"))
        self.tab_widget.addTab(self.osc_parameters_tab, _("main.tabs.osc"))
        self.tab_widget.addTab(self.ton_damage_system_tab, _("main.tabs.ton"))
        self.tab_widget.addTab(self.log_viewer_tab, _("main.tabs.log"))
        self.tab_widget.addTab(self.about_tab, "关于")


        
        if self.settings.get('auto_update', False):
            asyncio.create_task(self.auto_update_check())



        # Setup logging to the log viewer
        self.app_setup_logging()
        
        # 监听语言变更信号
        language_signals.language_changed.connect(self.update_ui_language)

    def app_setup_logging(self):
        """设置日志系统输出到 QTextEdit 和控制台"""
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # 创建 QTextEditHandler 并添加到日志系统中
        self.log_handler = self.log_viewer_tab.log_handler
        logger.addHandler(self.log_handler)

        # 限制日志框中的最大行数
        self.log_viewer_tab.log_text_edit.textChanged.connect(lambda: self.limit_log_lines(max_lines=100))

    def limit_log_lines(self, max_lines=500):
        """限制 QTextEdit 中的最大行数，保留颜色和格式，并保持显示最新日志"""
        self.log_viewer_tab.limit_log_lines(max_lines)

    def update_current_channel_display(self, channel_name):
        """Update current selected channel display."""
        self.controller_settings_tab.update_current_channel_display(channel_name)

    def get_osc_addresses(self):
        return self.osc_parameters_tab.get_addresses()
    

    async def auto_update_check(self):
        result = await self.update_handler.check_update(manual_check=False)
        if result and result["available"]:
            self.show_update_dialog(result["release_info"])

    async def check_update_manual(self):
        """手动检查更新的入口方法"""
        try:
            # 使用async with创建独立任务上下文
            async with asyncio.TaskGroup() as tg:
                task = tg.create_task(self.update_handler.check_update(manual_check=True))
                result = await task
        except ExceptionGroup as e:
            logger.error(f"更新检查异常: {e}")
            return

        if result:
            if result["available"]:
                print("更新可用")
                self.show_update_dialog(result["release_info"])
                
            else:
                QMessageBox.information(self, "检查更新", result["message"])



    def show_update_dialog(self, release_info):
            print("更新信息:", release_info)
            dialog = UpdateDialog(self, release_info)

            async def on_update_clicked():
                try:
                    print("开始更新")
                    
                    await self.update_handler.start_download(release_info, dialog)
                except Exception as e:
                    QMessageBox.critical(self, "错误", str(e))

            def __on_update_clicked():
                asyncio.create_task(on_update_clicked())
            
            dialog.update_btn.clicked.connect(__on_update_clicked)
            dialog.exec()

    def save_settings(self):
        """保存配置到文件"""
        # 这里需要实现具体的保存逻辑，根据你的config.py实现
        # 示例：
        from config import save_settings
        save_settings(self.settings)


        
    def update_ui_language(self):
        """更新UI上的所有文本为当前语言"""
        # 更新窗口标题
        self.setWindowTitle(_("main.title"))
        
        # 更新选项卡标题
        self.tab_widget.setTabText(0, _("main.tabs.network"))
        self.tab_widget.setTabText(1, _("main.tabs.controller"))
        self.tab_widget.setTabText(2, _("main.tabs.osc"))
        self.tab_widget.setTabText(3, _("main.tabs.ton"))
        self.tab_widget.setTabText(4, _("main.tabs.log"))
        
        # 通知各个选项卡更新其UI
        # 通过发送信号或调用各选项卡的更新方法来实现
        if hasattr(self.network_config_tab, 'update_ui_texts'):
            self.network_config_tab.update_ui_texts()
        if hasattr(self.controller_settings_tab, 'update_ui_texts'):
            self.controller_settings_tab.update_ui_texts()
        if hasattr(self.ton_damage_system_tab, 'update_ui_texts'):
            self.ton_damage_system_tab.update_ui_texts()
        if hasattr(self.log_viewer_tab, 'update_ui_texts'):
            self.log_viewer_tab.update_ui_texts()
        if hasattr(self.osc_parameters_tab, 'update_ui_texts'):
            self.osc_parameters_tab.update_ui_texts()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()
