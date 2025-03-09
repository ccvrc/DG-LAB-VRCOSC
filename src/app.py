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
        self.setWindowTitle("DG-Lab WebSocket Controller for VRChat")
        self.setGeometry(300, 300, 800, 470)

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
        self.tab_widget.addTab(self.network_config_tab, "网络配置")
        self.tab_widget.addTab(self.controller_settings_tab, "控制器设置")
        self.tab_widget.addTab(self.osc_parameters_tab, "OSC参数配置")
        self.tab_widget.addTab(self.ton_damage_system_tab, "ToN游戏联动")
        self.tab_widget.addTab(self.log_viewer_tab, "日志查看")
        self.tab_widget.addTab(self.about_tab, "关于")


        
        if self.settings.get('auto_update', False):
            asyncio.create_task(self.auto_update_check())



        # Setup logging to the log viewer
        self.app_setup_logging()

    def app_setup_logging(self):
        """设置日志系统输出到 QTextEdit 和控制台"""
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # 创建 QTextEditHandler 并添加到日志系统中
        if not any(isinstance(handler, type(self.log_viewer_tab.log_handler)) for handler in logger.handlers):
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



if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()
