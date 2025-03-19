import sys
import asyncio
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from qasync import QEventLoop
import logging

from config import load_settings
from logger_config import setup_logging

# 导入事件总线与中间件
from event_bus import get_event_bus, EventType
from event_middleware import get_middleware

# 导入设备和服务适配器
from device_adapter import init_device_adapter, get_device_adapter
from osc_adapter import init_osc_adapter, get_osc_adapter
from ton_websocket_adapter import init_ton_adapter, get_ton_adapter

# 导入DG-LAB控制器
from dglab_controller import DGLabController

# 导入各种GUI组件
from gui.network_config_tab import NetworkConfigTab
from gui.controller_settings_tab import ControllerSettingsTab
from gui.ton_damage_system_tab import TonDamageSystemTab
from gui.log_viewer_tab import LogViewerTab
from gui.osc_parameters import OSCParametersTab

setup_logging()
# 配置日志记录器
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
        self.setGeometry(300, 300, 650, 600)

        # 设置窗口图标
        self.setWindowIcon(QIcon(resource_path('docs/images/fish-cake.ico')))

        # 加载设置或使用默认值
        self.settings = load_settings() or {
            'interface': '',
            'ip': '',
            'port': 5678,
            'osc_port': 9001
        }

        # 初始化事件总线和中间件
        self.event_bus = get_event_bus()
        self.middleware = get_middleware()
        
        # 初始化设备控制器和适配器
        self.controller = None
        self.app_status_online = False

        # 创建标签页控件
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        # 创建标签页并传递对MainWindow的引用
        self.network_config_tab = NetworkConfigTab(self)
        self.controller_settings_tab = ControllerSettingsTab(self)
        self.ton_damage_system_tab = TonDamageSystemTab(self)
        self.log_viewer_tab = LogViewerTab(self)
        self.osc_parameters_tab = OSCParametersTab(self)

        # 添加标签页到标签页控件
        self.tab_widget.addTab(self.network_config_tab, "网络配置")
        self.tab_widget.addTab(self.controller_settings_tab, "控制器设置")
        self.tab_widget.addTab(self.osc_parameters_tab, "OSC参数配置")
        self.tab_widget.addTab(self.ton_damage_system_tab, "ToN游戏联动")
        self.tab_widget.addTab(self.log_viewer_tab, "日志查看")

        # 设置日志输出到日志查看器
        self.app_setup_logging()
        
        # 注册事件监听器
        self.event_bus.on(EventType.OUTPUT_CHATBOX_MESSAGE, self.handle_chatbox_message)
        
        # 使用QTimer在事件循环启动后延迟初始化适配器
        QTimer.singleShot(0, self.schedule_init_adapters)

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
        """更新当前选择的通道显示"""
        self.controller_settings_tab.update_current_channel_display(channel_name)

    def get_osc_addresses(self):
        """获取OSC地址配置"""
        return self.osc_parameters_tab.get_addresses()
    
    async def handle_chatbox_message(self, message):
        """处理ChatBox消息事件"""
        # 这个方法不需要实际做什么，因为OSC适配器会自动处理
        # 但我们可以在这里记录日志
        logger.debug(f"ChatBox消息: {message}")
    
    async def init_adapters(self):
        """初始化适配器"""
        try:
            # 初始化OSC适配器
            osc_port = self.settings.get('osc_port', 9001)
            self.osc_adapter = init_osc_adapter('0.0.0.0', osc_port)
            
            # 初始化ToN WebSocket适配器
            ws_url = f"ws://{self.settings.get('ip', 'localhost')}:{self.settings.get('port', 5678)}"
            self.ton_adapter = init_ton_adapter(ws_url)
            
            # 启动OSC服务器
            await self.osc_adapter.start()
            
            # 通知初始化完成
            logger.info("适配器初始化完成")
        except Exception as e:
            logger.error(f"初始化适配器时出错: {e}")
    
    def schedule_init_adapters(self):
        """在事件循环中安排初始化适配器的执行"""
        loop = asyncio.get_event_loop()
        loop.create_task(self.init_adapters())
        logger.info("已安排适配器初始化任务")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()
