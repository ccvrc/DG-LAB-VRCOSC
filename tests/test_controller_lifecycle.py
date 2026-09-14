import asyncio
import unittest
from unittest.mock import Mock

from dglab_controller import DGLabController


class ControllerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cancels_workers_and_timers_without_sending_device_commands(self):
        device, osc = Mock(), Mock()
        controller = DGLabController(device, osc)
        controller.chatbox_toggle_timer = asyncio.create_task(asyncio.sleep(100))
        controller.mode_toggle_timer = asyncio.create_task(asyncio.sleep(100))
        controller.app_status_online = True
        tasks = [controller.send_status_task, controller.send_pulse_task,
                 controller.command_processing_task, controller.chatbox_toggle_timer,
                 controller.mode_toggle_timer]
        await controller.close()
        await controller.close()
        self.assertTrue(all(task.done() for task in tasks))
        self.assertFalse(controller.app_status_online)
        self.assertEqual(device.mock_calls, [])
        self.assertEqual(osc.mock_calls, [])
