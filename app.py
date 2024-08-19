"""
通过 VRChat OSC 参数控制郊狼 (DG-LAB) 的 python 小程序
"""
import logging
from logger_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

import asyncio
import io
from traceback import print_tb, print_list

import qrcode
from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from pythonosc import dispatcher, osc_server, udp_client

from dglab_controller import DGLabController


def print_qrcode(data: str):
    """输出二维码到终端界面"""
    qr = qrcode.QRCode()
    qr.add_data(data)
    f = io.StringIO()
    qr.print_ascii(out=f)
    f.seek(0)
    print(f.read())


def handle_osc_message_sync(address, list_object, *args):
    """
    将异步处理包装为同步调用，以便在 dispatcher 中使用
    TODO: 待优化?
    """
    asyncio.create_task(list_object[0].handle_osc_message(address, *args))

def some_function():
    logger.info("这是一个信息日志")
    logger.warning("这是一个警告日志")
    logger.error("这是一个错误日志")

async def DGLab_Server():
    async with DGLabWSServer("0.0.0.0", 5678, 60) as server:
        client = server.new_local_client()
        url = client.get_qrcode("ws://192.168.10.219:5678")  # 修改为当前电脑的实际局域网 IP，注意 PyCharm 开启时需要允许本地网络访问
        print("请用 DG-Lab App 扫描二维码以连接")
        print_qrcode(url)

        some_function()

        # OSC 客户端用于发送回复
        osc_client = udp_client.SimpleUDPClient("127.0.0.1", 9000)  # 修改为接收 OSC 回复的目标 IP 和端口, 9000 为 VRChat 默认 OSC 传入接口

        # OSC 服务器配置
        controller = DGLabController(client, osc_client)
        # 注册需要进行处理的 OSC 参数，绑定回调
        disp = dispatcher.Dispatcher()
        disp.map("/avatar/parameters/SoundPad/Button/*", handle_osc_message_sync, controller)  # 匹配所有按键操作
        disp.map("/avatar/parameters/SoundPad/Volume", handle_osc_message_sync, controller)  # 强度调节步进值
        # TODO: 未开启动骨或 Contact 时，detach 这部分 OSC 地址？
        disp.map("/avatar/parameters/DG-LAB/*", handle_osc_message_sync, controller)  # 自定义参数
        disp.map("/avatar/parameters/Tail_Stretch", handle_osc_message_sync, controller)  # 自定义参数

        osc_server_instance = osc_server.AsyncIOOSCUDPServer(
            ("0.0.0.0", 9102), disp, asyncio.get_event_loop()
            # 修改为接收 OSC 回复的端口。9001 为 VRChat 的默认传出接口，为了兼容 VRCFT 面捕，这里通过 OSC Router 转换为 9102
        )
        osc_transport, osc_protocol = await osc_server_instance.create_serve_endpoint()

        print("OSC Recv Serving on {}".format(osc_server_instance._server_address))

        # 等待绑定
        # TODO 避免在客户端未连接时终端输出 OSC 信息
        await client.bind()
        print(f"已与 App {client.target_id} 成功绑定")

        # 从 App 接收数据更新，并进行远控操作 （在VRC中应该不太会用到APP的按键）
        async for data in client.data_generator():

            # 接收通道强度数据
            if isinstance(data, StrengthData):
                print(f"从 App 收到通道强度数据更新：{data}")
                controller.last_strength = data
                # controller.send_message_to_vrchat_chatbox(f"当前强度 A:{data.a} B:{data.b}")

            # 接收 App 反馈按钮
            elif isinstance(data, FeedbackButton):
                print(f"App 触发了反馈按钮：{data.name}")

                if data == FeedbackButton.A1:
                    # 降低强度
                    print("对方按下了 A 通道圆圈按钮，减小力度")
                    if controller.last_strength:
                        await client.set_strength(
                            Channel.A,
                            StrengthOperationType.DECREASE,
                            2
                        )
                elif data == FeedbackButton.A2:
                    # 设置强度到 A 通道上限
                    print("对方按下了 A 通道三角按钮，加大力度")
                    if controller.last_strength:
                        await client.set_strength(
                            Channel.A,
                            StrengthOperationType.SET_TO,
                            controller.last_strength.a_limit
                        )

            # 接收 心跳 / App 断开通知
            elif data == RetCode.CLIENT_DISCONNECTED:
                print("App 已断开连接，你可以尝试重新扫码进行连接绑定")
                await client.rebind()
                print("重新绑定成功")

        osc_transport.close()


if __name__ == "__main__":
    asyncio.run(DGLab_Server())
