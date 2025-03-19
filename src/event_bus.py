"""
event_bus.py - 事件总线模块
实现基于 pyee.AsyncIOEventEmitter 的事件驱动架构
"""

import asyncio
import enum
import logging
from typing import Dict, Any, Optional, Union, List, Callable, Coroutine
from pyee.asyncio import AsyncIOEventEmitter

logger = logging.getLogger(__name__)

# 事件类型枚举
class EventType(enum.Enum):
    # 输入事件类型
    INPUT_UI_STRENGTH_CHANGE = "input.ui.strength_change"  # 界面强度变更
    INPUT_UI_PULSE_CHANGE = "input.ui.pulse_change"  # 界面波形变更
    INPUT_UI_MODE_CHANGE = "input.ui.mode_change"  # 界面模式变更
    INPUT_UI_CHANNEL_SELECT = "input.ui.channel_select"  # 界面通道选择
    INPUT_UI_PANEL_CONTROL = "input.ui.panel_control"  # 界面面板控制开关
    INPUT_UI_CHATBOX_TOGGLE = "input.ui.chatbox_toggle"  # 界面ChatBox开关
    
    INPUT_OSC_PANEL_CONTROL = "input.osc.panel_control"  # OSC面板控制
    INPUT_OSC_FLOAT_PARAM = "input.osc.float_param"  # OSC浮点参数
    
    INPUT_TON_DAMAGE = "input.ton.damage"  # ToN伤害事件
    
    # 输出事件类型
    OUTPUT_DEVICE_STRENGTH = "output.device.strength"  # 设备强度输出
    OUTPUT_DEVICE_PULSE = "output.device.pulse"  # 设备波形输出
    
    OUTPUT_CHATBOX_MESSAGE = "output.chatbox.message"  # ChatBox消息输出
    
    # 状态事件类型
    STATUS_UPDATE = "status.update"  # 状态更新
    STATUS_DEVICE_CONNECT = "status.device.connect"  # 设备连接状态
    STATUS_OSC_CONNECT = "status.osc.connect"  # OSC连接状态
    STATUS_TON_CONNECT = "status.ton.connect"  # ToN连接状态

# 单例事件总线
class EventBus:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EventBus, cls).__new__(cls)
            cls._instance.ee = AsyncIOEventEmitter()
            cls._instance.input_sources = {
                "ui": True,          # 界面输入
                "osc_panel": True,   # OSC面板输入
                "osc_param": True,   # OSC参数输入
                "ton": True,         # ToN输入
            }
            cls._instance.output_targets = {
                "device": True,      # 设备输出
                "chatbox": True,     # ChatBox输出
            }
            # 当前通道状态
            cls._instance.channel_states = {
                "A": {
                    "strength": 0,
                    "pulse_mode": 0,
                    "is_dynamic_bone_mode": False,
                },
                "B": {
                    "strength": 0,
                    "pulse_mode": 0,
                    "is_dynamic_bone_mode": False,
                }
            }
            # 当前选中的通道
            cls._instance.current_channel = "A"
            # 一键开火模式设置
            cls._instance.fire_mode = {
                "active": False,
                "strength_step": 30,
                "origin_strength_a": 0,
                "origin_strength_b": 0,
            }
            # 锁对象
            cls._instance.fire_mode_lock = asyncio.Lock()
            
        return cls._instance
    
    def emit(self, event: Union[EventType, str], *args, **kwargs):
        """发送事件"""
        if isinstance(event, EventType):
            event = event.value
        logger.debug(f"Emitting event: {event} with args: {args} kwargs: {kwargs}")
        return self.ee.emit(event, *args, **kwargs)
    
    def on(self, event: Union[EventType, str], f: Callable[..., Coroutine]):
        """注册事件监听器"""
        if isinstance(event, EventType):
            event = event.value
        logger.debug(f"Registering listener for event: {event}")
        return self.ee.on(event, f)
    
    def once(self, event: Union[EventType, str], f: Callable[..., Coroutine]):
        """注册一次性事件监听器"""
        if isinstance(event, EventType):
            event = event.value
        return self.ee.once(event, f)
    
    def remove_listener(self, event: Union[EventType, str], f: Callable[..., Coroutine]):
        """移除事件监听器"""
        if isinstance(event, EventType):
            event = event.value
        return self.ee.remove_listener(event, f)
    
    def remove_all_listeners(self, event: Optional[Union[EventType, str]] = None):
        """移除所有事件监听器"""
        if isinstance(event, EventType):
            event = event.value
        return self.ee.remove_all_listeners(event)
    
    def enable_input_source(self, source: str, enabled: bool = True):
        """启用或禁用输入源"""
        if source in self.input_sources:
            self.input_sources[source] = enabled
            logger.info(f"Input source {source} {'enabled' if enabled else 'disabled'}")
            return True
        return False
    
    def enable_output_target(self, target: str, enabled: bool = True):
        """启用或禁用输出目标"""
        if target in self.output_targets:
            self.output_targets[target] = enabled
            logger.info(f"Output target {target} {'enabled' if enabled else 'disabled'}")
            return True
        return False
    
    def is_source_enabled(self, source: str) -> bool:
        """检查输入源是否启用"""
        return self.input_sources.get(source, False)
    
    def is_target_enabled(self, target: str) -> bool:
        """检查输出目标是否启用"""
        return self.output_targets.get(target, False)
    
    def update_channel_state(self, channel: str, key: str, value: Any):
        """更新通道状态"""
        if channel in self.channel_states and key in self.channel_states[channel]:
            self.channel_states[channel][key] = value
            logger.debug(f"Updated channel {channel} {key} to {value}")
            return True
        return False
    
    def get_channel_state(self, channel: str, key: str = None) -> Any:
        """获取通道状态"""
        if channel in self.channel_states:
            if key is None:
                return self.channel_states[channel]
            return self.channel_states[channel].get(key)
        return None
    
    def set_current_channel(self, channel: str):
        """设置当前通道"""
        if channel in self.channel_states:
            self.current_channel = channel
            logger.info(f"Current channel set to {channel}")
            return True
        return False
    
    def get_current_channel(self) -> str:
        """获取当前通道"""
        return self.current_channel

# 获取事件总线单例
def get_event_bus() -> EventBus:
    return EventBus() 