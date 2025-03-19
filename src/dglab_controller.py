"""
dglab_controller.py - DG-LAB控制器模块
基于事件驱动架构重构，处理设备控制逻辑
"""
import asyncio
import math
import logging
from typing import Dict, Any, Optional, Union

from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from pulse_data import PULSE_DATA, PULSE_NAME
from event_bus import get_event_bus, EventType
import device_adapter

logger = logging.getLogger(__name__)
event_bus = get_event_bus()

class DGLabController:
    """DG-LAB控制器类，管理设备连接和状态"""
    
    def __init__(self, client, osc_client, ui_callback=None):
        """
        初始化DGLabController实例
        :param client: DGLabWSServer的客户端实例
        :param osc_client: 用于发送OSC回复的客户端实例
        :param ui_callback: UI回调函数
        """
        self.client = client
        self.osc_client = osc_client
        self.main_window = ui_callback
        
        # 添加兼容性属性
        self.app_status_online = False
        self.enable_panel_control = True
        self.is_dynamic_bone_mode_a = False
        self.is_dynamic_bone_mode_b = False
        self.pulse_mode_a = 0
        self.pulse_mode_b = 0
        self.fire_mode_strength_step = 30
        self.enable_chatbox_status = True
        self.last_strength = None
        self.data_updated_event = asyncio.Event()
        
        # 初始化设备适配器
        self.device_adapter = device_adapter.init_device_adapter(client)
        
        # 注册事件监听器
        self._register_event_listeners()
        
        # 当前选择的通道
        self.current_select_channel = Channel.A
        
        # 创建定时任务
        self.send_status_task = None
        self.send_pulse_task = None
        
        # 初始化定时器
        self.chatbox_toggle_timer = None
        self.set_mode_timer = None
        
        # 初始化一键开火模式锁
        self.fire_mode_lock = asyncio.Lock()
        
        # 启动定时任务
        self._start_tasks()
    
    def _register_event_listeners(self):
        """注册事件监听器"""
        # 输出设备事件
        event_bus.on(EventType.OUTPUT_DEVICE_STRENGTH, self._handle_strength_output)
        event_bus.on(EventType.OUTPUT_DEVICE_PULSE, self._handle_pulse_output)
        
        # 状态事件
        event_bus.on(EventType.STATUS_UPDATE, self._handle_status_update)
    
    def _start_tasks(self):
        """启动定时任务"""
        self.send_status_task = asyncio.create_task(self.periodic_status_update())
        self.send_pulse_task = asyncio.create_task(self.periodic_send_pulse_data())
    
    async def _handle_strength_output(self, channel: Channel, strength: int):
        """
        处理强度输出事件
        :param channel: 通道
        :param strength: 强度值
        """
        logger.debug(f"控制器强度输出: 通道{channel} 强度{strength}")
        
        # 更新最后的强度值
        if channel == Channel.A:
            if hasattr(self, "last_strength") and self.last_strength:
                self.last_strength.a = strength
        else:
            if hasattr(self, "last_strength") and self.last_strength:
                self.last_strength.b = strength
        
        # 标记设备为在线状态
        self.app_status_online = True
        self.device_adapter.set_connected(True)
        
        try:
            # 更新设备强度
            await self.device_adapter._handle_strength_output(channel, strength)
            
            # 更新UI显示
            if self.main_window and hasattr(self.main_window, "controller_settings_tab"):
                try:
                    if channel == Channel.A:
                        if hasattr(self.main_window.controller_settings_tab, "update_strength_a"):
                            self.main_window.controller_settings_tab.update_strength_a(strength)
                        else:
                            logger.debug("controller_settings_tab没有update_strength_a方法")
                    else:
                        if hasattr(self.main_window.controller_settings_tab, "update_strength_b"):
                            self.main_window.controller_settings_tab.update_strength_b(strength)
                        else:
                            logger.debug("controller_settings_tab没有update_strength_b方法")
                except Exception as e:
                    logger.exception(f"更新UI显示时发生异常: {e}")
        except Exception as e:
            logger.exception(f"处理强度输出时发生异常: {e}")
    
    async def _handle_pulse_output(self, channel: Channel, pulse_data: Dict[str, Any]):
        """
        处理波形输出事件
        :param channel: 通道
        :param pulse_data: 波形数据
        """
        logger.debug(f"处理波形输出: 通道{channel} 波形{pulse_data['pulse_name']}")
        
        # 更新设备波形
        await self.device_adapter._handle_pulse_output(channel, pulse_data)
        
        # 更新UI显示
        if self.main_window:
            pulse_name = pulse_data['pulse_name']
            pulse_index = PULSE_NAME.index(pulse_name) if pulse_name in PULSE_NAME else 0
            if channel == Channel.A:
                self.main_window.controller_settings_tab.update_pulse_mode_a(pulse_index)
            else:
                self.main_window.controller_settings_tab.update_pulse_mode_b(pulse_index)
    
    async def _handle_status_update(self, status: Dict[str, Any]):
        """
        处理状态更新事件
        :param status: 状态信息
        """
        logger.debug(f"处理状态更新: {status}")
        
        # 更新ChatBox输出
        if "message" in status and event_bus.is_target_enabled("chatbox"):
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, status["message"])
        
        # 更新UI显示
        if self.main_window and "online" in status:
            self.main_window.network_config_tab.update_connection_status(status["online"])
    
    async def periodic_status_update(self):
        """周期性状态更新"""
        try:
            # 减少状态更新频率，每3秒检查一次
            check_interval = 3
            check_counter = 0
            last_status = None
            
            while True:
                # 每秒执行一次循环，但不一定检查连接状态
                await asyncio.sleep(1)
                
                check_counter += 1
                is_online = self.app_status_online  # 默认使用当前状态
                
                # 每隔check_interval秒检查一次连接状态
                if check_counter >= check_interval:
                    check_counter = 0
                    # 检查设备是否在线
                    is_online = await self.device_adapter.is_connected()
                
                # 只有状态变化时才发送更新事件
                if last_status != is_online:
                    logger.info(f"设备连接状态变化: {'在线' if is_online else '离线'}")
                    # 发送状态更新事件
                    event_bus.emit(EventType.STATUS_UPDATE, {"online": is_online})
                    last_status = is_online
                
                # 更新ChatBox状态
                if event_bus.is_target_enabled("chatbox") and is_online:
                    await self.send_strength_status()
        except asyncio.CancelledError:
            logger.info("状态更新任务已取消")
            raise
        except Exception as e:
            logger.exception(f"状态更新任务发生异常: {e}")
    
    async def periodic_send_pulse_data(self):
        """周期性发送波形数据"""
        try:
            logger.info("启动周期性波形数据发送任务")
            fail_count = 0  # 记录连续失败次数
            max_fail_retries = 3  # 最大失败重试次数
            send_interval = 1.5  # 发送间隔秒数，比2秒略小以确保稳定性
            
            # 确保设备状态初始化
            if not hasattr(self, '_initial_pulse_sent'):
                self._initial_pulse_sent = False
            
            while True:
                # 每隔send_interval秒更新一次波形数据，设备需要持续收到波形数据以保持激活状态
                await asyncio.sleep(send_interval)
                
                # 检查设备是否在线
                is_connected = await self.device_adapter.is_connected()
                if not is_connected:
                    logger.debug("设备未连接，跳过波形数据发送")
                    continue
                
                # 每次成功收到数据包并进行初始化后才标记为真正初始化完成
                if not self._initial_pulse_sent and self.app_status_online:
                    self._initial_pulse_sent = True
                
                logger.debug("执行周期性波形数据发送")
                
                # 获取当前通道状态 - 这部分代码有问题，可能是事件总线未正确更新通道状态
                # 直接使用类属性作为波形模式
                pulse_index_a = getattr(self, 'pulse_mode_a', 0)
                pulse_index_b = getattr(self, 'pulse_mode_b', 0)
                
                # 发送波形数据
                try:
                    # A通道
                    logger.debug(f"发送A通道波形，索引: {pulse_index_a}")
                    success_a = await self.set_pulse_data(pulse_index_a, Channel.A)
                    if success_a:
                        logger.debug(f"A通道波形已发送成功, 模式: {PULSE_NAME[pulse_index_a] if pulse_index_a < len(PULSE_NAME) else '未知'}")
                    
                    # B通道
                    logger.debug(f"发送B通道波形，索引: {pulse_index_b}")
                    success_b = await self.set_pulse_data(pulse_index_b, Channel.B)
                    if success_b:
                        logger.debug(f"B通道波形已发送成功, 模式: {PULSE_NAME[pulse_index_b] if pulse_index_b < len(PULSE_NAME) else '未知'}")
                    
                    # 重置失败计数
                    fail_count = 0
                except Exception as e:
                    fail_count += 1
                    if fail_count > max_fail_retries:
                        logger.error(f"波形发送连续失败{fail_count}次，将继续尝试: {e}")
                    else:
                        logger.exception(f"波形发送过程中发生异常: {e}")
                    
                    # 尝试使用基本强度保持设备活跃
                    if fail_count > max_fail_retries:
                        try:
                            # 发送一个固定强度来保持设备活跃
                            logger.info("使用基本强度保持设备活跃")
                            await self.client.set_strength(Channel.A, StrengthOperationType.SET_TO, 1)
                            await self.client.set_strength(Channel.B, StrengthOperationType.SET_TO, 1)
                        except Exception:
                            pass  # 忽略这里的错误
        except asyncio.CancelledError:
            logger.info("波形数据发送任务已取消")
            raise
        except Exception as e:
            logger.exception(f"波形数据发送任务发生异常: {e}")
    
    async def set_pulse_data(self, pulse_index: int, channel: Channel):
        """
        设置波形数据
        :param pulse_index: 波形索引
        :param channel: 通道
        :return: 成功返回True, 失败返回False
        """
        try:
            # 确保波形索引在有效范围内
            if pulse_index < 0 or pulse_index >= len(PULSE_NAME):
                logger.warning(f"波形索引{pulse_index}超出范围，使用默认波形(索引0)")
                pulse_index = 0
            
            # 获取波形数据
            pulse_name = PULSE_NAME[pulse_index]
            pulse_data = PULSE_DATA.get(pulse_name)
            
            if not pulse_data:
                logger.warning(f"无法找到波形数据: {pulse_name}，使用默认波形")
                # 使用默认波形
                pulse_name = PULSE_NAME[0]
                pulse_data = PULSE_DATA.get(pulse_name, [50])  # 默认使用50%强度
            
            # 保存当前的波形模式到对应的通道
            if channel == Channel.A:
                self.pulse_mode_a = pulse_index
            else:
                self.pulse_mode_b = pulse_index
            
            # 发送波形输出事件
            logger.info(f"发送波形: {pulse_name} 到通道 {channel}")
            
            # 直接使用设备适配器发送波形数据
            if hasattr(self, 'device_adapter') and self.device_adapter:
                await self.device_adapter._handle_pulse_output(channel, {
                    "pulse_name": pulse_name,
                    "pulse_data": pulse_data
                })
                
                logger.info(f"波形数据已成功发送到通道{channel}, 模式: {pulse_name}")
                return True
            else:
                logger.error("设备适配器未初始化，无法发送波形数据")
                return False
        except Exception as e:
            logger.exception(f"设置波形数据时发生异常: {e}")
            return False
    
    async def update_current_channel(self, channel_name: str):
        """
        更新当前选择的通道
        :param channel_name: 通道名称
        """
        # 转换通道名称为Channel枚举
        channel = Channel.A if channel_name == "A" else Channel.B
        self.current_select_channel = channel
        
        # 发送通道选择事件
        event_bus.emit(EventType.INPUT_UI_CHANNEL_SELECT, channel_name)
        
        # 更新UI显示
        if self.main_window:
            self.main_window.update_current_channel_display(channel_name)
    
    async def update_strength(self, channel: Union[str, Channel], strength: int):
        """
        更新通道强度
        :param channel: 通道
        :param strength: 强度值
        """
        # 转换通道为字符串
        channel_name = channel if isinstance(channel, str) else ("A" if channel == Channel.A else "B")
        
        # 发送强度变更事件
        event_bus.emit(EventType.INPUT_UI_STRENGTH_CHANGE, channel_name, strength)
    
    async def update_pulse_mode(self, channel: Union[str, Channel], pulse_index: int):
        """
        更新通道波形模式
        :param channel: 通道
        :param pulse_index: 波形索引
        """
        # 转换通道为字符串
        channel_name = channel if isinstance(channel, str) else ("A" if channel == Channel.A else "B")
        
        # 发送波形变更事件
        event_bus.emit(EventType.INPUT_UI_PULSE_CHANGE, channel_name, pulse_index)
    
    async def update_dynamic_bone_mode(self, channel: Union[str, Channel], is_dynamic_mode: bool):
        """
        更新通道动骨模式
        :param channel: 通道
        :param is_dynamic_mode: 是否为动骨模式
        """
        # 转换通道为字符串
        channel_name = channel if isinstance(channel, str) else ("A" if channel == Channel.A else "B")
        
        # 发送模式变更事件
        event_bus.emit(EventType.INPUT_UI_MODE_CHANGE, channel_name, is_dynamic_mode)
    
    async def set_panel_control(self, enabled: bool):
        """
        设置面板控制开关
        :param enabled: 是否启用面板控制
        """
        # 发送面板控制开关事件
        event_bus.emit(EventType.INPUT_UI_PANEL_CONTROL, enabled)
    
    async def toggle_chatbox(self, enabled: bool):
        """
        切换ChatBox开关
        :param enabled: 是否启用ChatBox
        """
        # 发送ChatBox开关事件
        event_bus.emit(EventType.INPUT_UI_CHATBOX_TOGGLE, enabled)
    
    async def send_strength_status(self):
        """发送强度状态消息到ChatBox"""
        # 获取当前通道状态
        channel_a = event_bus.get_channel_state("A")
        channel_b = event_bus.get_channel_state("B")
        
        if channel_a and channel_b:
            # 构建状态消息
            status_message = (
                f"A:{channel_a.get('strength', 0)}% "
                f"B:{channel_b.get('strength', 0)}%"
            )
            
            # 发送ChatBox消息事件
            event_bus.emit(EventType.OUTPUT_CHATBOX_MESSAGE, status_message)
    
    async def connect_device(self):
        """连接设备"""
        result = await self.device_adapter.connect()
        return result
    
    async def disconnect_device(self):
        """断开设备连接"""
        await self.device_adapter.disconnect()
    
    async def is_device_connected(self):
        """检查设备是否连接"""
        return await self.device_adapter.is_connected()
    
    async def cleanup(self):
        """清理资源"""
        # 取消定时任务
        if self.send_status_task:
            self.send_status_task.cancel()
            try:
                await self.send_status_task
            except asyncio.CancelledError:
                pass
        
        if self.send_pulse_task:
            self.send_pulse_task.cancel()
            try:
                await self.send_pulse_task
            except asyncio.CancelledError:
                pass
        
        # 断开设备连接
        await self.disconnect_device()

    def update_app_status(self, is_online):
        """更新应用状态，处理设备上线/下线逻辑"""
        logger.info(f"更新应用状态: {'在线' if is_online else '离线'}")
        
        # 更新设备连接状态
        self.app_status_online = is_online
        
        if hasattr(self, 'device_adapter') and self.device_adapter:
            # 更新设备适配器连接状态
            self.device_adapter.set_connected(is_online)
            
        # 触发状态更新事件
        if hasattr(self, 'status_changed_event') and self.status_changed_event:
            self.status_changed_event.set()
            self.status_changed_event.clear()
            
        # 发送状态更新事件
        event_bus.emit(EventType.STATUS_UPDATE, {"online": is_online})
        
        # 更新UI显示
        if hasattr(self, 'main_window') and self.main_window and hasattr(self.main_window.network_config_tab, 'update_connection_status'):
            self.main_window.network_config_tab.update_connection_status(is_online)

        # 只在设备从离线变为在线，且未发送过初始波形数据时发送
        if is_online and not getattr(self, '_initial_pulse_sent', False):
            logger.info("设备首次上线，准备发送初始波形数据")
            self._initial_pulse_sent = True
            # 发送初始波形数据
            asyncio.create_task(self.send_initial_pulse_data())

    async def send_initial_pulse_data(self):
        """发送初始波形数据给设备"""
        logger.info("开始发送初始波形数据")
        
        # 检查设备是否已连接
        if not self.app_status_online or not hasattr(self, 'client') or not self.client:
            logger.warning("设备未连接，无法发送初始波形数据")
            return
            
        try:
            # 发送初始强度设置到两个通道
            logger.info("发送初始A通道强度值")
            await self.client.set_strength(Channel.A, StrengthOperationType.SET_TO, 10)
            logger.info("发送初始B通道强度值")
            await self.client.set_strength(Channel.B, StrengthOperationType.SET_TO, 10)
            
            # 短暂延迟确保设备有时间处理
            await asyncio.sleep(0.5)
            
            # 发送A通道波形
            pulse_mode_a = getattr(self, 'pulse_mode_a', 0)
            logger.info(f"发送初始A通道波形，索引: {pulse_mode_a}")
            success_a = await self.set_pulse_data(pulse_mode_a, Channel.A)
            
            # 发送B通道波形
            pulse_mode_b = getattr(self, 'pulse_mode_b', 0)
            logger.info(f"发送初始B通道波形，索引: {pulse_mode_b}")
            success_b = await self.set_pulse_data(pulse_mode_b, Channel.B)
            
            # 确保周期性发送波形数据的任务已启动
            if not hasattr(self, '_waveform_task') or not self._waveform_task or self._waveform_task.done():
                logger.info("启动周期性波形发送任务")
                self._waveform_task = asyncio.create_task(self.periodic_send_pulse_data())
                
            logger.info("初始波形数据已成功发送")
        except Exception as e:
            logger.error(f"发送初始波形数据失败: {str(e)}")
            # 失败时重置标志，允许下次重试
            self._initial_pulse_sent = False
