"""Register the SteamVR app manifest and read connected OpenVR tracked devices.

The manifest identifies this application to SteamVR. Live sensor poses come from
OpenVR's ``getDeviceToAbsoluteTrackingPose`` API, not from the manifest file.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


RESOURCE_FILES = (
    "app.vrmanifest",
    "bindings/actions.json",
    "bindings/generic.json",
)


@dataclass(frozen=True)
class SensorSnapshot:
    index: int
    device_class: str
    serial: str
    tracking: str
    position: tuple[float, float, float] | None
    forward: tuple[float, float, float] | None
    linear_velocity: tuple[float, float, float] | None  # m/s
    angular_velocity: tuple[float, float, float] | None  # rad/s
    speed_m_s: float | None
    angular_speed_rad_s: float | None


def resource_directory() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "MoCap"
    return Path(__file__).resolve().parent


def runtime_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "DG-LAB-VRCOSC" / "MoCap"


def find_steam_config() -> Path:
    """Find the existing Steam appconfig without assuming Steam's install drive."""
    steam_roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except (OSError, ImportError):
            pass
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            steam_roots.append(Path(os.environ[variable]) / "Steam")

    for root in steam_roots:
        config = root / "config" / "appconfig.json"
        if config.is_file():
            return config
    raise FileNotFoundError("未找到 Steam/config/appconfig.json，请先安装并启动 Steam。")


def _write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _launch_command() -> tuple[str, str | None]:
    executable = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        return executable, None
    app_script = Path(__file__).resolve().parents[1] / "app.py"
    return executable, f'"{app_script}"'


def register_steamvr_manifest(
    *, config_path: Path | None = None, target_directory: Path | None = None
) -> bool:
    """Register this app once; return whether SteamVR needs a restart.

    The generated manifest lives in a persistent user directory because a
    PyInstaller one-file bundle extracts its resources to a temporary folder.
    Only the specific manifest path is appended to Steam's appconfig.
    """
    config_path = Path(config_path) if config_path else find_steam_config()
    target_directory = Path(target_directory) if target_directory else runtime_directory()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Steam appconfig.json 格式无效。")
    paths = config.get("manifest_paths", [])
    if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
        raise ValueError("Steam appconfig.json 的 manifest_paths 格式无效。")

    template = json.loads((resource_directory() / "app.vrmanifest").read_text(encoding="utf-8"))
    executable, arguments = _launch_command()
    app = template["applications"][0]
    app["binary_path_windows"] = executable
    if arguments:
        app["arguments"] = arguments
    else:
        app.pop("arguments", None)

    manifest_path = target_directory / "app.vrmanifest"
    manifest_content = json.dumps(template, ensure_ascii=False, indent=2) + "\n"
    changed = _write_if_changed(manifest_path, manifest_content)
    for name in RESOURCE_FILES[1:]:
        source = resource_directory() / name
        changed = _write_if_changed(
            target_directory / name, source.read_text(encoding="utf-8")
        ) or changed

    normalized_manifest = os.path.normcase(os.path.abspath(manifest_path))
    registered = any(
        os.path.normcase(os.path.abspath(item)) == normalized_manifest for item in paths
    )
    if not registered:
        config["manifest_paths"] = [*paths, str(manifest_path.resolve())]
        _write_if_changed(config_path, json.dumps(config, ensure_ascii=False, indent=3) + "\n")
    return changed or not registered


def _device_property(vr_system: Any, index: int, property_name: int) -> str:
    try:
        return str(vr_system.getStringTrackedDeviceProperty(index, property_name))
    except Exception:
        return ""


def matrix34_to_position_forward(
    matrix: Any,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return Standing position in metres and the device's local forward (-Z) axis."""
    position = (float(matrix[0][3]), float(matrix[1][3]), float(matrix[2][3]))
    forward = (-float(matrix[0][2]), -float(matrix[1][2]), -float(matrix[2][2]))
    return position, forward


def _velocity(vector: Any) -> tuple[float, float, float] | None:
    components = tuple(float(vector.v[axis]) for axis in range(3))
    return components if all(math.isfinite(value) for value in components) else None


def _magnitude(vector: tuple[float, float, float] | None) -> float | None:
    return math.hypot(*vector) if vector is not None else None


def _rotation_quaternion(matrix: Any) -> tuple[float, float, float, float] | None:
    """Convert the complete OpenVR rotation matrix to a normalized quaternion."""
    rows = tuple(tuple(float(matrix[row][col]) for col in range(3)) for row in range(3))
    if not all(math.isfinite(value) for row in rows for value in row):
        return None
    trace = rows[0][0] + rows[1][1] + rows[2][2]
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = (
            scale / 4.0,
            (rows[2][1] - rows[1][2]) / scale,
            (rows[0][2] - rows[2][0]) / scale,
            (rows[1][0] - rows[0][1]) / scale,
        )
    else:
        axis = max(range(3), key=lambda index: rows[index][index])
        if axis == 0:
            scale = math.sqrt(max(0.0, 1.0 + rows[0][0] - rows[1][1] - rows[2][2])) * 2.0
            if scale == 0:
                return None
            quaternion = (
                (rows[2][1] - rows[1][2]) / scale,
                scale / 4.0,
                (rows[0][1] + rows[1][0]) / scale,
                (rows[0][2] + rows[2][0]) / scale,
            )
        elif axis == 1:
            scale = math.sqrt(max(0.0, 1.0 + rows[1][1] - rows[0][0] - rows[2][2])) * 2.0
            if scale == 0:
                return None
            quaternion = (
                (rows[0][2] - rows[2][0]) / scale,
                (rows[0][1] + rows[1][0]) / scale,
                scale / 4.0,
                (rows[1][2] + rows[2][1]) / scale,
            )
        else:
            scale = math.sqrt(max(0.0, 1.0 + rows[2][2] - rows[0][0] - rows[1][1])) * 2.0
            if scale == 0:
                return None
            quaternion = (
                (rows[1][0] - rows[0][1]) / scale,
                (rows[0][2] + rows[2][0]) / scale,
                (rows[1][2] + rows[2][1]) / scale,
                scale / 4.0,
            )
    length = math.hypot(*quaternion)
    return tuple(value / length for value in quaternion) if length > 0 else None


def _quaternion_multiply(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _angular_velocity(previous, current, seconds: float) -> tuple[float, float, float]:
    delta = _quaternion_multiply(current, (previous[0], -previous[1], -previous[2], -previous[3]))
    if delta[0] < 0:
        delta = tuple(-value for value in delta)
    axis_length = math.hypot(*delta[1:])
    if axis_length < 1e-9:
        return (0.0, 0.0, 0.0)
    angle = 2.0 * math.atan2(axis_length, max(0.0, delta[0]))
    scale = angle / (axis_length * seconds)
    return tuple(value * scale for value in delta[1:])


def _smooth_vector(previous, current):
    if previous is None:
        return current
    return tuple(0.5 * old + 0.5 * new for old, new in zip(previous, current))


@dataclass(frozen=True)
class _PoseSample:
    index: int
    sampled_at: float
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    linear_velocity: tuple[float, float, float] | None
    angular_velocity: tuple[float, float, float] | None


class PoseVelocityEstimator:
    """Fill in missing or zero driver velocities from consecutive valid poses."""

    def __init__(self):
        self._samples: dict[str, _PoseSample] = {}

    def discard_missing(self, serials: set[str]):
        for serial in self._samples.keys() - serials:
            del self._samples[serial]

    def estimate(
        self, serial: str, index: int, tracking: str,
        position: tuple[float, float, float] | None, matrix: Any,
        linear_velocity: tuple[float, float, float] | None,
        angular_velocity: tuple[float, float, float] | None,
        sampled_at: float,
    ) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None]:
        if not serial or tracking != "OK" or position is None:
            self._samples.pop(serial, None)
            return linear_velocity, angular_velocity
        rotation = _rotation_quaternion(matrix)
        if rotation is None:
            self._samples.pop(serial, None)
            return linear_velocity, angular_velocity

        previous = self._samples.get(serial)
        inferred_linear = None
        inferred_angular = None
        if previous is not None and previous.index == index:
            seconds = sampled_at - previous.sampled_at
            if 0.025 <= seconds <= 0.75:
                raw_linear = tuple(
                    (current - old) / seconds
                    for current, old in zip(position, previous.position)
                )
                raw_angular = _angular_velocity(previous.rotation, rotation, seconds)
                if _magnitude(raw_linear) <= 10.0:
                    inferred_linear = _smooth_vector(previous.linear_velocity, raw_linear)
                if _magnitude(raw_angular) <= 30.0:
                    inferred_angular = _smooth_vector(previous.angular_velocity, raw_angular)

        self._samples[serial] = _PoseSample(
            index, sampled_at, position, rotation, inferred_linear, inferred_angular
        )
        if linear_velocity is None or _magnitude(linear_velocity) < 0.01:
            linear_velocity = inferred_linear if inferred_linear is not None else linear_velocity
        if angular_velocity is None or _magnitude(angular_velocity) < math.radians(1.0):
            angular_velocity = inferred_angular if inferred_angular is not None else angular_velocity
        return linear_velocity, angular_velocity


def read_sensors(
    openvr: Any, vr_system: Any, estimator: PoseVelocityEstimator | None = None,
    sampled_at: float | None = None,
) -> list[SensorSnapshot]:
    """Read all connected HMD, controller, tracker and tracking reference poses."""
    poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
    vr_system.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, poses)
    sampled_at = time.monotonic() if sampled_at is None else sampled_at
    class_names = {
        openvr.TrackedDeviceClass_HMD: "HMD",
        openvr.TrackedDeviceClass_Controller: "Controller",
        openvr.TrackedDeviceClass_GenericTracker: "Tracker",
        openvr.TrackedDeviceClass_TrackingReference: "BaseStation",
    }
    result_names = {
        openvr.TrackingResult_Uninitialized: "Uninitialized",
        openvr.TrackingResult_Calibrating_InProgress: "Calibrating",
        openvr.TrackingResult_Calibrating_OutOfRange: "CalibratingOutOfRange",
        openvr.TrackingResult_Running_OK: "OK",
        openvr.TrackingResult_Running_OutOfRange: "OutOfRange",
        openvr.TrackingResult_Fallback_RotationOnly: "RotationOnly",
    }
    sensors = []
    seen_serials = set()
    for index in range(openvr.k_unMaxTrackedDeviceCount):
        if not vr_system.isTrackedDeviceConnected(index):
            continue
        device_class = vr_system.getTrackedDeviceClass(index)
        if device_class not in class_names:
            continue
        serial = _device_property(vr_system, index, openvr.Prop_SerialNumber_String)
        if serial:
            seen_serials.add(serial)
        pose = poses[index]
        valid = bool(pose.bPoseIsValid)
        position, forward = matrix34_to_position_forward(pose.mDeviceToAbsoluteTracking) if valid else (None, None)
        if position is not None and not all(math.isfinite(value) for value in (*position, *forward)):
            valid = False
            position, forward = None, None
        linear_velocity = _velocity(pose.vVelocity) if valid else None
        angular_velocity = _velocity(pose.vAngularVelocity) if valid else None
        tracking_result = int(pose.eTrackingResult)
        tracking = result_names.get(tracking_result, str(tracking_result))
        if not valid:
            tracking = f"{tracking} / NoPose"
        if estimator is not None:
            linear_velocity, angular_velocity = estimator.estimate(
                serial, index, tracking, position, pose.mDeviceToAbsoluteTracking,
                linear_velocity, angular_velocity, sampled_at,
            )
        sensors.append(
            SensorSnapshot(
                index=index,
                device_class=class_names[device_class],
                serial=serial,
                tracking=tracking,
                position=position,
                forward=forward,
                linear_velocity=linear_velocity,
                angular_velocity=angular_velocity,
                speed_m_s=_magnitude(linear_velocity),
                angular_speed_rad_s=_magnitude(angular_velocity),
            )
        )
    if estimator is not None:
        estimator.discard_missing(seen_serials)
    return sensors
