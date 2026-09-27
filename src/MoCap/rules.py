"""Saved motion limits and sustained-limit detection for SteamVR sensors."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import tempfile
import time

import yaml

from .steamvr import SensorSnapshot, runtime_directory


METRICS = ("speed", "angular_speed")
BOUNDS = ("low", "high")
CHANNELS = ("A", "B", "both")
CALCULATION_FREQUENCY_MIN_HZ = 2
CALCULATION_FREQUENCY_MAX_HZ = 30
DEFAULT_CALCULATION_FREQUENCY_HZ = 10


def default_rule() -> dict:
    return {"threshold": 0.0, "channel": "A", "enabled": False}


def default_sensor_rules() -> dict:
    return {metric: {bound: default_rule() for bound in BOUNDS} for metric in METRICS}


def default_config() -> dict:
    return {
        "low_seconds": 3.0,
        "high_seconds": 1.0,
        "fire_seconds": 1.0,
        "continuous_fire": True,
        "calculation_frequency_hz": DEFAULT_CALCULATION_FREQUENCY_HZ,
        "sensors": {},
    }


def rules_path() -> Path:
    return runtime_directory() / "rules.yml"


def _number(value, fallback: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) and 0 <= number <= maximum else fallback


def normalize_config(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("动捕规则文件格式无效。")
    config = default_config()
    for bound in BOUNDS:
        key = f"{bound}_seconds"
        config[key] = max(0.5, _number(data.get(key), config[key], 60.0))
    config["fire_seconds"] = max(0.5, _number(data.get("fire_seconds"), 1.0, 60.0))
    config["continuous_fire"] = data.get("continuous_fire", True) is True
    frequency = _number(
        data.get("calculation_frequency_hz"),
        DEFAULT_CALCULATION_FREQUENCY_HZ,
        CALCULATION_FREQUENCY_MAX_HZ,
    )
    config["calculation_frequency_hz"] = max(CALCULATION_FREQUENCY_MIN_HZ, round(frequency))
    sensors = data.get("sensors", {})
    if not isinstance(sensors, dict):
        raise ValueError("动捕传感器规则格式无效。")
    for serial, sensor_rules in sensors.items():
        if not isinstance(serial, str) or not serial or not isinstance(sensor_rules, dict):
            continue
        normalized = {}
        for metric in METRICS:
            metric_rules = sensor_rules.get(metric, {})
            if not isinstance(metric_rules, dict):
                metric_rules = {}
            normalized[metric] = {}
            for bound in BOUNDS:
                source = metric_rules.get(bound, {})
                if not isinstance(source, dict):
                    source = {}
                rule = default_rule()
                rule["threshold"] = _number(
                    source.get("threshold"), 0.0, 20.0 if metric == "speed" else 2000.0
                )
                rule["channel"] = source.get("channel") if source.get("channel") in CHANNELS else "A"
                rule["enabled"] = source.get("enabled") is True
                normalized[metric][bound] = rule
        config["sensors"][serial] = normalized
    return config


def load_config(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else rules_path()
    if not path.is_file():
        return default_config()
    return normalize_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save_config(config: dict, path: Path | None = None) -> None:
    path = Path(path) if path is not None else rules_path()
    clean = normalize_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            yaml.safe_dump(clean, output, allow_unicode=True, sort_keys=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class MotionTrigger:
    serial: str
    metric: str
    bound: str
    channel: str
    started_at: float | None = field(default=None, compare=False, repr=False)


class MotionRuleEvaluator:
    def __init__(self):
        self._states: dict[tuple[str, str, str], tuple[float, bool]] = {}
        self._last_sampled_at: float | None = None

    def reset(self):
        self._states.clear()
        self._last_sampled_at = None

    def violating_channels(self, config: dict) -> set[str]:
        """Channels with any rule currently outside its limit, including its wait time."""
        channels = set()
        for serial, metric, bound in self._states:
            rule = config["sensors"][serial][metric][bound]
            channels.update(("A", "B") if rule["channel"] == "both" else (rule["channel"],))
        return channels

    def retry(self, trigger: MotionTrigger):
        key = (trigger.serial, trigger.metric, trigger.bound)
        if self.is_current(trigger):
            since, _issued = self._states[key]
            self._states[key] = (since, False)

    def is_current(self, trigger: MotionTrigger) -> bool:
        """Reject queued work from a violation that has already ended."""
        value = self._states.get((trigger.serial, trigger.metric, trigger.bound))
        return value is not None and (
            trigger.started_at is None or value[0] == trigger.started_at
        )

    def state(self, trigger: MotionTrigger, now: float | None = None) -> tuple[float, bool] | None:
        """Return elapsed violation time and whether an event has been issued."""
        value = self._states.get((trigger.serial, trigger.metric, trigger.bound))
        if value is None:
            return None
        since, fired = value
        # Display only observed time, never time spent waiting for another pose.
        now = self._last_sampled_at if now is None else now
        return max(0.0, now - since), fired

    def update(
        self, sensors: list[SensorSnapshot], config: dict, now: float | None = None,
        repeat: bool = True, max_sample_gap: float | None = None,
    ) -> list[MotionTrigger]:
        now = time.monotonic() if now is None else now
        if self._last_sampled_at is not None and (
            now <= self._last_sampled_at
            or (max_sample_gap is not None and now - self._last_sampled_at > max_sample_gap)
        ):
            self.reset()
        self._last_sampled_at = now
        active: set[tuple[str, str, str]] = set()
        triggers = []
        for sensor in sensors:
            if (
                not sensor.serial or sensor.tracking != "OK" or sensor.position is None
                or sensor.device_class == "BaseStation"
            ):
                continue
            sensor_rules = config["sensors"].get(sensor.serial)
            if not sensor_rules:
                continue
            values = {
                "speed": sensor.speed_m_s,
                "angular_speed": (
                    math.degrees(sensor.angular_speed_rad_s)
                    if sensor.angular_speed_rad_s is not None else None
                ),
            }
            for metric in METRICS:
                value = values[metric]
                if value is None or not math.isfinite(value):
                    continue
                for bound in BOUNDS:
                    rule = sensor_rules[metric][bound]
                    if not rule["enabled"] or (bound == "high" and rule["threshold"] <= 0):
                        continue
                    violated = value < rule["threshold"] if bound == "low" else value > rule["threshold"]
                    if not violated:
                        continue
                    key = (sensor.serial, metric, bound)
                    active.add(key)
                    since, issued = self._states.get(key, (now, False))
                    ready = now - since >= config[f"{bound}_seconds"]
                    if ready and (repeat or not issued):
                        triggers.append(MotionTrigger(sensor.serial, metric, bound, rule["channel"], since))
                        issued = True
                    self._states[key] = (since, issued)
        for key in set(self._states) - active:
            del self._states[key]
        return triggers
