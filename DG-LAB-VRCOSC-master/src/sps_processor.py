import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

OGB_PREFIX = "/avatar/parameters/OGB/"

SOCKET_SOURCE_KEYS = ("own_hands", "other_hands", "my_plugs", "other_plugs", "other_sockets")
PLUG_SOURCE_KEYS = ("own_hands", "other_hands", "my_sockets", "other_sockets", "other_plugs")


def default_sources(kind: str) -> dict[str, bool]:
    if kind == "Orf":
        return {
            "own_hands": False,
            "other_hands": True,
            "my_plugs": False,
            "other_plugs": True,
            "other_sockets": True,
        }
    if kind == "Pen":
        return {
            "own_hands": False,
            "other_hands": True,
            "my_sockets": False,
            "other_sockets": True,
            "other_plugs": True,
        }
    return {}


@dataclass
class SPSBinding:
    kind: str
    zone_id: str
    channels: dict[str, bool] = field(default_factory=lambda: {"A": False, "B": False})
    min_strength: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    max_strength: dict[str, int] = field(default_factory=lambda: {"A": 100, "B": 100})
    sources: dict[str, bool] = field(default_factory=dict)


class SPSPenetrationDepthEstimator:
    def __init__(self):
        self.length: float | None = None
        self.recent_lengths: list[float] = []
        self.penetrating_length_hint: float | None = None

    def reset(self):
        self.length = None
        self.recent_lengths = []
        self.penetrating_length_hint = None

    def update(self, root_proximity: Any, tip_proximity: Any):
        if not isinstance(root_proximity, (int, float)) or not isinstance(tip_proximity, (int, float)):
            self.reset()
            return

        root = float(root_proximity)
        tip = float(tip_proximity)
        if root < 0.01 or tip < 0.01:
            self.reset()
            return
        if root > 0.95:
            return

        candidate_length = tip - root
        if candidate_length < 0.02:
            return

        if tip > 0.99:
            if self.penetrating_length_hint is None or candidate_length > self.penetrating_length_hint:
                self.penetrating_length_hint = candidate_length
            if self.length is None:
                self.length = self.penetrating_length_hint
            return

        self.recent_lengths.insert(0, candidate_length)
        del self.recent_lengths[8:]
        self.length = self.calculate_stable_length()

    def calculate_stable_length(self) -> float | None:
        if len(self.recent_lengths) < 4:
            return self.penetrating_length_hint

        sorted_lengths = sorted(self.recent_lengths)
        closest_pair: tuple[float, float] | None = None
        closest_distance = 1.0
        for index in range(1, len(sorted_lengths)):
            previous = sorted_lengths[index - 1]
            current = sorted_lengths[index]
            distance = abs(current - previous)
            if distance < closest_distance:
                closest_distance = distance
                closest_pair = (previous, current)

        if closest_pair:
            return sum(closest_pair) / 2.0
        return self.recent_lengths[0] if self.recent_lengths else self.penetrating_length_hint

    def penetration_amount(self, root_proximity: Any, tip_proximity: Any) -> float | None:
        if not isinstance(root_proximity, (int, float)) or not isinstance(tip_proximity, (int, float)):
            return None

        root = float(root_proximity)
        tip = float(tip_proximity)
        if root <= 0 and tip <= 0:
            return None
        if tip <= 0.99:
            return 0.0
        if not self.length or self.length <= 0:
            return 0.0

        inserted_penetration = max(0.0, root)
        inserted_ratio = min(1.0, inserted_penetration / self.length)
        return max(0.0, inserted_ratio)


class SPSProcessor:
    def __init__(self):
        self.bindings: list[SPSBinding] = []
        self.zone_values: dict[tuple[str, str], dict[str, Any]] = {}
        self.depth_estimators: dict[tuple[str, str, str], SPSPenetrationDepthEstimator] = {}

    @staticmethod
    def normalize_zone_id(zone_id: Any) -> str:
        zone_id = str(zone_id)
        if re.search(r"\\u[0-9a-fA-F]{4}", zone_id):
            zone_id = re.sub(
                r"\\u([0-9a-fA-F]{4})",
                lambda match: chr(int(match.group(1), 16)),
                zone_id,
            )
        return zone_id

    @staticmethod
    def parse_ogb_address(address: str) -> tuple[str, str, str] | None:
        if not address.startswith(OGB_PREFIX):
            return None

        relative = address[len(OGB_PREFIX):]
        parts = relative.split("/")
        if len(parts) < 3:
            return None

        kind = parts[0]
        contact_type = parts[-1]
        zone_id = SPSProcessor.normalize_zone_id("/".join(parts[1:-1]))
        if kind not in {"Orf", "Pen"} or not zone_id or not contact_type:
            return None
        return kind, zone_id, contact_type

    @staticmethod
    def normalize_value(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        return value

    def update_value(self, address: str, value: Any) -> bool:
        parsed = self.parse_ogb_address(address)
        if not parsed:
            return False

        kind, zone_id, contact_type = parsed
        zone = self.zone_values.setdefault((kind, zone_id), {})
        zone[contact_type] = self.normalize_value(value)
        self.update_depth_estimator(kind, zone_id, contact_type, zone)
        return True

    def update_depth_estimator(self, kind: str, zone_id: str, contact_type: str, values: dict[str, Any]):
        if kind != "Orf" or contact_type not in {
            "PenSelfNewRoot",
            "PenSelfNewTip",
            "PenOthersNewRoot",
            "PenOthersNewTip",
        }:
            return

        owner = "self" if contact_type.startswith("PenSelf") else "others"
        root_key = f"Pen{owner.capitalize()}NewRoot"
        tip_key = f"Pen{owner.capitalize()}NewTip"
        estimator = self.depth_estimators.setdefault((kind, zone_id, owner), SPSPenetrationDepthEstimator())
        estimator.update(values.get(root_key), values.get(tip_key))

    def set_bindings(self, bindings: list[dict[str, Any]]):
        parsed_bindings: list[SPSBinding] = []
        for item in bindings:
            kind = item.get("kind")
            zone_id = self.normalize_zone_id(item.get("zone_id", ""))
            if kind not in {"Orf", "Pen"} or not zone_id:
                continue
            parsed_bindings.append(
                SPSBinding(
                    kind=kind,
                    zone_id=zone_id,
                    channels={
                        "A": bool(item.get("channels", {}).get("A", False)),
                        "B": bool(item.get("channels", {}).get("B", False)),
                    },
                    min_strength={
                        "A": int(item.get("min_strength", {}).get("A", 0)),
                        "B": int(item.get("min_strength", {}).get("B", 0)),
                    },
                    max_strength={
                        "A": int(item.get("max_strength", {}).get("A", 100)),
                        "B": int(item.get("max_strength", {}).get("B", 100)),
                    },
                    sources=self.normalize_sources(kind, item.get("sources")),
                )
            )
        self.bindings = parsed_bindings
        logger.info(f"SPS bindings updated: {len(self.bindings)}")

    @staticmethod
    def normalize_sources(kind: str, sources: Any) -> dict[str, bool]:
        normalized = default_sources(kind)
        if not isinstance(sources, dict):
            return normalized

        allowed_keys = SOCKET_SOURCE_KEYS if kind == "Orf" else PLUG_SOURCE_KEYS if kind == "Pen" else ()
        for key in allowed_keys:
            if key in sources:
                normalized[key] = bool(sources.get(key))
        return normalized

    def get_zone_level(self, kind: str, zone_id: str, sources: dict[str, bool] | None = None) -> float:
        values = self.zone_values.get((kind, zone_id), {})
        if not values:
            return 0.0

        levels: list[float] = []
        enabled_sources = self.normalize_sources(kind, sources)

        def add_number(key: str):
            value = values.get(key)
            if isinstance(value, (int, float)):
                levels.append(float(value))

        def add_gated(source_key: str, close_key: str, value_key: str):
            if not enabled_sources.get(source_key, False):
                return
            close_value = values.get(close_key)
            value = values.get(value_key)
            if close_value is False:
                return
            if isinstance(value, (int, float)):
                levels.append(float(value))

        def add_new_penetration(source_key: str, owner: str, legacy_key: str, close_key: str | None = None):
            if not enabled_sources.get(source_key, False):
                return
            if close_key and values.get(close_key) is False:
                legacy_value = None
            else:
                legacy_value = values.get(legacy_key)

            root_key = f"Pen{owner.capitalize()}NewRoot"
            tip_key = f"Pen{owner.capitalize()}NewTip"
            estimator = self.depth_estimators.get((kind, zone_id, owner))
            new_value = estimator.penetration_amount(values.get(root_key), values.get(tip_key)) if estimator else None
            if new_value is not None:
                levels.append(new_value)
            elif isinstance(legacy_value, (int, float)):
                levels.append(float(legacy_value))

        if kind == "Orf":
            add_gated("other_hands", "TouchOthersClose", "TouchOthers")
            add_gated("own_hands", "TouchSelfClose", "TouchSelf")
            add_new_penetration("my_plugs", "self", "PenSelf")
            add_new_penetration("other_plugs", "others", "PenOthers", "PenOthersClose")
            if enabled_sources.get("other_sockets", False):
                add_number("FrotOthers")
        elif kind == "Pen":
            add_gated("other_hands", "TouchOthersClose", "TouchOthers")
            add_gated("own_hands", "TouchSelfClose", "TouchSelf")
            add_gated("other_plugs", "FrotOthersClose", "FrotOthers")
            if enabled_sources.get("other_sockets", False):
                add_number("PenOthers")
            if enabled_sources.get("my_sockets", False):
                add_number("PenSelf")

        if not levels:
            return 0.0
        return max(0.0, min(1.0, max(levels)))

    def get_channel_levels(self) -> dict[str, float]:
        levels = {"A": 0.0, "B": 0.0}
        for binding in self.bindings:
            zone_level = self.get_zone_level(binding.kind, binding.zone_id, binding.sources)
            for channel_name in ("A", "B"):
                if not binding.channels.get(channel_name):
                    continue
                min_percent = max(0, min(100, binding.min_strength.get(channel_name, 0)))
                max_percent = max(0, min(100, binding.max_strength.get(channel_name, 100)))
                if min_percent > max_percent:
                    min_percent, max_percent = max_percent, min_percent
                mapped_level = (min_percent + zone_level * (max_percent - min_percent)) / 100.0
                levels[channel_name] = max(levels[channel_name], mapped_level)
        return levels

    @staticmethod
    def discover_zones_from_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
        zones: set[tuple[str, str]] = set()
        for node in nodes:
            path = node.get("path") or node.get("FULL_PATH")
            if not isinstance(path, str):
                continue
            parsed = SPSProcessor.parse_ogb_address(path)
            if parsed:
                kind, zone_id, _ = parsed
                zones.add((kind, zone_id))

        return [
            {"kind": kind, "zone_id": zone_id}
            for kind, zone_id in sorted(zones, key=lambda item: (item[0], item[1]))
        ]

    def has_any_activity(self) -> bool:
        """检查是否有任意 zone 包含非零的 OGB 数据（不受 source 过滤影响）。"""
        for values in self.zone_values.values():
            for value in values.values():
                if isinstance(value, (int, float)) and float(value) > 0:
                    return True
        return False
