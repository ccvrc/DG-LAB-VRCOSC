from PySide6.QtWidgets import (QWidget, QGroupBox, QFormLayout, QComboBox, QSpinBox,
                               QLabel, QPushButton, QHBoxLayout)
from PySide6.QtCore import Qt
import logging
import asyncio

from config import get_active_ip_addresses, save_settings
from pydglab_ws import DGLabWSServer, RetCode, StrengthData, FeedbackButton
from dglab_controller import DGLabController
from qasync import asyncio
from pythonosc import osc_server, dispatcher, udp_client
from i18n import translate as _, language_signals

import functools # Use the built-in functools module
import sys
import os
import qrcode
import io
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

class NetworkConfigTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.layout = QHBoxLayout(self)
        self.setLayout(self.layout)

        # 创建网络配置组
        self.network_config_group = QGroupBox(_("network_tab.title"))
        self.form_layout = QFormLayout()

        # 网卡选择
        self.ip_combobox = QComboBox()
        active_ips = get_active_ip_addresses()
        for interface, ip in active_ips.items():
            self.ip_combobox.addItem(f"{interface}: {ip}")
        self.form_layout.addRow(_("network_tab.interface") + ":", self.ip_combobox)

        # 端口选择
        self.port_spinbox = QSpinBox()
        self.port_spinbox.setRange(1024, 65535)
        self.port_spinbox.setValue(self.main_window.settings['port'])  # Set the default or loaded value
        self.form_layout.addRow(_("network_tab.websocket_port") + ":", self.port_spinbox)

        # OSC端口选择
        self.osc_port_spinbox = QSpinBox()
        self.osc_port_spinbox.setRange(1024, 65535)
        self.osc_port_spinbox.setValue(self.main_window.settings['osc_port'])  # Set the default or loaded value
        self.form_layout.addRow(_("network_tab.osc_port") + ":", self.osc_port_spinbox)

        # 创建 dispatcher 和地址处理器字典
        self.dispatcher = dispatcher.Dispatcher()
        self.osc_address_handlers = {}  # 自定义 OSC 地址的处理器
        self.panel_control_handlers = {}  # 面板控制 OSC 地址的处理器

        # 添加客户端连接状态标签
        self.connection_status_label = QLabel(_("network_tab.offline"))
        self.connection_status_label.setAlignment(Qt.AlignCenter)  # 设置内容居中
        self.connection_status_label.setStyleSheet("""
            QLabel {
                background-color: red;
                color: white;
                border-radius: 5px;  # 圆角
                padding: 5px;
            }
        """)
        self.connection_status_label.adjustSize()  # 调整大小以适应内容
        self.form_layout.addRow(_("network_tab.status") + ":", self.connection_status_label)

        # 启动按钮
        self.start_button = QPushButton(_("network_tab.connect"))
        self.start_button.setStyleSheet("background-color: green; color: white;")  # 设置按钮初始为绿色
        self.start_button.clicked.connect(self.start_server_button_clicked)
        self.form_layout.addRow(self.start_button)

        self.network_config_group.setLayout(self.form_layout)

        # 将网络配置组添加到布局
        self.layout.addWidget(self.network_config_group)

        # 二维码显示
        self.qrcode_label = QLabel(self)
        self.layout.addWidget(self.qrcode_label)

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
            if len(interface_ip) == 2:
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
        self.start_button.setText(_("network_tab.disconnect"))  # 修改按钮文本
        self.start_button.setStyleSheet("background-color: grey; color: white;")  # 将按钮置灰
        self.start_button.setEnabled(False)  # 禁用按钮
        self.start_server()  # 调用现有的启动服务器逻辑

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
            logger.error(error_message)
            # Update the UI to reflect the error
            self.start_button.setText("启动失败,请重试")
            self.start_button.setStyleSheet("background-color: red; color: white;")
            self.start_button.setEnabled(True)
            # 记录异常日志
            logger.error(f"服务器启动过程中发生异常: {str(e)}")

    async def run_server(self, ip: str, port: int, osc_port: int):
        """运行服务器并启动OSC服务器"""
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
                # 确保UI状态与控制器状态同步
                self.main_window.controller_settings_tab.sync_from_controller()

                # 设置 OSC 服务器
                osc_server_instance = osc_server.AsyncIOOSCUDPServer(
                    ("0.0.0.0", osc_port), self.dispatcher, asyncio.get_event_loop()
                )
                osc_transport, osc_protocol = await osc_server_instance.create_serve_endpoint()
                logger.info(f"OSC Server Listening on port {osc_port}")

                # 连接 addresses_updated 信号到 update_osc_mappings 方法
                self.main_window.osc_parameters_tab.addresses_updated.connect(self.update_osc_mappings)
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
                        # 重连成功后重置波形更新时间，强制下一次循环重新发送波形
                        controller.pulse_last_update_time = {} 
                        # 同步UI状态到控制器
                        self.main_window.controller_settings_tab.sync_from_controller()
                    else:
                        logger.info(f"获取到状态码：{RetCode}")

                osc_transport.close()
        except OSError as e:
            # Handle specific errors and log them
            error_message = f"WebSocket 服务器启动失败: {str(e)}"
            logger.error(error_message)

            # 启动过程中发生异常，恢复按钮状态为可点击的红色
            self.start_button.setText("启动失败，请重试")
            self.start_button.setStyleSheet("background-color: red; color: white;")
            self.start_button.setEnabled(True)
            self.main_window.log_viewer_tab.log_text_edit.append(f"ERROR: {error_message}")



    def generate_qrcode(self, data: str):
        """生成二维码并转换为PySide6可显示的QPixmap"""
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=2)
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
        """更新二维码并调整QLabel的大小"""
        self.qrcode_label.setPixmap(qrcode_pixmap)
        self.qrcode_label.setFixedSize(qrcode_pixmap.size())  # 根据二维码尺寸调整QLabel大小
        logger.info("二维码已更新")

    def update_connection_status(self, is_online):
        self.main_window.app_status_online = is_online
        """根据设备连接状态更新标签的文本和颜色"""
        if is_online:
            self.connection_status_label.setText(_("network_tab.online"))
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
            self.main_window.controller_settings_tab.command_types_group.setEnabled(True)  # 启用命令类型控制
            self.main_window.ton_damage_system_tab.damage_group.setEnabled(True)
            # 确保UI状态与控制器状态同步
            self.main_window.controller_settings_tab.sync_from_controller()
        else:
            self.connection_status_label.setText(_("network_tab.offline"))
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
            self.main_window.controller_settings_tab.command_types_group.setEnabled(False)  # 禁用命令类型控制
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
            # 确保有映射范围参数
            mapping_ranges = addr.get('mapping_ranges', {
                'A': {'min': 0, 'max': 100},
                'B': {'min': 0, 'max': 100}
            })
            handler = functools.partial(self.handle_osc_message_task_pb_with_channels, 
                                        controller=controller, 
                                        channels=channels,
                                        mapping_ranges=mapping_ranges)
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
        logger.info("OSC dispatcher mappings updated with panel control addresses.")

    def handle_osc_message_task_pad(self, address, *args, controller):
        """将OSC命令传递给控制器队列处理机制"""
        logger.info(f"收到OSC消息 (面板控制): {address} {args}")
        asyncio.create_task(controller.handle_osc_message_pad(address, *args))

    def handle_osc_message_task_pb_with_channels(self, address, *args, controller, channels, mapping_ranges=None):
        """将OSC命令传递给控制器队列处理机制，带通道信息和映射范围"""
        # 确保channels参数格式统一
        channel_list = []
        if isinstance(channels, dict):
            # 将字典格式 {'A': True, 'B': False} 转换为列表格式 ['A']
            if channels.get('A', False):
                channel_list.append('A')
            if channels.get('B', False):
                channel_list.append('B')
        elif isinstance(channels, list):
            # 如果已经是列表格式，直接使用
            channel_list = channels
        
        logger.info(f"收到OSC消息 (参数绑定): {address} {args} 通道: {channel_list}")
        asyncio.create_task(controller.handle_osc_message_pb(address, *args, channels=channel_list, mapping_ranges=mapping_ranges))

    def update_ui_texts(self):
        """更新所有UI文本为当前语言"""
        # 更新分组框标题
        self.network_config_group.setTitle(_("network_tab.title"))
        
        # 更新表单标签
        for i in range(self.form_layout.rowCount()):
            label_item = self.form_layout.itemAt(i, QFormLayout.LabelRole)
            if label_item and label_item.widget():
                label_widget = label_item.widget()
                if isinstance(label_widget, QLabel):
                    label_text = label_widget.text()
                    if label_text.startswith("选择网卡") or "Interface" in label_text:
                        label_widget.setText(_("network_tab.interface") + ":")
                    elif "WebSocket" in label_text or "端口" in label_text:
                        label_widget.setText(_("network_tab.websocket_port") + ":")
                    elif "OSC" in label_text:
                        label_widget.setText(_("network_tab.osc_port") + ":")
                    elif "状态" in label_text or "Status" in label_text:
                        label_widget.setText(_("network_tab.status") + ":")
        
        # 更新状态标签
        if self.main_window.app_status_online:
            self.connection_status_label.setText(_("network_tab.online"))
        else:
            self.connection_status_label.setText(_("network_tab.offline"))
            
        # 更新按钮文本
        if self.start_button.isEnabled():
            self.start_button.setText(_("network_tab.connect"))
        else:
            self.start_button.setText(_("network_tab.disconnect"))
