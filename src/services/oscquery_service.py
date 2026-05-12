"""
Local OSCQuery service for VRChat integration.

The production path intentionally owns OSCQuery instead of delegating to
vrchat_oscquery so that we can keep the local VRChat handshake on loopback and
survive VRChat starting after this application.
"""
import asyncio
import contextlib
import ipaddress
import logging
import socket
import struct
import uuid
from typing import Any, Optional

import psutil
from aiohttp import web
from pythonosc import udp_client
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from services.vrchat_oscquery_inspector import discover_vrchat_oscquery

logger = logging.getLogger(__name__)

LOOPBACK_HOST = "127.0.0.1"
VRC_DEFAULT_OSC_PORT = 9000
OSCQUERY_SERVICE_TYPE = "_oscjson._tcp.local."
OSC_SERVICE_TYPE = "_osc._udp.local."
MDNS_ADDRESS = "224.0.0.251"
MDNS_PORT = 5353


def _unused_tcp_port(host: str = LOOPBACK_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _normalise_local_host(host: Any) -> str:
    value = str(host or "").strip()
    if value in {"", "0.0.0.0", "::", "::1", "localhost"}:
        return LOOPBACK_HOST
    return value


def _encode_dns_name(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    out = bytearray()
    for label in labels:
        raw = label.encode("utf-8")
        if len(raw) > 63:
            raise ValueError(f"DNS label is too long: {label}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _dns_record(name: str, record_type: int, record_class: int, ttl: int, data: bytes) -> bytes:
    return (
        _encode_dns_name(name)
        + struct.pack("!HHIH", record_type, record_class, ttl, len(data))
        + data
    )


def _txt_record(*values: str) -> bytes:
    out = bytearray()
    for value in values:
        raw = value.encode("utf-8")
        if len(raw) > 255:
            raise ValueError(f"TXT value is too long: {value}")
        out.append(len(raw))
        out.extend(raw)
    return bytes(out)


def _srv_record(port: int, target: str) -> bytes:
    return struct.pack("!HHH", 0, 0, int(port)) + _encode_dns_name(target)


def _a_record(ip: str) -> bytes:
    return socket.inet_aton(ip)


def _active_multicast_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    for interface, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(interface)
        if not stats or not stats.isup:
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            try:
                parsed = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast:
                continue
            if ip not in addresses:
                addresses.append(ip)
    return addresses


class MdnsBroadcaster:
    """Sends explicit OSCQuery mDNS responses for VRChat's discovery window."""

    def __init__(self, instance_name: str):
        self.instance_name = instance_name
        self.target_name = f"{instance_name}.local."

    def broadcast(self, http_port: int, osc_port: int, reason: str = "periodic") -> int:
        packets = [
            self._build_packet(OSCQUERY_SERVICE_TYPE, http_port),
            self._build_packet(OSC_SERVICE_TYPE, osc_port),
        ]
        addresses = _active_multicast_ipv4_addresses()
        if not addresses:
            logger.debug("OSCQuery mDNS broadcast skipped: no active multicast IPv4 interface")
            return 0

        sent = 0
        for address in addresses:
            for packet in packets:
                try:
                    self._send_packet(address, packet)
                    sent += 1
                except OSError as exc:
                    logger.debug("OSCQuery mDNS broadcast failed on %s: %s", address, exc)
        if sent:
            logger.debug("OSCQuery mDNS broadcast sent %s packets (%s)", sent, reason)
        return sent

    def _build_packet(self, service_type: str, port: int) -> bytes:
        instance = f"{self.instance_name}.{service_type}"
        answers = [
            _dns_record(service_type, 12, 0x0001, 120, _encode_dns_name(instance)),
        ]
        additionals = [
            _dns_record(instance, 33, 0x8001, 120, _srv_record(port, self.target_name)),
            _dns_record(instance, 16, 0x8001, 120, _txt_record("txtvers=1")),
            _dns_record(self.target_name, 1, 0x8001, 120, _a_record(LOOPBACK_HOST)),
        ]
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, len(additionals))
        return header + b"".join(answers) + b"".join(additionals)

    def _send_packet(self, interface_ip: str, packet: bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
            try:
                sock.bind((interface_ip, MDNS_PORT))
            except OSError:
                sock.bind((interface_ip, 0))
            sock.sendto(packet, (MDNS_ADDRESS, MDNS_PORT))
        finally:
            sock.close()


class DynamicVRChatOSCClient:
    """Small send_message-compatible wrapper whose destination can be updated."""

    def __init__(self, host: str = LOOPBACK_HOST, port: int = VRC_DEFAULT_OSC_PORT):
        self._host = _normalise_local_host(host)
        self._port = int(port)
        self._client = udp_client.SimpleUDPClient(self._host, self._port)

    @property
    def endpoint(self) -> tuple[str, int]:
        return self._host, self._port

    def set_endpoint(self, host: str, port: int) -> bool:
        host = _normalise_local_host(host)
        port = int(port)
        if self._host == host and self._port == port:
            return False

        self._host = host
        self._port = port
        self._client = udp_client.SimpleUDPClient(self._host, self._port)
        return True

    def send_message(self, address: str, value: Any):
        self._client.send_message(address, value)


class OSCQueryService:
    """VRChat OSCQuery server and VRChat endpoint discovery."""

    def __init__(
        self,
        app_name: str = "DG-LAB-VRCOSC",
        rebroadcast_interval: float = 5.0,
        discovery_interval: float = 5.0,
    ):
        self.app_name = app_name
        self.instance_name = f"{app_name}-{uuid.uuid4().hex[:6]}"
        self.rebroadcast_interval = rebroadcast_interval
        self.discovery_interval = discovery_interval

        self._running = False
        self._dispatcher: Optional[Dispatcher] = None
        self._osc_port: Optional[int] = None
        self._http_port: Optional[int] = None
        self._osc_transport = None
        self._osc_protocol = None
        self._http_runner: Optional[web.AppRunner] = None
        self._zeroconf: Optional[AsyncZeroconf] = None
        self._service_infos: list[ServiceInfo] = []
        self._rebroadcast_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._discovery_had_success = False
        self._vrc_client = DynamicVRChatOSCClient()
        self._mdns_broadcaster = MdnsBroadcaster(self.instance_name)
        self._host_info_request_count = 0
        self._node_request_count = 0

    async def start(self, dispatcher: Dispatcher) -> int:
        """Start UDP OSC receive, HTTP OSCQuery, mDNS, and VRChat discovery."""
        if self._running:
            if self._osc_port is None:
                raise RuntimeError("OSCQuery service is running without an OSC port")
            return self._osc_port

        self._dispatcher = dispatcher
        try:
            await self._start_osc_server(dispatcher)
            await self._start_http_server()
            await self._register_mdns()

            self._running = True
            self._rebroadcast_task = asyncio.create_task(self._rebroadcast_loop())
            self._discovery_task = asyncio.create_task(self._vrchat_discovery_loop())
            await self._broadcast_mdns("startup")

            logger.info(
                "OSCQuery service '%s' started: HTTP %s:%s, OSC %s:%s",
                self.instance_name,
                LOOPBACK_HOST,
                self._http_port,
                LOOPBACK_HOST,
                self._osc_port,
            )
            return int(self._osc_port)
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        self._running = False

        for task in (self._rebroadcast_task, self._discovery_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._rebroadcast_task = None
        self._discovery_task = None

        if self._zeroconf:
            for service_info in self._service_infos:
                with contextlib.suppress(Exception):
                    await self._zeroconf.async_unregister_service(service_info)
        if self._zeroconf:
            with contextlib.suppress(Exception):
                await self._zeroconf.async_close()
        self._zeroconf = None
        self._service_infos = []

        if self._http_runner:
            with contextlib.suppress(Exception):
                await self._http_runner.cleanup()
        self._http_runner = None

        if self._osc_transport:
            self._osc_transport.close()
        self._osc_transport = None
        self._osc_protocol = None
        self._osc_port = None
        self._http_port = None

    def get_vrc_client(self) -> DynamicVRChatOSCClient:
        return self._vrc_client

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def osc_port(self) -> Optional[int]:
        return self._osc_port

    @property
    def http_port(self) -> Optional[int]:
        return self._http_port

    async def _start_osc_server(self, dispatcher: Dispatcher):
        osc_server = AsyncIOOSCUDPServer(
            (LOOPBACK_HOST, 0),
            dispatcher,
            asyncio.get_running_loop(),
        )
        self._osc_transport, self._osc_protocol = await osc_server.create_serve_endpoint()
        sock = self._osc_transport.get_extra_info("socket")
        self._osc_port = int(sock.getsockname()[1])

    async def _start_http_server(self):
        self._http_port = _unused_tcp_port()
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle_request)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, LOOPBACK_HOST, self._http_port)
        await site.start()

    async def _register_mdns(self):
        if self._http_port is None or self._osc_port is None:
            raise RuntimeError("HTTP port is not available")

        self._zeroconf = AsyncZeroconf()
        self._service_infos = [
            ServiceInfo(
                OSCQUERY_SERVICE_TYPE,
                f"{self.instance_name}.{OSCQUERY_SERVICE_TYPE}",
                addresses=[socket.inet_aton(LOOPBACK_HOST)],
                port=self._http_port,
                properties={"txtvers": "1"},
                server=f"{self.instance_name}.local.",
            ),
            ServiceInfo(
                OSC_SERVICE_TYPE,
                f"{self.instance_name}.{OSC_SERVICE_TYPE}",
                addresses=[socket.inet_aton(LOOPBACK_HOST)],
                port=self._osc_port,
                properties={"txtvers": "1"},
                server=f"{self.instance_name}.local.",
            ),
        ]
        for service_info in self._service_infos:
            await self._zeroconf.async_register_service(service_info)

    async def _handle_request(self, request: web.Request) -> web.Response:
        peername = request.transport.get_extra_info("peername") if request.transport else None
        if request.query_string == "HOST_INFO":
            self._host_info_request_count += 1
            logger.info(
                "OSCQuery HOST_INFO requested from %s (count=%s)",
                peername or "unknown",
                self._host_info_request_count,
            )
            return web.json_response(self._host_info())

        node = self._node_for_path(request.path)
        if node is None:
            return web.Response(status=404)
        self._node_request_count += 1
        logger.info(
            "OSCQuery node requested: %s from %s (count=%s)",
            request.path,
            peername or "unknown",
            self._node_request_count,
        )
        return web.json_response(node)

    def _host_info(self) -> dict[str, Any]:
        return {
            "NAME": self.instance_name,
            "OSC_IP": LOOPBACK_HOST,
            "OSC_PORT": self._osc_port,
            "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {
                "ACCESS": True,
                "VALUE": True,
                "RANGE": True,
                "DESCRIPTION": True,
                "TAGS": True,
                "CRITICAL": True,
                "CLIPMODE": True,
            },
        }

    def _root_node(self) -> dict[str, Any]:
        return {
            "FULL_PATH": "/",
            "ACCESS": 0,
            "CONTENTS": {
                "avatar": {
                    "FULL_PATH": "/avatar",
                    "ACCESS": 0,
                    "CONTENTS": {
                        "change": {
                            "FULL_PATH": "/avatar/change",
                            "TYPE": "s",
                            "ACCESS": 2,
                            "VALUE": [""],
                        },
                        "parameters": {
                            "FULL_PATH": "/avatar/parameters",
                            "ACCESS": 0,
                        },
                    },
                },
                "tracking": {
                    "FULL_PATH": "/tracking",
                    "ACCESS": 0,
                },
            },
        }

    def _node_for_path(self, path: str) -> Optional[dict[str, Any]]:
        root = self._root_node()
        if path in {"", "/"}:
            return root

        if path == "/avatar":
            return root["CONTENTS"]["avatar"]
        if path == "/avatar/change":
            return root["CONTENTS"]["avatar"]["CONTENTS"]["change"]
        if path == "/avatar/parameters":
            return root["CONTENTS"]["avatar"]["CONTENTS"]["parameters"]
        if path == "/tracking":
            return root["CONTENTS"]["tracking"]
        return None

    async def _rebroadcast_loop(self):
        for delay in (0.0, 1.0, 3.0):
            if not self._running:
                return
            if delay:
                await asyncio.sleep(delay)
            await self._update_registered_services()
            await self._broadcast_mdns(f"startup burst {delay:g}s")

        while self._running:
            await asyncio.sleep(self.rebroadcast_interval)
            await self._update_registered_services()
            await self._broadcast_mdns("periodic")

    async def _update_registered_services(self):
        if not self._zeroconf or not self._service_infos:
            return
        for service_info in self._service_infos:
            try:
                await self._zeroconf.async_update_service(service_info)
            except Exception as exc:
                logger.debug("OSCQuery zeroconf service update failed: %s", exc)

    async def _broadcast_mdns(self, reason: str):
        if self._http_port is None or self._osc_port is None:
            return
        try:
            sent = await asyncio.to_thread(
                self._mdns_broadcaster.broadcast,
                self._http_port,
                self._osc_port,
                reason,
            )
            if sent == 0:
                logger.debug("OSCQuery mDNS active broadcast sent no packets (%s)", reason)
        except Exception as exc:
            logger.debug("OSCQuery mDNS active broadcast failed (%s): %s", reason, exc)

    async def _vrchat_discovery_loop(self):
        while self._running:
            await self._discover_vrchat_once()
            await asyncio.sleep(self.discovery_interval)

    async def _discover_vrchat_once(self):
        try:
            host, port, host_info = await asyncio.to_thread(discover_vrchat_oscquery, 1.0)
            osc_host = _normalise_local_host(host_info.get("OSC_IP") or LOOPBACK_HOST)
            osc_port = int(host_info.get("OSC_PORT") or VRC_DEFAULT_OSC_PORT)
            changed = self._vrc_client.set_endpoint(osc_host, osc_port)
            if changed or not self._discovery_had_success:
                logger.info(
                    "VRChat OSCQuery discovered at %s:%s, OSC target %s:%s",
                    host,
                    port,
                    osc_host,
                    osc_port,
                )
                await self._broadcast_mdns("vrchat discovered")
            self._discovery_had_success = True
        except Exception as exc:
            if self._discovery_had_success:
                logger.warning("VRChat OSCQuery discovery lost: %s", exc)
            else:
                logger.debug("VRChat OSCQuery is not available yet: %s", exc)
            self._discovery_had_success = False
