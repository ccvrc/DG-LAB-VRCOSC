from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QGroupBox, QLabel, QHBoxLayout, QFormLayout
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt, QTimer
import logging

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
        level_short = {
            'DEBUG': 'D',
            'INFO': 'I',
            'WARNING': 'W',
            'ERROR': 'E',
            'CRITICAL': 'C'
        }.get(record.levelname, 'I')  # 默认 INFO
        record.levelname = level_short
        return super().format(record)

class LogViewerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.dg_controller = None

        self.layout = QFormLayout(self)
        self.setLayout(self.layout)

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

        # 设置日志处理器
        self.log_handler = QTextEditHandler(self.log_text_edit)
        self.log_handler.setLevel(logging.DEBUG)  # 捕获所有日志级别

        # 使用自定义格式化器，简化时间和日志级别
        formatter = SimpleFormatter('%(asctime)s-%(levelname)s: %(message)s', datefmt='%H:%M:%S')
        self.log_handler.setFormatter(formatter)

        # 增加可折叠的调试界面
        self.debug_group = QGroupBox("调试信息")
        self.debug_group.setCheckable(True)
        self.debug_group.setChecked(False)  # 默认折叠状态
        self.debug_group.toggled.connect(self.toggle_debug_info)  # 连接信号槽

        self.debug_layout = QHBoxLayout()
        self.debug_label = QLabel("DGLabController 参数:")
        self.debug_layout.addWidget(self.debug_label)

        # 显示控制器的参数
        self.param_label = QLabel("正在加载控制器参数...")
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
            self.dg_controller = self.main_window.controller
            params = (
                f"Device online: app_status_online= {self.dg_controller.app_status_online}\n "
                f"Enable Panel Control: {self.dg_controller.enable_panel_control}\n"
                f"Dynamic Bone Mode A: {self.dg_controller.is_dynamic_bone_mode_a}\n"
                f"Dynamic Bone Mode B: {self.dg_controller.is_dynamic_bone_mode_b}\n"
                f"Pulse Mode A: {self.dg_controller.pulse_mode_a}\n"
                f"Pulse Mode B: {self.dg_controller.pulse_mode_b}\n"
                f"Fire Mode Strength Step: {self.dg_controller.fire_mode_strength_step}\n"
                f"Enable ChatBox Status: {self.dg_controller.enable_chatbox_status}\n"
            )
            self.param_label.setText(params)
        else:
            self.param_label.setText("控制器未初始化.")