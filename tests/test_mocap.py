"""SteamVR manifest and sensor snapshot tests without VR hardware."""

import json
import math
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from MoCap.names import load_names, save_names
from MoCap.rules import (
    MotionRuleEvaluator,
    MotionTrigger,
    default_config,
    default_sensor_rules,
    load_config,
    normalize_config,
    save_config,
)
from MoCap.sampling import LatestSensorFrame, sample_timeout
from MoCap.steamvr import (
    PoseVelocityEstimator, SensorSnapshot, matrix34_to_position_forward,
    read_sensors, register_steamvr_manifest,
)


TEST_DIRECTORY = Path(__file__).resolve().parent


@contextmanager
def temporary_test_directory():
    root = TEST_DIRECTORY / f"_mocap_test_{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        resolved = root.resolve()
        if resolved.parent != TEST_DIRECTORY or not resolved.name.startswith("_mocap_test_"):
            raise RuntimeError(f"Unsafe test cleanup path: {resolved}")
        shutil.rmtree(resolved)


IDENTITY_AT_123 = (
    (1, 0, 0, 1),
    (0, 1, 0, 2),
    (0, 0, 1, 3),
)


class PoseArrayFactory:
    def __init__(self, poses):
        self.poses = poses

    def __mul__(self, count):
        assert count == len(self.poses)
        return lambda: self.poses


class FakeVRSystem:
    def __init__(self, serials=("HMD-1", "TRACKER-1", "CONTROLLER-1")):
        self.serials = serials

    def getDeviceToAbsoluteTrackingPose(self, universe, prediction, poses):
        assert universe == 1
        assert prediction == 0

    def isTrackedDeviceConnected(self, index):
        return index < 3

    def getTrackedDeviceClass(self, index):
        return (1, 3, 2)[index]

    def getStringTrackedDeviceProperty(self, index, prop):
        return self.serials[index] if prop == 10 else ""


def velocity(x, y, z):
    return SimpleNamespace(v=(x, y, z))


def fake_openvr(poses):
    return SimpleNamespace(
        TrackedDevicePose_t=PoseArrayFactory(poses),
        k_unMaxTrackedDeviceCount=len(poses),
        TrackingUniverseStanding=1,
        TrackedDeviceClass_HMD=1,
        TrackedDeviceClass_Controller=2,
        TrackedDeviceClass_GenericTracker=3,
        TrackedDeviceClass_TrackingReference=4,
        Prop_SerialNumber_String=10,
        TrackingResult_Uninitialized=0,
        TrackingResult_Calibrating_InProgress=1,
        TrackingResult_Calibrating_OutOfRange=2,
        TrackingResult_Running_OK=4,
        TrackingResult_Running_OutOfRange=5,
        TrackingResult_Fallback_RotationOnly=6,
    )


def fake_pose(valid=True, linear=(0, 0, 0), angular=(0, 0, 0), matrix=IDENTITY_AT_123):
    return SimpleNamespace(
        bPoseIsValid=valid,
        eTrackingResult=4 if valid else 0,
        mDeviceToAbsoluteTracking=matrix,
        vVelocity=velocity(*linear),
        vAngularVelocity=velocity(*angular),
    )


def sensor_snapshot(speed, angular=0.0, tracking="OK"):
    return SensorSnapshot(
        1, "Tracker", "TRACKER-1", tracking,
        (0.0, 1.0, 0.0) if tracking == "OK" else None,
        (0.0, 0.0, -1.0) if tracking == "OK" else None,
        None, None, speed, angular,
    )


class MoCapTests(unittest.TestCase):
    def test_normalization_rejects_malformed_roots_and_sanitizes_limits(self):
        for invalid in (None, [], "invalid", {"sensors": []}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_config(invalid)
        config = normalize_config({
            "low_seconds": -1, "high_seconds": float("inf"), "fire_seconds": 0,
            "calculation_frequency_hz": 1000, "continuous_fire": "true",
            "sensors": {"t": {"speed": {"low": {
                "threshold": float("nan"), "channel": "C", "enabled": "true"
            }}, "angular_speed": None}, "": {}, 1: {}},
        })
        self.assertEqual(config["low_seconds"], 3.0)
        self.assertEqual(config["high_seconds"], 1.0)
        self.assertEqual(config["fire_seconds"], 0.5)
        self.assertEqual(config["calculation_frequency_hz"], 10)
        self.assertFalse(config["continuous_fire"])
        self.assertEqual(config["sensors"], {"t": default_sensor_rules()})

    def test_names_ignore_invalid_entries_and_rules_default_without_file(self):
        with temporary_test_directory() as root:
            path = root / "names.yml"
            self.assertEqual(load_names(path), {})
            self.assertEqual(load_config(root / "rules.yml"), default_config())
            save_names({"one": " 腰部 ", "two": " ", "": "empty", 3: "wrong", "four": 4}, path)
            self.assertEqual(load_names(path), {"one": "腰部"})
            path.write_text("- malformed\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_names(path)

    def test_failed_atomic_save_preserves_existing_rules_and_names(self):
        for save, data, module in ((save_names, {"tracker": "腰"}, "MoCap.names"),
                                    (save_config, default_config(), "MoCap.rules")):
            with self.subTest(module=module), temporary_test_directory() as root:
                path = root / "config.yml"
                path.write_text("original: preserved\n", encoding="utf-8")
                with patch(module + ".os.replace", side_effect=OSError("simulated full disk")):
                    with self.assertRaises(OSError):
                        save(data, path)
                self.assertEqual(path.read_text(encoding="utf-8"), "original: preserved\n")
                self.assertEqual(list(root.iterdir()), [path])

    def test_velocity_estimator_handles_all_rotation_axes_and_wraparound(self):
        def rotation(axis, angle):
            c, s = math.cos(angle), math.sin(angle)
            if axis == 0:
                return ((1, 0, 0, 0), (0, c, -s, 0), (0, s, c, 0))
            if axis == 1:
                return ((c, 0, s, 0), (0, 1, 0, 0), (-s, 0, c, 0))
            return ((c, -s, 0, 0), (s, c, 0, 0), (0, 0, 1, 0))

        for axis in range(3):
            for degrees in (0, 90, 170, 179, 180, 270, 359):
                with self.subTest(axis=axis, degrees=degrees):
                    estimator = PoseVelocityEstimator()
                    first = rotation(axis, math.radians(degrees))
                    second = rotation(axis, math.radians((degrees + 2) % 360))
                    estimator.estimate("t", 1, "OK", (0, 0, 0), first, None, None, 0)
                    linear, angular = estimator.estimate("t", 1, "OK", (0, 0, 0), second,
                                                         None, None, 0.1)
                    self.assertEqual(linear, (0, 0, 0))
                    for component in range(3):
                        self.assertAlmostEqual(angular[component], math.radians(20) if axis == component else 0)

    def test_velocity_estimator_discards_gaps_index_changes_and_teleports(self):
        for timestamp, index, position in ((2.0, 1, (1, 2, 3)), (0.1, 2, (1, 2, 3)),
                                           (0.1, 1, (100, 2, 3)), (0.001, 1, (1, 2, 3))):
            with self.subTest(timestamp=timestamp, index=index, position=position):
                estimator = PoseVelocityEstimator()
                estimator.estimate("t", 1, "OK", (1, 2, 3), IDENTITY_AT_123, None, None, 0)
                linear, angular = estimator.estimate("t", index, "OK", position, IDENTITY_AT_123,
                                                     None, None, timestamp)
                self.assertIsNone(linear)
        estimator.discard_missing(set())
        self.assertEqual(estimator.estimate("t", 1, "OK", (1, 2, 3), IDENTITY_AT_123,
                                           None, None, 0.1), (None, None))

    def test_real_driver_velocities_take_priority_over_pose_estimates(self):
        estimator = PoseVelocityEstimator()
        estimator.estimate("t", 1, "OK", (1, 2, 3), IDENTITY_AT_123, None, None, 0)
        reported = ((2, 3, 4), (1, 2, 3))
        self.assertEqual(estimator.estimate("t", 1, "OK", (1.1, 2, 3), IDENTITY_AT_123,
                                           *reported, 0.1), reported)

    def test_equal_limits_disabled_rules_and_base_stations_never_trigger(self):
        config = default_config()
        config["sensors"]["TRACKER-1"] = default_sensor_rules()
        for bound in ("low", "high"):
            config["sensors"]["TRACKER-1"]["speed"][bound].update(threshold=1.0, enabled=True)
        for sensor in (sensor_snapshot(1.0), replace(sensor_snapshot(0.0), device_class="BaseStation"),
                       replace(sensor_snapshot(0.0), serial="")):
            with self.subTest(sensor=sensor):
                evaluator = MotionRuleEvaluator()
                evaluator.update([sensor], config, now=0)
                self.assertEqual(evaluator.update([sensor], config, now=10), [])
        config["sensors"]["TRACKER-1"]["speed"]["low"]["enabled"] = False
        config["sensors"]["TRACKER-1"]["speed"]["high"]["threshold"] = 0
        evaluator.update([sensor_snapshot(2.0)], config, now=11)
        self.assertEqual(evaluator.update([sensor_snapshot(2.0)], config, now=20), [])

    def test_all_countdowns_restart_after_tracking_loss_or_disconnection(self):
        for repeat in (True, False):
            for metric in ("speed", "angular_speed"):
                for bound, value in (("low", 0.1), ("high", 2.0)):
                    for lost in ([], [sensor_snapshot(0.0, tracking="OutOfRange")],
                                 [sensor_snapshot(0.0, tracking="RotationOnly")],
                                 [sensor_snapshot(0.0, tracking="OK / NoPose")]):
                        with self.subTest(repeat=repeat, metric=metric, bound=bound, lost=lost):
                            config = default_config()
                            config[f"{bound}_seconds"] = 2.0
                            config["sensors"]["TRACKER-1"] = default_sensor_rules()
                            config["sensors"]["TRACKER-1"][metric][bound].update(
                                threshold=1.0, enabled=True
                            )
                            sensor = sensor_snapshot(value, angular=math.radians(value))
                            evaluator = MotionRuleEvaluator()
                            event = MotionTrigger(sensor.serial, metric, bound, "A")
                            evaluator.update([sensor], config, now=0.0, repeat=repeat)
                            self.assertEqual(evaluator.update([sensor], config, now=1.9, repeat=repeat), [])
                            self.assertEqual(evaluator.update(lost, config, now=2.0, repeat=repeat), [])
                            self.assertIsNone(evaluator.state(event))
                            self.assertEqual(evaluator.violating_channels(config), set())
                            self.assertEqual(evaluator.update(lost, config, now=50.0, repeat=repeat), [])
                            self.assertEqual(evaluator.update([sensor], config, now=51.0, repeat=repeat), [])
                            self.assertEqual(evaluator.state(event), (0.0, False))
                            self.assertEqual(evaluator.update([sensor], config, now=52.9, repeat=repeat), [])
                            self.assertEqual(evaluator.update([sensor], config, now=53.0, repeat=repeat), [event])

    def test_tracking_loss_does_not_reset_other_sensors(self):
        config = default_config()
        config["low_seconds"] = 2.0
        for serial in ("TRACKER-1", "TRACKER-2"):
            config["sensors"][serial] = default_sensor_rules()
            config["sensors"][serial]["speed"]["low"].update(threshold=1.0, enabled=True)
        first = sensor_snapshot(0.0)
        second = replace(first, index=2, serial="TRACKER-2")
        evaluator = MotionRuleEvaluator()
        evaluator.update([first, second], config, now=0.0)
        evaluator.update([replace(first, tracking="OutOfRange"), second], config, now=1.0)
        self.assertEqual(
            [event.serial for event in evaluator.update([first, second], config, now=2.0)],
            ["TRACKER-2"],
        )

    def test_sample_gap_and_invalid_motion_do_not_count_as_stationary_time(self):
        config = default_config()
        config["low_seconds"] = 0.5
        config["sensors"]["TRACKER-1"] = default_sensor_rules()
        config["sensors"]["TRACKER-1"]["speed"]["low"].update(threshold=1.0, enabled=True)
        slow = sensor_snapshot(0.0)
        event = MotionTrigger(slow.serial, "speed", "low", "A")
        evaluator = MotionRuleEvaluator()
        for frequency in (2, 10, 30):
            with self.subTest(frequency=frequency):
                evaluator.reset()
                timeout = sample_timeout(frequency)
                evaluator.update([slow], config, now=0.0, max_sample_gap=timeout)
                self.assertEqual(evaluator.update([slow], config, now=10.0, max_sample_gap=timeout), [])
                self.assertEqual(evaluator.state(event), (0.0, False))
        for invalid in (sensor_snapshot(None), sensor_snapshot(math.nan),
                        sensor_snapshot(math.inf), replace(slow, position=None)):
            evaluator.update([slow], config, now=20.0)
            self.assertEqual(evaluator.update([invalid], config, now=21.0), [])
            self.assertIsNone(evaluator.state(event))
        evaluator.update([slow], config, now=30.0)
        with patch("MoCap.rules.time.monotonic", return_value=1000.0):
            self.assertEqual(evaluator.state(event), (0.0, False))

    def test_frame_handoff_is_bounded_and_marks_missed_tracking_loss(self):
        frames = LatestSensorFrame()
        self.assertIsNone(frames.take())
        self.assertTrue(frames.publish([sensor_snapshot(0.0)], 0.0))
        self.assertFalse(frames.take().interrupted)
        self.assertTrue(frames.publish([sensor_snapshot(None, tracking="OutOfRange")], 0.1))
        for number in range(1, 1001):
            self.assertFalse(frames.publish([sensor_snapshot(0.0)], float(number)))
        frame = frames.take()
        self.assertTrue(frame.interrupted)
        self.assertEqual(frame.sampled_at, 1000.0)
        self.assertEqual(frame.sensors[0].tracking, "OK")
        self.assertIsNone(frames.take())
        self.assertTrue(frames.publish([], 1001.0))
        self.assertFalse(frames.take().interrupted)

    def test_old_timed_request_cannot_rearm_a_new_violation(self):
        config = default_config()
        config["low_seconds"] = 0.5
        config["sensors"]["TRACKER-1"] = default_sensor_rules()
        config["sensors"]["TRACKER-1"]["speed"]["low"].update(threshold=1.0, enabled=True)
        evaluator = MotionRuleEvaluator()
        slow = sensor_snapshot(0.0)
        evaluator.update([slow], config, now=0.0, repeat=False)
        old = evaluator.update([slow], config, now=0.5, repeat=False)[0]
        evaluator.update([], config, now=0.6, repeat=False)
        evaluator.update([slow], config, now=0.7, repeat=False)
        current = evaluator.update([slow], config, now=1.3, repeat=False)[0]
        self.assertFalse(evaluator.is_current(old))
        evaluator.retry(old)
        self.assertTrue(evaluator.state(current)[1])
        self.assertEqual(evaluator.update([slow], config, now=1.4, repeat=False), [])

    def test_registers_manifest_once_and_preserves_other_steam_config(self):
        with temporary_test_directory() as root:
            steam_config = root / "Steam" / "config" / "appconfig.json"
            steam_config.parent.mkdir(parents=True)
            original = {"manifest_paths": ["C:/existing/app.vrmanifest"], "other_setting": 42}
            steam_config.write_text(json.dumps(original), encoding="utf-8")
            destination = root / "LocalAppData" / "MoCap"

            with patch("MoCap.steamvr._launch_command", return_value=("C:/app.exe", None)):
                self.assertTrue(register_steamvr_manifest(config_path=steam_config, target_directory=destination))
                self.assertFalse(register_steamvr_manifest(config_path=steam_config, target_directory=destination))

            result = json.loads(steam_config.read_text(encoding="utf-8"))
            self.assertEqual(result["other_setting"], 42)
            self.assertEqual(result["manifest_paths"][0], original["manifest_paths"][0])
            self.assertEqual(result["manifest_paths"].count(str((destination / "app.vrmanifest").resolve())), 1)
            manifest = json.loads((destination / "app.vrmanifest").read_text(encoding="utf-8"))
            self.assertEqual(manifest["applications"][0]["binary_path_windows"], "C:/app.exe")
            self.assertTrue((destination / "bindings" / "actions.json").is_file())
            self.assertTrue((destination / "bindings" / "generic.json").is_file())

    def test_invalid_appconfig_is_not_overwritten(self):
        with temporary_test_directory() as root:
            config = root / "appconfig.json"
            config.write_text('{"manifest_paths": "invalid"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                register_steamvr_manifest(config_path=config, target_directory=root / "MoCap")
            self.assertEqual(config.read_text(encoding="utf-8"), '{"manifest_paths": "invalid"}')

    def test_reads_speed_and_leaves_invalid_pose_without_motion_data(self):
        poses = [
            fake_pose(linear=(3, 4, 0), angular=(0, 0, 2)),
            fake_pose(valid=False, linear=(3, 4, 0), angular=(0, 0, 2)),
            fake_pose(linear=(0, 0, 0), angular=(0, 0, 0)),
        ]
        sensors = read_sensors(fake_openvr(poses), FakeVRSystem())

        self.assertEqual([sensor.device_class for sensor in sensors], ["HMD", "Tracker", "Controller"])
        self.assertEqual(sensors[0].position, (1.0, 2.0, 3.0))
        self.assertEqual(sensors[0].forward, (0.0, 0.0, -1.0))
        self.assertEqual(sensors[0].linear_velocity, (3.0, 4.0, 0.0))
        self.assertEqual(sensors[0].speed_m_s, 5.0)
        self.assertEqual(sensors[0].angular_speed_rad_s, 2.0)
        self.assertEqual(sensors[1].tracking, "Uninitialized / NoPose")
        self.assertIsNone(sensors[1].position)
        self.assertIsNone(sensors[1].speed_m_s)
        self.assertIsNone(sensors[1].angular_speed_rad_s)
        self.assertEqual(sensors[2].speed_m_s, 0.0)

    def test_custom_names_follow_serial_when_indexes_change(self):
        with temporary_test_directory() as root:
            path = root / "names.yml"
            save_names({"TRACKER-1": "腰部", "CONTROLLER-1": "右手"}, path)
            names = load_names(path)
            poses = [fake_pose(), fake_pose(), fake_pose()]
            before = read_sensors(fake_openvr(poses), FakeVRSystem())
            after = read_sensors(
                fake_openvr(poses), FakeVRSystem(("HMD-1", "CONTROLLER-1", "TRACKER-1"))
            )
            self.assertEqual(
                [(sensor.index, names.get(sensor.serial)) for sensor in before[1:]],
                [(1, "腰部"), (2, "右手")],
            )
            self.assertEqual(
                [(sensor.index, names.get(sensor.serial)) for sensor in after[1:]],
                [(1, "右手"), (2, "腰部")],
            )

    def test_pose_conversion_uses_metres_and_forward_axis(self):
        self.assertEqual(
            matrix34_to_position_forward(IDENTITY_AT_123),
            ((1.0, 2.0, 3.0), (0.0, 0.0, -1.0)),
        )

    def test_non_finite_velocity_is_unavailable(self):
        poses = [fake_pose(linear=(math.nan, 0, 0), angular=(0, 1, 0)) for _ in range(3)]
        sensor = read_sensors(fake_openvr(poses), FakeVRSystem())[0]
        self.assertIsNone(sensor.speed_m_s)
        self.assertEqual(sensor.angular_speed_rad_s, 1.0)

    def test_estimates_motion_when_driver_reports_zero_velocity(self):
        estimator = PoseVelocityEstimator()
        initial = [fake_pose() for _ in range(3)]
        read_sensors(fake_openvr(initial), FakeVRSystem(), estimator, sampled_at=0.0)
        # Rotate around local Z: the forward vector stays the same, but angular speed must change.
        rotated = (
            (0, -1, 0, 1.05),
            (1, 0, 0, 2),
            (0, 0, 1, 3),
        )
        moved = [fake_pose(), fake_pose(matrix=rotated), fake_pose()]
        tracker = read_sensors(fake_openvr(moved), FakeVRSystem(), estimator, sampled_at=0.1)[1]
        self.assertAlmostEqual(tracker.speed_m_s, 0.5)
        self.assertAlmostEqual(tracker.angular_speed_rad_s, math.pi / 0.2)
        self.assertEqual(tracker.forward, (0.0, 0.0, -1.0))

        lost = [fake_pose(), fake_pose(valid=False), fake_pose()]
        read_sensors(fake_openvr(lost), FakeVRSystem(), estimator, sampled_at=0.2)
        reacquired = read_sensors(fake_openvr(moved), FakeVRSystem(), estimator, sampled_at=0.3)[1]
        self.assertEqual(reacquired.speed_m_s, 0.0)
        self.assertEqual(reacquired.angular_speed_rad_s, 0.0)

    def test_motion_rules_require_sustained_valid_tracking_and_rearm_after_recovery(self):
        config = default_config()
        config["low_seconds"] = 2.0
        config["sensors"]["TRACKER-1"] = default_sensor_rules()
        rule = config["sensors"]["TRACKER-1"]["speed"]["low"]
        rule.update(threshold=0.2, channel="A", enabled=True)
        evaluator = MotionRuleEvaluator()
        slow = sensor_snapshot(0.1)
        self.assertEqual(evaluator.update([slow], config, now=0.0), [])
        self.assertEqual(evaluator.violating_channels(config), {"A"})
        self.assertEqual(evaluator.update([slow], config, now=1.9), [])
        self.assertEqual([event.channel for event in evaluator.update([slow], config, now=2.0)], ["A"])
        self.assertEqual([event.channel for event in evaluator.update([slow], config, now=5.0)], ["A"])
        self.assertEqual(evaluator.update([sensor_snapshot(0.3)], config, now=5.25), [])
        self.assertEqual(evaluator.violating_channels(config), set())
        self.assertEqual(evaluator.update([slow], config, now=6.0), [])
        self.assertEqual(evaluator.update([sensor_snapshot(0.1, tracking="OutOfRange")], config, now=7.0), [])
        self.assertEqual(evaluator.update([slow], config, now=8.0), [])
        self.assertEqual([event.channel for event in evaluator.update([slow], config, now=10.0)], ["A"])

    def test_angular_high_limit_and_saved_channel_selection(self):
        config = default_config()
        config["high_seconds"] = 1.0
        config["fire_seconds"] = 2.5
        config["continuous_fire"] = False
        config["sensors"]["TRACKER-1"] = default_sensor_rules()
        config["sensors"]["TRACKER-1"]["angular_speed"]["high"].update(
            threshold=90.0, channel="both", enabled=True
        )
        with temporary_test_directory() as root:
            path = root / "rules.yml"
            save_config(config, path)
            loaded = load_config(path)
        self.assertEqual(loaded["fire_seconds"], 2.5)
        self.assertFalse(loaded["continuous_fire"])
        evaluator = MotionRuleEvaluator()
        moving = sensor_snapshot(0.0, angular=math.pi)
        self.assertEqual(evaluator.update([moving], loaded, now=0.0, repeat=False), [])
        events = evaluator.update([moving], loaded, now=1.0, repeat=False)
        self.assertEqual([(event.metric, event.bound, event.channel) for event in events],
                         [("angular_speed", "high", "both")])
        self.assertEqual(evaluator.update([moving], loaded, now=2.0, repeat=False), [])


if __name__ == "__main__":
    unittest.main()
