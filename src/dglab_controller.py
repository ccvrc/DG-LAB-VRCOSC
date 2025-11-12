"""
dglab_controller.py
"""
import asyncio
import math
import time
import uuid
from enum import Enum

from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from pulse_data import PULSE_DATA, PULSE_NAME

import logging

from command_types import CommandType, ChannelCommand

logger = logging.getLogger(__name__)


class ChannelCommand:
    def __init__(self, command_type, channel, operation, value, source_id=None, timestamp=None):
        self.command_type = command_type  # 命令类型，决定优先级
        self.channel = channel  # 目标通道
        self.operation = operation  # 操作类型
        self.value = value  # 操作值
        self.source_id = source_id or str(uuid.uuid4())  # 来源标识
        self.timestamp = timestamp or time.time()  # 时间戳
    
    def __lt__(self, other):
        # 优先级比较函数，用于队列排序
        if self.command_type.value != other.command_type.value:
            return self.command_type.value < other.command_type.value
        return self.timestamp < other.timestamp  # 同优先级按时间排序

class DGLabController:
    def __init__(self, client, osc_client, ui_callback=None):
        """
        初始化 DGLabController 实例
        :param client: DGLabWSServer 的客户端实例
        :param osc_client: 用于发送 OSC 回复的客户端实例
        :param is_dynamic_bone_mode 强度控制模式，交互模式通过动骨和Contact控制输出强度，非动骨交互模式下仅可通过按键控制输出
        此处的默认参数会被 UI 界面的默认参数覆盖
        """
        self.client = client
        self.osc_client = osc_client
        self.main_window = ui_callback
        self.last_strength = None  # 记录上次的强度值, 从 app更新, 包含 a b a_limit b_limit
        self.app_status_online = False  # App 端在线情况
        # 功能控制参数
        self.pulse_mode_a = 0  # pulse mode for Channel A (双向 - 更新名称)
        self.pulse_mode_b = 0  # pulse mode for Channel B (双向 - 更新名称)
        self.current_select_channel = Channel.A  # 游戏内面板控制的通道选择, 默认为 A (双向)
        self.fire_mode_strength_step = 30    # 一键开火默认强度 (双向)
        self.adjust_strength_step = 5    # 按钮3和按钮4调节强度的步进值
        self.fire_mode_active = False  # 标记当前是否在进行开火操作
        self.fire_mode_lock = asyncio.Lock()  # 一键开火模式锁
        self.data_updated_event = asyncio.Event()  # 数据更新事件
        self.fire_mode_origin_strength_a = 0  # 进入一键开火模式前的强度值
        self.fire_mode_origin_strength_b = 0
        self.enable_chatbox_status = 1  # ChatBox 发送状态 (双向，游戏内暂无直接开关变量)
        self.previous_chatbox_status = 1
        # 定时任务
        self.send_status_task = asyncio.create_task(self.periodic_status_update())  # 启动ChatBox发送任务
        self.send_pulse_task = asyncio.create_task(self.periodic_send_pulse_data())  # 启动设定波形发送任务
        # 按键延迟触发计时
        self.chatbox_toggle_timer = None
        self.mode_toggle_timer = None
        # 回报速率设置为 1HZ，Updates every 0.1 to 1 seconds as needed based on parameter changes (1 to 10 updates per second), but you shouldn't rely on it for fast sync.
        self.pulse_update_lock = asyncio.Lock()  # 添加波形更新锁
        self.pulse_last_update_time = {}  # 记录每个通道最后波形更新时间
        
        # 命令队列相关
        self.command_queue = asyncio.PriorityQueue()  # 优先级队列
        self.command_processing_task = asyncio.create_task(self.process_commands())
        self.command_sources = {}  # 记录各来源的最后命令时间
        self.source_cooldowns = {  # 各来源的冷却时间（秒）
            CommandType.GUI_COMMAND: 0,  # GUI无冷却
            CommandType.PANEL_COMMAND: 0.1,  # 面板命令冷却
            CommandType.INTERACTION_COMMAND: 0.05,  # 交互命令冷却
            CommandType.TON_COMMAND: 0.2,  # 游戏联动冷却
        }
        
        # 命令类型控制
        self.enable_gui_commands = True  # 默认启用GUI命令
        self.enable_panel_commands = True  # 默认启用面板命令
        self.enable_interaction_commands = True  # 默认启用交互命令 
        self.enable_ton_commands = True  # 默认启用游戏联动命令
        
        # 交互模式控制 - 替代原来的动骨模式标志
        self.enable_interaction_mode_a = True  # 通道A交互模式开关
        self.enable_interaction_mode_b = True  # 通道B交互模式开关
        
        # 通道状态模型
        self.channel_states = {
            Channel.A: {
                "current_strength": 0,
                "target_strength": 0,
                "mode": "interaction" if self.enable_interaction_mode_a else "panel",
                "pulse_mode": self.pulse_mode_a,
                "last_command_source": None,
                "last_command_time": 0,
            },
            Channel.B: {
                "current_strength": 0,
                "target_strength": 0,
                "mode": "interaction" if self.enable_interaction_mode_b else "panel",
                "pulse_mode": self.pulse_mode_b,
                "last_command_source": None, 
                "last_command_time": 0,
            }
        }

    async def periodic_status_update(self):
        """
        周期性通过 ChatBox 发送当前的配置状态
        TODO: ChatBox 消息发送的速率限制是多少？当前的设置还是可能会撞到限制..
        """
        while True:
            try:
                if self.enable_chatbox_status:
                    await self.send_strength_status()
                    self.previous_chatbox_status = True
                elif self.previous_chatbox_status: # clear chatbox
                    self.send_message_to_vrchat_chatbox("")
                    self.previous_chatbox_status = False
            except Exception as e:
                logger.error(f"periodic_status_update 任务中发生错误: {e}")
                await asyncio.sleep(5)  # 延迟后重试
            await asyncio.sleep(3)  # 每 x 秒发送一次

    async def periodic_send_pulse_data(self):
        """
        波形维护后台任务：当波形超过3秒未被更新时发送更新
        该任务直接作为系统维护任务运行，不通过命令队列
        """
        while True:
            try:
                if self.last_strength:  # 当收到设备状态后再发送波形
                    current_time = asyncio.get_event_loop().time()
                    
                    # 使用锁防止并发访问
                    async with self.pulse_update_lock:
                        # 检查A通道是否需要更新（距离上次更新时间超过3秒）
                        if Channel.A not in self.pulse_last_update_time or \
                           current_time - self.pulse_last_update_time.get(Channel.A, 0) > 3:
                            
                            logger.info(f"波形维护：更新A通道波形: {PULSE_NAME[self.pulse_mode_a]}")
                            # A 通道发送当前设定波形
                            specific_pulse_data_a = PULSE_DATA[PULSE_NAME[self.pulse_mode_a]]
                            await self.client.clear_pulses(Channel.A)
                            
                            try:
                                if PULSE_NAME[self.pulse_mode_a] == '压缩' or PULSE_NAME[self.pulse_mode_a] == '节奏步伐':
                                    await self.client.add_pulses(Channel.A, *(specific_pulse_data_a * 3))
                                else:
                                    await self.client.add_pulses(Channel.A, *(specific_pulse_data_a * 5))
                                
                                # 只有在成功发送波形后才更新时间戳
                                self.pulse_last_update_time[Channel.A] = current_time
                            except Exception as e:
                                logger.error(f"A通道波形发送失败: {e}")
                                # 发送失败时删除时间戳，促使下次循环再次尝试
                                if Channel.A in self.pulse_last_update_time:
                                    del self.pulse_last_update_time[Channel.A]
                        
                        # 给设备一点时间处理
                        await asyncio.sleep(0.1)
                        
                        # 检查B通道是否需要更新（距离上次更新时间超过3秒）
                        if Channel.B not in self.pulse_last_update_time or \
                           current_time - self.pulse_last_update_time.get(Channel.B, 0) > 3:
                            
                            logger.info(f"波形维护：更新B通道波形: {PULSE_NAME[self.pulse_mode_b]}")
                            # B 通道发送当前设定波形
                            specific_pulse_data_b = PULSE_DATA[PULSE_NAME[self.pulse_mode_b]]
                            await self.client.clear_pulses(Channel.B)
                            
                            try:
                                if PULSE_NAME[self.pulse_mode_b] == '压缩' or PULSE_NAME[self.pulse_mode_b] == '节奏步伐':
                                    await self.client.add_pulses(Channel.B, *(specific_pulse_data_b * 3))
                                else:
                                    await self.client.add_pulses(Channel.B, *(specific_pulse_data_b * 5))
                                
                                # 只有在成功发送波形后才更新时间戳
                                self.pulse_last_update_time[Channel.B] = current_time
                            except Exception as e:
                                logger.error(f"B通道波形发送失败: {e}")
                                # 发送失败时删除时间戳，促使下次循环再次尝试
                                if Channel.B in self.pulse_last_update_time:
                                    del self.pulse_last_update_time[Channel.B]
            except Exception as e:
                logger.error(f"periodic_send_pulse_data 任务中发生错误: {e}")
                # 发生任何异常时清空所有时间戳，确保下次循环重新尝试发送所有波形
                self.pulse_last_update_time = {}
                await asyncio.sleep(5)  # 延迟后重试
            await asyncio.sleep(3)  # 每 x 秒检查一次

    async def handle_osc_message_pad(self, address, *args):
        """
        处理面板控制的 OSC 消息
        """
        try:
            if not args or len(args) == 0:
                return
            
            value = args[0]
            # 全局控制参数
            if address == "/avatar/parameters/SoundPad/Page":
                await self.set_channel(value)
            elif address == "/avatar/parameters/SoundPad/Volume":
                self.fire_mode_strength_step = int(value * 100)
                logger.info(f"更新一键开火强度为 {self.fire_mode_strength_step}")
                # 更新UI界面
                if self.main_window and hasattr(self.main_window, 'controller_settings_tab'):
                    self.main_window.controller_settings_tab.strength_step_spinbox.blockSignals(True)
                    self.main_window.controller_settings_tab.strength_step_spinbox.setValue(self.fire_mode_strength_step)
                    self.main_window.controller_settings_tab.strength_step_spinbox.blockSignals(False)
            elif address == "/avatar/parameters/SoundPad/PanelControl":
                await self.set_panel_control(value)
            
            # 按键功能转换为命令
            if address == "/avatar/parameters/SoundPad/Button/1":
                await self.set_mode(value, self.current_select_channel)
            elif address == "/avatar/parameters/SoundPad/Button/2":
                if value:  # 只处理按下事件
                    await self.add_command(CommandType.PANEL_COMMAND,
                                         self.current_select_channel,
                                         StrengthOperationType.SET_TO,
                                         0,
                                         "panel_reset")
            elif address == "/avatar/parameters/SoundPad/Button/3":
                if value:  # 只处理按下事件
                    await self.add_command(CommandType.PANEL_COMMAND,
                                         self.current_select_channel,
                                         StrengthOperationType.DECREASE,
                                         self.adjust_strength_step,
                                         "panel_decrease")
            elif address == "/avatar/parameters/SoundPad/Button/4":
                if value:  # 只处理按下事件
                    await self.add_command(CommandType.PANEL_COMMAND,
                                         self.current_select_channel,
                                         StrengthOperationType.INCREASE,
                                         self.adjust_strength_step,
                                         "panel_increase")
            elif address == "/avatar/parameters/SoundPad/Button/5":
                await self.strength_fire_mode(value, self.current_select_channel, self.fire_mode_strength_step, self.last_strength)
            # ChatBox 开关控制
            elif address == "/avatar/parameters/SoundPad/Button/6":
                await self.toggle_chatbox(value)
            # 波形控制 - 可根据需要添加到命令队列
            elif address.startswith("/avatar/parameters/SoundPad/Button/"):
                button_num = int(address.split("/")[-1])
                if button_num >= 7 and button_num <= 22:  # 波形选择按钮
                    pulse_index = button_num - 7
                    if value:  # 按下事件
                        # 使用特定前缀标识波形命令
                        await self.set_pulse_data(value, self.current_select_channel, pulse_index)
        except Exception as e:
            logger.error(f"处理面板 OSC 消息出错: {e}", exc_info=True)

    async def handle_osc_message_pb(self, address, value, channels=None, mapping_ranges=None):
        """
        处理交互控制的 OSC 消息（物理骨等）
        
        :param address: OSC 地址
        :param value: OSC 数据值 (0-1 之间的浮点数)
        :param channels: 要应用的通道列表 ["A", "B"]
        :param mapping_ranges: 映射范围字典 {'A': {'min': 0, 'max': 100}, 'B': {'min': 0, 'max': 100}}
        """
        try:
            if not channels:
                return
            
            # 如果没有提供映射范围，使用默认的 0-100%
            if mapping_ranges is None:
                mapping_ranges = {'A': {'min': 0, 'max': 100}, 'B': {'min': 0, 'max': 100}}
            
            # 修改交互数据处理逻辑
            for channel_name in channels:
                if channel_name == "A" and self.enable_interaction_mode_a:
                    # 只在 last_strength 存在时才发送交互命令
                    if self.last_strength:
                        # 获取A通道的映射范围
                        a_min = mapping_ranges.get('A', {}).get('min', 0) / 100.0
                        a_max = mapping_ranges.get('A', {}).get('max', 100) / 100.0
                        
                        # 确保min <= max
                        if a_min > a_max:
                            a_min, a_max = a_max, a_min
                        
                        # 在映射范围内进行线性映射
                        mapped_percent = a_min + (a_max - a_min) * value
                        mapped_value = int(mapped_percent * self.last_strength.a_limit)
                        
                        await self.add_command(CommandType.INTERACTION_COMMAND,
                                             Channel.A,
                                             StrengthOperationType.SET_TO,
                                             mapped_value,
                                             f"interaction_{address}")
                elif channel_name == "B" and self.enable_interaction_mode_b:
                    # 只在 last_strength 存在时才发送交互命令
                    if self.last_strength:
                        # 获取B通道的映射范围
                        b_min = mapping_ranges.get('B', {}).get('min', 0) / 100.0
                        b_max = mapping_ranges.get('B', {}).get('max', 100) / 100.0
                        
                        # 确保min <= max
                        if b_min > b_max:
                            b_min, b_max = b_max, b_min
                        
                        # 在映射范围内进行线性映射
                        mapped_percent = b_min + (b_max - b_min) * value
                        mapped_value = int(mapped_percent * self.last_strength.b_limit)
                        
                        await self.add_command(CommandType.INTERACTION_COMMAND,
                                             Channel.B,
                                             StrengthOperationType.SET_TO,
                                             mapped_value,
                                             f"interaction_{address}")
        except Exception as e:
            logger.error(f"处理交互 OSC 消息出错: {e}", exc_info=True)

    async def set_pulse_data(self, value, channel, pulse_index):
        """
        立即切换为当前指定波形，清空原有波形
        直接对设备发送波形数据，确保立即生效
        
        :param value: 触发值，用于按钮事件判断，None表示来自UI的调用
        :param channel: 要设置波形的通道
        :param pulse_index: 波形索引
        """
        if value is not None and not value:  # 仅处理按下事件，忽略释放事件，但允许None值(来自UI)
            return
        
        # 更新GUI和内部状态
        if channel == Channel.A:
            old_mode = self.pulse_mode_a
            self.pulse_mode_a = pulse_index
            
            # 使用 blockSignals 阻止UI更新引起的循环调用
            if self.main_window:
                self.main_window.controller_settings_tab.pulse_mode_a_combobox.blockSignals(True)
                try:
                    self.main_window.controller_settings_tab.pulse_mode_a_combobox.setCurrentIndex(pulse_index)
                finally:
                    self.main_window.controller_settings_tab.pulse_mode_a_combobox.blockSignals(False)
            
            # 更新通道状态
            self.channel_states[Channel.A]["pulse_mode"] = pulse_index
        else:
            old_mode = self.pulse_mode_b
            self.pulse_mode_b = pulse_index
            
            # 使用 blockSignals 阻止UI更新引起的循环调用
            if self.main_window:
                self.main_window.controller_settings_tab.pulse_mode_b_combobox.blockSignals(True)
                try:
                    self.main_window.controller_settings_tab.pulse_mode_b_combobox.setCurrentIndex(pulse_index)
                finally:
                    self.main_window.controller_settings_tab.pulse_mode_b_combobox.blockSignals(False)
            
            # 更新通道状态
            self.channel_states[Channel.B]["pulse_mode"] = pulse_index
        
        # 如果模式未变，不进行波形更新
        if value is not None and old_mode == pulse_index:  # 仅对外部触发的检查模式变化
            logger.debug(f"波形模式未变化，跳过更新: {channel} {PULSE_NAME[pulse_index]}")
            return
        
        # 使用锁确保波形更新的原子性
        async with self.pulse_update_lock:
            try:
                logger.info(f"发送波形 {channel} {PULSE_NAME[pulse_index]}")
                await self.client.clear_pulses(channel)  # 清空当前的生效的波形队列
                
                specific_pulse_data = PULSE_DATA[PULSE_NAME[pulse_index]]
                
                if PULSE_NAME[pulse_index] == '压缩' or PULSE_NAME[pulse_index] == '节奏步伐':
                    await self.client.add_pulses(channel, *(specific_pulse_data * 3))
                else:
                    await self.client.add_pulses(channel, *(specific_pulse_data * 5))
                
                # 记录最后更新时间
                self.pulse_last_update_time[channel] = asyncio.get_event_loop().time()
            except Exception as e:
                logger.error(f"设置波形时发生错误: {e}")
                # 在错误发生时强制下一次周期性更新尝试刷新
                if channel in self.pulse_last_update_time:
                    del self.pulse_last_update_time[channel]

    async def chatbox_toggle_timer_handle(self):
        """1秒计时器 计时结束后切换 Chatbox 状态"""
        await asyncio.sleep(1)

        self.enable_chatbox_status = not self.enable_chatbox_status
        mode_name = "开启" if self.enable_chatbox_status else "关闭"
        logger.info("ChatBox显示状态切换为:" + mode_name)
        # 若关闭 ChatBox, 则立即发送一次空字符串
        if not self.enable_chatbox_status:
            self.send_message_to_vrchat_chatbox("")
        self.chatbox_toggle_timer = None
        # 更新UI
        self.main_window.controller_settings_tab.enable_chatbox_status_checkbox.blockSignals(True)  # 防止触发 valueChanged 事件
        self.main_window.controller_settings_tab.enable_chatbox_status_checkbox.setChecked(self.enable_chatbox_status)
        self.main_window.controller_settings_tab.enable_chatbox_status_checkbox.blockSignals(False)

    async def toggle_chatbox(self, value):
        """
        开关 ChatBox 内容发送
        """
        if value == 1: # 按下按键
            if self.chatbox_toggle_timer is not None:
                self.chatbox_toggle_timer.cancel()
            self.chatbox_toggle_timer = asyncio.create_task(self.chatbox_toggle_timer_handle())
        elif value == 0: #松开按键
            if self.chatbox_toggle_timer:
                self.chatbox_toggle_timer.cancel()
                self.chatbox_toggle_timer = None

    async def set_mode_timer_handle(self, channel):
        """
        长按按键切换 面板/交互 模式控制，目前已失效
        TODO: FIX
        """
        await asyncio.sleep(1)

        if channel == Channel.A:
            self.enable_interaction_mode_a = not self.enable_interaction_mode_a
            mode_name = "可交互模式" if self.enable_interaction_mode_a else "面板设置模式"
            logger.info("通道 A 切换为" + mode_name)
            # 更新UI
            if self.main_window:
                self.main_window.controller_settings_tab.enable_interaction_commands_a_checkbox.blockSignals(True)
                self.main_window.controller_settings_tab.enable_interaction_commands_a_checkbox.setChecked(self.enable_interaction_mode_a)
                self.main_window.controller_settings_tab.enable_interaction_commands_a_checkbox.blockSignals(False)
        elif channel == Channel.B:
            self.enable_interaction_mode_b = not self.enable_interaction_mode_b
            mode_name = "可交互模式" if self.enable_interaction_mode_b else "面板设置模式"
            logger.info("通道 B 切换为" + mode_name)
            # 更新UI
            if self.main_window:
                self.main_window.controller_settings_tab.enable_interaction_commands_b_checkbox.blockSignals(True)
                self.main_window.controller_settings_tab.enable_interaction_commands_b_checkbox.setChecked(self.enable_interaction_mode_b)
                self.main_window.controller_settings_tab.enable_interaction_commands_b_checkbox.blockSignals(False)
                
        # 更新总体交互命令启用状态
        if self.main_window:
            self.enable_interaction_commands = self.enable_interaction_mode_a or self.enable_interaction_mode_b

    async def set_mode(self, value, channel):
        """切换通道面板控制/交互模式"""
        if not value:  # 只处理按下事件
            return
            
        if value == 1: # 按下按键
            if self.mode_toggle_timer is not None:
                self.mode_toggle_timer.cancel()
            self.mode_toggle_timer = asyncio.create_task(self.set_mode_timer_handle(channel))
        elif value == 0: #松开按键
            if self.mode_toggle_timer:
                self.mode_toggle_timer.cancel()
                self.mode_toggle_timer = None


    async def strength_fire_mode(self, value, channel, strength, last_strength_mod=None):
        """通过命令队列处理一键开火命令"""
        if not self.last_strength and not last_strength_mod:
            logger.warning("没有获取到当前强度信息，无法执行一键开火")
            return
        
        if value:  # 按下开火
            async with self.fire_mode_lock:
                # 记录当前强度
                if channel == Channel.A:
                    self.fire_mode_origin_strength_a = self.channel_states[Channel.A]["current_strength"]
                else:
                    self.fire_mode_origin_strength_b = self.channel_states[Channel.B]["current_strength"]
                
                # 发送开火命令
                await self.add_command(CommandType.PANEL_COMMAND,
                                      channel,
                                      StrengthOperationType.SET_TO,
                                      strength + self.channel_states[channel]["current_strength"],
                                      "panel_fire_start")
                self.fire_mode_active = True
        else:  # 松开按钮，恢复原强度
            async with self.fire_mode_lock:
                if self.fire_mode_active:
                    # 恢复原强度
                    original_strength = self.fire_mode_origin_strength_a if channel == Channel.A else self.fire_mode_origin_strength_b
                    await self.add_command(CommandType.PANEL_COMMAND,
                                          channel,
                                          StrengthOperationType.SET_TO,
                                          original_strength,
                                          "panel_fire_end")
                    self.fire_mode_active = False

    async def set_strength_step(self, value):
        """
          开火模式步进值设定
        """
        if value > 0.0:
            self.fire_mode_strength_step = math.ceil(self.map_value(value, 0, 100))  # 向上取整
            logger.info(f"current strength step: {self.fire_mode_strength_step}")
            # 更新 UI 组件 (QSpinBox) 以反映新的值
            self.main_window.controller_settings_tab.strength_step_spinbox.blockSignals(True)  # 防止触发 valueChanged 事件
            self.main_window.controller_settings_tab.strength_step_spinbox.setValue(self.fire_mode_strength_step)
            self.main_window.controller_settings_tab.strength_step_spinbox.blockSignals(False)

    async def set_channel(self, value):
        """
        value: INT
        选定当前调节对应的通道, 目前 Page 1-2 为 Channel A， Page 3 为 Channel B
        """
        if value >= 0:
            self.current_select_channel = Channel.A if value <= 1 else Channel.B
            logger.info(f"set activate channel to: {self.current_select_channel}")
            if self.main_window.controller_settings_tab:
                channel_name = "A" if self.current_select_channel == Channel.A else "B"
                self.main_window.controller_settings_tab.update_current_channel_display(channel_name)

    def map_value(self, value, min_value, max_value):
        """
        将 Contact/Physbones 值映射到强度范围
        """
        return min_value + value * (max_value - min_value)

    def send_message_to_vrchat_chatbox(self, message: str):
        '''
        /chatbox/input s b n Input text into the chatbox.
        '''
        self.osc_client.send_message("/chatbox/input", [message, True, False])

    async def send_value_to_vrchat(self, path: str, value):
        '''
        /chatbox/input s b n Input text into the chatbox.
        '''
        self.osc_client.send_message(path, value)

    async def send_strength_status(self):
        """
        通过 ChatBox 发送当前强度数值
        """
        if self.last_strength:
            mode_name_a = "交互" if self.enable_interaction_mode_a else "面板"
            mode_name_b = "交互" if self.enable_interaction_mode_b else "面板"
            channel_strength = f"[A]: {self.last_strength.a} B: {self.last_strength.b}" if self.current_select_channel == Channel.A else f"A: {self.last_strength.a} [B]: {self.last_strength.b}"
            self.send_message_to_vrchat_chatbox(
                f"MAX A: {self.last_strength.a_limit} B: {self.last_strength.b_limit}\n"
                f"Mode A: {mode_name_a} B: {mode_name_b} \n"
                f"Pulse A: {PULSE_NAME[self.pulse_mode_a]} B: {PULSE_NAME[self.pulse_mode_b]} \n"
                f"Fire Step: {self.fire_mode_strength_step} Adjust Step: {self.adjust_strength_step}\n"
                f"Current: {channel_strength} \n"
            )
        else:
            self.send_message_to_vrchat_chatbox("未连接")

    async def add_command(self, command_type, channel, operation, value, source_id=None):
        """添加命令到队列，带冷却检查"""
        now = time.time()
        source_key = f"{command_type.name}_{source_id or 'default'}"
        
        # 检查冷却时间
        if source_key in self.command_sources:
            last_time = self.command_sources[source_key]
            cooldown = self.source_cooldowns[command_type]
            if now - last_time < cooldown:
                logger.debug(f"命令在冷却期内，已忽略: {command_type.name}, 来源: {source_id}")
                return  # 在冷却期内，忽略命令
        
        # 记录时间并加入队列
        self.command_sources[source_key] = now
        await self.command_queue.put(ChannelCommand(command_type, channel, operation, value, source_id, now))
        logger.debug(f"已添加命令: {command_type.name}, 通道: {channel}, 操作: {operation}, 值: {value}")

    async def process_commands(self):
        """处理命令队列的主循环"""
        while True:
            try:
                command = await self.command_queue.get()
                
                # 检查命令类型是否被启用
                command_enabled = False
                if command.command_type == CommandType.GUI_COMMAND and self.enable_gui_commands:
                    command_enabled = True
                elif command.command_type == CommandType.PANEL_COMMAND and self.enable_panel_commands:
                    command_enabled = True
                elif command.command_type == CommandType.INTERACTION_COMMAND and self.enable_interaction_commands:
                    command_enabled = True
                elif command.command_type == CommandType.TON_COMMAND and self.enable_ton_commands:
                    command_enabled = True
                
                # 如果命令类型被禁用，则跳过处理
                if not command_enabled:
                    logger.debug(f"命令类型 {command.command_type.name} 已禁用，跳过处理")
                    self.command_queue.task_done()
                    continue
                
                # 更新通道状态模型
                channel_state = self.channel_states[command.channel]
                channel_state["last_command_source"] = command.source_id
                channel_state["last_command_time"] = command.timestamp
                
                # 根据命令类型和操作进行相应处理
                if command.operation == StrengthOperationType.SET_TO:
                    channel_state["target_strength"] = command.value
                    await self.client.set_strength(command.channel, command.operation, command.value)
                    logger.info(f"已设置通道 {command.channel.name} 强度为 {command.value}, 来源: {command.source_id}")
                elif command.operation == StrengthOperationType.INCREASE:
                    # 获取当前通道限制
                    limit = self.last_strength.a_limit if command.channel == Channel.A else self.last_strength.b_limit
                    # 计算新目标强度并应用
                    new_strength = min(channel_state["current_strength"] + command.value, limit)
                    channel_state["target_strength"] = new_strength
                    await self.client.set_strength(command.channel, StrengthOperationType.SET_TO, new_strength)
                    logger.info(f"已增加通道 {command.channel.name} 强度至 {new_strength}, 增量: {command.value}, 来源: {command.source_id}")
                elif command.operation == StrengthOperationType.DECREASE:
                    # 计算新目标强度并应用
                    new_strength = max(channel_state["current_strength"] - command.value, 0)
                    channel_state["target_strength"] = new_strength
                    await self.client.set_strength(command.channel, StrengthOperationType.SET_TO, new_strength)
                    logger.info(f"已减少通道 {command.channel.name} 强度至 {new_strength}, 减量: {command.value}, 来源: {command.source_id}")
                
                # 更新当前强度记录
                channel_state["current_strength"] = channel_state["target_strength"]
                
                # 完成命令处理
                self.command_queue.task_done()
                
            except Exception as e:
                logger.error(f"处理命令时出错: {e}", exc_info=True)
                await asyncio.sleep(0.1)  # 错误后短暂延迟

    async def handle_ton_damage(self, damage_value, damage_multiplier=1.0):
        """处理来自 ToN 游戏的伤害数据"""
        try:
            if damage_value <= 0:
                return
            
            # 计算增加的强度
            strength_increase = int(damage_value * damage_multiplier)
            logger.info(f"收到 ToN 伤害 {damage_value}，增加强度 {strength_increase}")
            
            # 对所有启用的通道应用伤害
            channels_to_affect = []
            if self.enable_interaction_mode_a:
                channels_to_affect.append(Channel.A)
            if self.enable_interaction_mode_b:
                channels_to_affect.append(Channel.B)
            
            # 如果没有启用任何通道，默认使用 A 通道
            if not channels_to_affect:
                channels_to_affect = [Channel.A]
            
            # 发送命令增加强度
            for channel in channels_to_affect:
                await self.add_command(CommandType.TON_COMMAND,
                                     channel,
                                     StrengthOperationType.INCREASE,
                                     strength_increase,
                                     "ton_damage")
        except Exception as e:
            logger.error(f"处理 ToN 伤害数据出错: {e}", exc_info=True)

    async def handle_ton_death(self, penalty_strength, penalty_time):
        """处理 ToN 游戏死亡惩罚"""
        try:
            logger.warning(f"触发死亡惩罚: 强度={penalty_strength}, 时间={penalty_time}秒")
            
            # 获取启用的通道
            channels_to_affect = []
            if self.enable_interaction_mode_a:
                channels_to_affect.append(Channel.A)
            if self.enable_interaction_mode_b:
                channels_to_affect.append(Channel.B)
            
            # 如果没有启用任何通道，默认使用 A 通道
            if not channels_to_affect:
                channels_to_affect = [Channel.A]
            
            # 记录原始强度
            original_strengths = {}
            for channel in channels_to_affect:
                original_strengths[channel] = self.channel_states[channel]["current_strength"]
            
            # 设置惩罚强度
            for channel in channels_to_affect:
                await self.add_command(CommandType.TON_COMMAND,
                                     channel,
                                     StrengthOperationType.SET_TO,
                                     penalty_strength,
                                     "ton_death_penalty")
            
            # 等待惩罚时间结束
            await asyncio.sleep(penalty_time)
            
            # 恢复原始强度
            for channel in channels_to_affect:
                await self.add_command(CommandType.TON_COMMAND,
                                     channel,
                                     StrengthOperationType.SET_TO,
                                     original_strengths[channel],
                                     "ton_death_penalty_end")
        except Exception as e:
            logger.error(f"处理 ToN 死亡惩罚出错: {e}", exc_info=True)
