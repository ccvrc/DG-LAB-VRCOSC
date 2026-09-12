import ipaddress
import json
import logging
import os
import re
import socket
import time
import urllib.request
from urllib.parse import quote
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_last_successful_candidate: tuple[str, int] | None = None


def _http_json(host: str, port: int, path: str, timeout: float = 2.0) -> Any:
    url_host = f"[{quote(host, safe=':')}]" if ":" in host else host
    request = urllib.request.Request(f"http://{url_host}:{port}{path}", headers={"Accept": "application/json"})
    # OSCQuery discovery only contacts this computer. System HTTP proxies must
    # not intercept these requests (especially requests to a local LAN address).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
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

        matches = re.finditer(r"\bOSCQuery\b[^\r\n]*?\bon\s+(?:port\s+)?(\d{1,5})(?!\d)", text, re.IGNORECASE)
        for match in reversed(list(matches)):
            port = int(match.group(1))
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
    return ports


def _normalized_address(address: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return str(parsed) if not parsed.is_unspecified else None


def _local_addresses() -> list[str]:
    addresses = ["127.0.0.1", "::1"]
    # Hostname resolution may omit some interfaces. psutil is already a project
    # dependency and gives us the actual local interfaces, including IPv6.
    try:
        import psutil

        for interface in psutil.net_if_addrs().values():
            for item in interface:
                if item.family in (socket.AF_INET, socket.AF_INET6) and item.address not in addresses:
                    addresses.append(item.address)
    except (ImportError, OSError):
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_UNSPEC):
            ip = result[4][0]
            if ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass
    return addresses


def _local_ipv4_addresses() -> list[str]:
    return [address for address in _local_addresses() if ":" not in address and _normalized_address(address)]


def _is_local_address(address: str, local_addresses: list[str] | None = None) -> bool:
    normalized = _normalized_address(address)
    if normalized is None:
        return False
    if ipaddress.ip_address(normalized).is_loopback:
        return True
    if local_addresses is None:
        local_addresses = _local_addresses()
    return normalized in {_normalized_address(local) for local in local_addresses}


def _ports_from_mdns(wait_seconds: float = 1.0) -> list[tuple[str, int]]:
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf  # type: ignore
    except Exception:
        return []

    local_addresses = _local_addresses()
    deadline = time.monotonic() + max(0.0, wait_seconds)

    class Listener(ServiceListener):  # type: ignore[misc]
        def __init__(self):
            self.found: list[tuple[str, int]] = []

        def add_service(self, zc: Zeroconf, service_type: str, name: str):
            if not name.startswith("VRChat-Client-") or time.monotonic() >= deadline:
                return
            try:
                info = zc.get_service_info(service_type, name, timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            except Exception as exc:
                logger.debug(f"OSCQuery mDNS service info lookup failed for {name}: {exc}")
                return
            if not info or type(info.port) is not int or not 1 <= info.port <= 65535:
                return
            for address in info.parsed_scoped_addresses():
                if not _is_local_address(address, local_addresses):
                    continue
                item = (address, info.port)
                if item not in self.found:
                    self.found.append(item)

        def update_service(self, zc: Zeroconf, service_type: str, name: str):
            self.add_service(zc, service_type, name)

        def remove_service(self, zc: Zeroconf, service_type: str, name: str):
            return

    listener = Listener()
    zeroconf = None
    browser = None
    try:
        zeroconf = Zeroconf()
        browser = ServiceBrowser(zeroconf, "_oscjson._tcp.local.", listener)
        time.sleep(max(0.0, deadline - time.monotonic()))
    except Exception as exc:
        logger.debug("OSCQuery mDNS discovery unavailable: %s", exc)
    finally:
        if browser is not None:
            try:
                browser.cancel()
            except Exception as exc:
                logger.debug("OSCQuery mDNS browser cleanup failed: %s", exc)
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception as exc:
                logger.debug("OSCQuery mDNS cleanup failed: %s", exc)
    return listener.found


def _is_vrchat(host_info: Any) -> bool:
    return isinstance(host_info, dict) and str(host_info.get("NAME", "")).startswith("VRChat-Client-")


def vrchat_osc_endpoint(host: str, http_port: int, host_info: dict[str, Any]) -> tuple[str, int]:
    """Resolve OSCQuery's optional endpoint fields for a local UDP peer."""
    if host_info.get("OSC_TRANSPORT", "UDP") != "UDP":
        raise ValueError("VRChat OSC transport must be UDP")
    # OSCQuery defaults to the HTTP address and port when these fields are absent.
    osc_host = host_info.get("OSC_IP", host)
    if not isinstance(osc_host, str):
        raise ValueError("VRChat OSC address must be an IP address")
    if osc_host in {"0.0.0.0", "::"}:
        osc_host = host
    if not _is_local_address(osc_host):
        raise ValueError("VRChat OSC address must belong to this computer")
    osc_port = host_info.get("OSC_PORT", http_port)
    if isinstance(osc_port, bool) or not isinstance(osc_port, int) or not 1 <= osc_port <= 65535:
        raise ValueError("VRChat OSC port must be an integer between 1 and 65535")
    return osc_host, osc_port


def discover_vrchat_oscquery(timeout: float = 2.0) -> tuple[str, int, dict[str, Any]]:
    global _last_successful_candidate
    seen: set[tuple[str, int]] = set()

    def probe(host: str, port: int) -> tuple[str, int, dict[str, Any]] | None:
        if (host, port) in seen:
            return None
        seen.add((host, port))
        if type(port) is not int or not 1 <= port <= 65535 or not _is_local_address(host):
            return None
        try:
            host_info = _http_json(host, port, "/?HOST_INFO", timeout)
        except Exception as exc:
            logger.debug(f"OSCQuery candidate failed {host}:{port}: {exc}")
            return None
        if _is_vrchat(host_info):
            try:
                vrchat_osc_endpoint(host, port, host_info)
            except ValueError as exc:
                logger.debug("OSCQuery candidate has an invalid OSC endpoint %s:%s: %s", host, port, exc)
                return None
            return host, port, host_info
        return None

    if _last_successful_candidate:
        cached = probe(*_last_successful_candidate)
        if cached:
            return cached
        _last_successful_candidate = None

    for host, port in _ports_from_mdns(wait_seconds=min(1.0, max(0.0, timeout))):
        discovered = probe(host, port)
        if discovered:
            _last_successful_candidate = (host, port)
            return discovered

    ports = _ports_from_logs()
    local_hosts = _local_ipv4_addresses() if ports else []
    for port in ports:
        for host in local_hosts:
            discovered = probe(host, port)
            if discovered:
                _last_successful_candidate = (host, port)
                return discovered

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
