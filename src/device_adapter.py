"""
device_adapter.py - 设备适配器模块
负责处理设备输出事件，将事件转换为设备可以理解的指令
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple

from event_bus import get_event_bus, EventType
from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer

logger = logging.getLogger(__name__)
event_bus = get_event_bus()

class DeviceAdapter:
    """设备适配器类，负责处理设备输出事件"""
    
    # 添加一个类变量用于跟踪警告状态
    _client_capability_logged = False
    
    def __init__(self, dglab_client):
        """
        初始化设备适配器
        :param dglab_client: DGLabWSServer 的客户端实例
        """
        self.client = dglab_client
        self.connected = False
        self.lock = asyncio.Lock()
        
        # 跟踪活动的波形序列发送任务
        self._active_sequences = {}
        
        # 注册事件监听器
        self._register_event_listeners()
    
    def _register_event_listeners(self):
        """注册事件监听器"""
        event_bus.on(EventType.OUTPUT_DEVICE_STRENGTH, self._handle_strength_output)
        event_bus.on(EventType.OUTPUT_DEVICE_PULSE, self._handle_pulse_output)
    
    async def _handle_strength_output(self, channel: Channel, strength: int):
        """
        处理强度输出事件
        :param channel: 通道
        :param strength: 强度值
        """
        logger.debug(f"设备强度输出: 通道{channel} 强度{strength}")
        
        # 接收到强度输出指令时，自动更新连接状态为"已连接"
        if not self.connected:
            self.set_connected(True)
            logger.info("通过强度输出活动自动更新设备状态为已连接")
        
        if not self.connected:
            logger.warning("设备未连接，无法发送强度输出")
            return
        
        try:
            async with self.lock:
                # 直接使用set_strength方法
                result = await self.client.set_strength(channel, StrengthOperationType.SET_TO, strength)
                # 部分客户端实现可能返回None而不是RetCode，我们将None视为成功
                if result is not None and result != RetCode.SUCCESS:
                    logger.error(f"设置通道{channel}强度失败: {result}")
                    return False
                else:
                    logger.debug(f"设置通道{channel}强度成功: {strength}")
                    return True
        except Exception as e:
            logger.exception(f"设置通道{channel}强度时发生异常: {e}")
            return False
    
    async def _handle_pulse_output(self, channel: Channel, pulse_data: Dict[str, Any]):
        """
        处理波形输出事件
        :param channel: 通道
        :param pulse_data: 波形数据字典，包含pulse_name和pulse_data字段
        """
        logger.info(f"设备波形输出: 通道{channel} 波形{pulse_data['pulse_name']}")
        
        # 接收到波形输出指令时，自动更新连接状态为"已连接"
        if not self.connected:
            self.set_connected(True)
            logger.info("通过波形输出活动自动更新设备状态为已连接")
        
        if not self.connected:
            logger.warning("设备未连接，无法发送波形输出")
            return
        
        try:
            async with self.lock:
                # 从pulse_data提取波形数据
                raw_pulse_data = pulse_data.get('pulse_data', [])
                
                # 尝试使用set_pulse_data方法
                try:
                    # 检查客户端是否有set_pulse_data方法
                    if hasattr(self.client, 'set_pulse_data') and callable(getattr(self.client, 'set_pulse_data')):
                        logger.info(f"使用客户端原生方法发送波形数据: {channel}")
                        # 检查set_pulse_data方法需要的参数数量
                        import inspect
                        sig = inspect.signature(self.client.set_pulse_data)
                        param_count = len(sig.parameters)
                        
                        if param_count >= 2:  # 至少需要channel和data两个参数
                            result = await self.client.set_pulse_data(channel, raw_pulse_data)
                            logger.info(f"原生set_pulse_data调用结果: {result}")
                        else:
                            logger.warning(f"客户端的set_pulse_data方法参数不正确，使用兼容模式")
                            result = await self.set_pulse_data(channel, raw_pulse_data)
                    else:
                        # 使用自定义方法
                        logger.info(f"使用兼容模式发送波形数据: {channel}")
                        result = await self.set_pulse_data(channel, raw_pulse_data)
                        
                    if result != RetCode.SUCCESS:
                        logger.error(f"设置通道{channel}波形失败: {result}")
                    else:
                        logger.info(f"设置通道{channel}波形成功: {pulse_data['pulse_name']}")
                except Exception as e:
                    logger.exception(f"设置通道{channel}波形时发生异常: {e}")
                    # 失败时，至少设置一个基本强度值
                    if raw_pulse_data and len(raw_pulse_data) > 0:
                        # 使用波形的第一个值作为强度
                        avg_strength = 50  # 默认中等强度
                        await self._handle_strength_output(channel, avg_strength)
                        logger.info(f"已使用基本强度({avg_strength})代替波形输出")
        except Exception as e:
            logger.exception(f"设置通道{channel}波形时发生异常: {e}")
    
    async def set_pulse_data(self, channel: Channel, pulse_data: List):
        """
        兼容性方法：设置波形数据
        由于客户端不支持set_pulse_data方法，我们使用最大强度值来模拟波形
        
        :param channel: 通道
        :param pulse_data: 波形数据列表
        :return: 返回码
        """
        logger.info(f"使用兼容模式设置通道{channel}波形，数据点数: {len(pulse_data) if pulse_data else 0}")
        
        # 如果波形数据为空，使用默认强度
        if not pulse_data or len(pulse_data) == 0:
            logger.warning("波形数据为空，使用默认强度")
            await self._handle_strength_output(channel, 50)
            return RetCode.SUCCESS
        
        try:
            # 提取所有强度值
            all_strengths = []
            
            for point in pulse_data:
                if isinstance(point, tuple) and len(point) == 2:
                    # 提取强度信息
                    # 格式: ((duration_tuple), (strength_tuple))
                    strength_tuple = point[1]
                    
                    # 获取强度值
                    if isinstance(strength_tuple, tuple) and strength_tuple:
                        max_val = max(strength_tuple) if strength_tuple else 0
                        all_strengths.append(max_val)
            
            # 如果没有提取到强度值，使用默认强度
            if not all_strengths:
                logger.warning("无法从波形数据中提取强度值，使用默认强度")
                await self._handle_strength_output(channel, 50)
                return RetCode.SUCCESS
            
            # 计算强度代表值
            max_strength = max(all_strengths)
            avg_strength = sum(all_strengths) / len(all_strengths)
            
            # 决定使用哪个强度值作为代表
            # 这里我们选择最大值，因为它通常能够提供最明显的感觉
            effective_strength = int(max_strength)
            
            logger.info(f"从波形数据提取的强度值 - 最大值: {max_strength}, 平均值: {avg_strength:.1f}, 使用: {effective_strength}")
            
            # 应用强度值
            result = await self._handle_strength_output(channel, effective_strength)
            
            # 设置连接状态为已连接
            self.connected = True
            
            if result:
                logger.info(f"成功应用波形强度{effective_strength}到通道{channel}")
                return RetCode.SUCCESS
            else:
                logger.warning(f"应用波形强度到通道{channel}失败")
                return RetCode.UNKNOWN_ERROR
        except Exception as e:
            logger.exception(f"处理波形数据时发生异常: {e}")
            return RetCode.UNKNOWN_ERROR
    
    async def _send_strength_sequence(self, channel: Channel, strength_sequence: List[tuple]):
        """
        按顺序发送一系列强度值来模拟波形
        :param channel: 通道
        :param strength_sequence: 强度序列，每个元素为(强度值, 持续时间)的元组
        """
        try:
            logger.info(f"开始发送强度序列到通道{channel}，共{len(strength_sequence)}个点")
            
            # 循环发送强度序列
            sequence_id = id(strength_sequence)  # 用于跟踪当前序列
            self._active_sequences[channel] = sequence_id
            
            # 连续失败计数
            consecutive_failures = 0
            max_failures = 3
            
            # 只取少量关键点来减少发送频率
            simplified_sequence = self._simplify_sequence(strength_sequence)
            logger.info(f"已简化序列从{len(strength_sequence)}点减少到{len(simplified_sequence)}点")
            
            for i, (strength, duration) in enumerate(simplified_sequence):
                # 检查是否有新的序列请求，如果有则终止当前序列
                if self._active_sequences.get(channel) != sequence_id:
                    logger.info(f"通道{channel}的强度序列已被更新的序列替代，停止当前序列")
                    break
                
                # 将持续时间从毫秒转换为秒，最小0.1秒
                duration_sec = max(0.1, duration / 1000.0)
                
                # 发送当前强度值
                success = await self._handle_strength_output(channel, int(strength))
                
                # 处理发送结果
                if success:
                    logger.debug(f"发送强度值{strength}到通道{channel}（序列点{i+1}/{len(simplified_sequence)}）")
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        logger.warning(f"连续{consecutive_failures}次发送失败，中止序列")
                        break
                
                # 等待指定的持续时间
                await asyncio.sleep(duration_sec)
            
            logger.info(f"强度序列发送完成，通道{channel}")
        except Exception as e:
            logger.exception(f"发送强度序列时发生异常: {e}")
            
    def _simplify_sequence(self, sequence: List[tuple]) -> List[tuple]:
        """
        简化序列，减少点的数量，只保留关键转折点
        :param sequence: 原始序列
        :return: 简化后的序列
        """
        if len(sequence) <= 3:
            return sequence
            
        # 提取起点、关键变化点和终点
        simplified = [sequence[0]]  # 起点
        
        # 查找关键转折点（强度变化大的点）
        last_strength = sequence[0][0]
        for i in range(1, len(sequence)-1):
            current_strength = sequence[i][0]
            # 如果强度变化超过20%，视为关键点
            if abs(current_strength - last_strength) >= 20:
                simplified.append(sequence[i])
                last_strength = current_strength
        
        # 添加终点
        if simplified[-1] != sequence[-1]:
            simplified.append(sequence[-1])
            
        return simplified
    
    async def connect(self):
        """连接设备"""
        try:
            result = await self.client.connect()
            if result == RetCode.SUCCESS:
                self.connected = True
                logger.info("设备连接成功")
                # 发送设备连接状态事件
                event_bus.emit(EventType.STATUS_DEVICE_CONNECT, True)
                return True
            else:
                logger.error(f"设备连接失败: {result}")
                return False
        except Exception as e:
            logger.exception(f"设备连接时发生异常: {e}")
            return False
    
    async def disconnect(self):
        """断开设备连接"""
        try:
            await self.client.disconnect()
            self.connected = False
            logger.info("设备断开连接")
            # 发送设备连接状态事件
            event_bus.emit(EventType.STATUS_DEVICE_CONNECT, False)
        except Exception as e:
            logger.exception(f"设备断开连接时发生异常: {e}")
    
    async def is_connected(self):
        """检查设备是否连接"""
        try:
            # 兼容不同版本的客户端实现
            if hasattr(self.client, 'is_online'):
                result = await self.client.is_online()
                self.connected = result
                return result
            elif hasattr(self.client, 'get_status'):
                status = await self.client.get_status()
                # 根据状态判断是否在线，具体逻辑需要根据实际情况调整
                self.connected = status is not None
                return self.connected
            else:
                # 如果都不支持，使用当前连接状态
                # 只在第一次检测到时输出警告
                if not DeviceAdapter._client_capability_logged:
                    logger.warning("客户端对象不支持检查连接状态的方法，使用记录的状态值")
                    DeviceAdapter._client_capability_logged = True
                else:
                    # 后续使用更低的日志级别
                    logger.debug("使用记录的连接状态值")
                return self.connected
        except Exception as e:
            logger.exception(f"检查设备连接状态时发生异常: {e}")
            return False
    
    # 添加一个新方法，用于直接设置连接状态
    def set_connected(self, connected: bool):
        """直接设置连接状态"""
        if self.connected != connected:
            self.connected = connected
            # 发送设备连接状态事件
            event_bus.emit(EventType.STATUS_DEVICE_CONNECT, connected)
            logger.info(f"设备连接状态已更新: {'已连接' if connected else '未连接'}")
        return self.connected

# 创建设备适配器单例
_adapter_instance = None

def init_device_adapter(dglab_client):
    """初始化设备适配器"""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = DeviceAdapter(dglab_client)
    return _adapter_instance

def get_device_adapter():
    """获取设备适配器单例"""
    global _adapter_instance
    if _adapter_instance is None:
        raise RuntimeError("设备适配器未初始化")
    return _adapter_instance 