from PySide6.QtWidgets import (QWidget, QGroupBox, QFormLayout, QComboBox, QSpinBox,
                               QLabel, QPushButton, QHBoxLayout, QSizePolicy)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtCore import QTimer
import logging
import asyncio
import functools
import sys
import os
import qrcode
import io
import time

project_root = os.path.dirname(os.path.dirname(__file__))
sys.path.append(project_root)

from config import get_active_ip_addresses, save_settings
from dglab_controller import DGLabController
from pydglab_ws import DGLabWSServer, RetCode, StrengthData, FeedbackButton
from pythonosc import osc_server, dispatcher, udp_client

logger = logging.getLogger(__name__)

class NetworkConfigTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.original_qrcode_pixmap = None  # 保存原始二维码图像

        # 主布局使用QHBoxLayout
        self.layout = QHBoxLayout(self)
        self.setLayout(self.layout)

        # 创建网络配置组（左侧固定部分）
        self.network_config_group = QGroupBox("网络配置")
        self.form_layout = QFormLayout()

        # 网卡选择
        self.ip_combobox = QComboBox()
        active_ips = get_active_ip_addresses()
        for interface, ip in active_ips.items():
            self.ip_combobox.addItem(f"{interface}: {ip}")
        self.form_layout.addRow("选择网卡:", self.ip_combobox)

        # 端口选择
        self.port_spinbox = QSpinBox()
        self.port_spinbox.setRange(1024, 65535)
        self.port_spinbox.setValue(self.main_window.settings['port'])  # Set the default or loaded value
        self.form_layout.addRow("WS连接端口:", self.port_spinbox)

        # OSC端口选择
        self.osc_port_spinbox = QSpinBox()
        self.osc_port_spinbox.setRange(1024, 65535)
        self.osc_port_spinbox.setValue(self.main_window.settings['osc_port'])  # Set the default or loaded value
        self.form_layout.addRow("OSC接收端口:", self.osc_port_spinbox)

        # 创建 dispatcher 和地址处理器字典
        self.dispatcher = dispatcher.Dispatcher()
        self.osc_address_handlers = {}  # 自定义 OSC 地址的处理器
        self.panel_control_handlers = {}  # 面板控制 OSC 地址的处理器

        # 添加客户端连接状态标签
        self.connection_status_label = QLabel("未连接, 请在点击启动后扫描二维码连接")
        self.connection_status_label.setAlignment(Qt.AlignCenter)  # 设置内容居中
        self.connection_status_label.setStyleSheet("""
            QLabel {
                background-color: red;
                color: white;
                border-radius: 5px;
                padding: 5px;
            }
        """)
        self.connection_status_label.adjustSize()  # 调整大小以适应内容
        self.form_layout.addRow("客户端连接状态:", self.connection_status_label)

        # 启动按钮
        self.start_button = QPushButton("启动")
        self.start_button.setStyleSheet("background-color: green; color: white;")  # 设置按钮初始为绿色
        self.start_button.clicked.connect(self.start_server_button_clicked)
        self.form_layout.addRow(self.start_button)

        self.network_config_group.setLayout(self.form_layout)
        
        # 将网络配置组添加到布局，设置stretch=0
        self.layout.addWidget(self.network_config_group, 0)  # stretch=0保持固定宽度

        # 二维码显示（右侧伸缩部分）
        self.qrcode_label = QLabel(self)
        self.qrcode_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.qrcode_label.setAlignment(Qt.AlignCenter)  # 居中显示
        self.layout.addWidget(self.qrcode_label, 1)  # stretch=1占据剩余空间

        # 二维码标签的尺寸策略强化
        self.qrcode_label.setSizePolicy(
            QSizePolicy.Expanding,  # 水平策略：尽可能扩展
            QSizePolicy.Expanding  # 垂直策略
        )
        self.qrcode_label.setMinimumSize(100, 100)  # 设置最小可显示尺寸

        # Apply loaded settings to the UI components
        self.apply_settings_to_ui()

        # Save settings whenever network configuration is changed
        self.ip_combobox.currentTextChanged.connect(self.save_network_settings)
        self.port_spinbox.valueChanged.connect(self.save_network_settings)
        self.osc_port_spinbox.valueChanged.connect(self.save_network_settings)

    def apply_settings_to_ui(self):
        """Apply the loaded settings to the UI elements."""
        # Find the correct index for the loaded interface and IP
        for i in range(self.ip_combobox.count()):
            interface_ip = self.ip_combobox.itemText(i).split(": ")
            if len(interface_ip) == 2 and all(interface_ip):
                interface, ip = interface_ip
                if interface == self.main_window.settings['interface'] and ip == self.main_window.settings['ip']:
                    self.ip_combobox.setCurrentIndex(i)
                    logger.info("set to previous used network interface")
                    break

    def save_network_settings(self):
        """Save network settings to the settings.yml file."""
        selected_interface_ip = self.ip_combobox.currentText().split(": ")
        if len(selected_interface_ip) == 2:
            selected_interface, selected_ip = selected_interface_ip
            selected_port = self.port_spinbox.value()
            osc_port = self.osc_port_spinbox.value()
            self.main_window.settings['interface'] = selected_interface
            self.main_window.settings['ip'] = selected_ip
            self.main_window.settings['port'] = selected_port
            self.main_window.settings['osc_port'] = osc_port

            save_settings(self.main_window.settings)
            logger.info("Network settings saved.")

    def start_server_button_clicked(self):
        """启动按钮被点击后的处理逻辑"""
        try:
            self.start_button.setText("已启动")  # 修改按钮文本
            self.start_button.setStyleSheet("background-color: grey; color: white;")  # 将按钮置灰
            self.start_button.setEnabled(False)  # 禁用按钮
            self.start_server()  # 调用现有的启动服务器逻辑
        except Exception as e:
            error_message = f"启动服务器失败: {str(e)}"
            logger.error(error_message, exc_info=True)
            self.start_button.setText("启动失败，请重试")
            self.start_button.setStyleSheet("background-color: red; color: white;")
            self.start_button.setEnabled(True)

    def start_server(self):
        """启动 WebSocket 服务器"""
        selected_ip = self.ip_combobox.currentText().split(": ")[-1]
        selected_port = self.port_spinbox.value()
        osc_port = self.osc_port_spinbox.value()
        logger.info(
            f"正在启动 WebSocket 服务器，监听地址: {selected_ip}:{selected_port} 和 OSC 数据接收端口: {osc_port}")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.run_server(selected_ip, selected_port, osc_port))
            logger.info('WebSocket 服务器已启动')
            # After starting the server, connect the addresses_updated signal
            self.main_window.osc_parameters_tab.addresses_updated.connect(self.update_osc_mappings)
            # 启动成功后，将按钮设为灰色并禁用
            self.start_button.setText("已启动")
            self.start_button.setStyleSheet("background-color: grey; color: white;")
            self.start_button.setEnabled(False)
        except OSError as e:
            error_message = f"启动服务器失败: {str(e)}"
            # Log the error with error level
            logger.error(error_message, exc_info=True)
            # Update the UI to reflect the error
            self.start_button.setText("启动失败，请重试")
            self.start_button.setStyleSheet("background-color: red; color: white;")
            self.start_button.setEnabled(True)
            # 记录异常日志
            logger.error(f"服务器启动过程中发生异常: {str(e)}")

    async def run_server(self, ip: str, port: int, osc_port: int):
        """运行服务器并启动OSC服务器"""
        osc_transport = None
        try:
            async with DGLabWSServer(ip, port, 60) as server:
                client = server.new_local_client()
                logger.info("WebSocket 客户端已初始化")

                # Generate QR code
                url = client.get_qrcode(f"ws://{ip}:{port}")
                qrcode_image = self.generate_qrcode(url)
                self.update_qrcode(qrcode_image)
                logger.info(f"二维码已生成，WebSocket URL: ws://{ip}:{port}")

                osc_client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
                # Initialize controller
                controller = DGLabController(client, osc_client, self.main_window)
                self.main_window.controller = controller
                logger.info("DGLabController 已初始化")
                # After controller initialization, bind settings
                self.main_window.controller_settings_tab.bind_controller_settings()

                # 设置 OSC 服务器
                osc_server_instance = osc_server.AsyncIOOSCUDPServer(
                    ("0.0.0.0", osc_port), self.dispatcher, asyncio.get_event_loop()
                )
                osc_transport, osc_protocol = await osc_server_instance.create_serve_endpoint()
                logger.info(f"OSC Server Listening on port {osc_port}")

                # 连接 addresses_updated 信号到 update_osc_mappings 方法
                if not hasattr(self.main_window.osc_parameters_tab, 'addresses_updated_connected'):
                    self.main_window.osc_parameters_tab.addresses_updated.connect(self.update_osc_mappings)
                    self.main_window.osc_parameters_tab.addresses_updated_connected = True

                # 初始化 OSC 映射，包括面板控制和自定义地址
                self.update_osc_mappings(controller)

                # Start the data processing loop
                async for data in client.data_generator():
                    if isinstance(data, StrengthData):
                        logger.info(f"接收到数据包 - A通道: {data.a}, B通道: {data.b}")
                        controller.last_strength = data
                        controller.data_updated_event.set()  # 数据更新，触发开火操作的后续事件
                        controller.app_status_online = True
                        self.main_window.app_status_online = True
                        self.update_connection_status(controller.app_status_online)
                        # Update UI components related to strength data
                        self.main_window.controller_settings_tab.update_channel_strength_labels(data)
                    elif isinstance(data, FeedbackButton):
                        logger.info(f"App 触发了反馈按钮：{data.name}")
                    elif data == RetCode.CLIENT_DISCONNECTED:
                        logger.info("App 已断开连接，你可以尝试重新扫码进行连接绑定")
                        controller.app_status_online = False
                        self.main_window.app_status_online = False
                        self.update_connection_status(controller.app_status_online)
                        await client.rebind()
                        logger.info("重新绑定成功")
                        controller.app_status_online = True
                        self.update_connection_status(controller.app_status_online)
                    else:
                        logger.info(f"获取到状态码：{RetCode}")

        except Exception as e:
            error_message = f"WebSocket 服务器启动失败: {str(e)}"
            logger.error(error_message, exc_info=True)

            # 启动过程中发生异常，恢复按钮状态为可点击的红色
            self.start_button.setText("启动失败，请重试")
            self.start_button.setStyleSheet("background-color: red; color: white;")
            self.start_button.setEnabled(True)
            self.main_window.log_viewer_tab.log_text_edit.append(f"ERROR: {error_message}")
        finally:
            if osc_transport:
                osc_transport.close()

    def handle_osc_message_task_pad(self, address, *args, controller=None):
        """处理带通道参数的OSC消息（新增方法）"""
        if controller is None:
            controller = self.main_window.controller
        asyncio.run_coroutine_threadsafe(
            controller.handle_osc_message_pad(address, *args),
            asyncio.get_event_loop()
        )

    def handle_osc_message_task_pb(self, address, *args, controller=None, channels=None):
        """处理带通道参数的OSC消息（新增方法）"""
        if controller is None:
            controller = self.main_window.controller
        asyncio.run_coroutine_threadsafe(
            controller.handle_osc_message_pb(address, *args, channels=channels),
            asyncio.get_event_loop()
        )

    def generate_qrcode(self, data: str):
        """生成二维码并转换为PySide6可显示的QPixmap"""
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=16, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        qimage = QPixmap()
        qimage.loadFromData(buffer.read(), 'PNG')

        return qimage

    def update_qrcode(self, qrcode_pixmap):
        """更新二维码并保存原始图像"""
        self.original_qrcode_pixmap = qrcode_pixmap
        self.scale_qrcode()
        logger.info("二维码已更新")

    def scale_qrcode(self):
        """根据当前标签尺寸缩放二维码"""
        if self.original_qrcode_pixmap and not self.original_qrcode_pixmap.isNull():
            scaled_pixmap = self.original_qrcode_pixmap.scaled(
                self.qrcode_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.qrcode_label.setPixmap(scaled_pixmap)


    def resizeEvent(self, event):
        """优化窗口缩放处理"""
        # 先执行父类的resize事件处理
        super().resizeEvent(event)
        # 延迟执行二维码缩放以保证尺寸计算准确
        QTimer.singleShot(0, self.scale_qrcode)

    def update_connection_status(self, is_online):
        self.main_window.app_status_online = is_online
        """根据设备连接状态更新标签的文本和颜色"""
        if is_online:
            self.connection_status_label.setText("已连接")
            self.connection_status_label.setStyleSheet("""
                QLabel {
                    background-color: green;
                    color: white;
                    border-radius: 5px;
                    padding: 5px;
                }
            """)
            # 启用 DGLabController 设置
            self.main_window.controller_settings_tab.controller_group.setEnabled(True)  # 启用控制器设置
            self.main_window.ton_damage_system_tab.damage_group.setEnabled(True)
        else:
            self.connection_status_label.setText("未连接")
            self.connection_status_label.setStyleSheet("""
                QLabel {
                    background-color: red;
                    color: white;
                    border-radius: 5px;
                    padding: 5px;
                }
            """)
            # 禁用 DGLabController 设置
            self.main_window.controller_settings_tab.controller_group.setEnabled(False)  # 禁用控制器设置
            self.main_window.ton_damage_system_tab.damage_group.setEnabled(False)
        self.connection_status_label.adjustSize()  # 根据内容调整标签大小

    def update_osc_mappings(self, controller=None):
        if controller is None:
            controller = self.main_window.controller
        asyncio.run_coroutine_threadsafe(self._update_osc_mappings(controller), asyncio.get_event_loop())

    async def _update_osc_mappings(self, controller):
        # 首先，移除之前的自定义 OSC 地址映射
        for address, handler in self.osc_address_handlers.items():
            self.dispatcher.unmap(address, handler)
        self.osc_address_handlers.clear()

        # 添加新的自定义 OSC 地址映射
        osc_addresses = self.main_window.get_osc_addresses()
        for addr in osc_addresses:
            address = addr['address']
            channels = addr['channels']
            handler = functools.partial(self.handle_osc_message_task_pb_with_channels, controller=controller, channels=channels)
            self.dispatcher.map(address, handler)
            self.osc_address_handlers[address] = handler
        logger.info("OSC dispatcher mappings updated with custom addresses.")

        # 确保面板控制的 OSC 地址映射被添加（如果尚未添加）
        if not self.panel_control_handlers:
            self.add_panel_control_mappings(controller)

    def add_panel_control_mappings(self, controller):
        # 添加面板控制功能的 OSC 地址映射
        panel_addresses = [
            "/avatar/parameters/SoundPad/Button/*",
            "/avatar/parameters/SoundPad/Volume",
            "/avatar/parameters/SoundPad/Page",
            "/avatar/parameters/SoundPad/PanelControl"
        ]
        for address in panel_addresses:
            handler = functools.partial(self.handle_osc_message_task_pad, controller=controller)
            self.dispatcher.map(address, handler)
            self.panel_control_handlers[address] = handler


    def handle_osc_message_task_pb_with_channels(self, address, *args, controller, channels):
        asyncio.create_task(controller.handle_osc_message_pb(address, *args, channels=channels))
