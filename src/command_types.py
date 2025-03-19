from enum import Enum

class CommandType(Enum):
    GUI_COMMAND = 0      # 优先级最高
    PANEL_COMMAND = 1    # 面板命令
    INTERACTION_COMMAND = 2  # 交互命令
    TON_COMMAND = 3      # 游戏联动命令
    PERIODIC_UPDATE = 4  # 周期性更新，优先级最低

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