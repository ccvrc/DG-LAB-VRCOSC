"""Exercise the real command queue with an entirely simulated DG-LAB client."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from pydglab_ws import Channel, StrengthOperationType
from command_types import CommandType
from dglab_controller import DGLabController


class MoCapControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = Mock(set_strength=AsyncMock(side_effect=self.apply_strength))
        self.controller = DGLabController(self.client, Mock())
        for task in (self.controller.send_status_task, self.controller.send_pulse_task):
            task.cancel()
        await asyncio.gather(self.controller.send_status_task, self.controller.send_pulse_task,
                             return_exceptions=True)
        self.controller.app_status_online = True
        self.controller.last_strength = SimpleNamespace(a=10, b=20, a_limit=35, b_limit=80)
        self.controller.channel_states[Channel.A]["current_strength"] = 10
        self.controller.channel_states[Channel.B]["current_strength"] = 20

    async def apply_strength(self, channel, operation, value):
        self.assertEqual(operation, StrengthOperationType.SET_TO)
        setattr(self.controller.last_strength, channel.name.lower(), value)

    async def asyncTearDown(self):
        await asyncio.wait_for(self.controller.close(), timeout=2)

    async def settle(self):
        task = self.controller._mocap_task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
        await asyncio.wait_for(self.controller.command_queue.join(), timeout=1)

    async def command(self, kind, channel, value, source):
        return await asyncio.wait_for(self.controller.add_command(
            kind, channel, StrengthOperationType.SET_TO, value, source, wait_processed=True
        ), timeout=1)

    async def test_continuous_channels_clamp_restore_and_do_not_repeat_commands(self):
        self.controller.request_mocap_channels({"A", "B"})
        await self.settle()
        self.assertEqual(self.controller.mocap_active_channels, {"A", "B"})
        self.assertEqual((self.controller.last_strength.a, self.controller.last_strength.b), (35, 50))
        for _ in range(300):
            self.controller.request_mocap_channels({"A", "B"})
        await self.settle()
        self.assertEqual(self.client.set_strength.await_count, 2)
        self.controller.request_mocap_channels({"B"})
        await self.settle()
        self.assertEqual(self.controller.mocap_active_channels, {"B"})
        self.assertEqual((self.controller.last_strength.a, self.controller.last_strength.b), (10, 50))
        self.controller.cancel_mocap_triggers()
        await self.settle()
        self.assertEqual((self.controller.last_strength.a, self.controller.last_strength.b), (10, 20))

    async def test_offline_missing_status_panel_fire_and_zero_step_cannot_start(self):
        for attr, value in (("app_status_online", False), ("last_strength", None),
                            ("fire_mode_active", True), ("fire_mode_strength_step", 0)):
            with self.subTest(attr=attr):
                original = getattr(self.controller, attr)
                setattr(self.controller, attr, value)
                self.assertEqual(await self.controller.trigger_mocap_timed({"A"}, 1.0), set())
                await self.settle()
                setattr(self.controller, attr, original)
        self.client.set_strength.assert_not_awaited()

    async def test_manual_override_survives_stop_and_blocks_retrigger_until_recovery(self):
        self.controller.request_mocap_channels({"A"})
        await self.settle()
        self.assertTrue(await self.command(CommandType.GUI_COMMAND, Channel.A, 7, "manual"))
        self.controller.request_mocap_channels({"A"})
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 7)
        self.assertEqual(self.controller.mocap_suppressed_channels, {"A"})
        self.controller.release_mocap_overrides({"A"})
        self.assertEqual(self.controller.mocap_suppressed_channels, {"A"})
        self.controller.request_mocap_channels(set())
        self.controller.release_mocap_overrides(set())
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 7)
        self.controller.request_mocap_channels({"A"})
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 35)
        self.controller.cancel_mocap_triggers()
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 7)

    async def test_sps_and_ton_cannot_replace_an_active_motion_burst(self):
        self.controller.request_mocap_channels({"A"})
        await self.settle()
        for kind in (CommandType.INTERACTION_COMMAND, CommandType.TON_COMMAND):
            self.assertFalse(await self.command(kind, Channel.A, 1, kind.name))
        self.assertEqual(self.controller.last_strength.a, 35)
        self.assertTrue(await self.command(CommandType.INTERACTION_COMMAND, Channel.B, 8, "other"))

    async def test_timed_burst_expires_and_restores_baseline(self):
        self.assertEqual(await self.controller.trigger_mocap_timed({"A", "B"}, 0.01), {"A", "B"})
        await asyncio.wait_for(self.controller._mocap_timer_task, timeout=1)
        await self.settle()
        self.assertEqual(self.controller.mocap_active_channels, set())
        self.assertEqual((self.controller.last_strength.a, self.controller.last_strength.b), (10, 20))

    async def test_overlapping_timed_bursts_extend_without_stacking_strength(self):
        await self.controller.trigger_mocap_timed({"A"}, 10)
        original = self.controller._mocap_timed_deadlines[Channel.A]
        await self.controller.trigger_mocap_timed({"A", "B"}, 20)
        self.assertGreater(self.controller._mocap_timed_deadlines[Channel.A], original)
        self.assertEqual(self.client.set_strength.await_count, 2)
        self.assertEqual((self.controller.last_strength.a, self.controller.last_strength.b), (35, 50))
        await self.controller.trigger_mocap_timed({"A"}, 1)
        self.assertGreater(self.controller._mocap_timed_deadlines[Channel.A], original)
        self.controller.cancel_mocap_triggers()
        await self.settle()
        self.assertEqual(self.controller._mocap_timed_deadlines, {})

    async def test_cancel_invalidates_timed_requests_already_waiting_on_lock(self):
        async with self.controller._mocap_timed_lock:
            pending = asyncio.create_task(self.controller.trigger_mocap_timed({"A"}, 10))
            await asyncio.sleep(0)
            self.controller.cancel_mocap_triggers()
        self.assertEqual(await asyncio.wait_for(pending, timeout=1), set())
        await self.settle()
        self.client.set_strength.assert_not_awaited()

    async def test_cancel_during_device_write_restores_strength_without_new_deadline(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_write(channel, operation, value):
            if value == 35:
                entered.set()
                await release.wait()
            await self.apply_strength(channel, operation, value)

        self.client.set_strength.side_effect = slow_write
        pending = asyncio.create_task(self.controller.trigger_mocap_timed({"A"}, 10))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            self.controller.cancel_mocap_triggers()
        finally:
            release.set()
        self.assertEqual(await asyncio.wait_for(pending, timeout=1), set())
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 10)
        self.assertEqual(self.controller._mocap_timed_deadlines, {})

    async def test_lost_tracking_invalidates_request_waiting_on_lock(self):
        valid = True
        async with self.controller._mocap_timed_lock:
            pending = asyncio.create_task(self.controller.trigger_mocap_timed(
                {"A"}, 10, is_valid=lambda: valid
            ))
            await asyncio.sleep(0)
            valid = False
        self.assertEqual(await asyncio.wait_for(pending, timeout=1), set())
        await self.settle()
        self.client.set_strength.assert_not_awaited()

    async def test_lost_tracking_during_write_restores_only_new_burst(self):
        await self.controller.trigger_mocap_timed({"B"}, 10)
        valid = True

        async def losing_tracking(channel, operation, value):
            nonlocal valid
            if channel == Channel.A and value == 35:
                valid = False
            await self.apply_strength(channel, operation, value)

        self.client.set_strength.side_effect = losing_tracking
        self.assertEqual(await self.controller.trigger_mocap_timed(
            {"A"}, 10, is_valid=lambda: valid
        ), set())
        await self.settle()
        self.assertEqual(self.controller.last_strength.a, 10)
        self.assertEqual(self.controller.mocap_active_channels, {"B"})
        self.assertEqual(set(self.controller._mocap_timed_deadlines), {Channel.B})

    async def test_rapid_start_then_stop_does_not_send_a_burst(self):
        self.controller.request_mocap_channels({"A"})
        self.controller.request_mocap_channels(set())
        await self.settle()
        self.client.set_strength.assert_not_awaited()

    async def test_repeated_bursts_do_not_accumulate_unique_cooldown_entries(self):
        for _ in range(20):
            self.controller.request_mocap_channels({"A"})
            await self.settle()
            self.controller.request_mocap_channels(set())
            await self.settle()
        self.assertEqual(self.client.set_strength.await_count, 40)
        self.assertEqual(self.controller.command_sources, {})

    async def test_failed_device_write_does_not_claim_channel_or_arm_timer(self):
        self.client.set_strength.side_effect = RuntimeError("simulated disconnected device")
        self.assertEqual(await self.controller.trigger_mocap_timed({"A"}, 1), set())
        self.assertEqual(self.controller.mocap_active_channels, set())
        self.assertEqual(self.controller._mocap_timed_deadlines, {})


if __name__ == "__main__":
    unittest.main()
