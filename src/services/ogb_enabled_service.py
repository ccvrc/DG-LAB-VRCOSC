"""Keep VRCFury's local OGB compatibility contacts enabled."""

import asyncio
import logging


logger = logging.getLogger(__name__)

OGB_ENABLED_ADDRESS = "/avatar/parameters/OGB_ENABLED"
OGB_ENABLED_INTERVAL_SECONDS = 5.0


class OGBEnabledService:
    def __init__(self, osc_client, interval: float = OGB_ENABLED_INTERVAL_SECONDS):
        self.osc_client = osc_client
        self.interval = interval
        self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self):
        if self.is_running:
            return
        self._task = asyncio.create_task(
            self._run(),
            name="ogb-enabled-heartbeat",
        )

    async def stop(self):
        task = self._task
        self._task = None
        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def send_now(self):
        try:
            self.osc_client.send_message(OGB_ENABLED_ADDRESS, True)
        except Exception:
            logger.warning("发送 OGB_ENABLED 心跳失败", exc_info=True)

    async def _run(self):
        while True:
            self.send_now()
            await asyncio.sleep(self.interval)
