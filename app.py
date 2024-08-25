"""
通过 VRChat OSC 参数控制郊狼 (DG-LAB) 的 python 小程序
"""
import logging
from logger_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

import asyncio
import io
import webbrowser
import os
import qrcode
from PIL import Image
from pydglab_ws import StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode, DGLabWSServer
from pythonosc import dispatcher, osc_server, udp_client
from dglab_controller import DGLabController
from config import get_settings


def print_qrcode(data: str):
    """生成二维码图片并用浏览器打开"""
    # 生成二维码图片
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # 保存图片
    img_path = "qrcode.png"
    img.save(img_path)
    
    # 获取图片的绝对路径
    abs_path = os.path.abspath(img_path)
    
    # 用默认浏览器打开图片
    webbrowser.open('file://' + abs_path)


def handle_osc_message_task_pad(address, list_object, *args):
    """
    将异步处理包装为同步调用，以便在 dispatcher 中使用. 实际就是创建新协程？
    TODO: 待优化?
    """
    asyncio.create_task(list_object[0].handle_osc_message_pad(address, *args))

def handle_osc_message_task_pb(address, list_object, *args):
    asyncio.create_task(list_object[0].handle_osc_message_pb(address, *args))

def some_function():
    logger.info("这是一个信息日志")
    logger.warning("这是一个警告日志")
    logger.error("这是一个错误日志")


async def DGLab_Server():
    settings = get_settings()
    local_ip = settings['ip']
    osc_port = settings['port']

    async with DGLabWSServer("0.0.0.0", 5678, 60) as server:
        ipurl = f"ws://{local_ip}:5678"
        print(ipurl)
        client = server.new_local_client()
        url = client.get_qrcode(ipurl)  # 注意 PyCharm 开启时需要允许本地网络访问
        print("请用 DG-Lab App 扫描二维码以连接")
        print_qrcode(url)
        # OSC 客户端用于发送回复        
        osc_client = udp_client.SimpleUDPClient("127.0.0.1", 9000)  # 修改为接收 OSC 回复的目标 IP 和端口, 9000 为 VRChat 默认 OSC 传入接口

        # OSC 服务器配置
        controller = DGLabController(client, osc_client)
        # 注册需要进行处理的 OSC 参数，绑定回调
        disp = dispatcher.Dispatcher()
        # 面板控制对应的 OSC 地址
        disp.map("/avatar/parameters/SoundPad/Button/*", handle_osc_message_task_pad, controller)
        disp.map("/avatar/parameters/SoundPad/Volume", handle_osc_message_task_pad, controller)
        disp.map("/avatar/parameters/SoundPad/Page", handle_osc_message_task_pad, controller)
        disp.map("/avatar/parameters/SoundPad/PanelControl", handle_osc_message_task_pad, controller)
        # PB/Contact 交互对应的 OSC 地址
        disp.map("/avatar/parameters/DG-LAB/*", handle_osc_message_task_pb, controller)
        disp.map("/avatar/parameters/Tail_Stretch", handle_osc_message_task_pb, controller)

        osc_server_instance = osc_server.AsyncIOOSCUDPServer(
            ("0.0.0.0", osc_port), disp, asyncio.get_event_loop()
            # 修改为接收 OSC 回复的端口。9001 为 VRChat 的默认传出接口，为了兼容 VRCFT 面捕，这里通过 OSC Router 转换为 9102
        )
        osc_transport, osc_protocol = await osc_server_instance.create_serve_endpoint()

        logger.info("OSC Recv Serving on {}".format(osc_server_instance._server_address))
        
        # 等待绑定
        await client.bind()
        logger.info(f"已与 App {client.target_id} 成功绑定")

        # 从 App 接收数据更新，并进行远控操作 （在VRC中应该不太会用到APP的按键）
        async for data in client.data_generator():

            # 接收通道强度数据
            if isinstance(data, StrengthData):
                logger.info(f"从 App 收到通道强度数据更新：{data}")
                controller.last_strength = data
                # controller.send_message_to_vrchat_chatbox(f"当前强度 A:{data.a} B:{data.b}")

            # 接收 App 反馈按钮
            elif isinstance(data, FeedbackButton):
                logger.info(f"App 触发了反馈按钮：{data.name}")

                if data == FeedbackButton.A1:
                    # 降低强度
                    logger.info("对方按下了 A 通道圆圈按钮，减小力度")
                    if controller.last_strength:
                        await client.set_strength(
                            Channel.A,
                            StrengthOperationType.DECREASE,
                            2
                        )
                elif data == FeedbackButton.A2:
                    # 设置强度到 A 通道上限
                    logger.info("对方按下了 A 通道三角按钮，加大力度")
                    if controller.last_strength:
                        await client.set_strength(
                            Channel.A,
                            StrengthOperationType.SET_TO,
                            controller.last_strength.a_limit
                        )

            # 接收 心跳 / App 断开通知
            elif data == RetCode.CLIENT_DISCONNECTED:
                logger.info("App 已断开连接，你可以尝试重新扫码进行连接绑定")
                await client.rebind()
                logger.info("重新绑定成功")

        osc_transport.close()


if __name__ == "__main__":
    asyncio.run(DGLab_Server())
