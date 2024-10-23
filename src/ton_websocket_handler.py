# src/ton_websocket_handler.py
import asyncio
import websockets
import json
import logging
from PySide6.QtCore import Signal, QObject

class WebSocketClient(QObject):
    status_update_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self, uri="ws://localhost:11398", parent=None):
        super().__init__(parent)
        self.uri = uri
        self.websocket = None
        self.connected = False
        self.logger = logging.getLogger("WebSocketClient")

    async def start_connection(self):
        """Establish the WebSocket connection and start listening for messages."""
        try:
            async with websockets.connect(self.uri) as ws:
                self.websocket = ws
                self.connected = True
                self.logger.info("Connected to WebSocket server")
                await self.listen()
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.error_signal.emit(str(e))
            self.connected = False

    async def listen(self):
        """Listen for incoming messages from the WebSocket server."""
        try:
            async for message in self.websocket:
                self.logger.info(f"Received message: {message}")
                data = json.loads(message)
                if "Type" in data:
                    self.status_update_signal.emit(f"Event: {data['Type']}, Details: {data}")
                else:
                    self.status_update_signal.emit(f"Unknown message: {message}")
        except websockets.ConnectionClosed as e:
            self.logger.warning(f"Connection closed: {e}")
            self.connected = False
        except Exception as e:
            self.logger.error(f"Error receiving message: {e}")

    async def send(self, message):
        """Send a message to the WebSocket server."""
        if self.connected and self.websocket:
            await self.websocket.send(message)
            self.logger.info(f"Sent message: {message}")
        else:
            self.logger.warning("WebSocket is not connected")

    async def close(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.logger.info("WebSocket connection closed")
            self.connected = False