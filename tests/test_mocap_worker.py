"""SteamVR worker lifecycle and bounded delivery with a fake runtime."""

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from MoCap.tab import SteamVRWorker


class SteamVRWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = SteamVRWorker(None, 10)
        self.worker._poll_wake = Mock()
        self.states, self.errors, self.notifications = [], [], []
        self.worker.state_changed.connect(self.states.append)
        self.worker.failed.connect(self.errors.append)
        self.worker.sensors_updated.connect(lambda: self.notifications.append(True))
        self.system = Mock(pollNextEvent=Mock(return_value=False))
        self.runtime = SimpleNamespace(
            isRuntimeInstalled=Mock(return_value=True),
            init=Mock(return_value=self.system), shutdown=Mock(),
            VRApplication_Background=3, VREvent_t=lambda: SimpleNamespace(eventType=0),
            VREvent_Quit=700,
        )

    def run_worker(self, *, manifest_changed=False, read=None):
        with patch.dict(sys.modules, {"openvr": self.runtime}), \
             patch("MoCap.tab.register_steamvr_manifest", return_value=manifest_changed), \
             patch("MoCap.tab.read_sensors", side_effect=read):
            self.worker.run()

    def test_manifest_registration_requests_restart_without_opening_runtime(self):
        self.run_worker(manifest_changed=True)
        self.assertEqual(self.states, ["restart"])
        self.runtime.init.assert_not_called()
        self.runtime.shutdown.assert_not_called()

    def test_missing_runtime_reports_failure(self):
        self.runtime.isRuntimeInstalled.return_value = False
        with self.assertLogs("MoCap.tab", level="ERROR"):
            self.run_worker()
        self.assertEqual(len(self.errors), 1)
        self.runtime.init.assert_not_called()

    def test_multiple_samples_queue_one_notification_and_shutdown_once(self):
        calls = []
        # run() is invoked synchronously; Qt only honours interruption on a
        # running native thread, so model that flag explicitly in this test.
        self.worker.isInterruptionRequested = lambda: len(calls) >= 3

        def read(*_args, **_kwargs):
            calls.append(True)
            return []

        self.run_worker(read=read)
        self.assertEqual(self.states, ["connected"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.notifications), 1)
        self.assertTrue(self.worker.frames.take().interrupted)
        self.runtime.shutdown.assert_called_once()
        self.assertEqual(self.errors, [])

    def test_pose_read_failure_shuts_down_runtime(self):
        with self.assertLogs("MoCap.tab", level="ERROR"):
            self.run_worker(read=RuntimeError("tracking read failed"))
        self.assertEqual(self.errors, ["tracking read failed"])
        self.runtime.shutdown.assert_called_once()

    def test_runtime_quit_event_exits_without_reading_another_pose(self):
        def quit_event(event):
            event.eventType = 700
            return True

        self.system.pollNextEvent.side_effect = quit_event
        read = Mock()
        with self.assertLogs("MoCap.tab", level="ERROR"):
            self.run_worker(read=read)
        read.assert_not_called()
        self.assertEqual(len(self.errors), 1)
        self.runtime.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
