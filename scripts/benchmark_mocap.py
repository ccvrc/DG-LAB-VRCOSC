"""Synthetic MoCap CPU/GUI benchmark; never connects to SteamVR or DG-LAB.

Run: .venv/Scripts/python scripts/benchmark_mocap.py --sensors 16 --frames 1000
Native driver latency and in-game frame times require a separate hardware test.
"""

import argparse
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QWidget
from MoCap.rules import MotionRuleEvaluator, default_config, default_sensor_rules
from MoCap.sampling import LatestSensorFrame, sample_timeout
from MoCap.steamvr import PoseVelocityEstimator, SensorSnapshot
from MoCap.tab import MotionCaptureTab


def summary(samples):
    ordered = sorted(samples)
    return {
        "mean_ms": round(statistics.mean(samples) * 1000, 4),
        "p95_ms": round(ordered[int(len(ordered) * 0.95)] * 1000, 4),
        "max_ms": round(max(samples) * 1000, 4),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensors", type=int, default=16, choices=range(1, 65))
    parser.add_argument("--frames", type=int, default=1000)
    args = parser.parse_args()
    if args.frames < 100:
        parser.error("--frames must be at least 100")
    config = default_config()
    config["calculation_frequency_hz"] = 30
    sensors = [SensorSnapshot(i, "Tracker", f"tracker-{i}", "OK", (i * 0.1, 1, 0),
                              (0, 0, -1), (0, 0, 0), (0, 0, 0), 0.1, 0.1)
               for i in range(args.sensors)]
    for sensor in sensors:
        config["sensors"][sensor.serial] = default_sensor_rules()
        for metric in ("speed", "angular_speed"):
            for bound in ("low", "high"):
                config["sensors"][sensor.serial][metric][bound].update(
                    threshold=1.0, enabled=True
                )
    estimator, evaluator, frames = PoseVelocityEstimator(), MotionRuleEvaluator(), LatestSensorFrame()
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.controller = SimpleNamespace(
        app_status_online=True, last_strength=SimpleNamespace(a=0, b=0, a_limit=100, b_limit=100),
        fire_mode_active=False, fire_mode_strength_step=10,
        mocap_active_channels=set(), mocap_suppressed_channels=set(),
        request_mocap_channels=lambda _: None, release_mocap_overrides=lambda _: None,
    )
    with patch("MoCap.tab.load_names", return_value={}), patch("MoCap.tab.load_config", return_value=config):
        tab = MotionCaptureTab(window)
    tab._state = "connected"
    window.resize(1500, 900)
    tab.resize(1500, 900)
    window.show()
    tab.show()
    cpu_times, gui_times = [], []
    for tick in range(args.frames + 50):
        sampled_at = tick / 30
        started = time.perf_counter()
        angle = sampled_at * 0.2
        sine, cosine = math.sin(angle), math.cos(angle)
        moved = []
        for sensor in sensors:
            position = (sensor.index * 0.1 + math.sin(sampled_at) * 0.1, 1.0, 0.0)
            matrix = ((cosine, -sine, 0, position[0]), (sine, cosine, 0, 1), (0, 0, 1, 0))
            linear, angular = estimator.estimate(sensor.serial, sensor.index, "OK", position,
                                                 matrix, (0, 0, 0), (0, 0, 0), sampled_at)
            moved.append(replace(sensor, position=position, linear_velocity=linear,
                                 angular_velocity=angular, speed_m_s=math.hypot(*linear),
                                 angular_speed_rad_s=math.hypot(*angular)))
        frames.publish(moved, sampled_at)
        frame = frames.take()
        evaluator.update(frame.sensors, config, now=sampled_at, max_sample_gap=sample_timeout(30))
        cpu_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        tab._show_sensors(moved, sampled_at=sampled_at)
        app.processEvents()  # Include layout and offscreen painting, not just setters.
        gui_elapsed = time.perf_counter() - started
        if tick >= 50:
            cpu_times.append(cpu_elapsed)
            gui_times.append(gui_elapsed)
    print(json.dumps({"sensors": args.sensors, "frames": args.frames, "frequency_hz": 30,
                      "pose_math_rules_handoff": summary(cpu_times),
                      "gui_update_and_paint": summary(gui_times)}, indent=2))
    window.close()


if __name__ == "__main__":
    main()
