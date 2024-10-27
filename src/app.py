import math
import sys
import asyncio
import io
import os
import qrcode
import logging
import json
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
                               QPushButton, QComboBox, QSpinBox, QFormLayout, QGroupBox, QSlider,
                               QTextEdit, QCheckBox, QToolTip, QProgressBar)
from PySide6.QtGui import QPixmap, QIcon, QTextCursor, QColor
from PySide6.QtCore import Qt, QByteArray, QTimer, QPoint
from qasync import QEventLoop
from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from config import load_settings, save_settings, get_active_ip_addresses
from pythonosc import dispatcher, osc_server, udp_client

from dglab_controller import DGLabController
from pulse_data import PULSE_NAME
from logger_config import setup_logging
from ton_websocket_handler import WebSocketClient

setup_logging()
# 配置日志记录器
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QTextEditHandler(logging.Handler):
    """Custom log handler to output log messages to QTextEdit."""
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit

    def emit(self, record):
        msg = self.format(record)
        # Highlight error logs in red
        if record.levelno >= logging.ERROR:
            msg = f"<b style='color:red;'>{msg}</b>"  # Display error messages in red
        elif record.levelno == logging.WARNING:
            msg = f"<b style='color:orange;'>{msg}</b>"  # Display warnings in orange
        else:
            msg = f"<span>{msg}</span>"  # 默认使用普通字体
        # Append the message to the text edit and reset the cursor position
        self.text_edit.append(msg)
        self.text_edit.ensureCursorVisible()  # Ensure the latest log is visible

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
        self.setGeometry(300, 300, 650, 900)

        # 设置窗口图标
        self.setWindowIcon(QIcon(resource_path('docs/images/fish-cake.ico')))

        # Load settings from file or use defaults
        self.settings = load_settings() or {
            'interface': '',
            'ip': '',
            'port': 5678,
            'osc_port': 9001
        }
        # 创建主布局
        self.layout = QVBoxLayout()

        # # 创建一个水平布局，用于放置网络配置和二维码
        self.network_layout = QHBoxLayout()

        # 创建网络配置组
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
        self.port_spinbox.setValue(self.settings['port'])  # Set the default or loaded value
        self.form_layout.addRow("WS连接端口:", self.port_spinbox)

        # OSC端口选择
        self.osc_port_spinbox = QSpinBox()
        self.osc_port_spinbox.setRange(1024, 65535)
        self.osc_port_spinbox.setValue(self.settings['osc_port'])  # Set the default or loaded value
        self.form_layout.addRow("OSC接收端口:", self.osc_port_spinbox)

        # 添加客户端连接状态标签
        self.connection_status_label = QLabel("未连接, 请在点击启动后扫描二维码连接")
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
        self.form_layout.addRow("客户端连接状态:", self.connection_status_label)

        # 启动按钮
        self.start_button = QPushButton("启动")
        self.start_button.setStyleSheet("background-color: green; color: white;")  # 设置按钮初始为绿色
        self.start_button.clicked.connect(self.start_server_button_clicked)
        self.form_layout.addRow(self.start_button)


        self.network_config_group.setLayout(self.form_layout)

        # 将网络配置组添加到水平布局
        self.network_layout.addWidget(self.network_config_group)

        # 二维码显示
        self.qrcode_label = QLabel(self)
        self.network_layout.addWidget(self.qrcode_label)

        # 将水平布局添加到主布局
        self.layout.addLayout(self.network_layout)

        # 控制器参数设置
        self.controller_group = QGroupBox("DGLabController 设置")
        self.controller_group.setEnabled(False)  # 默认禁用
        self.controller_form = QFormLayout()

        # 添加 A 通道滑动条和标签
        self.a_channel_label = QLabel("A 通道强度: 0 / 100")  # 默认显示
        self.a_channel_slider = QSlider(Qt.Horizontal)
        self.a_channel_slider.setRange(0, 100)  # 默认范围
        self.a_channel_slider.valueChanged.connect(self.set_a_channel_strength)
        self.a_channel_slider.sliderPressed.connect(self.disable_a_channel_updates)  # 用户开始拖动时禁用外部更新
        self.a_channel_slider.sliderReleased.connect(self.enable_a_channel_updates)  # 用户释放时重新启用外部更新
        self.a_channel_slider.valueChanged.connect(lambda: self.show_tooltip(self.a_channel_slider))  # 实时显示提示
        self.controller_form.addRow(self.a_channel_label)
        self.controller_form.addRow(self.a_channel_slider)

        # 添加 B 通道滑动条和标签
        self.b_channel_label = QLabel("B 通道强度: 0 / 100")  # 默认显示
        self.b_channel_slider = QSlider(Qt.Horizontal)
        self.b_channel_slider.setRange(0, 100)  # 默认范围
        self.b_channel_slider.valueChanged.connect(self.set_b_channel_strength)
        self.b_channel_slider.sliderPressed.connect(self.disable_b_channel_updates)  # 用户开始拖动时禁用外部更新
        self.b_channel_slider.sliderReleased.connect(self.enable_b_channel_updates)  # 用户释放时重新启用外部更新
        self.b_channel_slider.valueChanged.connect(lambda: self.show_tooltip(self.b_channel_slider))  # 实时显示提示
        self.controller_form.addRow(self.b_channel_label)
        self.controller_form.addRow(self.b_channel_slider)

        # 控制滑动条外部更新的状态标志
        self.allow_a_channel_update = True
        self.allow_b_channel_update = True

        # 是否启用面板控制
        self.enable_panel_control_checkbox = QCheckBox("允许 avatar 控制设备") # PanelControl 关闭后忽略所有游戏内传入的控制
        self.enable_panel_control_checkbox.setChecked(True)
        self.controller_form.addRow(self.enable_panel_control_checkbox)

        # ChatBox状态开关
        self.enable_chatbox_status_checkbox = QCheckBox("启用ChatBox状态显示")
        self.enable_chatbox_status_checkbox.setChecked(False)
        self.controller_form.addRow(self.enable_chatbox_status_checkbox)

        self.controller_group.setLayout(self.controller_form)
        self.layout.addWidget(self.controller_group)

        # 创建水平布局用于放置 dynamic_bone_mode 和 current_select_channel 显示
        dynamic_bone_layout = QHBoxLayout()

        # 动骨模式选择
        self.dynamic_bone_mode_a_checkbox = QCheckBox("A通道交互模式")
        self.dynamic_bone_mode_b_checkbox = QCheckBox("B通道交互模式")

        # 添加复选框到水平布局
        dynamic_bone_layout.addWidget(self.dynamic_bone_mode_a_checkbox)
        dynamic_bone_layout.addWidget(self.dynamic_bone_mode_b_checkbox)

        # 在同行右侧增加 current_select_channel 显示标签
        self.current_channel_label = QLabel("面板当前控制通道: 未设置")
        dynamic_bone_layout.addWidget(self.current_channel_label)

        # 将水平布局添加到主布局
        self.controller_form.addRow(dynamic_bone_layout)

        # 波形模式选择
        self.pulse_mode_a_combobox = QComboBox()
        self.pulse_mode_b_combobox = QComboBox()
        for pulse_name in PULSE_NAME:
            self.pulse_mode_a_combobox.addItem(pulse_name)
            self.pulse_mode_b_combobox.addItem(pulse_name)
        self.controller_form.addRow("A通道波形模式:", self.pulse_mode_a_combobox)
        self.controller_form.addRow("B通道波形模式:", self.pulse_mode_b_combobox)

        # 强度步长
        self.strength_step_spinbox = QSpinBox()
        self.strength_step_spinbox.setRange(0, 100)
        self.strength_step_spinbox.setValue(30)
        self.controller_form.addRow("开火强度步长:", self.strength_step_spinbox)

        # TON
        # Damage System UI
        self.damage_group = QGroupBox("Terrors of Nowhere")
        self.damage_group.setEnabled(False)
        self.damage_layout = QFormLayout()

        self.damage_info_layout = QHBoxLayout()
        # Enable Damage System Checkbox
        self.enable_damage_checkbox = QCheckBox("ToN Damage System")
        self.enable_damage_checkbox.stateChanged.connect(self.toggle_damage_system)
        self.damage_info_layout.addWidget(self.enable_damage_checkbox)  # 使用 addWidget() 而不是 addRow()

        # 增加用于显示 DisplayName 的标签
        self.display_name_label = QLabel("User Display Name: 未知")  # 默认显示为 "未知"
        self.damage_info_layout.addWidget(self.display_name_label)  # 使用 addWidget() 而不是 addRow()

        # WebSocket Status Label
        self.websocket_status_label = QLabel("WebSocket Status: Disconnected")
        self.damage_info_layout.addWidget(self.websocket_status_label)  # 使用 addWidget() 而不是 addRow()

        # 将水平布局添加到主布局中
        self.damage_layout.addRow(self.damage_info_layout)

        # Damage Progress Bar
        self.damage_progress_bar = QProgressBar()
        self.damage_progress_bar.setRange(0, 100)
        self.damage_progress_bar.setValue(0)  # Initial damage is 0%
        self.damage_layout.addRow("累计伤害:", self.damage_progress_bar)

        # 统一滑动条的宽度
        slider_max_width = 450

        # 创建横向布局，用于伤害减免滑动条和标签
        self.damage_reduction_layout = QHBoxLayout()
        self.damage_reduction_label = QLabel("每秒伤害减免强度: 2 / 10")  # 默认显示
        self.damage_reduction_slider = QSlider(Qt.Horizontal)
        self.damage_reduction_slider.setRange(0, 10)
        self.damage_reduction_slider.setValue(2)  # Default reduction strength per second
        self.damage_reduction_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.damage_reduction_slider.valueChanged.connect(
            lambda value: self.damage_reduction_label.setText(f"每秒伤害减免强度: {value} / 10"))
        self.damage_reduction_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.damage_reduction_slider))  # 实时显示提示
        self.damage_reduction_layout.addWidget(self.damage_reduction_label)
        self.damage_reduction_layout.addWidget(self.damage_reduction_slider)
        self.damage_reduction_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.damage_reduction_layout)

        # 创建横向布局，用于伤害强度滑动条和标签
        self.damage_strength_layout = QHBoxLayout()
        self.damage_strength_label = QLabel("伤害对应强度上限: 50 / 200")  # 默认显示
        self.damage_strength_slider = QSlider(Qt.Horizontal)
        self.damage_strength_slider.setRange(0, 200)
        self.damage_strength_slider.setValue(60)  # Default strength multiplier
        self.damage_strength_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.damage_strength_slider.valueChanged.connect(
            lambda value: self.damage_strength_label.setText(f"伤害对应强度上限: {value} / 100"))
        self.damage_strength_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.damage_strength_slider))  # 实时显示提示
        self.damage_strength_layout.addWidget(self.damage_strength_label)
        self.damage_strength_layout.addWidget(self.damage_strength_slider)
        self.damage_strength_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.damage_strength_layout)

        # 创建横向布局，用于死亡惩罚强度滑动条和标签
        self.death_penalty_strength_layout = QHBoxLayout()
        self.death_penalty_strength_label = QLabel("死亡惩罚强度: 30 / 100")  # 默认显示
        self.death_penalty_strength_slider = QSlider(Qt.Horizontal)
        self.death_penalty_strength_slider.setRange(0, 100)
        self.death_penalty_strength_slider.setValue(30)  # Default death penalty strength is 100%
        self.death_penalty_strength_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.death_penalty_strength_slider.valueChanged.connect(
            lambda value: self.death_penalty_strength_label.setText(f"死亡惩罚强度: {value} / 100"))
        self.death_penalty_strength_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.death_penalty_strength_slider))  # 实时显示提示
        self.death_penalty_strength_layout.addWidget(self.death_penalty_strength_label)
        self.death_penalty_strength_layout.addWidget(self.death_penalty_strength_slider)
        self.death_penalty_strength_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.death_penalty_strength_layout)

        # 死亡惩罚持续时间
        self.death_penalty_time_spinbox = QSpinBox()
        self.death_penalty_time_spinbox.setRange(0, 60)
        self.death_penalty_time_spinbox.setValue(5)  # Default penalty time is 10 seconds
        self.damage_layout.addRow("死亡惩罚持续时间 (s):", self.death_penalty_time_spinbox)



        self.damage_group.setLayout(self.damage_layout)
        self.layout.addWidget(self.damage_group)

        # Main Timer for Damage Reduction
        self.damage_timer = QTimer(self)
        self.damage_timer.timeout.connect(self.reduce_damage)

        # WebSocket Client (Initialized as None)
        self.websocket_client = None

        # 日志
        # 日志显示框 - 使用 QGroupBox 包装
        self.log_groupbox = QGroupBox("简约日志")
        self.log_groupbox.setCheckable(True)
        self.log_groupbox.setChecked(True)
        self.log_groupbox.toggled.connect(self.toggle_log_display)

        # 日志显示框
        self.log_text_edit = QTextEdit(self)
        self.log_text_edit.setReadOnly(True)

        # 将日志显示框添加到 GroupBox 的布局中
        log_layout = QVBoxLayout()
        log_layout.addWidget(self.log_text_edit)
        self.log_groupbox.setLayout(log_layout)

        # 将 GroupBox 添加到主布局
        self.layout.addWidget(self.log_groupbox)

        # 启动日志记录系统
        self.app_setup_logging()

        # 增加可折叠的调试界面
        self.debug_group = QGroupBox("调试信息")
        self.debug_group.setCheckable(True)
        self.debug_group.setChecked(False)  # 默认折叠状态
        self.debug_group.toggled.connect(self.toggle_debug_info)  # 连接信号槽

        self.debug_layout = QVBoxLayout()
        self.debug_label = QLabel("DGLabController 参数:")
        self.debug_layout.addWidget(self.debug_label)

        # 显示控制器的参数
        self.param_label = QLabel("正在加载控制器参数...")
        self.debug_layout.addWidget(self.param_label)

        self.debug_group.setLayout(self.debug_layout)
        self.layout.addWidget(self.debug_group)

        # 设置窗口布局
        container = QWidget()
        container.setLayout(self.layout)
        self.setCentralWidget(container)

        # 设置日志处理器
        self.log_handler = QTextEditHandler(self.log_text_edit)
        logger.addHandler(self.log_handler)
        logger.setLevel(logging.INFO)

        # 设置 controller 初始为 None
        self.controller = None
        self.app_status_online = False

        # 启动定时器，每秒刷新一次调试信息
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_debug_info)
        self.timer.start(1000)  # 每秒刷新一次

        # Connect UI to controller update methods
        self.strength_step_spinbox.valueChanged.connect(self.update_strength_step)
        self.enable_panel_control_checkbox.stateChanged.connect(self.update_panel_control)
        self.dynamic_bone_mode_a_checkbox.stateChanged.connect(self.update_dynamic_bone_mode_a)
        self.dynamic_bone_mode_b_checkbox.stateChanged.connect(self.update_dynamic_bone_mode_b)
        self.pulse_mode_a_combobox.currentIndexChanged.connect(self.update_pulse_mode_a)
        self.pulse_mode_b_combobox.currentIndexChanged.connect(self.update_pulse_mode_b)
        self.enable_chatbox_status_checkbox.stateChanged.connect(self.update_chatbox_status)

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
                if interface == self.settings['interface'] and ip == self.settings['ip']:
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
            self.settings['interface'] = selected_interface
            self.settings['ip'] = selected_ip
            self.settings['port'] = selected_port
            self.settings['osc_port'] = osc_port

            save_settings(self.settings)
            logger.info("Network settings saved.")

    def update_qrcode(self, qrcode_pixmap):
        """更新二维码并调整QLabel的大小"""
        self.qrcode_label.setPixmap(qrcode_pixmap)
        self.qrcode_label.setFixedSize(qrcode_pixmap.size())  # 根据二维码尺寸调整QLabel大小
        logger.info("二维码已更新")

    def update_status(self, strength_data):
        """更新通道强度和波形"""
        logger.info(f"通道状态已更新 - A通道强度: {strength_data.a}, B通道强度: {strength_data.b}")

        if self.controller and self.controller.last_strength:
            # 仅当允许外部更新时更新 A 通道滑动条
            if self.allow_a_channel_update:
                self.a_channel_slider.blockSignals(True)
                self.a_channel_slider.setRange(0, self.controller.last_strength.a_limit)  # 根据限制更新范围
                self.a_channel_slider.setValue(self.controller.last_strength.a)
                self.a_channel_slider.blockSignals(False)
                self.a_channel_label.setText(
                    f"A 通道强度: {self.controller.last_strength.a} 强度上限: {self.controller.last_strength.a_limit}  波形: {PULSE_NAME[self.controller.pulse_mode_a]}")

            # 仅当允许外部更新时更新 B 通道滑动条
            if self.allow_b_channel_update:
                self.b_channel_slider.blockSignals(True)
                self.b_channel_slider.setRange(0, self.controller.last_strength.b_limit)  # 根据限制更新范围
                self.b_channel_slider.setValue(self.controller.last_strength.b)
                self.b_channel_slider.blockSignals(False)
                self.b_channel_label.setText(
                    f"B 通道强度: {self.controller.last_strength.b} 强度上限: {self.controller.last_strength.b_limit}  波形: {PULSE_NAME[self.controller.pulse_mode_b]}")

    def update_connection_status(self, is_online):
        self.app_status_online = is_online
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
            self.controller_group.setEnabled(True)  # 启用控制器设置
            self.damage_group.setEnabled(True)
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
            self.controller_group.setEnabled(False)  # 禁用控制器设置
            self.damage_group.setEnabled(False)
        self.connection_status_label.adjustSize()  # 根据内容调整标签大小

    def disable_a_channel_updates(self):
        """禁用 A 通道的外部更新"""
        self.allow_a_channel_update = False

    def enable_a_channel_updates(self):
        """启用 A 通道的外部更新"""
        self.allow_a_channel_update = True
        self.set_a_channel_strength(self.a_channel_slider.value())  # 用户释放时，更新设备

    def disable_b_channel_updates(self):
        """禁用 B 通道的外部更新"""
        self.allow_b_channel_update = False

    def enable_b_channel_updates(self):
        """启用 B 通道的外部更新"""
        self.allow_b_channel_update = True
        self.set_b_channel_strength(self.b_channel_slider.value())  # 用户释放时，更新设备

    def start_server_button_clicked(self):
        """启动按钮被点击后的处理逻辑"""
        self.start_button.setText("已启动")  # 修改按钮文本
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
            loop.create_task(run_server(self, selected_ip, selected_port, osc_port))
            logger.info('WebSocket 服务器已启动')
            # 关闭调试界面
            self.toggle_debug_info(False)
            # 启动成功后，将按钮设为灰绿色并禁用
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

    def update_debug_info(self):
        """更新调试信息"""
        if self.controller:
            params = (
                f"Device online: app_status_online= {self.controller.app_status_online}\n "
                f"Enable Panel Control: {self.controller.enable_panel_control}\n"
                f"Dynamic Bone Mode A: {self.controller.is_dynamic_bone_mode_a}\n"
                f"Dynamic Bone Mode B: {self.controller.is_dynamic_bone_mode_b}\n"
                f"Pulse Mode A: {self.controller.pulse_mode_a}\n"
                f"Pulse Mode B: {self.controller.pulse_mode_b}\n"
                f"Fire Mode Strength Step: {self.controller.fire_mode_strength_step}\n"
                f"Enable ChatBox Status: {self.controller.enable_chatbox_status}\n"
                f"GUI Parameters:\n"
                f"A  strength allow update:{self.allow_a_channel_update}\n"
                f"B  strength allow update:{self.allow_a_channel_update}\n"
            )
            self.param_label.setText(params)
        else:
            self.param_label.setText("控制器未初始化.")

    def toggle_debug_info(self, checked):
        """当调试组被启用/禁用时折叠或展开内容"""
        # 控制调试信息组中所有子组件的可见性，而不是整个调试组
        for child in self.debug_group.findChildren(QWidget):
            child.setVisible(checked)

    def bind_controller_settings(self):
        """将GUI设置与DGLabController变量绑定"""
        if self.controller:
            self.controller.fire_mode_strength_step = self.strength_step_spinbox.value()
            self.controller.enable_panel_control = self.enable_panel_control_checkbox.isChecked()
            self.controller.is_dynamic_bone_mode_a = self.dynamic_bone_mode_a_checkbox.isChecked()
            self.controller.is_dynamic_bone_mode_b = self.dynamic_bone_mode_b_checkbox.isChecked()
            self.controller.pulse_mode_a = self.pulse_mode_a_combobox.currentIndex()
            self.controller.pulse_mode_b = self.pulse_mode_b_combobox.currentIndex()
            self.controller.enable_chatbox_status = self.enable_chatbox_status_checkbox.isChecked()
            logger.info("DGLabController 参数已绑定")
        else:
            logger.warning("Controller is not initialized yet.")

    # Controller update methods
    def update_strength_step(self, value):
        if self.controller:
            self.controller.fire_mode_strength_step = value
            logger.info(f"Updated strength step to {value}")
            self.controller.send_value_to_vrchat("/avatar/parameters/SoundPad/Volume", 0.01*value)

    def update_panel_control(self, state):
        if self.controller:
            self.controller.enable_panel_control = bool(state)
            logger.info(f"Panel control enabled: {self.controller.enable_panel_control}")
            self.controller.send_value_to_vrchat("/avatar/parameters/SoundPad/PanelControl", bool(state))

    def update_dynamic_bone_mode_a(self, state):
        if self.controller:
            self.controller.is_dynamic_bone_mode_a = bool(state)
            logger.info(f"Dynamic bone mode A: {self.controller.is_dynamic_bone_mode_a}")

    def update_dynamic_bone_mode_b(self, state):
        if self.controller:
            self.controller.is_dynamic_bone_mode_b = bool(state)
            logger.info(f"Dynamic bone mode B: {self.controller.is_dynamic_bone_mode_b}")

    def update_pulse_mode_a(self, index):
        if self.controller:
            asyncio.create_task(self.controller.set_pulse_data(None,Channel.A,index))
            logger.info(f"Pulse mode A updated to {PULSE_NAME[index]}")

    def update_pulse_mode_b(self, index):
        if self.controller:
            asyncio.create_task(self.controller.set_pulse_data(None, Channel.B, index))
            logger.info(f"Pulse mode B updated to {PULSE_NAME[index]}")

    def update_chatbox_status(self, state):
        if self.controller:
            self.controller.enable_chatbox_status = bool(state)
            logger.info(f"ChatBox status enabled: {self.controller.enable_chatbox_status}")

    def set_a_channel_strength(self, value):
        """根据滑动条的值设定 A 通道强度"""
        if self.controller:
            asyncio.create_task(self.controller.client.set_strength(Channel.A, StrengthOperationType.SET_TO, value))
            self.controller.last_strength.a = value  # 同步更新 last_strength 的 A 通道值
            self.a_channel_slider.setToolTip(f"SET A 通道强度: {value}")

    def set_b_channel_strength(self, value):
        """根据滑动条的值设定 B 通道强度"""
        if self.controller:
            asyncio.create_task(self.controller.client.set_strength(Channel.B, StrengthOperationType.SET_TO, value))
            self.controller.last_strength.b = value  # 同步更新 last_strength 的 B 通道值
            self.b_channel_slider.setToolTip(f"SET B 通道强度: {value}")

    def show_tooltip(self, slider):
        """显示滑动条当前值的工具提示在滑块上方"""
        value = slider.value()

        # 获取滑块的位置
        slider_min = slider.minimum()
        slider_max = slider.maximum()
        slider_range = slider_max - slider_min
        slider_length = slider.width()  # 滑条的总长度

        # 计算滑块的位置
        slider_pos = (value - slider_min) / slider_range * slider_length

        # 滑块的位置转换为全局坐标，并计算显示位置
        global_pos = slider.mapToGlobal(slider.rect().topLeft())
        tooltip_x = global_pos.x() + slider_pos - 15  # 调整 tooltip 水平位置，使其居中
        tooltip_y = global_pos.y() - 40  # 调整 tooltip 垂直位置，使其显示在滑块上方

        # 显示提示框
        QToolTip.showText(QPoint(tooltip_x, tooltip_y), f"{value}", slider)

    class SimpleFormatter(logging.Formatter):
        """自定义格式化器，将日志级别缩写并调整时间格式"""

        def format(self, record):
            level_short = {
                'DEBUG': 'D',
                'INFO': 'I',
                'WARNING': 'W',
                'ERROR': 'E',
                'CRITICAL': 'C'
            }.get(record.levelname, 'I')  # 默认 INFO
            record.levelname = level_short
            return super().format(record)

    def app_setup_logging(self):
        """设置日志系统输出到 QTextEdit 和控制台"""
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # 创建 QTextEditHandler 并添加到日志系统中
        self.log_handler = QTextEditHandler(self.log_text_edit)
        self.log_handler.setLevel(logging.DEBUG)  # 捕获所有日志级别

        # 使用自定义格式化器，简化时间和日志级别
        formatter = self.SimpleFormatter('%(asctime)s-%(levelname)s: %(message)s', datefmt='%H:%M:%S')
        self.log_handler.setFormatter(formatter)

        # 添加 QTextEditHandler 到 logger
        logger.addHandler(self.log_handler)

        # 限制日志框中的最大行数
        self.log_text_edit.textChanged.connect(lambda: self.limit_log_lines(max_lines=100))

    def limit_log_lines(self, max_lines=500):
        """限制 QTextEdit 中的最大行数，保留颜色和格式，并保持显示最新日志"""
        document = self.log_text_edit.document()
        block_count = document.blockCount()
        cursor = self.log_text_edit.textCursor()
        # 如果当前行数超过最大行数
        if block_count > max_lines:
            cursor.movePosition(QTextCursor.Start)  # 移动到文本开头

            # 选择并删除前面的行，直到行数符合要求
            for _ in range(block_count - max_lines):
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()  # 删除行后保留格式
        # 无论是否删除行，都移动光标到文本末尾
        cursor.movePosition(QTextCursor.End)
        self.log_text_edit.setTextCursor(cursor)
        # 确保最新日志可见
        self.log_text_edit.ensureCursorVisible()

    # 当 GroupBox 展开时，显示日志内容；当折叠时，隐藏日志框
    def toggle_log_display(self, enabled):
        """折叠或展开日志显示框"""
        if enabled:
            self.log_text_edit.show()  # 展开时显示日志框
        else:
            self.log_text_edit.hide()  # 折叠时隐藏日志框

    def toggle_damage_system(self, enabled):
        """Enable or disable the damage system, including WebSocket connection."""
        if enabled:
            logger.info("Enabling damage system and starting WebSocket connection.")
            # Start WebSocket connection and damage timer
            self.websocket_client = WebSocketClient("ws://localhost:11398")
            self.websocket_client.status_update_signal.connect(self.handle_websocket_status_update)
            self.websocket_client.message_received.connect(self.handle_websocket_message)
            self.websocket_client.error_signal.connect(self.handle_websocket_error)
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(self.websocket_client.start_connection(), loop)
            self.damage_timer.start(1000)  # Reduce damage every second
        else:
            logger.info("Disabling damage system and closing WebSocket connection.")
            # Stop WebSocket connection and damage timer
            if self.websocket_client:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(self.websocket_client.close(), loop)
                self.websocket_client = None
            self.damage_timer.stop()
            self.reset_damage()
            self.websocket_status_label.setText("WebSocket Status: 未连接")
            self.websocket_status_label.setStyleSheet("color: red;")

    def reduce_damage(self):
        """Reduce the accumulated damage based on the set reduction strength every second."""
        reduction_strength = self.damage_reduction_slider.value()
        current_value = self.damage_progress_bar.value()
        new_value = max(0, current_value - reduction_strength)  # Ensure damage does not go below 0%
        new_strength = math.floor(0.01 * new_value * self.damage_strength_slider.value())
        self.damage_progress_bar.setValue(new_value)
        if current_value > 0:
            logger.info(f"Damage reduced by {reduction_strength}%. Current damage: {new_value}%")
        if self.app_status_online and self.controller.last_strength.a is not new_value and not self.controller.fire_mode_active:
            asyncio.create_task(self.controller.client.set_strength(Channel.A, StrengthOperationType.SET_TO, new_strength))

    def handle_websocket_message(self, message):
        """Handle incoming WebSocket messages and update status or damage accordingly."""
        logger.info(f"Received WebSocket message: {message}")

        # 如果消息是字符串类型，尝试解析为 JSON
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                logger.error("Received message is not valid JSON format.")
                return

        # 处理不同类型的消息
        if message.get("Type") == "DAMAGED":
            damage_value = message.get("Value", 0)  # 确保获取大小写正确的 "Value"
            self.accumulate_damage(damage_value)
        elif message.get("Type") == "SAVED":
            self.reset_damage()
            logger.info("存档更新，重置强度")
        elif message.get("Type") == "ALIVE":
            is_alive = message.get("Value", 0)
            if not is_alive:
                asyncio.create_task(self.trigger_death_penalty())
                logger.info("已死亡，触发死亡惩罚")
        elif message.get("Type") == "STATS":
            if message.get("DisplayName"):
                user_display_name = message.get("DisplayName")
                self.display_name_label.setText(f"User Display Name: {user_display_name}")
        elif message.get("Type") == "CONNECTED":
            if message.get("DisplayName"):
                user_display_name = message.get("DisplayName")
                self.display_name_label.setText(f"User Display Name: {user_display_name}")


    def handle_websocket_status_update(self, status):
        """Update WebSocket status label based on connection status."""
        logger.info(f"WebSocket status updated: {status}")
        # Log the exact value of the status for better debugging
        if status.lower() == "connected":
            self.websocket_status_label.setText("WebSocket Status: 已连接")
            self.websocket_status_label.setStyleSheet("color: green;")
        elif status.lower() == "disconnected":
            self.websocket_status_label.setText("WebSocket Status: 未连接")
            self.websocket_status_label.setStyleSheet("color: red;")
        else:
            logger.warning(f"Unexpected WebSocket status: {status}")
            self.websocket_status_label.setText(f"WebSocket Status: 错误 - {status}")
            self.websocket_status_label.setStyleSheet("color: orange;")

    def handle_websocket_error(self, error_message):
        """Handle WebSocket errors by displaying an error message."""
        logger.error(f"WebSocket error: {error_message}")
        self.websocket_status_label.setText(f"WebSocket Status: 错误 - {error_message}")
        self.websocket_status_label.setStyleSheet("color: orange;")

    def accumulate_damage(self, value):
        """Accumulate damage based on incoming value."""
        current_value = self.damage_progress_bar.value()
        new_value = min(100, current_value + value)  # Cap damage at 100%
        self.damage_progress_bar.setValue(new_value)
        logger.info(f"Accumulated damage by {value}%. Current damage: {new_value}%")

    def reset_damage(self):
        """Reset the damage accumulation."""
        logger.info("Resetting damage accumulation.")
        self.damage_progress_bar.setValue(0)
        if self.app_status_online:
            asyncio.create_task(self.controller.client.set_strength(Channel.A, StrengthOperationType.SET_TO, 0))
            asyncio.create_task(self.controller.strength_fire_mode(False, Channel.A, self.death_penalty_strength_slider.value(), self.controller.last_strength)) #可能遗漏

    async def trigger_death_penalty(self):
        """Trigger death penalty by setting damage to 100% and applying penalty."""
        penalty_strength = self.death_penalty_strength_slider.value()  # 获取惩罚强度
        penalty_time = self.death_penalty_time_spinbox.value()  # 获取惩罚持续时间
        logger.warning(f"Death penalty triggered: Strength={penalty_strength}, Time={penalty_time}s")
        self.damage_progress_bar.setValue(100)  # 将伤害设置为 100%
        # asyncio.create_task(self.controller.client.set_strength(Channel.A, StrengthOperationType.SET_TO, self.damage_strength_slider.value()))
        last_strength_mod = self.controller.last_strength
        last_strength_mod.a = self.damage_strength_slider.value() # 开火值基于伤害强度上限更新
        logger.warning(f"Death penalty triggered: a {last_strength_mod.a} fire {penalty_strength}")
        # 开始惩罚
        if self.app_status_online:
            asyncio.create_task(self.controller.strength_fire_mode(True, Channel.A, penalty_strength, last_strength_mod))
            await asyncio.sleep(penalty_time)  # 等待指定的惩罚持续时间
            asyncio.create_task(self.controller.strength_fire_mode(False, Channel.A, penalty_strength, last_strength_mod))

    def update_current_channel_display(self, channel_name):
        """更新当前选择通道显示"""
        self.current_channel_label.setText(f"面板当前控制通道: {channel_name}")

def generate_qrcode(data: str):
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


def handle_osc_message_task_pad(address, list_object, *args):
    asyncio.create_task(list_object[0].handle_osc_message_pad(address, *args))


def handle_osc_message_task_pb(address, list_object, *args):
    asyncio.create_task(list_object[0].handle_osc_message_pb(address, *args))


async def run_server(window: MainWindow, ip: str, port: int, osc_port: int):
    """运行服务器并启动OSC服务器"""
    try:
        async with DGLabWSServer(ip, port, 60) as server:
            client = server.new_local_client()
            logger.info("WebSocket 客户端已初始化")

            # 生成二维码
            url = client.get_qrcode(f"ws://{ip}:{port}")
            qrcode_image = generate_qrcode(url)
            window.update_qrcode(qrcode_image)
            logger.info(f"二维码已生成，WebSocket URL: ws://{ip}:{port}")

            osc_client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
            # 初始化控制器
            controller = DGLabController(client, osc_client, window)
            window.controller = controller
            logger.info("DGLabController 已初始化")
            # 在 controller 初始化后调用绑定函数
            window.bind_controller_settings()

            # 设置OSC服务器
            disp = dispatcher.Dispatcher()
            # 面板控制对应的 OSC 地址
            disp.map("/avatar/parameters/SoundPad/Button/*", handle_osc_message_task_pad, controller)
            disp.map("/avatar/parameters/SoundPad/Volume", handle_osc_message_task_pad, controller)
            disp.map("/avatar/parameters/SoundPad/Page", handle_osc_message_task_pad, controller)
            disp.map("/avatar/parameters/SoundPad/PanelControl", handle_osc_message_task_pad, controller)
            # PB/Contact 交互对应的 OSC 地址
            disp.map("/avatar/parameters/DG-LAB/*", handle_osc_message_task_pb, controller)
            disp.map("/avatar/parameters/Tail_Stretch", handle_osc_message_task_pb, controller)

            osc_server_instance = osc_server.AsyncIOOSCUDPServer(
                ("0.0.0.0", osc_port), disp, asyncio.get_event_loop()
            )
            osc_transport, osc_protocol = await osc_server_instance.create_serve_endpoint()
            logger.info(f"OSC Server Listening on port {osc_port}")

            async for data in client.data_generator():
                if isinstance(data, StrengthData):
                    controller.last_strength = data
                    controller.data_updated_event.set()  # 数据更新，触发开火操作的后续事件
                    logger.info(f"接收到数据包 - A通道: {data.a}, B通道: {data.b}")
                    controller.app_status_online = True
                    window.update_connection_status(controller.app_status_online)
                    window.update_status(data)
                # 接收 App 反馈按钮
                elif isinstance(data, FeedbackButton):
                    logger.info(f"App 触发了反馈按钮：{data.name}")
                # 接收 心跳 / App 断开通知
                elif data == RetCode.CLIENT_DISCONNECTED:
                    logger.info("App 已断开连接，你可以尝试重新扫码进行连接绑定")
                    controller.app_status_online = False
                    window.update_connection_status(controller.app_status_online)
                    await client.rebind()
                    logger.info("重新绑定成功")
                    controller.app_status_online = True
                    window.update_connection_status(controller.app_status_online)

            osc_transport.close()
    except OSError as e:
        # Handle specific errors and log them
        error_message = f"WebSocket 服务器启动失败: {str(e)}"
        logger.error(error_message)

        # 启动过程中发生异常，恢复按钮状态为可点击的红色
        window.start_button.setText("启动失败，请重试")
        window.start_button.setStyleSheet("background-color: red; color: white;")
        window.start_button.setEnabled(True)
        window.log_text_edit.append(f"ERROR: {error_message}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()
