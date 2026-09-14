"""Test connection setup and UI state without contacting VRChat or a device."""
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS, load_settings
from gui.network_config_tab import NetworkConfigTab
from i18n import translate


class NetworkConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ip_patch = patch('gui.network_config_tab.get_active_ip_addresses',
                              return_value={'Test': '127.0.0.1'})
        self.save_patch = patch('gui.network_config_tab.save_settings')
        self.ip_patch.start()
        self.save_patch.start()
        self.addCleanup(self.ip_patch.stop)
        self.addCleanup(self.save_patch.stop)

    def create_tab(self, settings=None):
        window = SimpleNamespace(
            settings=dict(DEFAULT_SETTINGS if settings is None else settings),
            controller=None,
            app_status_online=False,
            get_osc_addresses=lambda: [],
            log_viewer_tab=SimpleNamespace(log_text_edit=Mock()),
            controller_settings_tab=Mock(),
            ton_damage_system_tab=Mock(),
            sps_config_tab=Mock(),
            osc_parameters_tab=SimpleNamespace(addresses_updated=Mock()),
        )
        tab = NetworkConfigTab(window)
        self.addCleanup(tab.close)
        self.addCleanup(tab._osc_status_timer.stop)
        return tab

    def test_old_settings_default_to_auto_and_manual_choice_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.yml'
            path.write_text('osc_port: 9012\n', encoding='utf-8')
            with patch('config.get_config_file_path', return_value=str(path)):
                settings = load_settings()
        self.assertTrue(settings['osc_auto'])
        tab = self.create_tab(settings)
        self.assertTrue(tab.osc_auto_checkbox.isChecked())
        self.assertTrue(tab.osc_port_spinbox.isHidden())
        self.assertEqual(tab.osc_port_spinbox.value(), 9012)

        tab.osc_auto_checkbox.setChecked(False)
        self.assertFalse(tab.osc_port_spinbox.isHidden())
        self.assertFalse(tab.main_window.settings['osc_auto'])
        self.assertEqual(tab.main_window.settings['osc_port'], 9012)
        reloaded = self.create_tab(tab.main_window.settings)
        self.assertFalse(reloaded.osc_auto_checkbox.isChecked())
        self.assertFalse(reloaded.osc_port_spinbox.isHidden())

    def test_remote_input_and_language_changes_cannot_restart_running_server(self):
        tab = self.create_tab()

        async def scenario():
            blocker = asyncio.Event()

            async def run_server(*args):
                await blocker.wait()

            with patch.object(tab, 'run_server', new=AsyncMock(side_effect=run_server)) as run:
                tab.start_server()
                task = tab._server_task
                await asyncio.sleep(0)
                tab.enable_remote_checkbox.setChecked(True)
                tab.remote_address_edit.setText('192.0.2.5')
                tab.update_ui_texts()
                tab.start_server_button_clicked()
                self.assertIs(tab._server_task, task)
                self.assertEqual(run.await_count, 1)
                self.assertFalse(tab.start_button.isEnabled())
                self.assertFalse(tab.osc_auto_checkbox.isEnabled())
                self.assertFalse(tab.remote_address_edit.isEnabled())
                run.assert_awaited_once_with('127.0.0.1', 5678, None)
                blocker.set()
                await task
                await asyncio.sleep(0)
                self.assertIsNone(tab._server_task)
                self.assertTrue(tab.start_button.isEnabled())
                self.assertTrue(tab.osc_auto_checkbox.isEnabled())
                self.assertEqual(tab.start_button.text(), translate('network_tab.connect'))

        asyncio.run(scenario())

    def test_missing_event_loop_reports_error_and_allows_retry(self):
        tab = self.create_tab()
        tab.start_server()
        self.assertIsNone(tab._server_task)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertEqual(tab.start_button.text(), translate('network_tab.start_failed'))
        self.assertIn('no running event loop', tab.osc_status_label.text())

    def test_empty_remote_address_prevents_start(self):
        tab = self.create_tab()
        tab.enable_remote_checkbox.setChecked(True)
        self.assertFalse(tab.start_button.isEnabled())
        tab.start_server()
        self.assertIsNone(tab._server_task)
        tab.enable_remote_checkbox.setChecked(False)
        self.assertTrue(tab.start_button.isEnabled())

    def test_status_reflects_discovery_loss_and_replacement_endpoint(self):
        tab = self.create_tab()
        service = SimpleNamespace(
            is_running=True, http_port=14000, osc_port=15000, vrc_discovered=False,
            get_vrc_client=lambda: SimpleNamespace(endpoint=('127.0.0.1', 16000)),
        )
        tab.oscquery_service = service
        tab.refresh_osc_status()
        self.assertIn('127.0.0.1:14000', tab.osc_status_label.text())
        self.assertIn('127.0.0.1:15000', tab.osc_status_label.text())
        self.assertIn(translate('network_tab.osc_waiting'), tab.osc_status_label.text())
        self.assertNotIn('16000', tab.osc_status_label.text())

        service.vrc_discovered = True
        tab.refresh_osc_status()
        self.assertIn('127.0.0.1:16000', tab.osc_status_label.text())
        service.get_vrc_client = lambda: SimpleNamespace(endpoint=('127.0.0.1', 16001))
        tab.refresh_osc_status()
        self.assertIn('127.0.0.1:16001', tab.osc_status_label.text())
        self.assertNotIn('16000', tab.osc_status_label.text())
        service.vrc_discovered = False
        tab.refresh_osc_status()
        self.assertIn(translate('network_tab.osc_waiting'), tab.osc_status_label.text())
        self.assertNotIn('16001', tab.osc_status_label.text())

    def fake_server(self, blocker):
        client = Mock()

        async def data_generator():
            await blocker.wait()
            if False:
                yield

        client.data_generator = data_generator
        server = Mock()
        server.new_local_client.return_value = client
        server.__aenter__ = AsyncMock(return_value=server)
        server.__aexit__ = AsyncMock(return_value=False)
        return server

    def test_auto_start_failure_never_falls_back_and_restores_controls(self):
        tab = self.create_tab()

        async def scenario():
            service = Mock()
            service.start = AsyncMock(side_effect=RuntimeError('OSCQuery unavailable'))
            service.stop = AsyncMock()
            service.is_running = False
            server = self.fake_server(asyncio.Event())
            with patch('gui.network_config_tab.DGLabWSServer', return_value=server), \
                 patch('services.oscquery_service.OSCQueryService', return_value=service), \
                 patch('gui.network_config_tab.osc_server.AsyncIOOSCUDPServer') as udp_server, \
                 patch('gui.network_config_tab.DGLabController') as controller, \
                 patch.object(tab, 'generate_qrcode'), patch.object(tab, 'update_qrcode'):
                tab.start_server()
                await tab._server_task
                await asyncio.sleep(0)
                udp_server.assert_not_called()
                controller.assert_not_called()
                service.stop.assert_awaited_once()
                server.__aexit__.assert_awaited_once()
                self.assertIsNone(tab.oscquery_service)
                self.assertIsNone(tab._server_task)
                self.assertTrue(tab.start_button.isEnabled())
                self.assertTrue(tab.osc_auto_checkbox.isEnabled())
                self.assertEqual(tab.start_button.text(), translate('network_tab.start_failed'))
                self.assertIn('OSCQuery unavailable', tab.osc_status_label.text())

        asyncio.run(scenario())

    def test_manual_mode_binds_configured_port_without_oscquery(self):
        tab = self.create_tab({**DEFAULT_SETTINGS, 'osc_auto': False, 'osc_port': 9013})

        async def scenario():
            blocker = asyncio.Event()
            server = self.fake_server(blocker)
            transport = Mock()
            transport.get_extra_info.return_value = ('127.0.0.1', 9013)
            udp_server_instance = Mock()
            udp_server_instance.create_serve_endpoint = AsyncMock(return_value=(transport, Mock()))
            osc_client = Mock()
            with patch('gui.network_config_tab.DGLabWSServer', return_value=server), \
                 patch('services.oscquery_service.OSCQueryService') as query, \
                 patch('gui.network_config_tab.osc_server.AsyncIOOSCUDPServer',
                       return_value=udp_server_instance) as udp_server, \
                 patch('gui.network_config_tab.udp_client.SimpleUDPClient', return_value=osc_client), \
                 patch('gui.network_config_tab.DGLabController') as controller, \
                 patch.object(tab, 'generate_qrcode'), patch.object(tab, 'update_qrcode'):
                controller.return_value.close = AsyncMock()
                tab.start_server()
                task = tab._server_task
                await asyncio.sleep(0)
                query.assert_not_called()
                self.assertEqual(udp_server.call_args.args[0], ('127.0.0.1', 9013))
                self.assertIs(controller.call_args.args[1], osc_client)
                self.assertTrue(tab._server_started)
                self.assertIn('127.0.0.1:9013', tab.osc_status_label.text())
                self.assertIn('127.0.0.1:9000', tab.osc_status_label.text())
                blocker.set()
                await task
                await asyncio.sleep(0)
                transport.close.assert_called_once()
                osc_client.close.assert_called_once()
                controller.return_value.close.assert_awaited_once()
                self.assertIsNone(tab.main_window.controller)
                self.assertFalse(tab.main_window.app_status_online)
                self.assertIsNone(tab._osc_transport)
                self.assertTrue(tab.start_button.isEnabled())

        asyncio.run(scenario())

    def test_auto_mode_uses_dynamic_client_and_cancellation_cleans_up(self):
        tab = self.create_tab()

        async def scenario():
            service = Mock()
            service.start = AsyncMock(return_value=15002)
            service.stop = AsyncMock()
            service.is_running = True
            service.http_port = 14002
            service.osc_port = 15002
            service.vrc_discovered = True
            service.get_vrc_client.return_value.endpoint = ('127.0.0.1', 16002)
            server = self.fake_server(asyncio.Event())
            with patch('gui.network_config_tab.DGLabWSServer', return_value=server), \
                 patch('services.oscquery_service.OSCQueryService', return_value=service), \
                 patch('gui.network_config_tab.osc_server.AsyncIOOSCUDPServer') as udp_server, \
                 patch('gui.network_config_tab.DGLabController') as controller, \
                 patch.object(tab, 'generate_qrcode'), patch.object(tab, 'update_qrcode'):
                controller.return_value.close = AsyncMock()
                tab.start_server()
                task = tab._server_task
                await asyncio.sleep(0)
                udp_server.assert_not_called()
                self.assertIs(controller.call_args.args[1], service.get_vrc_client.return_value)
                self.assertTrue(tab._osc_status_timer.isActive())
                self.assertIn('127.0.0.1:16002', tab.osc_status_label.text())

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.sleep(0)
                controller.return_value.close.assert_awaited_once()
                service.stop.assert_awaited_once()
                server.__aexit__.assert_awaited_once()
                self.assertIsNone(tab.oscquery_service)
                self.assertIsNone(tab.main_window.controller)
                self.assertEqual(tab.panel_control_handlers, {})
                self.assertEqual(tab.sps_control_handlers, {})
                self.assertFalse(tab._osc_status_timer.isActive())
                self.assertIsNone(tab._server_task)
                self.assertTrue(tab.start_button.isEnabled())

        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
