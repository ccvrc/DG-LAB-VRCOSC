"""Bounded handoff from the SteamVR thread to the GUI thread."""

from dataclasses import dataclass
from threading import Lock

from .steamvr import SensorSnapshot


def sample_timeout(frequency_hz: int) -> float:
    # Allow scheduling jitter, but never count a long unsampled interval.
    return max(0.25, 2.5 / frequency_hz)


@dataclass(frozen=True)
class SensorFrame:
    sensors: list[SensorSnapshot]
    sampled_at: float
    interrupted: bool = False


class LatestSensorFrame:
    """Keep at most one frame and one queued GUI notification.

    If the GUI misses a frame, restart countdowns: an unseen frame could have
    contained tracking loss or a return inside a motion limit.
    """

    def __init__(self):
        self._lock = Lock()
        self._pending: SensorFrame | None = None

    def publish(self, sensors: list[SensorSnapshot], sampled_at: float) -> bool:
        with self._lock:
            notify = self._pending is None
            self._pending = SensorFrame(sensors, sampled_at, interrupted=not notify)
            return notify

    def take(self) -> SensorFrame | None:
        with self._lock:
            frame = self._pending
            self._pending = None
            return frame
