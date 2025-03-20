"""
command_types.py - 命令类型和命令队列处理

该模块定义了系统中所有命令的类型和优先级，以及命令对象的结构。
设计目标是统一所有输入源的数据流向，解决"同时传入多输入数据时输出以及相应的数据流向混乱"的问题。

数据流向处理逻辑：
1. 所有输入源（GUI命令、面板命令、交互命令、游戏联动、周期更新）统一通过add_command方法添加到命令队列
2. 命令队列根据命令类型的优先级和时间戳进行排序
3. 命令处理器按优先级顺序处理命令，确保高优先级命令先执行
4. 每种命令类型有独立的冷却时间，防止某一输入源过于频繁地发送命令
5. 通道状态模型记录每个通道的当前状态，用于决策和状态显示

这种设计确保了:
- 优先级明确：GUI命令 > 面板命令 > 交互命令 > 游戏联动命令
- 流向清晰：所有命令通过统一接口进入系统，按优先级处理
- 冲突解决：高优先级命令可覆盖低优先级命令的效果
- 状态一致：所有输出通过统一的处理器执行，确保设备状态与内部模型一致
"""

from enum import Enum

class CommandType(Enum):
    GUI_COMMAND = 0      # 优先级最高
    PANEL_COMMAND = 1    # 面板命令
    INTERACTION_COMMAND = 2  # 交互命令
    TON_COMMAND = 3      # 游戏联动命令

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