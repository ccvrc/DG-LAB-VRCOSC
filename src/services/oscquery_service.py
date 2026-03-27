"""
OSCQuery service wrapper using vrchat_oscquery library.
This module provides VRChat OSCQuery integration for automatic port discovery.
"""
import logging
import asyncio
from typing import Optional, Callable
from pythonosc.dispatcher import Dispatcher
from pythonosc import udp_client

logger = logging.getLogger(__name__)

# VRChat 默认发送到的端口
VRC_SEND_PORT = 9000

class OSCQueryService:
    """VRChat OSCQuery 服务封装类"""
    
    def __init__(self, app_name: str = "DG-LAB-VRCOSC"):
        """
        初始化 OSCQuery 服务
        
        Args:
            app_name: 在 VRChat 中显示的应用名称
        """
        self.app_name = app_name
        self._osc_port: Optional[int] = None
        self._running = False
        self._dispatcher: Optional[Dispatcher] = None
        
    async def start(self, dispatcher: Dispatcher) -> int:
        """
        启动 OSCQuery 服务
        
        Args:
            dispatcher: OSC 消息分发器
            
        Returns:
            int: 分配的 OSC 监听端口
        """
        from vrchat_oscquery.asyncio import vrc_osc
        
        self._dispatcher = dispatcher
        
        # 使用 vrchat_oscquery 库启动服务
        # 这会自动处理：
        # 1. 随机端口分配
        # 2. mDNS 服务注册
        # 3. HTTP OSCQuery 服务器
        # 4. OSC UDP 服务器
        await vrc_osc(self.app_name, dispatcher, foreground=False)
        
        self._running = True
        logger.info(f"OSCQuery service '{self.app_name}' started successfully")
        
        return self._osc_port
    
    def get_vrc_client(self) -> udp_client.SimpleUDPClient:
        """获取用于向 VRChat 发送消息的 OSC 客户端"""
        from vrchat_oscquery.common import vrc_client
        return vrc_client()
    
    @property
    def is_running(self) -> bool:
        return self._running
