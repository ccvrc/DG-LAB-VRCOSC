import json
import logging
import os
import re
import socket
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_last_successful_candidate: tuple[str, int] | None = None


def _http_json(host: str, port: int, path: str, timeout: float = 2.0) -> Any:
    request = urllib.request.Request(f"http://{host}:{port}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _vrc_config_dir() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    candidate = Path(local_app_data).parent / "LocalLow" / "VRChat" / "VRChat"
    return candidate if candidate.exists() else None


def _latest_vrc_log() -> Path | None:
    config_dir = _vrc_config_dir()
    if not config_dir:
        return None
    logs = sorted(config_dir.glob("output_log*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _recent_vrc_logs(limit: int = 3) -> list[Path]:
    config_dir = _vrc_config_dir()
    if not config_dir:
        return []
    return sorted(config_dir.glob("output_log*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _ports_from_logs() -> list[int]:
    ports: list[int] = []
    for log_path in _recent_vrc_logs():
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for pattern in (r"of type OSCQuery on (\d+)", r"OSCQuery.*?on.*?(\d{4,5})"):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                port = int(match.group(1))
                if port not in ports:
                    ports.append(port)
    return ports


def _local_ipv4_addresses() -> list[str]:
    addresses = ["127.0.0.1"]
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = result[4][0]
            if ip not in addresses and not ip.startswith("169.254."):
                addresses.append(ip)
    except OSError:
        pass
    return addresses


def _ports_from_mdns(wait_seconds: float = 1.0) -> list[tuple[str, int]]:
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf  # type: ignore
    except Exception:
        return []

    class Listener(ServiceListener):  # type: ignore[misc]
        def __init__(self):
            self.found: list[tuple[str, int]] = []

        def add_service(self, zc: Zeroconf, service_type: str, name: str):
            info = zc.get_service_info(service_type, name)
            if not info:
                return
            for address in info.parsed_scoped_addresses() or ["127.0.0.1"]:
                item = (address, int(info.port))
                if item not in self.found:
                    self.found.append(item)

        def update_service(self, zc: Zeroconf, service_type: str, name: str):
            self.add_service(zc, service_type, name)

        def remove_service(self, zc: Zeroconf, service_type: str, name: str):
            return

    listener = Listener()
    zeroconf = Zeroconf()
    try:
        ServiceBrowser(zeroconf, "_oscjson._tcp.local.", listener)
        time.sleep(wait_seconds)
    finally:
        zeroconf.close()
    return listener.found


def _is_vrchat(host_info: Any) -> bool:
    return isinstance(host_info, dict) and str(host_info.get("NAME", "")).startswith("VRChat-Client-")


def discover_vrchat_oscquery(timeout: float = 2.0) -> tuple[str, int, dict[str, Any]]:
    global _last_successful_candidate
    candidates: list[tuple[str, int]] = []
    if _last_successful_candidate:
        candidates.append(_last_successful_candidate)
    for port in _ports_from_logs():
        candidates.extend((host, port) for host in _local_ipv4_addresses())
    candidates.extend(_ports_from_mdns())

    seen: set[tuple[str, int]] = set()
    for host, port in candidates:
        if (host, port) in seen:
            continue
        seen.add((host, port))
        try:
            host_info = _http_json(host, port, "/?HOST_INFO", timeout)
        except Exception as exc:
            logger.debug(f"OSCQuery candidate failed {host}:{port}: {exc}")
            continue
        if _is_vrchat(host_info):
            _last_successful_candidate = (host, port)
            return host, port, host_info

    raise RuntimeError("未能发现 VRChat OSCQuery 服务，请确认 VRChat OSC 已开启")


def collect_nodes(node: Any, output: list[dict[str, Any]]):
    if isinstance(node, dict):
        full_path = node.get("FULL_PATH")
        if isinstance(full_path, str):
            output.append(
                {
                    "path": full_path,
                    "type": node.get("TYPE"),
                    "value": node.get("VALUE"),
                    "access": node.get("ACCESS"),
                }
            )

        contents = node.get("CONTENTS")
        if isinstance(contents, dict):
            for child in contents.values():
                collect_nodes(child, output)
        elif isinstance(contents, list):
            for child in contents:
                collect_nodes(child, output)
    elif isinstance(node, list):
        for child in node:
            collect_nodes(child, output)


def fetch_vrchat_osc_nodes(timeout: float = 2.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    host, port, host_info = discover_vrchat_oscquery(timeout)
    tree = _http_json(host, port, "/", timeout)
    nodes: list[dict[str, Any]] = []
    collect_nodes(tree, nodes)
    logger.info(f"Fetched {len(nodes)} OSCQuery nodes from VRChat at {host}:{port}")
    return nodes, host_info


def extract_avatar_id(nodes: list[dict[str, Any]]) -> str | None:
    for node in nodes:
        path = node.get("path") or node.get("FULL_PATH")
        if path != "/avatar/change":
            continue

        value = node.get("value") if "value" in node else node.get("VALUE")
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str) and value.startswith("avtr_"):
            return value
    return None
