import os
import sys
import yaml
import psutil
import socket
import ipaddress

import logging
logger = logging.getLogger(__name__)

def get_config_file_path(filename):
    """
    获取配置文件的绝对路径，确保开发和打包后都能正常使用
    配置文件保存在用户可访问的位置，而不是临时目录
    """
    if hasattr(sys, '_MEIPASS'):  # PyInstaller 打包后的环境
        # 打包后，配置文件保存在可执行文件同目录下
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, filename)
    else:
        # 开发环境下，配置文件保存在项目根目录
        # 从 src 目录跳到项目根目录
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        return os.path.join(project_root, filename)

# 默认设置
DEFAULT_SETTINGS = {
    'interface': '',
    'ip': '',
    'port': 5678,
    'osc_port': 9001,
    'language': 'zh'  # 添加默认语言设置
}

# Get active IP addresses (unchanged)
def get_active_ip_addresses():
    ip_addresses = {}
    for interface, addrs in psutil.net_if_addrs().items():
        if psutil.net_if_stats()[interface].isup:
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip_addresses[interface] = addr.address
    return ip_addresses

# Validate IP address (unchanged)
def validate_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

# Validate port (unchanged)
def validate_port(port):
    try:
        port = int(port)
        return 0 < port < 65536
    except ValueError:
        return False

# Load the configuration from a YAML file
def load_settings():
    settings_path = get_config_file_path('settings.yml')
    logger.info(f"尝试从 {settings_path} 加载设置配置")

    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                logger.info("settings.yml found")
                settings = yaml.safe_load(f) or {}

                # 确保所有设置都存在
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value

                return settings
        except Exception as e:
            logger.error(f"加载设置文件时出错: {str(e)}")
            return DEFAULT_SETTINGS.copy()

    logger.info("No settings.yml found, using default settings")
    return DEFAULT_SETTINGS.copy()

# Save the configuration to a YAML file
def save_settings(settings):
    settings_path = get_config_file_path('settings.yml')
    try:
        with open(settings_path, 'w', encoding='utf-8') as f:
            yaml.dump(settings, f, allow_unicode=True)
            logger.info(f"settings.yml saved to {settings_path}")
    except Exception as e:
        logger.error(f"保存设置文件时出错: {str(e)}")
