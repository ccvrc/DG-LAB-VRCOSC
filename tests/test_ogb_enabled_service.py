import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.ogb_enabled_service import (  # noqa: E402
    OGB_ENABLED_ADDRESS,
    OGBEnabledService,
)


class FakeOSCClient:
    def __init__(self):
        self.messages = []
        self.message_received = asyncio.Event()

    def send_message(self, address, value):
        self.messages.append((address, value))
        self.message_received.set()


class OGBEnabledServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_sends_boolean_true_immediately_and_periodically(self):
        client = FakeOSCClient()
        service = OGBEnabledService(client, interval=0.01)

        service.start()
        await asyncio.wait_for(client.message_received.wait(), timeout=0.2)
        await asyncio.sleep(0.025)
        await service.stop()

        self.assertGreaterEqual(len(client.messages), 2)
        for address, value in client.messages:
            self.assertEqual(address, OGB_ENABLED_ADDRESS)
            self.assertIs(value, True)

    async def test_start_is_idempotent_and_stop_ends_the_heartbeat(self):
        client = FakeOSCClient()
        service = OGBEnabledService(client, interval=60)

        service.start()
        service.start()
        await asyncio.wait_for(client.message_received.wait(), timeout=0.2)
        await asyncio.sleep(0)
        self.assertEqual(len(client.messages), 1)

        await service.stop()
        count_after_stop = len(client.messages)
        await asyncio.sleep(0.02)

        self.assertFalse(service.is_running)
        self.assertEqual(len(client.messages), count_after_stop)

        client.message_received.clear()
        service.start()
        await asyncio.wait_for(client.message_received.wait(), timeout=0.2)
        await service.stop()

        self.assertEqual(len(client.messages), count_after_stop + 1)

    async def test_send_now_reasserts_after_avatar_change(self):
        client = FakeOSCClient()
        service = OGBEnabledService(client)

        service.send_now()

        self.assertEqual(client.messages, [(OGB_ENABLED_ADDRESS, True)])


if __name__ == "__main__":
    unittest.main()
