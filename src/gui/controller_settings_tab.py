from PySide6.QtWidgets import (QWidget, QGroupBox, QFormLayout, QLabel, QSlider,
                               QCheckBox, QComboBox, QSpinBox, QHBoxLayout, QToolTip)
from PySide6.QtCore import Qt, QTimer, QPoint
import math
import asyncio
import logging

from pydglab_ws import Channel, StrengthOperationType
from pulse_data import PULSE_NAME
from command_types import CommandType

logger = logging.getLogger(__name__)

class ControllerSettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.dg_controller = None

        self.layout = QFormLayout(self)
        self.setLayout(self.layout)

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

        # ChatBox状态开关
        self.enable_chatbox_status_checkbox = QCheckBox("启用ChatBox状态显示")
        self.enable_chatbox_status_checkbox.setChecked(False)
        self.controller_form.addRow(self.enable_chatbox_status_checkbox)

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
        self.controller_form.addRow("开火强度步进:", self.strength_step_spinbox)

        # 调节强度步长
        self.adjust_strength_step_spinbox = QSpinBox()
        self.adjust_strength_step_spinbox.setRange(0, 100)
        self.adjust_strength_step_spinbox.setValue(5)
        self.controller_form.addRow("调节强度步进:", self.adjust_strength_step_spinbox)

        self.controller_group.setLayout(self.controller_form)
        self.layout.addRow(self.controller_group)

        # 命令类型控制组
        self.command_types_group = QGroupBox("命令来源控制")
        self.command_types_group.setEnabled(False)  # 默认禁用
        self.command_types_form = QFormLayout()

        # 创建命令类型控制复选框
        self.enable_gui_commands_checkbox = QCheckBox("启用程序界面控制")
        self.enable_gui_commands_checkbox.setChecked(True)
        self.command_types_form.addRow(self.enable_gui_commands_checkbox)

        # 创建水平布局，包含面板控制复选框和当前通道显示
        panel_layout = QHBoxLayout()
        
        # 添加面板控制复选框
        self.enable_panel_commands_checkbox = QCheckBox("启用Soundpad控制")
        self.enable_panel_commands_checkbox.setChecked(True)
        panel_layout.addWidget(self.enable_panel_commands_checkbox)
        
        # 添加当前选择通道显示标签
        self.current_channel_label = QLabel("面板当前控制通道: 未设置")
        panel_layout.addWidget(self.current_channel_label)
        
        # 将水平布局添加到主布局
        self.command_types_form.addRow(panel_layout)

        # 将交互命令拆分为A/B通道独立控制
        interaction_layout = QHBoxLayout()
        
        self.enable_interaction_commands_a_checkbox = QCheckBox("A通道交互控制")
        self.enable_interaction_commands_a_checkbox.setChecked(True)
        interaction_layout.addWidget(self.enable_interaction_commands_a_checkbox)
        
        self.enable_interaction_commands_b_checkbox = QCheckBox("B通道交互控制")
        self.enable_interaction_commands_b_checkbox.setChecked(True)
        interaction_layout.addWidget(self.enable_interaction_commands_b_checkbox)
        
        self.command_types_form.addRow("启用交互方式控制:", interaction_layout)

        self.enable_ton_commands_checkbox = QCheckBox("启用游戏联动控制")
        self.enable_ton_commands_checkbox.setChecked(True)
        self.command_types_form.addRow(self.enable_ton_commands_checkbox)

        self.command_types_group.setLayout(self.command_types_form)
        self.layout.addRow(self.command_types_group)

        # Connect UI to controller update methods
        self.strength_step_spinbox.valueChanged.connect(self.update_strength_step)
        self.adjust_strength_step_spinbox.valueChanged.connect(self.update_adjust_strength_step)
        self.pulse_mode_a_combobox.currentIndexChanged.connect(self.update_pulse_mode_a)
        self.pulse_mode_b_combobox.currentIndexChanged.connect(self.update_pulse_mode_b)
        self.enable_chatbox_status_checkbox.stateChanged.connect(self.update_chatbox_status)
        
        # 连接命令类型控制复选框
        self.enable_gui_commands_checkbox.stateChanged.connect(self.update_gui_commands_state)
        self.enable_panel_commands_checkbox.stateChanged.connect(self.update_panel_commands_state)
        self.enable_interaction_commands_a_checkbox.stateChanged.connect(self.update_interaction_commands_a_state)
        self.enable_interaction_commands_b_checkbox.stateChanged.connect(self.update_interaction_commands_b_state)
        self.enable_ton_commands_checkbox.stateChanged.connect(self.update_ton_commands_state)

    def bind_controller_settings(self):
        """将GUI设置与DGLabController变量绑定"""
        if self.main_window.controller:
            self.dg_controller = self.main_window.controller
            self.dg_controller.fire_mode_strength_step = self.strength_step_spinbox.value()
            self.dg_controller.adjust_strength_step = self.adjust_strength_step_spinbox.value()
            self.dg_controller.pulse_mode_a = self.pulse_mode_a_combobox.currentIndex()
            self.dg_controller.pulse_mode_b = self.pulse_mode_b_combobox.currentIndex()
            self.dg_controller.enable_chatbox_status = self.enable_chatbox_status_checkbox.isChecked()
            
            # 绑定命令类型控制状态
            self.dg_controller.enable_gui_commands = self.enable_gui_commands_checkbox.isChecked()
            self.dg_controller.enable_panel_commands = self.enable_panel_commands_checkbox.isChecked()
            self.dg_controller.enable_interaction_commands = (self.enable_interaction_commands_a_checkbox.isChecked() or 
                                                            self.enable_interaction_commands_b_checkbox.isChecked())
            self.dg_controller.enable_ton_commands = self.enable_ton_commands_checkbox.isChecked()
            
            # 同步交互模式状态变量
            self.dg_controller.enable_interaction_mode_a = self.enable_interaction_commands_a_checkbox.isChecked()
            self.dg_controller.enable_interaction_mode_b = self.enable_interaction_commands_b_checkbox.isChecked()
            
            # 同步更新通道状态模型
            if hasattr(self.dg_controller, 'channel_states'):
                if Channel.A in self.dg_controller.channel_states:
                    self.dg_controller.channel_states[Channel.A]["mode"] = "interaction" if self.dg_controller.enable_interaction_mode_a else "panel"
                if Channel.B in self.dg_controller.channel_states:
                    self.dg_controller.channel_states[Channel.B]["mode"] = "interaction" if self.dg_controller.enable_interaction_mode_b else "panel"
            
            logger.info(f"DGLabController 参数已绑定，A通道交互模式：{self.dg_controller.enable_interaction_mode_a}，B通道交互模式：{self.dg_controller.enable_interaction_mode_b}")
        else:
            logger.warning("Controller is not initialized yet.")
            
    def sync_from_controller(self):
        """从控制器恢复UI状态，确保UI和后端状态一致"""
        if self.main_window.controller:
            controller = self.main_window.controller
            
            # 阻止信号触发的更新循环 - 阻断各个控件的信号，而不是整个tab
            self.enable_gui_commands_checkbox.blockSignals(True)
            self.enable_panel_commands_checkbox.blockSignals(True)
            self.enable_interaction_commands_a_checkbox.blockSignals(True)
            self.enable_interaction_commands_b_checkbox.blockSignals(True)
            self.enable_ton_commands_checkbox.blockSignals(True)
            self.enable_chatbox_status_checkbox.blockSignals(True)
            self.strength_step_spinbox.blockSignals(True)
            self.adjust_strength_step_spinbox.blockSignals(True)
            self.pulse_mode_a_combobox.blockSignals(True)
            self.pulse_mode_b_combobox.blockSignals(True)
            
            # 同步命令类型控制复选框状态
            self.enable_gui_commands_checkbox.setChecked(controller.enable_gui_commands)
            self.enable_panel_commands_checkbox.setChecked(controller.enable_panel_commands)
            self.enable_interaction_commands_a_checkbox.setChecked(controller.enable_interaction_mode_a)
            self.enable_interaction_commands_b_checkbox.setChecked(controller.enable_interaction_mode_b)
            self.enable_ton_commands_checkbox.setChecked(controller.enable_ton_commands)
            
            # 同步其他控制设置
            self.enable_chatbox_status_checkbox.setChecked(controller.enable_chatbox_status)
            self.strength_step_spinbox.setValue(controller.fire_mode_strength_step)
            self.adjust_strength_step_spinbox.setValue(controller.adjust_strength_step)
            self.pulse_mode_a_combobox.setCurrentIndex(controller.pulse_mode_a)
            self.pulse_mode_b_combobox.setCurrentIndex(controller.pulse_mode_b)
            
            # 恢复信号
            self.enable_gui_commands_checkbox.blockSignals(False)
            self.enable_panel_commands_checkbox.blockSignals(False)
            self.enable_interaction_commands_a_checkbox.blockSignals(False)
            self.enable_interaction_commands_b_checkbox.blockSignals(False)
            self.enable_ton_commands_checkbox.blockSignals(False)
            self.enable_chatbox_status_checkbox.blockSignals(False)
            self.strength_step_spinbox.blockSignals(False)
            self.adjust_strength_step_spinbox.blockSignals(False)
            self.pulse_mode_a_combobox.blockSignals(False)
            self.pulse_mode_b_combobox.blockSignals(False)
            
            logger.info("已从控制器同步UI状态")

    # Controller update methods
    def update_strength_step(self, value):
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.fire_mode_strength_step = value
            logger.info(f"Updated strength step to {value}")
            # 使用统一的命令处理
            asyncio.run_coroutine_threadsafe(
                controller.send_value_to_vrchat("/avatar/parameters/SoundPad/Volume", 0.01*value),
                asyncio.get_event_loop()
            )

    def update_pulse_mode_a(self, index):
        """更新 A 通道脉冲模式"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.pulse_mode_a = index
            logger.info(f"更新 A 通道脉冲模式为 {PULSE_NAME[index]}")
            # 脉冲模式已更新，会在下一次周期任务中自动应用

    def update_pulse_mode_b(self, index):
        """更新 B 通道脉冲模式"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.pulse_mode_b = index
            logger.info(f"更新 B 通道脉冲模式为 {PULSE_NAME[index]}")
            # 脉冲模式已更新，会在下一次周期任务中自动应用

    def update_chatbox_status(self, state):
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_chatbox_status = bool(state)
            logger.info(f"ChatBox status enabled: {controller.enable_chatbox_status}")

    def set_a_channel_strength(self, value):
        """根据滑动条的值设定 A 通道强度"""
        if self.main_window.controller and self.allow_a_channel_update:
            controller = self.main_window.controller
            asyncio.create_task(controller.add_command(
                CommandType.GUI_COMMAND,
                Channel.A,
                StrengthOperationType.SET_TO,
                value,
                "gui_slider_a"
            ))
            self.a_channel_slider.setToolTip(f"SET A 通道强度: {value}")

    def set_b_channel_strength(self, value):
        """根据滑动条的值设定 B 通道强度"""
        if self.main_window.controller and self.allow_b_channel_update:
            controller = self.main_window.controller
            asyncio.create_task(controller.add_command(
                CommandType.GUI_COMMAND,
                Channel.B,
                StrengthOperationType.SET_TO,
                value,
                "gui_slider_b"
            ))
            self.b_channel_slider.setToolTip(f"SET B 通道强度: {value}")

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

    def update_current_channel_display(self, channel_name):
        """更新当前选择通道显示"""
        self.current_channel_label.setText(f"面板当前控制通道: {channel_name}")

    def update_channel_strength_labels(self, strength_data):
        logger.info(f"通道状态已更新 - A通道强度: {strength_data.a}, B通道强度: {strength_data.b}")
        if self.main_window.controller and self.main_window.controller.last_strength:
            # 仅当允许外部更新时更新 A 通道滑动条
            if self.allow_a_channel_update:
                self.a_channel_slider.blockSignals(True)
                self.a_channel_slider.setRange(0, self.main_window.controller.last_strength.a_limit)  # 根据限制更新范围
                self.a_channel_slider.setValue(self.main_window.controller.last_strength.a)
                self.a_channel_slider.blockSignals(False)
                self.a_channel_label.setText(
                    f"A 通道强度: {self.main_window.controller.last_strength.a} 强度上限: {self.main_window.controller.last_strength.a_limit}  波形: {PULSE_NAME[self.main_window.controller.pulse_mode_a]}")

            # 仅当允许外部更新时更新 B 通道滑动条
            if self.allow_b_channel_update:
                self.b_channel_slider.blockSignals(True)
                self.b_channel_slider.setRange(0, self.main_window.controller.last_strength.b_limit)  # 根据限制更新范围
                self.b_channel_slider.setValue(self.main_window.controller.last_strength.b)
                self.b_channel_slider.blockSignals(False)
                self.b_channel_label.setText(
                    f"B 通道强度: {self.main_window.controller.last_strength.b} 强度上限: {self.main_window.controller.last_strength.b_limit}  波形: {PULSE_NAME[self.main_window.controller.pulse_mode_b]}")

    # 命令类型控制方法
    def update_gui_commands_state(self, state):
        """更新GUI命令启用状态"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_gui_commands = bool(state)
            logger.info(f"GUI命令已{'启用' if state else '禁用'}")
            
    def update_panel_commands_state(self, state):
        """更新面板命令启用状态"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_panel_commands = bool(state)
            logger.info(f"面板命令已{'启用' if state else '禁用'}")
            
    def update_interaction_commands_a_state(self, state):
        """更新A通道交互命令启用状态"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_interaction_mode_a = bool(state)  # 更新为新的交互模式状态变量
            # 更新总体交互命令状态
            controller.enable_interaction_commands = (bool(state) or self.enable_interaction_commands_b_checkbox.isChecked())
            
            # 更新通道状态模型
            if hasattr(controller, 'channel_states') and Channel.A in controller.channel_states:
                controller.channel_states[Channel.A]["mode"] = "interaction" if bool(state) else "panel"
                
            logger.info(f"A通道交互命令已{'启用' if state else '禁用'}")
    
    def update_interaction_commands_b_state(self, state):
        """更新B通道交互命令启用状态"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_interaction_mode_b = bool(state)  # 更新为新的交互模式状态变量
            # 更新总体交互命令状态
            controller.enable_interaction_commands = (self.enable_interaction_commands_a_checkbox.isChecked() or bool(state))
            
            # 更新通道状态模型
            if hasattr(controller, 'channel_states') and Channel.B in controller.channel_states:
                controller.channel_states[Channel.B]["mode"] = "interaction" if bool(state) else "panel"
                
            logger.info(f"B通道交互命令已{'启用' if state else '禁用'}")
    
    def update_ton_commands_state(self, state):
        """更新游戏联动命令启用状态"""
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.enable_ton_commands = bool(state)
            logger.info(f"游戏联动命令已{'启用' if state else '禁用'}")

    def update_adjust_strength_step(self, value):
        if self.main_window.controller:
            controller = self.main_window.controller
            controller.adjust_strength_step = value
            logger.info(f"更新调节强度步进为 {value}")
