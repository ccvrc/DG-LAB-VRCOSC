"""
dglab_controller.py
"""
import asyncio
import math

from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from pulse_data import PULSE_DATA, PULSE_NAME

import logging

logger = logging.getLogger(__name__)


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
        self.enable_panel_control = True   # 禁用面板控制功能 (双向)
        self.is_dynamic_bone_mode_a = False  # Default mode for Channel A (仅程序端)
        self.is_dynamic_bone_mode_b = False  # Default mode for Channel B (仅程序端)
        self.pulse_mode_a = 0  # pulse mode for Channel A (双向 - 更新名称)
        self.pulse_mode_b = 0  # pulse mode for Channel B (双向 - 更新名称)
        self.current_select_channel = Channel.A  # 游戏内面板控制的通道选择, 默认为 A (双向)
        self.fire_mode_strength_step = 30    # 一键开火默认强度 (双向)
        self.fire_mode_active = False  # 标记当前是否在进行开火操作
        self.fire_mode_lock = asyncio.Lock()  # 一键开火模式锁
        self.data_updated_event = asyncio.Event()  # 数据更新事件
        self.fire_mode_origin_strength_a = 0  # 进入一键开火模式前的强度值
        self.fire_mode_origin_strength_b = 0
        self.enable_chatbox_status = 1  # ChatBox 发送状态 (双向，游戏内暂无直接开关变量)
        self.previous_chatbox_status = 1  # ChatBox 状态记录, 关闭 ChatBox 后进行内容清除
        # 定时任务
        self.send_status_task = asyncio.create_task(self.periodic_status_update())  # 启动ChatBox发送任务
        self.send_pulse_task = asyncio.create_task(self.periodic_send_pulse_data())  # 启动设定波形发送任务
        # 按键延迟触发计时
        self.chatbox_toggle_timer = None
        self.set_mode_timer = None
        #TODO: 增加状态消息OSC发送, 比使用 ChatBox 反馈更快
        # 回报速率设置为 1HZ，Updates every 0.1 to 1 seconds as needed based on parameter changes (1 to 10 updates per second), but you shouldn't rely on it for fast sync.
        self.pulse_update_lock = asyncio.Lock()  # 添加波形更新锁
        self.pulse_last_update_time = {}  # 记录每个通道最后波形更新时间
        self.osc_command_queue = {}  # 按地址存储最新的OSC命令
        self.osc_queue_lock = asyncio.Lock()  # OSC队列锁
        self.osc_processing_task = asyncio.create_task(self.process_osc_commands())  # 启动OSC处理任务

    async def periodic_status_update(self):
        """
        周期性通过 ChatBox 发送当前的配置状态
        TODO: ChatBox 消息发送的速率限制是多少？当前的设置还是会撞到限制..
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
        """顺序发送波形，定期刷新设备波形数据"""
        while True:
            try:
                if self.last_strength:  # 当收到设备状态后再发送波形
                    current_time = asyncio.get_event_loop().time()
                    
                    # 使用锁防止并发访问
                    async with self.pulse_update_lock:
                        # 检查A通道是否需要更新（距离上次更新时间超过3秒）
                        if Channel.A not in self.pulse_last_update_time or \
                           current_time - self.pulse_last_update_time.get(Channel.A, 0) > 3:
                            
                            logger.info(f"周期更新A通道波形: {PULSE_NAME[self.pulse_mode_a]}")
                            # A 通道发送当前设定波形
                            specific_pulse_data_a = PULSE_DATA[PULSE_NAME[self.pulse_mode_a]]
                            await self.client.clear_pulses(Channel.A)
                            
                            if PULSE_NAME[self.pulse_mode_a] == '压缩' or PULSE_NAME[self.pulse_mode_a] == '节奏步伐':
                                await self.client.add_pulses(Channel.A, *(specific_pulse_data_a * 3))
                            else:
                                await self.client.add_pulses(Channel.A, *(specific_pulse_data_a * 5))
                            
                            self.pulse_last_update_time[Channel.A] = current_time
                        
                        # 给设备一点时间处理
                        await asyncio.sleep(0.1)
                        
                        # 检查B通道是否需要更新（距离上次更新时间超过3秒）
                        if Channel.B not in self.pulse_last_update_time or \
                           current_time - self.pulse_last_update_time.get(Channel.B, 0) > 3:
                            
                            logger.info(f"周期更新B通道波形: {PULSE_NAME[self.pulse_mode_b]}")
                            # B 通道发送当前设定波形
                            specific_pulse_data_b = PULSE_DATA[PULSE_NAME[self.pulse_mode_b]]
                            await self.client.clear_pulses(Channel.B)
                            
                            if PULSE_NAME[self.pulse_mode_b] == '压缩' or PULSE_NAME[self.pulse_mode_b] == '节奏步伐':
                                await self.client.add_pulses(Channel.B, *(specific_pulse_data_b * 3))
                            else:
                                await self.client.add_pulses(Channel.B, *(specific_pulse_data_b * 5))
                            
                            self.pulse_last_update_time[Channel.B] = current_time
            except Exception as e:
                logger.error(f"periodic_send_pulse_data 任务中发生错误: {e}")
                await asyncio.sleep(5)  # 延迟后重试
            await asyncio.sleep(3)  # 每 x 秒发送一次

    async def process_osc_commands(self):
        """处理OSC命令队列，合并相同地址的命令，保留最新值"""
        while True:
            try:
                # 获取并清空队列
                async with self.osc_queue_lock:
                    current_commands = self.osc_command_queue.copy()
                    self.osc_command_queue.clear()
                
                # 处理当前队列中的命令
                for address, command_data in current_commands.items():
                    try:
                        args, channels = command_data['args'], command_data.get('channels', None)
                        if channels:  # 这是物理骨骼控制命令
                            await self.handle_osc_message_pb(address, *args, channels=channels)
                        else:  # 这是面板控制命令
                            await self._original_handle_osc_message_pad(address, *args)
                    except Exception as e:
                        logger.error(f"处理OSC命令时发生错误: {address} {args} - {e}")
                
                # 短暂休眠后再次处理队列
                await asyncio.sleep(0.05)  # 20Hz 处理频率，可根据需要调整
            except Exception as e:
                logger.error(f"OSC命令处理循环发生错误: {e}")
                await asyncio.sleep(1)  # 错误后延迟重试

    async def handle_osc_message_pad(self, address, *args):
        """
        处理 OSC 消息 - 改为入队操作
        1. Bool: Bool 类型变量触发时，VRC 会先后发送 True 与 False, 回调中仅处理 True
        2. Float: -1.0 to 1.0， 但对于 Contact 与  Physbones 来说范围为 0.0-1.0
        """
        # 记录收到的OSC消息
        logger.debug(f"收到OSC消息: {address} 参数: {args}")
        
        # 某些特定命令可以直接处理(不走队列)
        if address == "/avatar/parameters/SoundPad/PanelControl":
            await self.set_panel_control(args[0])
            return
        
        # 将消息放入队列，仅保留每个地址的最新命令
        async with self.osc_queue_lock:
            self.osc_command_queue[address] = {
                'args': args,
                'channels': None  # 面板控制没有通道信息
            }

    async def handle_osc_message_pb(self, address, *args, channels=None):
        """
        处理物理骨骼 OSC 消息 - 改为入队操作
        """
        # 检查是否禁用面板控制功能
        if not self.enable_panel_control:
            logger.debug(f"已禁用面板控制功能，忽略命令: {address}")
            return
        
        # 将消息放入队列，仅保留每个地址的最新命令
        async with self.osc_queue_lock:
            self.osc_command_queue[address] = {
                'args': args,
                'channels': channels  # 存储通道信息
            }

    async def handle_osc_message_internal_pad(self, address, *args):
        """内部处理面板控制OSC命令"""
        # 面板控制功能禁用检查
        if not self.enable_panel_control and address.startswith("/avatar/parameters/SoundPad/"):
            if address != "/avatar/parameters/SoundPad/PanelControl":
                logger.debug(f"已禁用面板控制功能，忽略命令: {address}")
                return
        
        # 波形切换处理
        if address == "/avatar/parameters/SoundPad/Button/1":
            await self.set_mode(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/2":
            await self.reset_strength(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/3":
            await self.set_pulse_data(args[0], self.current_select_channel, 0)  # 呼吸
        elif address == "/avatar/parameters/SoundPad/Button/4":
            await self.set_pulse_data(args[0], self.current_select_channel, 1)  # 潮汐
        elif address == "/avatar/parameters/SoundPad/Button/5":
            await self.set_pulse_data(args[0], self.current_select_channel, 2)  # 连击
        elif address == "/avatar/parameters/SoundPad/Button/6":
            await self.set_pulse_data(args[0], self.current_select_channel, 3)  # 快速按捏
        elif address == "/avatar/parameters/SoundPad/Button/7":
            await self.set_pulse_data(args[0], self.current_select_channel, 13)  # 信号灯
        elif address == "/avatar/parameters/SoundPad/Button/8":
            await self.set_pulse_data(args[0], self.current_select_channel, 14)  # 挑逗1
        # 通道选择 
        elif address == "/avatar/parameters/SoundPad/Page" and args[0] > 0:
            await self.toggle_active_channel()
        # 面板音量控制
        elif address == "/avatar/parameters/SoundPad/Volume":
            await self.set_strength_step(args[0])
        # 其他命令处理...

    async def handle_osc_message_internal_pb(self, address, *args, channels=None):
        """内部处理物理骨骼OSC命令"""
        try:
            if not channels:
                logger.warning(f"物理骨骼OSC命令缺少通道信息: {address}")
                return
            
            value = args[0]  # OSC参数值
            
            # 处理A通道
            if 'A' in channels and self.is_dynamic_bone_mode_a:
                # 确保值在0到1之间
                if 0 <= value <= 1:
                    # 激活动态骨骼控制A通道
                    if self.last_strength:  # 确保有上次的强度数据
                        target_strength_a = int(self.map_value(value, 0, self.last_strength.a_limit))
                        await self.client.set_strength(Channel.A, StrengthOperationType.SET_TO, target_strength_a)
                        if self.last_strength:  # 更新本地状态
                            self.last_strength.a = target_strength_a
            
            # 处理B通道
            if 'B' in channels and self.is_dynamic_bone_mode_b:
                # 确保值在0到1之间
                if 0 <= value <= 1:
                    # 激活动态骨骼控制B通道
                    if self.last_strength:  # 确保有上次的强度数据
                        target_strength_b = int(self.map_value(value, 0, self.last_strength.b_limit))
                        await self.client.set_strength(Channel.B, StrengthOperationType.SET_TO, target_strength_b)
                        if self.last_strength:  # 更新本地状态
                            self.last_strength.b = target_strength_b
        except Exception as e:
            logger.error(f"处理物理骨骼OSC命令时发生错误: {e}")

    async def set_pulse_data(self, value, channel, pulse_index):
        """
        立即切换为当前指定波形，清空原有波形
        """
        if value is not None and not value:  # 仅处理按下事件，忽略释放事件，但允许None值(来自UI)
            return
        
        # 更新GUI和内部状态
        if channel == Channel.A:
            old_mode = self.pulse_mode_a
            self.pulse_mode_a = pulse_index
            if self.main_window:
                self.main_window.controller_settings_tab.pulse_mode_a_combobox.setCurrentIndex(pulse_index)
        else:
            old_mode = self.pulse_mode_b
            self.pulse_mode_b = pulse_index
            if self.main_window:
                self.main_window.controller_settings_tab.pulse_mode_b_combobox.setCurrentIndex(pulse_index)
        
        # 如果模式未变，不进行波形更新
        if value is not None and old_mode == pulse_index:  # 仅对外部触发的检查模式变化
            logger.debug(f"波形模式未变化，跳过更新: {channel} {PULSE_NAME[pulse_index]}")
            return
        
        # 使用锁确保波形更新的原子性
        async with self.pulse_update_lock:
            try:
                await self.client.clear_pulses(channel)  # 清空当前的生效的波形队列
                
                logger.info(f"开始发送波形 {channel} {PULSE_NAME[pulse_index]}")
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

    async def set_float_output(self, value, channel):
        """
        动骨与碰撞体激活对应通道输出
        """
        if value >= 0.0:
            if channel == Channel.A and self.is_dynamic_bone_mode_a:
                final_output_a = math.ceil(
                    self.map_value(value, self.last_strength.a_limit * 0.2, self.last_strength.a_limit))
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, final_output_a)
            elif channel == Channel.B and self.is_dynamic_bone_mode_b:
                final_output_b = math.ceil(
                    self.map_value(value, self.last_strength.b_limit * 0.2, self.last_strength.b_limit))
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, final_output_b)

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
        TODO: 修改为按键按下 3 秒后触发 enable_chatbox_status 的变更
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
        await asyncio.sleep(1)

        if channel == Channel.A:
            self.is_dynamic_bone_mode_a = not self.is_dynamic_bone_mode_a
            mode_name = "可交互模式" if self.is_dynamic_bone_mode_a else "面板设置模式"
            logger.info("通道 A 切换为" + mode_name)
            # 更新UI
            self.main_window.controller_settings_tab.dynamic_bone_mode_a_checkbox.blockSignals(True)  # 防止触发 valueChanged 事件
            self.main_window.controller_settings_tab.dynamic_bone_mode_a_checkbox.setChecked(self.is_dynamic_bone_mode_a)
            self.main_window.controller_settings_tab.dynamic_bone_mode_a_checkbox.blockSignals(False)
        elif channel == Channel.B:
            self.is_dynamic_bone_mode_b = not self.is_dynamic_bone_mode_b
            mode_name = "可交互模式" if self.is_dynamic_bone_mode_b else "面板设置模式"
            logger.info("通道 B 切换为" + mode_name)
            # 更新UI
            self.main_window.controller_settings_tab.dynamic_bone_mode_b_checkbox.blockSignals(True)  # 防止触发 valueChanged 事件
            self.main_window.controller_settings_tab.dynamic_bone_mode_b_checkbox.setChecked(self.is_dynamic_bone_mode_b)
            self.main_window.controller_settings_tab.dynamic_bone_mode_b_checkbox.blockSignals(False)

    async def set_mode(self, value, channel):
        """
        切换工作模式, 延时一秒触发，更改按下时对应的通道
        """
        if value == 1: # 按下按键
            if self.set_mode_timer is not None:
                self.set_mode_timer.cancel()
            self.set_mode_timer = asyncio.create_task(self.set_mode_timer_handle(channel))
        elif value == 0: #松开按键
            if self.set_mode_timer:
                self.set_mode_timer.cancel()
                self.set_mode_timer = None


    async def reset_strength(self, value, channel):
        """
        强度重置为 0
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.SET_TO, 0)

    async def increase_strength(self, value, channel):
        """
        增大强度, 固定 5
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.INCREASE, 5)

    async def decrease_strength(self, value, channel):
        """
        减小强度, 固定 5
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.DECREASE, 5)

    async def strength_fire_mode(self, value, channel, fire_strength, last_strength):
        """
        一键开火：
            按下后设置为当前通道强度值 +fire_mode_strength_step
            松开后恢复为通道进入前的强度
        TODO: 修复连点开火按键导致输出持续上升的问题
        """
        logger.info(f"Trigger FireMode: {value}")

        await asyncio.sleep(0.01)

        # 如果是开始开火并且已经在进行中，直接跳过
        if value and self.fire_mode_active:
            print("已有开火操作在进行中，跳过本次开始请求")
            return
        # 如果是结束开火并且当前没有进行中的开火操作，跳过
        if not value and not self.fire_mode_active:
            print("没有进行中的开火操作，跳过本次结束请求")
            return

        async with self.fire_mode_lock:
            if value:
                # 开始 fire mode
                self.fire_mode_active = True
                logger.debug(f"FIRE START {last_strength}")
                if last_strength:
                    if channel == Channel.A:
                        self.fire_mode_origin_strength_a = last_strength.a
                        await self.client.set_strength(
                            channel,
                            StrengthOperationType.SET_TO,
                            min(self.fire_mode_origin_strength_a + fire_strength, last_strength.a_limit)
                        )
                    elif channel == Channel.B:
                        self.fire_mode_origin_strength_b = last_strength.b
                        await self.client.set_strength(
                            channel,
                            StrengthOperationType.SET_TO,
                            min(self.fire_mode_origin_strength_b + fire_strength, last_strength.b_limit)
                        )
                self.data_updated_event.clear()
                await self.data_updated_event.wait()
            else:
                if channel == Channel.A:
                    await self.client.set_strength(channel, StrengthOperationType.SET_TO, self.fire_mode_origin_strength_a)
                elif channel == Channel.B:
                    await self.client.set_strength(channel, StrengthOperationType.SET_TO, self.fire_mode_origin_strength_b)
                # 等待数据更新
                self.data_updated_event.clear()  # 清除事件状态
                await self.data_updated_event.wait()  # 等待下次数据更新
                # 结束 fire mode
                logger.debug(f"FIRE END {last_strength}")
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

    async def set_panel_control(self, value):
        """
        面板控制功能开关，禁用控制后无法通过 OSC 对郊狼进行调整
        """
        if value > 0:
            self.enable_panel_control = True
        else:
            self.enable_panel_control = False
        mode_name = "开启面板控制" if self.enable_panel_control else "已禁用面板控制"
        logger.info(f": {mode_name}")
        # 更新 UI 组件 (QSpinBox) 以反映新的值
        self.main_window.controller_settings_tab.enable_panel_control_checkbox.blockSignals(True)  # 防止触发 valueChanged 事件
        self.main_window.controller_settings_tab.enable_panel_control_checkbox.setChecked(self.enable_panel_control)
        self.main_window.controller_settings_tab.enable_panel_control_checkbox.blockSignals(False)

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

    def send_value_to_vrchat(self, path: str, value):
        '''
        /chatbox/input s b n Input text into the chatbox.
        '''
        self.osc_client.send_message(path, value)

    async def send_strength_status(self):
        """
        通过 ChatBox 发送当前强度数值
        """
        if self.last_strength:
            mode_name_a = "交互" if self.is_dynamic_bone_mode_a else "面板"
            mode_name_b = "交互" if self.is_dynamic_bone_mode_b else "面板"
            channel_strength = f"[A]: {self.last_strength.a} B: {self.last_strength.b}" if self.current_select_channel == Channel.A else f"A: {self.last_strength.a} [B]: {self.last_strength.b}"
            self.send_message_to_vrchat_chatbox(
                f"MAX A: {self.last_strength.a_limit} B: {self.last_strength.b_limit}\n"
                f"Mode A: {mode_name_a} B: {mode_name_b} \n"
                f"Pulse A: {PULSE_NAME[self.pulse_mode_a]} B: {PULSE_NAME[self.pulse_mode_b]} \n"
                f"Fire Step: {self.fire_mode_strength_step}\n"
                f"Current: {channel_strength} \n"
            )
        else:
            self.send_message_to_vrchat_chatbox("未连接")

    # 保存原始的OSC消息处理函数
    async def _original_handle_osc_message_pad(self, address, *args):
        """
        处理 OSC 消息 - 保持原有功能不变
        1. Bool: Bool 类型变量触发时，VRC 会先后发送 True 与 False, 回调中仅处理 True
        2. Float: -1.0 to 1.0， 但对于 Contact 与  Physbones 来说范围为 0.0-1.0
        """
        # Parameters Debug
        logger.info(f"Received OSC message on {address} with arguments {args}")

        # 面板控制功能禁用
        if address == "/avatar/parameters/SoundPad/PanelControl":
            await self.set_panel_control(args[0])
        if not self.enable_panel_control:
            logger.info(f"已禁用面板控制功能")
            return

        #按键功能
        if address == "/avatar/parameters/SoundPad/Button/1":
            await self.set_mode(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/2":
            await self.reset_strength(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/3":
            await self.decrease_strength(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/4":
            await self.increase_strength(args[0], self.current_select_channel)
        elif address == "/avatar/parameters/SoundPad/Button/5":
            await self.strength_fire_mode(args[0], self.current_select_channel, self.fire_mode_strength_step, self.last_strength)

        # ChatBox 开关控制
        elif address == "/avatar/parameters/SoundPad/Button/6":#
            await self.toggle_chatbox(args[0])
        # 波形控制
        elif address == "/avatar/parameters/SoundPad/Button/7":
            await self.set_pulse_data(args[0], self.current_select_channel, 2)
        elif address == "/avatar/parameters/SoundPad/Button/8":
            await self.set_pulse_data(args[0], self.current_select_channel, 14)
        elif address == "/avatar/parameters/SoundPad/Button/9":
            await self.set_pulse_data(args[0], self.current_select_channel, 4)
        elif address == "/avatar/parameters/SoundPad/Button/10":
            await self.set_pulse_data(args[0], self.current_select_channel, 5)
        elif address == "/avatar/parameters/SoundPad/Button/11":
            await self.set_pulse_data(args[0], self.current_select_channel, 6)
        elif address == "/avatar/parameters/SoundPad/Button/12":
            await self.set_pulse_data(args[0], self.current_select_channel, 7)
        elif address == "/avatar/parameters/SoundPad/Button/13":
            await self.set_pulse_data(args[0], self.current_select_channel, 8)
        elif address == "/avatar/parameters/SoundPad/Button/14":
            await self.set_pulse_data(args[0], self.current_select_channel, 9)
        elif address == "/avatar/parameters/SoundPad/Button/15":
            await self.set_pulse_data(args[0], self.current_select_channel, 1)

        # 数值调节
        elif address == "/avatar/parameters/SoundPad/Volume": # Float
            await self.set_strength_step(args[0])
        # 通道调节
        elif address == "/avatar/parameters/SoundPad/Page": # INT
            await self.set_channel(args[0])
