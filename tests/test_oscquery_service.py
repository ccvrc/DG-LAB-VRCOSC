"""Local OSCQuery/UDP interoperability checks; no VRChat or device is used."""
import asyncio
import socket
import struct
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from services import vrchat_oscquery_inspector as inspector
from services.oscquery_service import (
    DynamicVRChatOSCClient, MdnsResponder, OSCQueryService, _parse_query_names,
)
from services.vrchat_oscquery_inspector import vrchat_osc_endpoint


class EndpointTests(unittest.TestCase):
    def test_optional_endpoint_fields_default_to_http_peer(self):
        self.assertEqual(vrchat_osc_endpoint('::1', 45678, {}), ('::1', 45678))
        self.assertEqual(vrchat_osc_endpoint('127.0.0.1', 45678, {'OSC_IP': '0.0.0.0'}),
                         ('127.0.0.1', 45678))

    def test_invalid_endpoint_cannot_replace_a_working_client(self):
        with patch('services.oscquery_service.udp_client.SimpleUDPClient') as factory:
            client = DynamicVRChatOSCClient()
            first = factory.return_value
            for port in (0, -1, 65536, True, 9000.5, '9000'):
                with self.assertRaises(ValueError):
                    client.set_endpoint('127.0.0.1', port)
            factory.side_effect = OSError('cannot create socket')
            with self.assertRaises(OSError):
                client.set_endpoint('127.0.0.1', 12345)
            self.assertEqual(client.endpoint, ('127.0.0.1', 9000))
            first.close.assert_not_called()
            client.send_message('/test', 1)
            first.send_message.assert_called_once_with('/test', 1)
            client.close()
            client.close()
            first.close.assert_called_once()

    def test_unsupported_transport_and_remote_host_are_rejected(self):
        for info in ({'OSC_TRANSPORT': 'TCP'}, {'OSC_IP': '192.0.2.123'},
                     {'OSC_PORT': None}, {'OSC_PORT': 0}, {'OSC_PORT': True}):
            with self.subTest(info=info), self.assertRaises(ValueError):
                vrchat_osc_endpoint('127.0.0.1', 45678, info)


class OSCQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        inspector._last_successful_candidate = None
        self.candidates = []
        self.patches = [
            patch.object(inspector, '_ports_from_mdns', side_effect=lambda *args, **kwargs: list(self.candidates)),
            patch.object(inspector, '_ports_from_logs', return_value=[]),
        ]
        for item in self.patches:
            item.start()
        self.service = OSCQueryService(discovery_interval=0.02)
        # Never advertise a test receiver to a running VRChat instance or the LAN.
        self.service._register_mdns = AsyncMock()
        self.service._broadcast_mdns = AsyncMock()
        self.service._update_registered_services = AsyncMock()
        self.service._start_mdns_responder = AsyncMock()
        self.service._stop_mdns_responder = AsyncMock()
        self.runners = []
        self.transports = []

    async def asyncTearDown(self):
        await self.service.stop()
        # Cancelling to_thread does not stop the worker. Keep discovery stubs
        # installed until all workers finish, so no test can browse real mDNS.
        await asyncio.get_running_loop().shutdown_default_executor()
        for runner in self.runners:
            await runner.cleanup()
        for transport in self.transports:
            transport.close()
        for item in reversed(self.patches):
            item.stop()
        inspector._last_successful_candidate = None

    async def wait_until(self, condition):
        async with asyncio.timeout(3):
            while not condition():
                await asyncio.sleep(0.01)

    async def start_fake_vrchat(self):
        messages = []
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(lambda address, *args: messages.append((address, args)))
        transport, _ = await AsyncIOOSCUDPServer(
            ('127.0.0.1', 0), dispatcher, asyncio.get_running_loop()
        ).create_serve_endpoint()
        self.transports.append(transport)
        osc_port = transport.get_extra_info('socket').getsockname()[1]
        info = {'NAME': 'VRChat-Client-test', 'OSC_IP': '127.0.0.1',
                'OSC_PORT': osc_port, 'OSC_TRANSPORT': 'UDP'}
        app = web.Application()

        async def host_info(request):
            return web.json_response(info)

        app.router.add_get('/', host_info)
        runner = web.AppRunner(app)
        self.runners.append(runner)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        self.candidates[:] = [('127.0.0.1', runner.addresses[0][1])]
        return runner, osc_port, messages

    async def test_http_advertises_bound_udp_port_and_avatar_subtree(self):
        received = []
        dispatcher = Dispatcher()
        dispatcher.map('/avatar/parameters/Test', lambda *args: received.append(args))
        port = await self.service.start(dispatcher)
        async with ClientSession() as session:
            base = f'http://127.0.0.1:{self.service.http_port}'
            async with session.get(base + '/?HOST_INFO') as response:
                info = await response.json()
            self.assertEqual(info['OSC_PORT'], port)
            self.assertEqual(info['OSC_TRANSPORT'], 'UDP')
            self.assertTrue(self.service.registration_confirmed)
            # Clients may append further query parameters; vrc-oscquery-lib
            # only looks for the token itself.
            async with session.get(base + '/?HOST_INFO&extra=1') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['OSC_PORT'], port)
            self.assertEqual(self.service.host_info_requests, 2)
            async with session.get(base + '/') as response:
                root = await response.json()
            self.assertIn('avatar', root['CONTENTS'])
            async with session.get(base + '/avatar/change') as response:
                self.assertEqual((await response.json())['TYPE'], 's')
            # Unknown paths must not answer with an empty body: VRChat's
            # OSCQuery client treats that as a parse failure and dereferences
            # null ("Could not make new OscClient").
            async with session.get(base + '/missing') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['FULL_PATH'], '/missing')
        with SimpleUDPClient(info['OSC_IP'], info['OSC_PORT']) as sender:
            sender.send_message('/avatar/parameters/Test', 0.5)
            await self.wait_until(lambda: bool(received))
        self.assertEqual(received, [('/avatar/parameters/Test', 0.5)])

    async def test_late_vrchat_start_and_restart_updates_send_destination(self):
        await self.service.start(Dispatcher())
        self.assertFalse(self.service.vrc_discovered)
        runner, first_port, first_messages = await self.start_fake_vrchat()
        await self.wait_until(lambda: self.service.vrc_discovered)
        sender = self.service.get_vrc_client()
        self.assertEqual(sender.endpoint, ('127.0.0.1', first_port))
        sender.send_message('/chatbox/input', ['first', True])
        await self.wait_until(lambda: bool(first_messages))
        await runner.cleanup()
        self.candidates.clear()
        await self.wait_until(lambda: not self.service.vrc_discovered)
        _, second_port, second_messages = await self.start_fake_vrchat()
        await self.wait_until(lambda: sender.endpoint[1] == second_port)
        sender.send_message('/chatbox/input', ['second', True])
        await self.wait_until(lambda: bool(second_messages))
        self.assertEqual(first_messages, [('/chatbox/input', ('first', True))])
        self.assertEqual(second_messages, [('/chatbox/input', ('second', True))])

    async def test_stop_releases_ports_and_can_restart(self):
        osc_port = await self.service.start(Dispatcher())
        http_port = self.service.http_port
        await self.service.stop()
        await self.service.stop()
        await asyncio.sleep(0)
        self.assertFalse(self.service.is_running)
        self.assertIsNone(self.service.osc_port)
        for kind, port in ((socket.SOCK_DGRAM, osc_port), (socket.SOCK_STREAM, http_port)):
            with socket.socket(socket.AF_INET, kind) as sock:
                sock.bind(('127.0.0.1', port))
        await self.service.start(Dispatcher())
        self.assertTrue(self.service.is_running)

    async def test_cancelled_start_releases_partially_started_servers(self):
        registering = asyncio.Event()

        async def register_forever():
            registering.set()
            await asyncio.Future()

        self.service._register_mdns = register_forever
        task = asyncio.create_task(self.service.start(Dispatcher()))
        await registering.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.service._http_runner)
        self.assertIsNone(self.service._osc_transport)
        self.assertFalse(self.service.is_running)

    async def test_registration_uses_actual_http_and_udp_ports(self):
        await self.service.start(Dispatcher())
        with patch('services.oscquery_service.AsyncZeroconf') as factory:
            factory.return_value.async_register_service = AsyncMock()
            factory.return_value.async_unregister_service = AsyncMock()
            factory.return_value.async_close = AsyncMock()
            await OSCQueryService._register_mdns(self.service)
            infos = self.service._service_infos
            self.assertEqual({info.type: info.port for info in infos}, {
                '_oscjson._tcp.local.': self.service.http_port,
                '_osc._udp.local.': self.service.osc_port,
            })
            self.assertTrue(all(info.parsed_addresses() == ['127.0.0.1'] for info in infos))
            await self.service.stop()
            self.assertEqual(factory.return_value.async_unregister_service.await_count, 2)


def _dns_query(*names: str) -> bytes:
    """Build an mDNS query packet for the given names (QTYPE=ANY, QCLASS=IN)."""

    body = b''
    for name in names:
        encoded = b''.join(bytes([len(part)]) + part.encode() for part in name.split('.'))
        body += encoded + b'\x00' + struct.pack('!HH', 255, 1)
    return struct.pack('!HHHHHH', 0, 0, len(names), 0, 0, 0) + body


class MdnsResponderTests(unittest.TestCase):
    """The responder must resolve VRChat's queries, not only announce."""

    instance = 'DG-LAB-VRCOSC-abcdef'

    def setUp(self):
        self.responder = MdnsResponder(self.instance)
        self.responder.announce(50000, 51000, 'test')

    def tearDown(self):
        self.responder.stop()

    def test_parse_query_names(self):
        self.assertEqual(
            _parse_query_names(_dns_query('_oscjson._tcp.local')), ['_oscjson._tcp.local']
        )
        self.assertEqual(_parse_query_names(b''), [])
        # Responses are not queries.
        response = bytearray(_dns_query('_oscjson._tcp.local'))
        response[2] |= 0x80
        self.assertEqual(_parse_query_names(bytes(response)), [])

    def test_service_type_query_returns_ptr_and_srv_target(self):
        for service_type, port in (
            ('_oscjson._tcp.local', 50000),
            ('_osc._udp.local', 51000),
        ):
            with self.subTest(service_type=service_type):
                packets = self.responder._responses_for_query(_dns_query(service_type))
                self.assertEqual(len(packets), 1)
                packet = packets[0]
                self.assertEqual(struct.unpack('!H', packet[6:8])[0], 1)  # ANCOUNT
                self.assertIn(self.instance.encode(), packet)
                self.assertIn(struct.pack('!H', port), packet)
                self.assertIn(b'\x7f\x00\x00\x01', packet)  # A record: 127.0.0.1

    def test_instance_name_query_returns_srv_txt_and_a(self):
        packets = self.responder._responses_for_query(
            _dns_query(f'{self.instance}._oscjson._tcp.local')
        )
        self.assertEqual(len(packets), 1)
        self.assertEqual(struct.unpack('!H', packets[0][6:8])[0], 3)  # SRV + TXT + A

    def test_host_name_query_returns_address(self):
        packets = self.responder._responses_for_query(
            _dns_query(f'{self.instance}.local')
        )
        self.assertEqual(len(packets), 1)
        self.assertEqual(struct.unpack('!H', packets[0][6:8])[0], 1)
        self.assertIn(b'\x7f\x00\x00\x01', packets[0])

    def test_unrelated_queries_are_ignored(self):
        self.assertEqual(self.responder._responses_for_query(_dns_query('_http._tcp.local')), [])
        self.assertEqual(self.responder._responses_for_query(b'\x00'), [])

    def test_announce_requires_a_listening_interface(self):
        logger_name = 'services.oscquery_service'
        with self.assertLogs(logger_name, level='WARNING') as captured:
            self.assertEqual(self.responder.announce(50000, 51000, 'test'), 0)
        self.assertTrue(any('mDNS 公告未能发送' in line for line in captured.output))

