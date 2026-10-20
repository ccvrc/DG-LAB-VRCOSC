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
import threading
import uuid
from typing import Any, Optional

import psutil
from aiohttp import web
from pythonosc import udp_client
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from services.vrchat_oscquery_inspector import discover_vrchat_oscquery, vrchat_osc_endpoint

logger = logging.getLogger(__name__)

LOOPBACK_HOST = "127.0.0.1"
VRC_DEFAULT_OSC_PORT = 9000
OSCQUERY_SERVICE_TYPE = "_oscjson._tcp.local."
OSC_SERVICE_TYPE = "_osc._udp.local."
MDNS_ADDRESS = "224.0.0.251"
MDNS_PORT = 5353


def _normalise_local_host(host: Any) -> str:
    value = str(host or "").strip()
    if value in {"", "0.0.0.0", "::", "localhost"}:
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


def _read_dns_name(data: bytes, offset: int) -> tuple[Optional[str], int]:
    """Read a (possibly compressed) DNS name; returns (lower-case name, next offset)."""

    labels: list[str] = []
    next_offset = offset
    jumped = False
    hops = 0
    while True:
        if offset >= len(data):
            return None, next_offset
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                return None, next_offset
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            offset += 2
            if not jumped:
                next_offset = offset
            jumped = True
            hops += 1
            if hops > 8 or pointer >= len(data):
                return None, next_offset
            offset = pointer
            continue
        if length > 63 or offset + 1 + length > len(data):
            return None, next_offset
        labels.append(data[offset + 1:offset + 1 + length].decode("ascii", "ignore").lower())
        offset += 1 + length
    return ".".join(labels), next_offset


def _parse_query_names(data: bytes, limit: int = 16) -> list[str]:
    """Return the lower-cased names that an mDNS *query* packet asks about."""

    if len(data) < 12:
        return []
    _ident, flags, qdcount = struct.unpack("!HHH", data[0:6])
    if flags & 0x8000:
        return []  # a response, not a query
    offset = 12
    names: list[str] = []
    for _ in range(min(qdcount, limit)):
        name, offset = _read_dns_name(data, offset)
        if not name:
            break
        names.append(name)
        offset += 4  # QTYPE + QCLASS
        if offset > len(data):
            break
    return names


class MdnsResponder:
    """Persistent mDNS responder for our OSCQuery and OSC services.

    VRChat only sends avatar parameters to services whose mDNS records it has
    resolved, and it resolves them by sending its own queries.  The previous
    implementation opened a fresh socket per announcement, so it could only
    announce and never answer; when an announcement was missed, VRChat never
    learned the dynamic port.  This keeps one long lived socket per active
    interface, bound to 5353 and joined to the mDNS group (the same approach
    VRCFaceTracking uses), so VRChat's queries are answered as well.
    """

    def __init__(self, instance_name: str):
        self.instance_name = instance_name
        self.target_name = f"{instance_name}.local."
        self.interfaces: list[str] = []
        self.answered_queries = 0
        self.announced_packets = 0
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()
        self._sockets: list[tuple[socket.socket, str]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._http_port = 0
        self._osc_port = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> list[str]:
        self._stop_event.clear()
        for interface_ip in _active_multicast_ipv4_addresses():
            try:
                self._sockets.append((self._open_socket(interface_ip), interface_ip))
                self.interfaces.append(interface_ip)
            except OSError as exc:
                logger.warning("mDNS 无法在 %s:5353 监听，跳过该网卡: %s", interface_ip, exc)
        if not self._sockets:
            logger.warning("mDNS 应答器未启动：没有可用于组播的 IPv4 网卡")
            return []
        self._thread = threading.Thread(target=self._serve, name="oscquery-mdns", daemon=True)
        self._thread.start()
        return list(self.interfaces)

    def stop(self):
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        for sock, _interface in self._sockets:
            with contextlib.suppress(OSError):
                sock.close()
        self._sockets.clear()
        self.interfaces.clear()

    def _open_socket(self, interface_ip: str) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
            sock.bind((interface_ip, MDNS_PORT))
            group = socket.inet_aton(MDNS_ADDRESS) + socket.inet_aton(interface_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        return sock

    def _serve(self):
        while not self._stop_event.is_set():
            for sock, _interface in list(self._sockets):
                try:
                    data, source = sock.recvfrom(9000)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if not self._stop_event.is_set():
                        logger.debug("mDNS 收包失败: %s", exc)
                    continue
                for packet in self._responses_for_query(data):
                    try:
                        sock.sendto(packet, (MDNS_ADDRESS, MDNS_PORT))
                    except OSError as exc:
                        self.last_error = f"{source}: {exc}"
                        logger.debug("mDNS 应答发送失败: %s", exc)
                    else:
                        self.answered_queries += 1
                        if self.answered_queries == 1:
                            logger.info("mDNS 应答器已开始应答查询（来自 %s）：VRChat 可以解析本程序", source)

    # ---------------------------------------------------------------- answering
    def _match_name(self, name: str) -> Optional[tuple[str, str]]:
        name = name.rstrip(".").lower()
        for service_type in (OSCQUERY_SERVICE_TYPE, OSC_SERVICE_TYPE):
            if name == service_type.rstrip(".").lower():
                return "service", service_type
            if name == f"{self.instance_name}.{service_type.rstrip('.')}".lower():
                return "instance", service_type
        if name in {self.target_name.rstrip(".").lower(), self.instance_name.lower()}:
            return "host", ""
        return None

    def _responses_for_query(self, data: bytes) -> list[bytes]:
        with self._lock:
            http_port, osc_port = self._http_port, self._osc_port
        if not http_port or not osc_port:
            return []
        packets: list[bytes] = []
        for name in _parse_query_names(data):
            match = self._match_name(name)
            if match is None:
                continue
            kind, service_type = match
            if kind == "service":
                port = http_port if service_type == OSCQUERY_SERVICE_TYPE else osc_port
                packets.append(self._build_packet(service_type, port))
            elif kind == "instance":
                port = http_port if service_type == OSCQUERY_SERVICE_TYPE else osc_port
                packets.append(self._build_instance_packet(service_type, port))
            else:
                packets.append(self._build_address_packet())
        return packets

    # --------------------------------------------------------------- announcing
    def announce(self, http_port: int, osc_port: int, reason: str = "periodic") -> int:
        with self._lock:
            self._http_port = int(http_port)
            self._osc_port = int(osc_port)
            sockets = list(self._sockets)
        if not sockets:
            logger.warning("mDNS 公告未能发送（%s）：应答器没有可用网卡", reason)
            return 0
        packets = [
            self._build_packet(OSCQUERY_SERVICE_TYPE, int(http_port)),
            self._build_packet(OSC_SERVICE_TYPE, int(osc_port)),
        ]
        sent = 0
        for sock, interface_ip in sockets:
            for packet in packets:
                try:
                    sock.sendto(packet, (MDNS_ADDRESS, MDNS_PORT))
                except OSError as exc:
                    self.last_error = f"{interface_ip}: {exc}"
                    logger.warning("mDNS 公告在 %s 发送失败: %s", interface_ip, exc)
                else:
                    sent += 1
        if sent:
            self.announced_packets += sent
            logger.info(
                "mDNS 公告已发送 %s 个包（%s），网卡: %s",
                sent,
                reason,
                ", ".join(self.interfaces) or "-",
            )
        return sent

    # ------------------------------------------------------------------ packets
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

    def _build_instance_packet(self, service_type: str, port: int) -> bytes:
        """SRV/TXT/A answers for a query that asked about our instance name."""

        instance = f"{self.instance_name}.{service_type}"
        answers = [
            _dns_record(instance, 33, 0x8001, 120, _srv_record(port, self.target_name)),
            _dns_record(instance, 16, 0x8001, 120, _txt_record("txtvers=1")),
            _dns_record(self.target_name, 1, 0x8001, 120, _a_record(LOOPBACK_HOST)),
        ]
        return struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, 0) + b"".join(answers)

    def _build_address_packet(self) -> bytes:
        answers = [
            _dns_record(self.target_name, 1, 0x8001, 120, _a_record(LOOPBACK_HOST)),
        ]
        return struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, 0) + b"".join(answers)


# Backwards compatible alias: older call sites and tests refer to this name.
MdnsBroadcaster = MdnsResponder


class DynamicVRChatOSCClient:
    """Small send_message-compatible wrapper whose destination can be updated."""

    def __init__(self, host: str = LOOPBACK_HOST, port: int = VRC_DEFAULT_OSC_PORT):
        self._host = ""
        self._port = 0
        self._client = None
        self.set_endpoint(host, port)

    @property
    def endpoint(self) -> tuple[str, int]:
        return self._host, self._port

    def set_endpoint(self, host: str, port: int) -> bool:
        host = _normalise_local_host(host)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("OSC port must be an integer between 1 and 65535")
        if self._client is not None and self._host == host and self._port == port:
            return False

        client = udp_client.SimpleUDPClient(host, port)
        self.close()
        self._host = host
        self._port = port
        self._client = client
        return True

    def send_message(self, address: str, value: Any):
        if self._client is not None:
            self._client.send_message(address, value)

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None


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
        self._mdns_broadcaster = MdnsResponder(self.instance_name)
        self._mdns_started = False
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
            self._vrc_client.set_endpoint(LOOPBACK_HOST, VRC_DEFAULT_OSC_PORT)
            await self._start_osc_server(dispatcher)
            await self._start_http_server()
            # The responder is what VRChat resolves us through, so it must not
            # be able to fail the whole service: a broken interface is logged
            # and the run continues with the remaining advertisement paths.
            await self._start_mdns_responder()
            try:
                await self._register_mdns()
            except Exception as exc:
                logger.warning("python-zeroconf 注册失败（mDNS 应答器仍可工作）: %s", exc)

            self._running = True
            self._rebroadcast_task = asyncio.create_task(self._rebroadcast_loop())
            self._discovery_task = asyncio.create_task(self._vrchat_discovery_loop())
            await self._broadcast_mdns("startup")

            logger.info(
                "OSCQuery service '%s' started: HTTP %s:%s, OSC %s:%s (mDNS 网卡: %s)",
                self.instance_name,
                LOOPBACK_HOST,
                self._http_port,
                LOOPBACK_HOST,
                self._osc_port,
                ", ".join(self._mdns_broadcaster.interfaces) or "-",
            )
            return int(self._osc_port)
        except (Exception, asyncio.CancelledError):
            await self.stop()
            raise

    async def _start_mdns_responder(self):
        interfaces = await asyncio.to_thread(self._mdns_broadcaster.start)
        self._mdns_started = bool(interfaces)
        if interfaces:
            logger.info(
                "mDNS 应答器已启动（5353 常驻，应答 _oscjson._tcp / _osc._udp 查询），网卡: %s",
                ", ".join(interfaces),
            )
        else:
            logger.warning("mDNS 应答器未启动：没有可用网卡，自动发现可能无法工作")

    async def _stop_mdns_responder(self):
        if self._mdns_started:
            await asyncio.to_thread(self._mdns_broadcaster.stop)
        self._mdns_started = False

    async def stop(self):
        self._running = False
        self._discovery_had_success = False

        for task in (self._rebroadcast_task, self._discovery_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._rebroadcast_task = None
        self._discovery_task = None

        with contextlib.suppress(Exception):
            await self._stop_mdns_responder()

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
        self._vrc_client.close()

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

    @property
    def vrc_discovered(self) -> bool:
        return self._discovery_had_success

    @property
    def host_info_requests(self) -> int:
        """How often VRChat asked us for HOST_INFO (proof it registered us)."""

        return self._host_info_request_count

    @property
    def registration_confirmed(self) -> bool:
        """True once VRChat resolved our OSCQuery service and queried us."""

        return self._host_info_request_count > 0

    @property
    def mdns_interfaces(self) -> list[str]:
        return list(self._mdns_broadcaster.interfaces)

    @property
    def mdns_answered_queries(self) -> int:
        return self._mdns_broadcaster.answered_queries

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
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle_request)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        # Bind port zero once: probing and then rebinding an unused port races
        # other OSC applications starting at the same time.
        site = web.TCPSite(self._http_runner, LOOPBACK_HOST, 0)
        await site.start()
        self._http_port = int(self._http_runner.addresses[0][1])

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
        logger.info(
            "python-zeroconf 注册完成: %s",
            ", ".join(
                f"{info.type}->{info.port}" for info in self._service_infos
            ),
        )

    async def _handle_request(self, request: web.Request) -> web.Response:
        peername = request.transport.get_extra_info("peername") if request.transport else None
        # VRChat (and other clients) may append further query parameters, and
        # the vrc-oscquery-lib matcher only looks for the token itself.
        query_parts = {part.strip().upper() for part in request.query_string.split("&")}
        if "HOST_INFO" in query_parts:
            self._host_info_request_count += 1
            logger.info(
                "OSCQuery HOST_INFO 请求（第 %s 次，来自 %s）：VRChat 已登记本程序",
                self._host_info_request_count,
                peername or "unknown",
            )
            return web.json_response(self._host_info())

        node = self._node_for_path(request.path)
        self._node_request_count += 1
        if node is None:
            # vrc-oscquery-lib treats a non-2xx or empty body as a parse
            # failure and then dereferences null ("Could not make new
            # OscClient"), so unknown paths answer with an empty container
            # node instead of an empty 404.
            logger.info(
                "OSCQuery 未知路径 %s（第 %s 次，来自 %s）：返回空容器节点",
                request.path,
                self._node_request_count,
                peername or "unknown",
            )
            return web.json_response({"FULL_PATH": request.path or "/", "ACCESS": 0})
        logger.info(
            "OSCQuery 节点请求: %s 来自 %s（第 %s 次）",
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
            },
        }

    def _root_node(self) -> dict[str, Any]:
        # VRChat subscribes by subtree path. ACCESS=0 describes a container with
        # no value of its own; it does not disable reception of its children.
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
                logger.warning("python-zeroconf 服务刷新失败: %s", exc)

    async def _broadcast_mdns(self, reason: str):
        if self._http_port is None or self._osc_port is None:
            return
        try:
            sent = await asyncio.to_thread(
                self._mdns_broadcaster.announce,
                self._http_port,
                self._osc_port,
                reason,
            )
            if sent == 0 and not self._mdns_started:
                logger.warning("mDNS 公告未能发送（%s）：应答器未运行", reason)
        except Exception as exc:
            logger.warning("mDNS 公告发送异常（%s）: %s", reason, exc)

    async def _vrchat_discovery_loop(self):
        while self._running:
            await self._discover_vrchat_once()
            await asyncio.sleep(self.discovery_interval)

    async def _discover_vrchat_once(self):
        try:
            host, port, host_info = await asyncio.to_thread(discover_vrchat_oscquery, 1.0)
            osc_host, osc_port = vrchat_osc_endpoint(host, port, host_info)
            changed = self._vrc_client.set_endpoint(osc_host, osc_port)
            if changed or not self._discovery_had_success:
                logger.info(
                    "已发现 VRChat: OSCQuery %s:%s（%s），发送目标 %s:%s",
                    host,
                    port,
                    host_info.get("NAME", "?"),
                    osc_host,
                    osc_port,
                )
                await self._broadcast_mdns("vrchat discovered")
            self._discovery_had_success = True
        except Exception as exc:
            if self._discovery_had_success:
                logger.warning("VRChat OSCQuery 发现丢失: %s", exc)
            else:
                logger.debug("VRChat OSCQuery 暂不可用: %s", exc)
            self._discovery_had_success = False
