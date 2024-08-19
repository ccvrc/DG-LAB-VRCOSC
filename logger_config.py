# logger_config.py
import logging
import colorlog

def setup_logging():
    # 定义彩色日志格式，包含文件名和行号
    color_formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s '
        '[in %(filename)s:%(lineno)d]',
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    )

    # 定义文件日志格式（无颜色），包含文件名和行号
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s '
        '[in %(filename)s:%(lineno)d]',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 创建控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(color_formatter)  # 使用彩色格式化器

    # 创建文件日志处理器
    file_handler = logging.FileHandler("app.log", encoding='utf-8')
    file_handler.setFormatter(file_formatter)  # 使用无颜色的格式化器

    # 配置根记录器
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[console_handler, file_handler]
    )