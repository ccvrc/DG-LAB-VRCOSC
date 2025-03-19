"""
osc_adapter.py - OSC适配器模块
负责处理OSC输入和输出，将OSC消息转换为事件，并处理事件到OSC的输出
"""

import asyncio
import logging
import re
import yaml
from typing import Dict, Any, Optional, List, Tuple

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from event_bus import get_event_bus, EventType

logger = logging.getLogger(__name__)
event_bus = get_event_bus()

class OSCAdapter:
    """OSC适配器类，负责处理OSC输入和输出"""
    
    def __init__(self, ip, port, osc_addresses_file="osc_addresses.yml"):
        """
        初始化OSC适配器
        :param ip: 监听IP
        :param port: 监听端口
        :param osc_addresses_file: OSC地址配置文件
        """
        self.ip = ip
        self.port = port
        self.osc_addresses_file = osc_addresses_file
        self.osc_addresses = self._load_osc_addresses()
        
        # OSC客户端，用于发送消息到VRChat
        self.vrchat_client = SimpleUDPClient("127.0.0.1", 9000)  # VRChat默认端口
        
        # OSC服务器
        self.dispatcher = Dispatcher()
        self.server = None
        
        # 注册事件监听器
        self._register_event_listeners()
        
        # 注册OSC消息处理
        self._register_osc_handlers()
    
    def _load_osc_addresses(self) -> Dict:
        """加载OSC地址配置"""
        try:
            with open(self.osc_addresses_file, 'r', encoding='utf-8') as f:
                addresses = yaml.safe_load(f)
                logger.info(f"加载OSC地址配置: {len(addresses)} 个条目")
                return addresses
        except Exception as e:
            logger.exception(f"加载OSC地址配置失败: {e}")
            return []
    
    def _register_event_listeners(self):
        """注册事件监听器"""
        event_bus.on(EventType.OUTPUT_CHATBOX_MESSAGE, self._handle_chatbox_message)
    
    def _register_osc_handlers(self):
        """注册OSC消息处理器"""
        # 注册通用处理器，处理所有消息
        self.dispatcher.map("*", self._handle_osc_message)
        
        # 特别处理SoundPad消息，这些是面板控制消息
        self.dispatcher.map("/avatar/parameters/SoundPad/*", self._handle_soundpad_message)
    
    async def _handle_osc_message(self, address, *args):
        """
        处理OSC消息
        :param address: OSC地址
        :param args: OSC参数
        """
        logger.debug(f"收到OSC消息: {address} {args}")
        
        # 检查是否为浮点参数控制地址
        channel_mapping = self._check_float_param_mapping(address)
        if channel_mapping and args and len(args) > 0 and isinstance(args[0], (int, float)):
            # 发送OSC浮点参数事件
            event_bus.emit(EventType.INPUT_OSC_FLOAT_PARAM, address, float(args[0]), channel_mapping)
    
    async def _handle_soundpad_message(self, address, *args):
        """
        处理SoundPad消息，这些是面板控制消息
        :param address: OSC地址
        :param args: OSC参数
        """
        logger.debug(f"收到SoundPad消息: {address} {args}")
        
        # 发送OSC面板控制事件
        event_bus.emit(EventType.INPUT_OSC_PANEL_CONTROL, address, *args)
    
    async def _handle_chatbox_message(self, message: str):
        """
        处理ChatBox消息事件
        :param message: 消息内容
        """
        logger.debug(f"发送ChatBox消息: {message}")
        
        # 发送ChatBox消息到VRChat
        self.send_message_to_vrchat_chatbox(message)
    
    def _check_float_param_mapping(self, address) -> Dict[str, List[str]]:
        """
        检查地址是否为浮点参数映射
        :param address: OSC地址
        :return: 通道映射字典，键为通道名，值为地址列表
        """
        result = {}
        
        for addr_config in self.osc_addresses:
            addr_pattern = addr_config.get('address')
            channels = addr_config.get('channels', {})
            
            # 精确匹配
            if addr_pattern == address:
                for channel, enabled in channels.items():
                    if enabled:
                        if channel not in result:
                            result[channel] = []
                        result[channel].append(address)
                continue
            
            # 通配符匹配
            if '*' in addr_pattern:
                pattern = addr_pattern.replace('*', '.*')
                if re.match(pattern, address):
                    for channel, enabled in channels.items():
                        if enabled:
                            if channel not in result:
                                result[channel] = []
                            result[channel].append(address)
        
        return result
    
    def send_message_to_vrchat_chatbox(self, message: str):
        """
        发送消息到VRChat ChatBox
        :param message: 消息内容
        """
        try:
            # 发送消息到VRChat ChatBox
            self.vrchat_client.send_message("/chatbox/input", [message, True, False])
            logger.debug(f"已发送消息到VRChat ChatBox: {message}")
        except Exception as e:
            logger.exception(f"发送消息到VRChat ChatBox失败: {e}")
    
    def send_value_to_vrchat(self, path: str, value):
        """
        发送值到VRChat
        :param path: OSC路径
        :param value: 值
        """
        try:
            # 发送值到VRChat
            self.vrchat_client.send_message(path, value)
            logger.debug(f"已发送值到VRChat: {path} = {value}")
        except Exception as e:
            logger.exception(f"发送值到VRChat失败: {e}")
    
    async def start(self):
        """启动OSC服务器"""
        try:
            self.server = AsyncIOOSCUDPServer(
                (self.ip, self.port), self.dispatcher, asyncio.get_event_loop()
            )
            transport, protocol = await self.server.create_serve_endpoint()
            logger.info(f"OSC服务器已启动，监听 {self.ip}:{self.port}")
            
            # 发送OSC连接状态事件
            event_bus.emit(EventType.STATUS_OSC_CONNECT, True)
            
            return transport
        except Exception as e:
            logger.exception(f"启动OSC服务器失败: {e}")
            # 发送OSC连接状态事件
            event_bus.emit(EventType.STATUS_OSC_CONNECT, False)
            return None

# 创建OSC适配器单例
_adapter_instance = None

def init_osc_adapter(ip, port, osc_addresses_file="osc_addresses.yml"):
    """初始化OSC适配器"""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = OSCAdapter(ip, port, osc_addresses_file)
    return _adapter_instance

def get_osc_adapter():
    """获取OSC适配器单例"""
    global _adapter_instance
    if _adapter_instance is None:
        raise RuntimeError("OSC适配器未初始化")
    return _adapter_instance 