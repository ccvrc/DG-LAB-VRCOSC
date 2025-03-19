"""
ton_websocket_adapter.py - ToN WebSocket适配器模块
负责处理ToN游戏联动的WebSocket通信
"""

import asyncio
import json
import logging
import websockets
from typing import Dict, Any, Optional, List, Tuple
from PySide6.QtCore import Signal, QObject

from event_bus import get_event_bus, EventType

logger = logging.getLogger(__name__)
event_bus = get_event_bus()

class TonWebSocketAdapter(QObject):
    """ToN WebSocket适配器类，负责处理ToN游戏联动"""
    
    # Qt信号定义，用于UI更新
    status_update_signal = Signal(str)
    message_received = Signal(str)
    error_signal = Signal(str)
    
    def __init__(self, url="ws://localhost:5678"):
        """
        初始化ToN WebSocket适配器
        :param url: WebSocket连接地址
        """
        super().__init__()
        self.url = url
        self.websocket = None
        self.connected = False
        self.task = None
        self.lock = asyncio.Lock()
    
    async def connect(self):
        """连接到ToN WebSocket服务器"""
        if self.task:
            logger.warning("已有连接任务正在运行")
            return
        
        self.task = asyncio.create_task(self._connect_task())
        logger.info(f"正在连接到ToN WebSocket服务器: {self.url}")
    
    async def disconnect(self):
        """断开ToN WebSocket连接"""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                logger.info("ToN WebSocket连接任务已取消")
            finally:
                self.task = None
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            self.connected = False
            
            # 更新状态
            self.status_update_signal.emit("disconnected")
            event_bus.emit(EventType.STATUS_TON_CONNECT, False)
            
            logger.info("已断开ToN WebSocket连接")
    
    async def _connect_task(self):
        """ToN WebSocket连接任务"""
        try:
            async with websockets.connect(self.url) as ws:
                self.websocket = ws
                self.connected = True
                
                # 更新状态
                self.status_update_signal.emit("connected")
                event_bus.emit(EventType.STATUS_TON_CONNECT, True)
                
                logger.info("已连接到ToN WebSocket服务器")
                
                # 处理收到的消息
                async for message in ws:
                    await self._process_message(message)
        except asyncio.CancelledError:
            logger.info("ToN WebSocket连接任务被取消")
            raise
        except Exception as e:
            logger.exception(f"ToN WebSocket连接发生异常: {e}")
            self.error_signal.emit(f"连接错误: {e}")
            event_bus.emit(EventType.STATUS_TON_CONNECT, False)
        finally:
            self.websocket = None
            self.connected = False
            self.status_update_signal.emit("disconnected")
    
    async def _process_message(self, message: str):
        """
        处理接收到的WebSocket消息
        :param message: 消息内容
        """
        logger.debug(f"收到ToN WebSocket消息: {message}")
        self.message_received.emit(message)
        
        try:
            # 解析JSON消息
            data = json.loads(message)
            
            # 检查消息类型
            if data.get("type") == "DAMAGE":
                # 处理伤害消息
                damage_value = data.get("amount", 0)
                target_channel = data.get("channel")  # 可能为None
                
                # 发送ToN伤害事件
                event_bus.emit(EventType.INPUT_TON_DAMAGE, damage_value, target_channel)
                
            elif data.get("type") == "STATS":
                # 处理统计消息
                # 这里可以处理统计信息，如果需要的话
                pass
            
            # 更新状态
            self.status_update_signal.emit("data_received")
            
        except json.JSONDecodeError:
            logger.warning("收到的消息不是有效的JSON格式")
            self.status_update_signal.emit("error")
        except Exception as e:
            logger.exception(f"处理ToN WebSocket消息时发生异常: {e}")
            self.status_update_signal.emit("error")

# 创建ToN WebSocket适配器单例
_adapter_instance = None

def init_ton_adapter(url="ws://localhost:5678"):
    """初始化ToN WebSocket适配器"""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = TonWebSocketAdapter(url)
    return _adapter_instance

def get_ton_adapter():
    """获取ToN WebSocket适配器单例"""
    global _adapter_instance
    if _adapter_instance is None:
        raise RuntimeError("ToN WebSocket适配器未初始化")
    return _adapter_instance 