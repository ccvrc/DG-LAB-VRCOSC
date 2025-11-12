from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QGroupBox, QLabel, QHBoxLayout, QFormLayout, QPushButton, QCheckBox
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat
from PySide6.QtCore import Qt, QTimer, Signal
import logging
import time
import os
from pydglab_ws import Channel
from pulse_data import PULSE_NAME
from i18n import translate as _

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

class SimpleFormatter(logging.Formatter):
    """自定义格式化器，将日志级别缩写并调整时间格式"""

    def format(self, record):
        # 简化日志级别显示
        levelname = record.levelname
        if levelname == 'DEBUG':
            levelname = 'D'
        elif levelname == 'INFO':
            levelname = 'I'
        elif levelname == 'WARNING':
            levelname = 'W'
        elif levelname == 'ERROR':
            levelname = 'E'
        elif levelname == 'CRITICAL':
            levelname = 'C'
        
        # 使用简化的格式
        return f"{record.asctime}-{levelname}: {record.getMessage()}"

class LogViewerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.dg_controller = None

        self.layout = QFormLayout(self)
        self.setLayout(self.layout)

        # 日志显示框 - 使用 QGroupBox 包装
        self.log_groupbox = QGroupBox(_("log_tab.simple_log"))
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

        # 设置日志处理器
        self.log_handler = QTextEditHandler(self.log_text_edit)
        self.log_handler.setLevel(logging.DEBUG)  # 捕获所有日志级别

        # 使用自定义格式化器，简化时间和日志级别
        formatter = SimpleFormatter('%(asctime)s-%(levelname)s: %(message)s', datefmt='%H:%M:%S')
        self.log_handler.setFormatter(formatter)

        # 增加可折叠的调试界面
        self.debug_group = QGroupBox(_("log_tab.debug_info"))
        self.debug_group.setCheckable(True)
        self.debug_group.setChecked(False)  # 默认折叠状态
        self.debug_group.toggled.connect(self.toggle_debug_info)  # 连接信号槽

        self.debug_layout = QHBoxLayout()
        self.debug_label = QLabel(_("log_tab.controller_params") + ":")
        self.debug_layout.addWidget(self.debug_label)

        # 显示控制器的参数
        self.param_label = QLabel(_("log_tab.loading_params"))
        self.debug_layout.addWidget(self.param_label)

        self.debug_group.setLayout(self.debug_layout)
        self.layout.addRow(self.debug_group)

        # 启动定时器，每秒刷新一次调试信息
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_debug_info)
        self.timer.start(1000)  # 每秒刷新一次

    def toggle_log_display(self, enabled):
        """折叠或展开日志显示框"""
        if enabled:
            self.log_text_edit.show()  # 展开时显示日志框
        else:
            self.log_text_edit.hide()  # 折叠时隐藏日志框

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

    def toggle_debug_info(self, checked):
        """当调试组被启用/禁用时折叠或展开内容"""
        # 控制调试信息组中所有子组件的可见性，而不是整个调试组
        for child in self.debug_group.findChildren(QWidget):
            child.setVisible(checked)

    def update_debug_info(self):
        """更新调试信息"""
        if self.main_window.controller is not None:
            controller = self.main_window.controller
            # A 通道状态
            channel_a_state = controller.channel_states[Channel.A]
            channel_a_info = (
                f"== A {_('controller_tab.current_channel')} ==\n"
                f"{_('controller_tab.intensity')}: {channel_a_state['current_strength']}\n"
                f"{_('controller_tab.target_strength')}: {channel_a_state['target_strength']}\n"
                f"{_('controller_tab.mode')}: {channel_a_state['mode']}\n"
                f"{_('controller_tab.pulse_mode')}: {PULSE_NAME[channel_a_state['pulse_mode']]}\n"
                f"{_('log_tab.last_command_source')}: {channel_a_state['last_command_source']}\n"
                f"{_('log_tab.last_command_time')}: {time.strftime('%H:%M:%S', time.localtime(channel_a_state['last_command_time']))}\n"
            )
            
            # B 通道状态
            channel_b_state = controller.channel_states[Channel.B]
            channel_b_info = (
                f"== B {_('controller_tab.current_channel')} ==\n"
                f"{_('controller_tab.intensity')}: {channel_b_state['current_strength']}\n"
                f"{_('controller_tab.target_strength')}: {channel_b_state['target_strength']}\n"
                f"{_('controller_tab.mode')}: {channel_b_state['mode']}\n"
                f"{_('controller_tab.pulse_mode')}: {PULSE_NAME[channel_b_state['pulse_mode']]}\n"
                f"{_('log_tab.last_command_source')}: {channel_b_state['last_command_source']}\n"
                f"{_('log_tab.last_command_time')}: {time.strftime('%H:%M:%S', time.localtime(channel_b_state['last_command_time']))}\n"
            )
            
            # 控制器基本信息
            controller_info = (
                f"== {_('log_tab.controller_status')} ==\n"
                f"{_('log_tab.device_online')}: {controller.app_status_online}\n"
                f"{_('log_tab.fire_strength')}: {controller.fire_mode_strength_step}\n"
                f"{_('log_tab.chatbox_status')}: {controller.enable_chatbox_status}\n"
                f"{_('log_tab.current_channel')}: {'A' if controller.current_select_channel == Channel.A else 'B'}\n"
                f"\n== {_('log_tab.command_status')} ==\n"
                f"{_('log_tab.gui_commands')}: {'启用' if controller.enable_gui_commands else '禁用'}\n"
                f"{_('log_tab.panel_commands')}: {'启用' if controller.enable_panel_commands else '禁用'}\n"
                f"{_('log_tab.interaction_commands')}: {'启用' if controller.enable_interaction_commands else '禁用'}\n"
                f"  A{_('log_tab.channel_interaction')}: {'启用' if controller.enable_interaction_mode_a else '禁用'}\n"
                f"  B{_('log_tab.channel_interaction')}: {'启用' if controller.enable_interaction_mode_b else '禁用'}\n"
                f"{_('log_tab.game_commands')}: {'启用' if controller.enable_ton_commands else '禁用'}\n"
            )
            
            # 命令队列状态
            queue_info = (
                f"== {_('log_tab.command_queue')} ==\n"
                f"{_('log_tab.queue_size')}: {controller.command_queue.qsize()}\n"
            )
            
            # 合并所有信息
            combined_info = controller_info + "\n" + channel_a_info + "\n" + channel_b_info + "\n" + queue_info
            self.param_label.setText(combined_info)
        else:
            self.param_label.setText(_("log_tab.controller_not_initialized"))

    def update_log_level(self, level_name):
        """更新日志级别"""
        # 获取日志处理器
        logger = logging.getLogger()
        
        # 设置日志级别
        if level_name == "DEBUG":
            logger.setLevel(logging.DEBUG)
        elif level_name == "INFO":
            logger.setLevel(logging.INFO)
        elif level_name == "WARNING":
            logger.setLevel(logging.WARNING)
        elif level_name == "ERROR":
            logger.setLevel(logging.ERROR)
        elif level_name == "CRITICAL":
            logger.setLevel(logging.CRITICAL)
        
        # 更新日志文本框中显示的级别
        logger.info(f"日志级别已更新为 {level_name}")
    
    def update_ui_texts(self):
        """更新UI上的所有文本为当前语言"""
        # 更新日志组标题
        self.log_groupbox.setTitle(_("log_tab.simple_log"))
        
        # 更新调试组标题
        self.debug_group.setTitle(_("log_tab.debug_info"))
        
        # 更新标签文本
        self.debug_label.setText(_("log_tab.controller_params") + ":")
        if not self.main_window.controller:
            self.param_label.setText(_("log_tab.controller_not_initialized"))
        else:
            # 更新控制器参数文本
            self.update_debug_info()
        
        # 更新按钮文本
        for i in range(self.layout.count()):
            widget = self.layout.itemAt(i).widget()
            if isinstance(widget, QPushButton):
                if widget.text() == "清除" or widget.text() == "Clear":
                    widget.setText(_("log_tab.clear"))
                elif widget.text() == "保存日志" or widget.text() == "Save Log":
                    widget.setText(_("log_tab.save"))
        
        # 更新日志级别标签
        for i in range(self.layout.count()):
            layout_item = self.layout.itemAt(i)
            if isinstance(layout_item, QHBoxLayout):
                for j in range(layout_item.count()):
                    widget = layout_item.itemAt(j).widget()
                    if isinstance(widget, QLabel) and (widget.text() == "日志级别" or widget.text() == "Log Level" or "level" in widget.text().lower()):
                        widget.setText(_("log_tab.level"))