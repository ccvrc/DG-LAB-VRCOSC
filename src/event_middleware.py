"""
event_middleware.py - 事件处理中间件模块
负责处理输入事件，协调多输入源，并触发相应的输出事件
"""

import asyncio
import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple

from event_bus import get_event_bus, EventType
from pulse_data import PULSE_DATA, PULSE_NAME
from pydglab_ws import Channel, StrengthData

logger = logging.getLogger(__name__)
event_bus = get_event_bus()

class ChannelPriority(Enum):
    """通道输入优先级"""
    UI = 0           # 界面控制，最高优先级
    OSC_PANEL = 1    # OSC面板控制
    OSC_PARAM = 2    # OSC参数控制
    TON = 3          # ToN游戏联动

class OutputType(Enum):
    """输出类型"""
    STRENGTH = 0     # 强度输出
    PULSE = 1        # 波形输出

class EventMiddleware:
    """事件处理中间件类，负责处理输入事件并协调输出"""
    
    def __init__(self):
        self.channel_active_sources = {
            "A": {src.name: True for src in ChannelPriority},
            "B": {src.name: True for src in ChannelPriority}
        }
        
        # 默认优先级
        self.channel_priority = {
            "A": ChannelPriority.UI,
            "B": ChannelPriority.UI
        }
        
        # 通道当前输入来源
        self.channel_current_source = {
            "A": ChannelPriority.UI,
            "B": ChannelPriority.UI
        }
        
        # 注册事件监听器
        self._register_event_listeners()
    
    def _register_event_listeners(self):
        """注册事件监听器"""
        # UI输入事件
        event_bus.on(EventType.INPUT_UI_STRENGTH_CHANGE, self._handle_ui_strength_change)
        event_bus.on(EventType.INPUT_UI_PULSE_CHANGE, self._handle_ui_pulse_change)
        event_bus.on(EventType.INPUT_UI_MODE_CHANGE, self._handle_ui_mode_change)
        event_bus.on(EventType.INPUT_UI_CHANNEL_SELECT, self._handle_ui_channel_select)
        event_bus.on(EventType.INPUT_UI_PANEL_CONTROL, self._handle_ui_panel_control)
        event_bus.on(EventType.INPUT_UI_CHATBOX_TOGGLE, self._handle_ui_chatbox_toggle)
        
        # OSC输入事件
        event_bus.on(EventType.INPUT_OSC_PANEL_CONTROL, self._handle_osc_panel_control)
        event_bus.on(EventType.INPUT_OSC_FLOAT_PARAM, self._handle_osc_float_param)
        
        # ToN输入事件
        event_bus.on(EventType.INPUT_TON_DAMAGE, self._handle_ton_damage)
        
        # 状态事件
        event_bus.on(EventType.STATUS_DEVICE_CONNECT, self._handle_device_connect)
    
    async def _handle_ui_strength_change(self, channel: str, strength: int):
        """处理UI强度变更事件"""
        logger.debug(f"UI强度变更: 通道{channel} 强度{strength}")
        if not event_bus.is_source_enabled("ui"):
            return
        
        # 更新通道状态
        event_bus.update_channel_state(channel, "strength", strength)
        
        # 发送设备强度输出事件
        await self._process_output(channel, OutputType.STRENGTH, strength, ChannelPriority.UI)
    
    async def _handle_ui_pulse_change(self, channel: str, pulse_index: int):
        """处理UI波形变更事件"""
        logger.debug(f"UI波形变更: 通道{channel} 波形{pulse_index}")
        if not event_bus.is_source_enabled("ui"):
            return
        
        # 更新通道状态
        event_bus.update_channel_state(channel, "pulse_mode", pulse_index)
        
        # 如果存在波形数据，则发送设备波形输出事件
        if 0 <= pulse_index < len(PULSE_NAME):
            pulse_name = PULSE_NAME[pulse_index]
            pulse_data = PULSE_DATA.get(pulse_name)
            if pulse_data:
                await self._process_output(channel, OutputType.PULSE, 
                                         {"pulse_name": pulse_name, "pulse_data": pulse_data}, 
                                         ChannelPriority.UI)
    
    async def _handle_ui_mode_change(self, channel: str, is_dynamic_mode: bool):
        """处理UI模式变更事件"""
        logger.debug(f"UI模式变更: 通道{channel} 动态模式{is_dynamic_mode}")
        if not event_bus.is_source_enabled("ui"):
            return
        
        # 更新通道状态
        event_bus.update_channel_state(channel, "is_dynamic_bone_mode", is_dynamic_mode)
        
        # 发送模式变更消息到ChatBox
        if event_bus.is_target_enabled("chatbox"):
            mode_text = "动骨交互模式" if is_dynamic_mode else "按键控制模式"
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, 
                          f"通道{channel}已切换为{mode_text}")
    
    async def _handle_ui_channel_select(self, channel: str):
        """处理UI通道选择事件"""
        logger.debug(f"UI通道选择: 通道{channel}")
        event_bus.set_current_channel(channel)
        
        # 发送通道选择消息到ChatBox
        if event_bus.is_target_enabled("chatbox"):
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, 
                          f"已选择通道{channel}")
    
    async def _handle_ui_panel_control(self, enabled: bool):
        """处理UI面板控制开关事件"""
        logger.debug(f"UI面板控制开关: {enabled}")
        event_bus.enable_input_source("osc_panel", enabled)
        
        # 发送面板控制状态消息到ChatBox
        if event_bus.is_target_enabled("chatbox"):
            status_text = "启用" if enabled else "禁用"
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, 
                          f"面板控制已{status_text}")
    
    async def _handle_ui_chatbox_toggle(self, enabled: bool):
        """处理UI ChatBox开关事件"""
        logger.debug(f"UI ChatBox开关: {enabled}")
        event_bus.enable_output_target("chatbox", enabled)
        
        if enabled:
            # 发送ChatBox启用消息
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, "ChatBox已启用")
    
    async def _handle_osc_panel_control(self, address: str, *args):
        """处理OSC面板控制事件"""
        logger.debug(f"OSC面板控制: 地址{address} 参数{args}")
        if not event_bus.is_source_enabled("osc_panel"):
            return
        
        # 根据地址和参数进行相应处理
        # 这里需要根据具体OSC协议实现
        # 示例实现
        if address.endswith("/ChannelSelect"):
            channel = "A" if args[0] == 0 else "B"
            await self._handle_ui_channel_select(channel)
        elif address.endswith("/StrengthUp"):
            if args[0] > 0:
                channel = event_bus.get_current_channel()
                current_strength = event_bus.get_channel_state(channel, "strength") or 0
                new_strength = min(current_strength + 10, 100)
                await self._handle_ui_strength_change(channel, new_strength)
        # 其他控制逻辑...
    
    async def _handle_osc_float_param(self, address: str, value: float, channel_mapping: Dict[str, List[str]]):
        """处理OSC浮点参数事件"""
        logger.debug(f"OSC浮点参数: 地址{address} 值{value}")
        if not event_bus.is_source_enabled("osc_param"):
            return
        
        # 检查参数映射到哪些通道
        for channel_name, addresses in channel_mapping.items():
            if address in addresses and event_bus.get_channel_state(channel_name, "is_dynamic_bone_mode"):
                # 将浮点参数映射到强度值
                strength = int(value * 100)
                await self._process_output(channel_name, OutputType.STRENGTH, strength, ChannelPriority.OSC_PARAM)
    
    async def _handle_ton_damage(self, damage_value: float, target_channel: str = None):
        """处理ToN伤害事件"""
        logger.debug(f"ToN伤害: 值{damage_value} 目标通道{target_channel}")
        if not event_bus.is_source_enabled("ton"):
            return
        
        # 如果未指定目标通道，则使用当前选中通道
        if target_channel is None:
            target_channel = event_bus.get_current_channel()
        
        # 将伤害值映射到强度值
        strength = min(int(damage_value), 100)
        
        # 发送强度输出事件
        await self._process_output(target_channel, OutputType.STRENGTH, strength, ChannelPriority.TON)
    
    async def _handle_device_connect(self, connected: bool):
        """处理设备连接状态事件"""
        logger.debug(f"设备连接状态: {connected}")
        # 设备连接状态处理逻辑
        pass
    
    async def _process_output(self, channel: str, output_type: OutputType, data: Any, source: ChannelPriority):
        """处理输出，根据优先级决定是否发送到设备"""
        # 检查输入源是否激活
        if not self.channel_active_sources[channel][source.name]:
            logger.debug(f"输入源 {source.name} 为通道 {channel} 已禁用，忽略输出")
            return
        
        # 检查优先级
        current_priority = self.channel_current_source[channel]
        if source.value > current_priority.value:
            logger.debug(f"输入源 {source.name} 优先级低于当前源 {current_priority.name}，忽略输出")
            return
        
        # 更新当前输入源
        self.channel_current_source[channel] = source
        
        # 发送输出事件
        if output_type == OutputType.STRENGTH:
            # 发送强度输出事件
            if event_bus.is_target_enabled("device"):
                # 对应Channel.A或Channel.B
                ch = Channel.A if channel == "A" else Channel.B
                event_bus.emit(EventType.OUTPUT_DEVICE_STRENGTH, ch, data)
                
                # 发送状态消息到ChatBox
                if event_bus.is_target_enabled("chatbox"):
                    event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, 
                                  f"通道{channel}强度：{data}%")
        
        elif output_type == OutputType.PULSE:
            # 发送波形输出事件
            if event_bus.is_target_enabled("device"):
                ch = Channel.A if channel == "A" else Channel.B
                event_bus.emit(EventType.OUTPUT_DEVICE_PULSE, ch, data)
                
                # 发送状态消息到ChatBox
                if event_bus.is_target_enabled("chatbox"):
                    event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, 
                                  f"通道{channel}波形：{data['pulse_name']}")

# 创建中间件单例
_middleware_instance = None

def get_middleware():
    """获取中间件单例"""
    global _middleware_instance
    if _middleware_instance is None:
        _middleware_instance = EventMiddleware()
    return _middleware_instance 