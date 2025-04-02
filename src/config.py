import os
import yaml
import psutil
import socket
import ipaddress

import logging
logger = logging.getLogger(__name__)

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
    if os.path.exists('settings.yml'):
        with open('settings.yml', 'r') as f:
            logger.info("settings.yml found")
            settings = yaml.safe_load(f) or {}
            
            # 确保所有设置都存在
            for key, value in DEFAULT_SETTINGS.items():
                if key not in settings:
                    settings[key] = value
                    
            return settings
    logger.info("No settings.yml found, using default settings")
    return DEFAULT_SETTINGS.copy()

# Save the configuration to a YAML file
def save_settings(settings):
    with open('settings.yml', 'w') as f:
        yaml.dump(settings, f)
        logger.info("settings.yml saved")
