from PySide6.QtWidgets import (QWidget, QGroupBox, QFormLayout, QCheckBox, QLabel,
                               QProgressBar, QSlider, QSpinBox, QHBoxLayout, QToolTip)
from PySide6.QtCore import Qt, QTimer, QPoint, QLocale
import math
import math
import asyncio
import json
import logging

from pydglab_ws import Channel, StrengthOperationType
from command_types import CommandType
from i18n import translate as _

from ton_websocket_handler import WebSocketClient

logger = logging.getLogger(__name__)

class TonDamageSystemTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.layout = QFormLayout(self)
        self.setLayout(self.layout)

        # Damage System UI
        self.damage_group = QGroupBox(_("ton_tab.title"))
        self.damage_group.setEnabled(False)
        self.damage_layout = QFormLayout()

        self.damage_info_layout = QHBoxLayout()
        # Enable Damage System Checkbox
        self.enable_damage_checkbox = QCheckBox(_("ton_tab.enable"))
        self.enable_damage_checkbox.stateChanged.connect(self.toggle_damage_system)
        self.damage_info_layout.addWidget(self.enable_damage_checkbox)

        # 增加用于显示 DisplayName 的标签
        self.display_name_label = QLabel(_("ton_tab.user_display_name") + ": " + _("ton_tab.unknown"))  # 默认显示为 "未知"
        self.damage_info_layout.addWidget(self.display_name_label)

        # WebSocket Status Label
        self.websocket_status_label = QLabel(_("ton_tab.websocket_status") + ": " + _("ton_tab.disconnected"))
        self.damage_info_layout.addWidget(self.websocket_status_label)

        # 将水平布局添加到主布局中
        self.damage_layout.addRow(self.damage_info_layout)

        # Damage Progress Bar
        self.damage_progress_bar = QProgressBar()
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.damage_progress_bar.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.damage_progress_bar.setRange(0, 100)
        self.damage_progress_bar.setValue(0)  # Initial damage is 0%
        self.damage_layout.addRow(_("ton_tab.accumulated_damage") + ":", self.damage_progress_bar)

        # 统一滑动条的宽度
        slider_max_width = 450

        # 创建横向布局，用于伤害减免滑动条和标签
        self.damage_reduction_layout = QHBoxLayout()
        self.damage_reduction_label = QLabel(_("ton_tab.damage_reduction") + ": 2 / 10")  # 默认显示
        self.damage_reduction_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.damage_reduction_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.damage_reduction_slider.setRange(0, 10)
        self.damage_reduction_slider.setValue(2)  # Default reduction strength per second
        self.damage_reduction_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.damage_reduction_slider.valueChanged.connect(
            lambda value: self.damage_reduction_label.setText(f"{_('ton_tab.damage_reduction')}: {value} / 10"))
        self.damage_reduction_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.damage_reduction_slider))  # 实时显示提示
        self.damage_reduction_layout.addWidget(self.damage_reduction_label)
        self.damage_reduction_layout.addWidget(self.damage_reduction_slider)
        self.damage_reduction_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.damage_reduction_layout)

        # 创建横向布局，用于伤害强度滑动条和标签
        self.damage_strength_layout = QHBoxLayout()
        self.damage_strength_label = QLabel(_("ton_tab.damage_strength") + ": 50 / 200")  # 默认显示
        self.damage_strength_slider = QSlider(Qt.Horizontal)
        self.damage_strength_slider.setRange(0, 200)
        self.damage_strength_slider.setValue(60)  # Default strength multiplier
        self.damage_strength_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.damage_strength_slider.valueChanged.connect(
            lambda value: self.damage_strength_label.setText(f"{_('ton_tab.damage_strength')}: {value} / 200"))
        self.damage_strength_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.damage_strength_slider))  # 实时显示提示
        self.damage_strength_layout.addWidget(self.damage_strength_label)
        self.damage_strength_layout.addWidget(self.damage_strength_slider)
        self.damage_strength_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.damage_strength_layout)

        # 创建横向布局，用于死亡惩罚强度滑动条和标签
        self.death_penalty_strength_layout = QHBoxLayout()
        self.death_penalty_strength_label = QLabel(_("ton_tab.death_penalty") + ": 30 / 100")  # 默认显示
        self.death_penalty_strength_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.death_penalty_strength_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.death_penalty_strength_slider.setRange(0, 100)
        self.death_penalty_strength_slider.setValue(30)  # Default death penalty strength is 100%
        self.death_penalty_strength_slider.setMaximumWidth(slider_max_width)  # 设置滑动条的最大宽度
        self.death_penalty_strength_slider.valueChanged.connect(
            lambda value: self.death_penalty_strength_label.setText(f"{_('ton_tab.death_penalty')}: {value} / 100"))
        self.death_penalty_strength_slider.valueChanged.connect(
            lambda: self.show_tooltip(self.death_penalty_strength_slider))  # 实时显示提示
        self.death_penalty_strength_layout.addWidget(self.death_penalty_strength_label)
        self.death_penalty_strength_layout.addWidget(self.death_penalty_strength_slider)
        self.death_penalty_strength_layout.setAlignment(Qt.AlignRight)  # 使整个布局靠右对齐
        self.damage_layout.addRow(self.death_penalty_strength_layout)

        # 死亡惩罚持续时间
        self.death_penalty_time_spinbox = QSpinBox()
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.death_penalty_time_spinbox.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.death_penalty_time_spinbox.setRange(0, 60)
        self.death_penalty_time_spinbox.setValue(5)  # Default penalty time is 10 seconds
        self.damage_layout.addRow(_("ton_tab.death_penalty_time") + ":", self.death_penalty_time_spinbox)
        self.damage_group.setLayout(self.damage_layout)
        self.layout.addRow(self.damage_group)

        # Main Timer for Damage Reduction
        self.damage_timer = QTimer(self)
        self.damage_timer.timeout.connect(self.reduce_damage)

        # WebSocket Client (Initialized as None)
        self.websocket_client = None

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
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.disconnected"))
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
        if self.main_window.app_status_online and self.main_window.controller.last_strength and self.main_window.controller.last_strength.a != new_value and not self.main_window.controller.fire_mode_active:
            asyncio.create_task(self.main_window.controller.add_command(
                CommandType.TON_COMMAND,
                Channel.A,
                StrengthOperationType.SET_TO,
                new_strength,
                "ton_damage_reduction"
            ))

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
                self.display_name_label.setText(_("ton_tab.user_display_name") + f": {user_display_name}")
        elif message.get("Type") == "CONNECTED":
            if message.get("DisplayName"):
                user_display_name = message.get("DisplayName")
                self.display_name_label.setText(_("ton_tab.user_display_name") + f": {user_display_name}")

    def handle_websocket_status_update(self, status):
        """Update WebSocket status label based on connection status."""
        logger.info(f"WebSocket status updated: {status}")
        # Log the exact value of the status for better debugging
        if status.lower() == "connected":
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.connected"))
            self.websocket_status_label.setStyleSheet("color: green;")
        elif status.lower() == "disconnected":
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.disconnected"))
            self.websocket_status_label.setStyleSheet("color: red;")
        else:
            logger.warning(f"Unexpected WebSocket status: {status}")
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.error") + f" - {status}")
            self.websocket_status_label.setStyleSheet("color: orange;")

    def handle_websocket_error(self, error_message):
        """Handle WebSocket errors by displaying an error message."""
        logger.error(f"WebSocket error: {error_message}")
        self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.error") + f" - {error_message}")
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
        if self.main_window.app_status_online and self.main_window.controller:
            asyncio.create_task(self.main_window.controller.add_command(
                CommandType.TON_COMMAND,
                Channel.A,
                StrengthOperationType.SET_TO,
                0,
                "ton_damage_reset"
            ))
            asyncio.create_task(self.main_window.controller.strength_fire_mode(False, Channel.A, self.death_penalty_strength_slider.value(), self.main_window.controller.last_strength)) #可能遗漏

    async def trigger_death_penalty(self):
        """Trigger death penalty by setting damage to 100% and applying penalty."""
        penalty_strength = self.death_penalty_strength_slider.value()  # 获取惩罚强度
        penalty_time = self.death_penalty_time_spinbox.value()  # 获取惩罚持续时间
        logger.warning(f"Death penalty triggered: Strength={penalty_strength}, Time={penalty_time}s")
        self.damage_progress_bar.setValue(100)  # 将伤害设置为 100%
        
        # 使用控制器的统一接口处理死亡惩罚
        if self.main_window.controller and self.main_window.app_status_online:
            await self.main_window.controller.handle_ton_death(penalty_strength, penalty_time)

    def update_damage(self, damage_value):
        """Update the damage value and progress bar."""
        if damage_value > 0 and self.enable_damage_checkbox.isChecked():
            # 当前伤害进度
            current_value = self.damage_progress_bar.value()
            # 根据伤害倍率计算实际增加的伤害值
            actual_damage = damage_value * self.damage_multiplier_slider.value()
            # 计算新的伤害进度
            new_value = min(current_value + actual_damage, 100)
            # 更新伤害进度条
            self.damage_progress_bar.setValue(int(new_value))
            logger.info(f"收到伤害 {damage_value}，伤害倍率 {self.damage_multiplier_slider.value()}，实际伤害 {actual_damage}")
            
            # 如果控制器初始化且设备在线，发送伤害命令
            if self.main_window.app_status_online and self.main_window.controller:
                # 获取伤害强度上限
                max_strength = self.damage_strength_slider.value()
                # 计算实际应用的强度值
                applied_strength = int((new_value / 100) * max_strength)
                # 创建伤害处理任务
                asyncio.create_task(
                    self.main_window.controller.handle_ton_damage(
                        actual_damage, 
                        self.damage_multiplier_slider.value()
                    )
                )

    def update_ui_texts(self):
        """更新UI上的所有文本为当前语言"""
        # 更新分组框标题
        self.damage_group.setTitle(_("ton_tab.title"))
        
        # 更新复选框和标签文本
        self.enable_damage_checkbox.setText(_("ton_tab.enable"))
        self.display_name_label.setText(_("ton_tab.user_display_name") + ": " + _("ton_tab.unknown"))
        
        # 更新WebSocket状态标签
        if self.websocket_client and hasattr(self.websocket_client, 'websocket') and self.websocket_client.websocket:
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.connected"))
        else:
            self.websocket_status_label.setText(_("ton_tab.websocket_status") + ": " + _("ton_tab.disconnected"))
        
        # 更新滑动条标签
        self.damage_layout.itemAt(1, QFormLayout.LabelRole).widget().setText(_("ton_tab.accumulated_damage") + ":")
        self.damage_reduction_label.setText(_("ton_tab.damage_reduction") + f": {self.damage_reduction_slider.value()} / 10")
        self.damage_strength_label.setText(_("ton_tab.damage_strength") + f": {self.damage_strength_slider.value()} / 200")
        self.death_penalty_strength_label.setText(_("ton_tab.death_penalty") + f": {self.death_penalty_strength_slider.value()} / 100")
        
        # 更新死亡惩罚持续时间标签
        self.damage_layout.itemAt(5, QFormLayout.LabelRole).widget().setText(_("ton_tab.death_penalty_time") + ":")

