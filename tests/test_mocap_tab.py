"""Motion countdown integration, without SteamVR or device output."""

import asyncio
from dataclasses import replace
import os
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from MoCap.rules import MotionTrigger, default_config, default_sensor_rules
from MoCap.steamvr import SensorSnapshot
from MoCap.tab import MotionCaptureTab, SteamVRWorker


class MotionCaptureTabTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.controller = Mock(
            app_status_online=True,
            last_strength=SimpleNamespace(a=0, b=0, a_limit=100, b_limit=100),
            fire_mode_active=False, fire_mode_strength_step=10,
            mocap_active_channels=set(), mocap_suppressed_channels=set(),
        )
        self.controller.trigger_mocap_timed = AsyncMock(return_value={"A"})
        self.window.controller = self.controller
        config = default_config()
        config["low_seconds"] = 0.5
        config["sensors"]["tracker"] = default_sensor_rules()
        config["sensors"]["tracker"]["speed"]["low"].update(threshold=1.0, enabled=True)
        with patch("MoCap.tab.load_names", return_value={}), patch("MoCap.tab.load_config", return_value=config):
            self.tab = MotionCaptureTab(self.window)
        self.tab._state = "connected"
        self.worker = SteamVRWorker(self.tab, 10)
        self.tab.worker = self.worker
        self.worker.sensors_updated.connect(self.tab._consume_sensor_frame)
        self.sensor = SensorSnapshot(1, "Tracker", "tracker", "OK", (0, 0, 0),
                                     (0, 0, -1), (0, 0, 0), (0, 0, 0), 0.0, 0.0)
        self.trigger = MotionTrigger("tracker", "speed", "low", "A")

    def tearDown(self):
        self.tab.worker = None
        self.tab._clear_sensors()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def deliver(self, sampled_at, sensors=None):
        self.assertTrue(self.worker.frames.publish(
            [self.sensor] if sensors is None else sensors, sampled_at
        ))
        with patch("MoCap.tab.time.monotonic", return_value=sampled_at + 0.01):
            self.worker.sensors_updated.emit()

    async def test_loss_stops_continuous_output_and_reacquisition_restarts_countdown(self):
        for tick in range(6):
            self.deliver(tick / 10)
        self.controller.request_mocap_channels.assert_called_with({"A"})
        self.deliver(0.6, [])
        self.controller.request_mocap_channels.assert_called_with(set())
        self.assertIsNone(self.tab.rule_evaluator.state(self.trigger))
        for tick in range(7, 12):
            self.deliver(tick / 10)
            self.controller.request_mocap_channels.assert_called_with(set())
        self.deliver(1.3)  # Entire 0.5 s must be observed again.
        self.controller.request_mocap_channels.assert_called_with({"A"})

    async def test_coalesced_frames_cannot_hide_a_tracking_loss(self):
        for tick in range(5):
            self.deliver(tick / 10)
        self.assertTrue(self.worker.frames.publish([], 0.5))
        self.assertFalse(self.worker.frames.publish([self.sensor], 0.6))
        with patch("MoCap.tab.time.monotonic", return_value=0.61):
            self.worker.sensors_updated.emit()
        self.assertEqual(self.tab.rule_evaluator.state(self.trigger), (0.0, False))
        self.controller.request_mocap_channels.assert_called_with(set())
        self.controller.cancel_mocap_triggers.assert_called_once()

    async def test_watchdog_clears_countdown_and_motion_when_sampling_stops(self):
        self.deliver(0.0)
        self.deliver(0.1)
        with patch("MoCap.tab.time.monotonic", return_value=2.0):
            self.tab._expire_stale_sample()
        self.assertIsNone(self.tab.rule_evaluator.state(self.trigger))
        self.assertIsNone(self.tab._last_sampled_at)
        self.assertIsNone(self.tab._sensors[0].speed_m_s)
        self.assertEqual(self.tab.sensor_table.item(0, 2).text(), "—")
        self.controller.cancel_mocap_triggers.assert_called_once()
        self.deliver(2.1)
        self.assertEqual(self.tab.rule_evaluator.state(self.trigger), (0.0, False))

    async def test_stale_frame_is_not_allowed_to_trigger(self):
        for tick in range(5):
            self.deliver(tick / 10)
        self.worker.frames.publish([self.sensor], 0.5)
        with patch("MoCap.tab.time.monotonic", return_value=5.0):
            self.worker.sensors_updated.emit()
        self.assertIsNone(self.tab.rule_evaluator.state(self.trigger))
        self.controller.request_mocap_channels.assert_called_with(set())

    async def test_queued_timed_trigger_is_discarded_after_tracking_loss(self):
        self.tab.rule_config["continuous_fire"] = False
        for tick in range(6):
            self.deliver(tick / 10)
        # Do not yield to the pending burst until tracking has already been lost.
        self.deliver(0.6, [])
        await asyncio.sleep(0)
        self.controller.trigger_mocap_timed.assert_not_awaited()
        self.assertEqual(self.tab._timed_pending_keys, set())
        for tick in range(7, 14):
            self.deliver(tick / 10)
        await asyncio.sleep(0)
        self.controller.trigger_mocap_timed.assert_awaited_once_with({"A"}, 1.0, is_valid=ANY)

    async def test_inflight_timed_request_checks_tracking_again_before_start(self):
        entered, release = asyncio.Event(), asyncio.Event()
        accepted = []

        async def waiting_trigger(_channels, _duration, *, is_valid):
            entered.set()
            await release.wait()
            accepted.append(is_valid())
            return {"A"} if accepted[-1] else set()

        self.controller.trigger_mocap_timed.side_effect = waiting_trigger
        self.tab.rule_config["continuous_fire"] = False
        for tick in range(6):
            self.deliver(tick / 10)
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.deliver(0.6, [])
        release.set()
        await asyncio.sleep(0)
        self.assertEqual(accepted, [False])
        self.assertEqual(self.tab._timed_pending_keys, set())

    async def test_queued_timed_trigger_is_discarded_after_rule_reset(self):
        self.tab.rule_config["continuous_fire"] = False
        for tick in range(6):
            self.deliver(tick / 10)
        self.tab._reset_rule_evaluator()
        await asyncio.sleep(0)
        self.controller.trigger_mocap_timed.assert_not_awaited()

    async def test_inline_rule_edit_saves_correct_serial_and_cancels_old_trigger(self):
        other = replace(self.sensor, serial="other", index=2)
        self.deliver(0.0, [self.sensor, other])
        with patch("MoCap.tab.save_config") as save:
            threshold, channel, enabled = self.tab.inline_rule_controls["angular_speed"]["other"]["high"]
            threshold.setValue(120.0)
            channel.setCurrentIndex(channel.findData("both"))
            enabled.setChecked(True)
        self.assertEqual(self.tab.rule_config["sensors"]["other"]["angular_speed"]["high"],
                         {"threshold": 120.0, "channel": "both", "enabled": True})
        self.assertEqual(self.tab.rule_config["sensors"]["tracker"]["speed"]["low"]["threshold"], 1.0)
        self.assertTrue(save.called)
        self.assertIsNone(self.tab.rule_evaluator.state(self.trigger))
        self.assertTrue(self.controller.cancel_mocap_triggers.called)
        self.deliver(0.1, [replace(other, index=0), replace(self.sensor, index=3)])
        controls = self.tab.inline_rule_controls["angular_speed"]["other"]["high"]
        self.assertEqual(controls[0].value(), 120.0)
        self.assertEqual(controls[1].currentData(), "both")
        self.assertTrue(controls[2].isChecked())

    async def test_name_edit_follows_serial_after_row_reordering(self):
        other = replace(self.sensor, serial="other", index=2)
        self.deliver(0.0, [self.sensor, other])
        with patch("MoCap.tab.save_names") as save:
            self.tab.sensor_table.item(1, 1).setText("右手")
        self.assertEqual(self.tab.names, {"other": "右手"})
        save.assert_called_with({"other": "右手"})
        self.deliver(0.1, [replace(other, index=0), replace(self.sensor, index=3)])
        self.assertEqual(self.tab.sensor_table.item(0, 1).text(), "右手")
        self.assertEqual(self.tab.sensor_table.item(1, 1).text(), "")

    async def test_samples_from_previous_worker_are_ignored_after_reconnect(self):
        self.deliver(0.0)
        old = self.worker
        self.tab.worker = SteamVRWorker(self.tab, 10)
        old.frames.publish([], 0.1)
        old.sensors_updated.emit()
        self.assertEqual(self.tab._sensors, [self.sensor])
        self.assertEqual(self.tab.rule_evaluator.state(self.trigger), (0.0, False))


if __name__ == "__main__":
    unittest.main()
