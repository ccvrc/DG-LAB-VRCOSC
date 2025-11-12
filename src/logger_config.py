import logging
import colorlog
from datetime import datetime
import os

def setup_logging():
    # 获取当前时间，用于生成日志文件名
    log_filename = datetime.now().strftime("DG-LAB-VRCOSC_%Y-%m-%d_%H-%M-%S.log")

    # 创建日志目录（如果不存在）
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    # 配置日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s [in %(filename)s:%(lineno)d]'

    # 创建文件日志处理器，写入新创建的日志文件
    file_handler = logging.FileHandler(os.path.join(log_dir, log_filename), encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)  # 文件日志级别
    file_formatter = logging.Formatter(log_format)
    file_handler.setFormatter(file_formatter)

    # 创建彩色控制台日志处理器
    console_handler = colorlog.StreamHandler()
    console_handler.setLevel(logging.DEBUG)  # 控制台日志级别
    console_formatter = colorlog.ColoredFormatter(
        '%(log_color)s' + log_format,
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    )
    console_handler.setFormatter(console_formatter)

    # 获取根记录器，并添加文件和控制台处理器
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # 全局日志级别
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # 可选：禁用第三方库的日志
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    logging.getLogger("websockets.protocol").setLevel(logging.WARNING)
    logging.getLogger('qasync').setLevel(logging.WARNING)

