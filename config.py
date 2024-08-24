import os
import yaml
import psutil
import socket
import ipaddress

def get_active_ip_addresses():
    ip_addresses = {}
    for interface, addrs in psutil.net_if_addrs().items():
        if psutil.net_if_stats()[interface].isup:
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip_addresses[interface] = addr.address
    return ip_addresses

def validate_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

def validate_port(port):
    try:
        port = int(port)
        return 0 < port < 65536
    except ValueError:
        return False

def get_settings():
    if os.path.exists('settings.yml'):
        with open('settings.yml', 'r') as f:
            settings = yaml.safe_load(f)
        
        current_ips = get_active_ip_addresses()
        if settings['interface'] in current_ips and current_ips[settings['interface']] == settings['ip']:
            return settings
        
        print("网络环境已变化，需要重新配置IP地址。")
    
    # 首次运行或需要重新配置
    print("请选择网卡和IP地址：")
    ip_addresses = get_active_ip_addresses()
    for i, (interface, ip) in enumerate(ip_addresses.items(), 1):
        print(f"{i}. {interface}: {ip}")
    print(f"{len(ip_addresses) + 1}. 手动输入")

    while True:
        choice = input("请输入选项数字: ")
        if choice.isdigit() and 1 <= int(choice) <= len(ip_addresses) + 1:
            choice = int(choice)
            break
        print("无效选择，请重新输入。")

    if choice <= len(ip_addresses):
        interface, ip = list(ip_addresses.items())[choice - 1]
    else:
        while True:
            ip = input("请输入IPV4地址: ")
            if validate_ip(ip):
                interface = "manual"
                break
            print("无效的IP地址，请重新输入。")

    print("\n请选择OSC接收回复的端口：")
    print("1. 9001 (默认)")
    print("2. 9102 (面捕)")
    print("3. 手动输入")

    while True:
        port_choice = input("请输入选项数字 (直接回车选择默认): ")
        if port_choice == "":
            port = 9001
            break
        elif port_choice == "1":
            port = 9001
            break
        elif port_choice == "2":
            port = 9102
            break
        elif port_choice == "3":
            while True:
                port = input("请输入端口号: ")
                if validate_port(port):
                    port = int(port)
                    break
                print("无效的端口号，请重新输入。")
            break
        print("无效选择，请重新输入。")

    settings = {
        'interface': interface,
        'ip': ip,
        'port': port
    }

    with open('settings.yml', 'w') as f:
        yaml.dump(settings, f)

    return settings