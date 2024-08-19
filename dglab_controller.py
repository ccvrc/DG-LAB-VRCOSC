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
    def __init__(self, client, osc_client):
        """
        初始化 DGLabController 实例
        :param client: DGLabWSServer 的客户端实例
        :param osc_client: 用于发送 OSC 回复的客户端实例
        :param is_dynamic_bone_mode 强度控制模式，目前设定两种不能同时启用，交互模式通过动骨和Contact控制输出强度，此时禁用面板的强度控制操作
        """
        self.client = client
        self.osc_client = osc_client
        self.last_strength = None  # 记录上次的强度值, 从 app更新, 包含 a b a_limit b_limit
        # 功能控制参数
        self.is_dynamic_bone_mode_a = False  # Default mode for Channel A
        self.is_dynamic_bone_mode_b = False  # Default mode for Channel B
        self.pulse_mode_a = 0  # pulse mode for Channel A
        self.pulse_mode_b = 0  # pulse mode for Channel B
        self.current_strength_step = 2  # 强度调节步进
        self.enable_chatbox_status = 1  # ChatBox 发送状态
        # dynamic_bone 模式下的最终设定输出，随时间自动回落，总是更新为当前支持参数中的最大值
        self.final_strength_a = 0.0
        self.final_strength_b = 0.0
        # 定时任务
        self.send_status_task = asyncio.create_task(self.periodic_status_update())  # 启动ChatBox发送任务
        self.send_pulse_task = asyncio.create_task(self.periodic_send_pulse_data())  # 启动设定波形发送任务
        self.dynamic_bone_mode_output_task = asyncio.create_task(self.periodic_decrease_output())  # 启动设定波形发送任务

    async def periodic_status_update(self):
        """
        周期性通过 ChatBox 发送当前的配置状态
        """
        while True:
            if self.enable_chatbox_status:
                await self.send_strength_status()
            await asyncio.sleep(3)  # 每 x 秒发送一次

    async def periodic_send_pulse_data(self):
        # 顺序发送波形
        # TODO： 修复重连后自动发送中断
        while True:
            if self.last_strength:  # 当收到设备状态后再发送波形
                print(f"更新波形 A {PULSE_NAME[self.pulse_mode_a]} B {PULSE_NAME[self.pulse_mode_b]}")
                specific_pulse_data_a = PULSE_DATA[PULSE_NAME[self.pulse_mode_a]]
                specific_pulse_data_b = PULSE_DATA[PULSE_NAME[self.pulse_mode_b]]
                await self.client.clear_pulses(Channel.A)
                await self.client.add_pulses(Channel.A, *(specific_pulse_data_a * 5))  # 单组波形大约维持 1~2 秒，不同波形持续时间不同？
                await self.client.clear_pulses(Channel.B)
                await self.client.add_pulses(Channel.B, *(specific_pulse_data_b * 5))
            await asyncio.sleep(3)  # 每 x 秒发送一次

    async def set_pulse_data(self, value, channel, pulse_index):
        """
            立即切换为当前指定波形，清空原有波形
        """
        if channel == Channel.A:
            self.pulse_mode_a = pulse_index
        else:
            self.pulse_mode_b = pulse_index

        await self.client.clear_pulses(channel)

        print(f"开始发送波形 {PULSE_NAME[pulse_index]}")
        specific_pulse_data = PULSE_DATA[PULSE_NAME[pulse_index]]  # 当前准备发送的波形
        # 如果波形都发送过了，则开始新一轮的发送
        await self.client.add_pulses(channel, *(specific_pulse_data * 5))  # 直接发送一份

    async def periodic_decrease_output(self):
        """
            每秒输出值降低可选范围的 50%
        """
        while True:
            if self.is_dynamic_bone_mode_a:
                self.final_strength_a = self.final_strength_a - 0.5
            else:
                self.final_strength_a = 0

            if self.is_dynamic_bone_mode_b:
                self.final_strength_b = self.final_strength_b - 0.5
            else:
                self.final_strength_b = 0

            await asyncio.sleep(1)

    async def set_float_output(self, value, channel):
        """
        动骨与碰撞体激活对应通道输出
        """
        if value > 0.0:
            if channel == Channel.A and self.is_dynamic_bone_mode_a:
                self.final_strength_a = max(self.final_strength_a, value)
                final_output_a = math.ceil(
                    self.map_value(self.final_strength_a, self.last_strength.a_limit * 0.4, self.last_strength.a_limit))
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, final_output_a)
            elif channel == Channel.B and self.is_dynamic_bone_mode_b:
                self.final_strength_b = max(self.final_strength_b, value)
                final_output_b = math.ceil(
                    self.map_value(self.final_strength_b, self.last_strength.b_limit * 0.4, self.last_strength.b_limit))
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, final_output_b)

    async def toggle_chatbox(self, value):
        """
        开关 ChatBox 内容发送
        """
        if value:
            self.enable_chatbox_status = not self.enable_chatbox_status
            mode_name = "开启" if self.enable_chatbox_status else "关闭"
            print("ChatBox显示状态切换为:" + mode_name)

    async def set_mode(self, value, channel):
        """
        切换工作模式
        """
        if value:
            if channel == Channel.A:
                self.is_dynamic_bone_mode_a = not self.is_dynamic_bone_mode_a
                mode_name = "可交互模式" if self.is_dynamic_bone_mode_a else "面板设置模式"
                print("通道 A 切换为" + mode_name)

            elif channel == Channel.B:
                self.is_dynamic_bone_mode_b = not self.is_dynamic_bone_mode_b
                mode_name = "可交互模式" if self.is_dynamic_bone_mode_b else "面板设置模式"
                print("通道 B 切换为" + mode_name)

    async def reset_strength(self, value, channel):
        """
        强度重置为 0
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.SET_TO, 0)
            # await self.send_strength_status()

    async def increase_strength(self, value, channel):
        """
        增大强度
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.INCREASE, self.current_strength_step)
            # await self.send_strength_status()

    async def decrease_strength(self, value, channel):
        """
        减小强度
        """
        if value:
            await self.client.set_strength(channel, StrengthOperationType.DECREASE, self.current_strength_step)
            # await self.send_strength_status()

    async def set_strength_to_max(self, value, channel):
        """
        强度到当前通道上限（一键开火？)
        """
        if self.last_strength:
            if channel == Channel.A:
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, self.last_strength.a_limit)
            elif channel == Channel.B:
                await self.client.set_strength(channel, StrengthOperationType.SET_TO, self.last_strength.b_limit)
            # await self.send_strength_status()

    async def set_strength_step(self, value):
        """
        开关 ChatBox 内容发送
        """
        if value > 0.0:
            self.current_strength_step = math.ceil(self.map_value(value, 0, 10))  # 向上取整
            print(f"current strength step: {self.current_strength_step}")
            # self.current_strength_step = self.map_value(value, self.last_strength.a_limit, self.last_strength.b_limit)

    async def handle_osc_message(self, address, *args):
        """
        处理 OSC 消息
        1. Bool: Bool 类型变量触发时，VRC 会先后发送 True 与 False, 回调中仅处理 True
        2. Float: -1.0 to 1.0， 但对于 Contact 与  Physbones 来说范围为 0.0-1.0

        TODO: 两种控制模式的兼容？
        应该修改为，通过所有 OSC 参数按当前设定计算好对应通道输出后，再进行发送（触控板可覆盖交互式的输出值？）
        """
        # Parameters Debug
        print(f"Received OSC message on {address} with arguments {args}")

        # Float 参数映射为强度数值
        # Note: 好像没有下限设置，那就默认为上限的 40% 吧
        if address == "/avatar/parameters/DG-LAB/UpperLeg_R":
            await self.set_float_output(args[0], Channel.A)
        elif address == "/avatar/parameters/DG-LAB/UpperLeg_L":
            await self.set_float_output(args[0], Channel.A)
        elif address == "/avatar/parameters/Tail_Stretch":
            await self.set_float_output(args[0], Channel.A)

        # A 通道按键功能
        elif address == "/avatar/parameters/SoundPad/Button/1":
            await self.set_mode(args[0], Channel.A)

        elif address == "/avatar/parameters/SoundPad/Button/2":
            await self.reset_strength(args[0], Channel.A)

        elif address == "/avatar/parameters/SoundPad/Button/3":
            await self.decrease_strength(args[0], Channel.A)

        elif address == "/avatar/parameters/SoundPad/Button/4":
            await self.increase_strength(args[0], Channel.A)

        elif address == "/avatar/parameters/SoundPad/Button/5":
            await self.set_strength_to_max(args[0], Channel.A)

        # 波形控制
        elif address == "/avatar/parameters/SoundPad/Button/6":
            await self.set_pulse_data(args[0], Channel.A, 1)

        elif address == "/avatar/parameters/SoundPad/Button/7":
            await self.set_pulse_data(args[0], Channel.A, 2)

        elif address == "/avatar/parameters/SoundPad/Button/8":
            await self.set_pulse_data(args[0], Channel.A, 3)

        elif address == "/avatar/parameters/SoundPad/Button/9":
            await self.set_pulse_data(args[0], Channel.A, 4)

        elif address == "/avatar/parameters/SoundPad/Button/10":
            await self.set_pulse_data(args[0], Channel.A, 5)

        elif address == "/avatar/parameters/SoundPad/Button/11":
            await self.set_pulse_data(args[0], Channel.A, 6)
        elif address == "/avatar/parameters/SoundPad/Button/12":
            await self.set_pulse_data(args[0], Channel.A, 7)
        elif address == "/avatar/parameters/SoundPad/Button/13":
            await self.set_pulse_data(args[0], Channel.A, 8)
        elif address == "/avatar/parameters/SoundPad/Button/14":
            await self.set_pulse_data(args[0], Channel.A, 9)

        # 其他功能
        elif address == "/avatar/parameters/SoundPad/Button/15":
            await self.toggle_chatbox(args[0])
        # 数值调节
        elif address == "/avatar/parameters/SoundPad/Volume":
            await self.set_strength_step(args[0])

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

    async def send_strength_status(self):
        """
        通过 ChatBox 发送当前强度数值
        """
        if self.last_strength:
            mode_name_a = "交互" if self.is_dynamic_bone_mode_a else "面板"
            mode_name_b = "交互" if self.is_dynamic_bone_mode_b else "面板"
            self.send_message_to_vrchat_chatbox(
                f"MAX A: {self.last_strength.a_limit} B: {self.last_strength.b_limit}\n"
                f"Mode A: {mode_name_a} B: {mode_name_b} \n"
                f"Pulse A: {PULSE_NAME[self.pulse_mode_a]} B: {self.pulse_mode_b} \n"
                f"Strength Step: {self.current_strength_step}\n"
                f"Current A: {self.last_strength.a} B: {self.last_strength.b}"
            )
        else:
            self.send_message_to_vrchat_chatbox("未连接")
