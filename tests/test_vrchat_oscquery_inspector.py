import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from services import vrchat_oscquery_inspector as inspector


HOST_INFO = {"NAME": "VRChat-Client-test", "OSC_PORT": 9123, "OSC_TRANSPORT": "UDP"}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous_candidate = inspector._last_successful_candidate
        inspector._last_successful_candidate = None
        self.addCleanup(setattr, inspector, "_last_successful_candidate", self.previous_candidate)
        self.mdns = self.enterContext(patch.object(inspector, "_ports_from_mdns", return_value=[]))
        self.logs = self.enterContext(patch.object(inspector, "_ports_from_logs", return_value=[]))
        self.http = self.enterContext(patch.object(inspector, "_http_json", return_value=HOST_INFO))
        self.addresses = self.enterContext(
            patch.object(inspector, "_local_addresses", return_value=["127.0.0.1", "::1", "192.168.1.10"])
        )

    def test_healthy_cache_returns_before_discovery_or_log_io(self):
        inspector._last_successful_candidate = ("127.0.0.1", 9120)

        self.assertEqual(inspector.discover_vrchat_oscquery(0.25), ("127.0.0.1", 9120, HOST_INFO))

        self.http.assert_called_once_with("127.0.0.1", 9120, "/?HOST_INFO", 0.25)
        self.mdns.assert_not_called()
        self.logs.assert_not_called()
        self.addresses.assert_not_called()

    def test_failed_cache_is_replaced_by_current_mdns_service(self):
        inspector._last_successful_candidate = ("127.0.0.1", 9120)
        self.http.side_effect = [OSError("closed port"), HOST_INFO]
        self.mdns.return_value = [("127.0.0.1", 9120), ("127.0.0.1", 9130)]

        self.assertEqual(inspector.discover_vrchat_oscquery(), ("127.0.0.1", 9130, HOST_INFO))

        self.assertEqual(inspector._last_successful_candidate, ("127.0.0.1", 9130))
        self.assertEqual(self.http.call_count, 2)
        self.logs.assert_not_called()

    def test_cache_is_cleared_when_port_now_belongs_to_another_service(self):
        inspector._last_successful_candidate = ("127.0.0.1", 9120)
        self.http.return_value = {"NAME": "Another OSC application"}

        with self.assertRaises(RuntimeError):
            inspector.discover_vrchat_oscquery()

        self.assertIsNone(inspector._last_successful_candidate)

    def test_invalid_osc_endpoint_does_not_trap_discovery_in_cached_candidate(self):
        inspector._last_successful_candidate = ("127.0.0.1", 9120)
        self.http.side_effect = [{**HOST_INFO, "OSC_PORT": 0}, HOST_INFO]
        self.mdns.return_value = [("127.0.0.1", 9130)]

        self.assertEqual(inspector.discover_vrchat_oscquery(), ("127.0.0.1", 9130, HOST_INFO))
        self.assertEqual(inspector._last_successful_candidate, ("127.0.0.1", 9130))

    def test_mdns_is_tried_before_reading_logs(self):
        self.mdns.return_value = [("127.0.0.1", 9130)]

        self.assertEqual(inspector.discover_vrchat_oscquery(0.25), ("127.0.0.1", 9130, HOST_INFO))

        self.mdns.assert_called_once_with(wait_seconds=0.25)
        self.http.assert_called_once_with("127.0.0.1", 9130, "/?HOST_INFO", 0.25)
        self.logs.assert_not_called()

    def test_logs_are_used_when_mdns_has_no_reachable_vrchat(self):
        self.mdns.return_value = [("127.0.0.1", 9130)]
        self.http.side_effect = [OSError("closed port"), HOST_INFO]
        self.logs.return_value = [9140]

        self.assertEqual(inspector.discover_vrchat_oscquery(), ("127.0.0.1", 9140, HOST_INFO))

        self.assertEqual(inspector._last_successful_candidate, ("127.0.0.1", 9140))
        self.http.assert_has_calls(
            [call("127.0.0.1", 9130, "/?HOST_INFO", 2.0), call("127.0.0.1", 9140, "/?HOST_INFO", 2.0)]
        )

    def test_remote_or_invalid_candidates_are_never_contacted(self):
        inspector._last_successful_candidate = ("192.168.1.99", 9120)
        self.mdns.return_value = [
            ("192.168.1.99", 9130),
            ("0.0.0.0", 9130),
            ("127.0.0.1", 0),
            ("127.0.0.1", 65536),
            ("127.0.0.1", True),
            ("127.0.0.1", 9130.5),
            ("192.168.1.10", 9140),
        ]

        self.assertEqual(inspector.discover_vrchat_oscquery(), ("192.168.1.10", 9140, HOST_INFO))

        self.http.assert_called_once_with("192.168.1.10", 9140, "/?HOST_INFO", 2.0)


class LogTests(unittest.TestCase):
    def test_ports_are_latest_first_deduplicated_and_in_range(self):
        with tempfile.TemporaryDirectory() as directory:
            latest = Path(directory) / "latest.txt"
            older = Path(directory) / "older.txt"
            latest.write_text(
                "Started service of type OSCQuery on 9100\n"
                "Started service of type OSCQuery on 9101\n"
                "Started service of type OSCQuery on 0\n"
                "Started service of type OSCQuery on 65536\n"
                "Started service of type OSCQuery on 655350\n"
                "Started service of type OSCQuery on -1\n"
                "Started service of type OSCQuery on 9100\n",
                encoding="utf-8",
            )
            older.write_text("Started service of type OSCQuery on 9000\n", encoding="utf-8")
            with patch.object(inspector, "_recent_vrc_logs", return_value=[latest, older]):
                self.assertEqual(inspector._ports_from_logs(), [9100, 9101, 9000])


class HttpTests(unittest.TestCase):
    def test_local_http_bypasses_system_proxy_formats_ipv6_and_preserves_timeout(self):
        for address, expected_host in [("::1", "[::1]"), ("fe80::1%12", "[fe80::1%2512]")]:
            with self.subTest(address=address), patch.object(inspector.urllib.request, "build_opener") as build:
                response = build.return_value.open.return_value.__enter__.return_value
                response.read.return_value = b'{"NAME": "VRChat-Client-test"}'

                self.assertEqual(inspector._http_json(address, 9120, "/?HOST_INFO", 0.3), {"NAME": "VRChat-Client-test"})

                handler = build.call_args.args[0]
                self.assertIsInstance(handler, inspector.urllib.request.ProxyHandler)
                self.assertEqual(handler.proxies, {})
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, f"http://{expected_host}:9120/?HOST_INFO")
                self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 0.3})


class MdnsTests(unittest.TestCase):
    def test_services_require_vrchat_name_local_address_and_valid_port(self):
        services = {
            "VRChat-Client-remote._oscjson._tcp.local.": (9130, ["192.168.1.99"]),
            "VRChat-Client-empty._oscjson._tcp.local.": (9130, []),
            "Other-Client._oscjson._tcp.local.": (9130, ["127.0.0.1"]),
            "VRChat-Client-invalid._oscjson._tcp.local.": (65536, ["127.0.0.1"]),
            "VRChat-Client-local._oscjson._tcp.local.": (9140, ["192.168.1.10", "::1", "fe80::1%12"]),
        }
        zc = MagicMock()

        def service_info(service_type, name, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 1000)
            port, addresses = services[name]
            return SimpleNamespace(port=port, parsed_scoped_addresses=lambda: addresses)

        zc.get_service_info.side_effect = service_info
        browser = MagicMock()

        def create_browser(zc_instance, service_type, listener):
            for name in services:
                listener.add_service(zc_instance, service_type, name)
            return browser

        fake_module = SimpleNamespace(
            Zeroconf=MagicMock(return_value=zc), ServiceListener=object, ServiceBrowser=create_browser
        )
        with (
            patch.dict(sys.modules, {"zeroconf": fake_module}),
            patch.object(inspector.time, "sleep"),
            patch.object(inspector, "_local_addresses", return_value=["192.168.1.10", "::1", "fe80::1%12"]),
        ):
            self.assertEqual(
                inspector._ports_from_mdns(), [("192.168.1.10", 9140), ("::1", 9140), ("fe80::1%12", 9140)]
            )

        browser.cancel.assert_called_once_with()
        zc.close.assert_called_once_with()

    def test_mdns_startup_failure_allows_log_fallback(self):
        fake_module = SimpleNamespace(
            Zeroconf=MagicMock(side_effect=OSError("multicast unavailable")),
            ServiceListener=object,
            ServiceBrowser=MagicMock(),
        )
        with patch.dict(sys.modules, {"zeroconf": fake_module}), patch.object(inspector, "_local_addresses", return_value=[]):
            self.assertEqual(inspector._ports_from_mdns(), [])

    def test_mdns_cleanup_failure_does_not_interrupt_discovery(self):
        browser = MagicMock()
        browser.cancel.side_effect = OSError("browser already closed")
        zc = MagicMock()
        zc.close.side_effect = OSError("socket already closed")
        fake_module = SimpleNamespace(
            Zeroconf=MagicMock(return_value=zc), ServiceListener=object, ServiceBrowser=MagicMock(return_value=browser)
        )
        with (
            patch.dict(sys.modules, {"zeroconf": fake_module}),
            patch.object(inspector.time, "sleep"),
            patch.object(inspector, "_local_addresses", return_value=[]),
        ):
            self.assertEqual(inspector._ports_from_mdns(), [])
        zc.close.assert_called_once_with()

    def test_loopback_ipv4_mapped_and_interface_addresses_are_local(self):
        local_addresses = ["192.168.1.10", "fe80::1%12"]
        for address in ["127.0.0.2", "::1", "::ffff:127.0.0.1", "192.168.1.10", "fe80::1%12"]:
            with self.subTest(address=address):
                self.assertTrue(inspector._is_local_address(address, local_addresses))
        for address in ["192.168.1.99", "0.0.0.0", "::", "remote.example"]:
            with self.subTest(address=address):
                self.assertFalse(inspector._is_local_address(address, local_addresses))


if __name__ == "__main__":
    unittest.main()
