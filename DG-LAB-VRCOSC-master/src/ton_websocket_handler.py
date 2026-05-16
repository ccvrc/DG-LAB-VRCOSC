# ton_websocket_handler.py
import asyncio
import websockets
import json
import logging
from PySide6.QtCore import Signal, QObject
import qrcode
import io
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

def generate_qrcode(data: str):
    """生成二维码并转换为PySide6可显示的QPixmap"""
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qrcode.make_image(fill='black', back_color='white')

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    qimage = QPixmap()
    qimage.loadFromData(buffer.read(), 'PNG')

    return qimage

class WebSocketClient(QObject):
    status_update_signal = Signal(str)
    message_received = Signal(str)
    error_signal = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.websocket = None

    async def start_connection(self):
        """Starts the WebSocket connection and listens for messages."""
        try:
            async with websockets.connect(self.url) as ws:
                self.websocket = ws
                async for message in ws:
                    # Process received message
                    await self.process_message(message)
        except Exception as e:
            self.error_signal.emit(f"WebSocket connection error: {e}")

    async def process_message(self, message):
        """Process the received WebSocket message and parse JSON."""
        logger.info(message)
        try:
            # 直接解析收到的消息，不添加 'Received: ' 前缀
            json_data = json.loads(message)

            self.message_received.emit(f"{json.dumps(json_data, indent=4)}")
            self.status_update_signal.emit("connected")

            # # Process based on message type
            # if json_data.get("type") == "STATS":
            #     stats_data = json_data.get("data", {})
            #     formatted_stats = "\n".join([f"{key}: {value}" for key, value in stats_data.items()])
            #     self.status_update_signal.emit(f"STATS Update:\n{formatted_stats}")
            # else:
            #     # Emit the full JSON formatted message for other types
            #     self.status_update_signal.emit(f"{json.dumps(json_data, indent=4)}")
        except json.JSONDecodeError:
            # 如果消息不是 JSON 格式，显示原始消息
            logger.warning("ws message is not json format")
            self.message_received.emit(message)
            self.status_update_signal.emit("error")

    async def close(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.status_update_signal.emit("disconnected")
