"""Exercise dispatcher rebuilds without starting a device or a GUI event loop."""
from types import SimpleNamespace
import unittest

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_message_builder import OscMessageBuilder

from gui.network_config_tab import NetworkConfigTab


class MappingHarness:
    update_osc_mappings = NetworkConfigTab.update_osc_mappings
    _update_osc_mappings = NetworkConfigTab._update_osc_mappings
    _clear_osc_mappings = NetworkConfigTab._clear_osc_mappings
    add_panel_control_mappings = NetworkConfigTab.add_panel_control_mappings
    add_sps_control_mappings = NetworkConfigTab.add_sps_control_mappings

    def __init__(self):
        self.addresses = []
        self.main_window = SimpleNamespace(controller=None, get_osc_addresses=lambda: self.addresses)
        self.dispatcher = Dispatcher()
        self.osc_address_handlers = {}
        self.panel_control_handlers = {}
        self.sps_control_handlers = {}
        self.received = []

    def receive(self, address, *args, **kwargs):
        self.received.append((address, kwargs['controller']))

    handle_osc_message_task_pb_with_channels = receive
    handle_osc_message_task_pad = receive
    handle_osc_message_task_sps = receive
    handle_avatar_change_task = receive

    def send(self, address):
        message = OscMessageBuilder(address=address)
        message.add_arg(0.5)
        self.dispatcher.call_handlers_for_packet(message.build().dgram, ('127.0.0.1', 9000))


class MappingTests(unittest.TestCase):
    def test_duplicate_addresses_can_be_removed_without_leaving_handlers(self):
        tab = MappingHarness()
        controller = object()
        tab.addresses = [
            {'address': '/avatar/parameters/Test', 'channels': ['A']},
            {'address': '/avatar/parameters/Test', 'channels': ['B']},
        ]
        tab.update_osc_mappings(controller)
        tab.update_osc_mappings(controller)
        tab.send('/avatar/parameters/Test')
        self.assertEqual(len(tab.received), 2)
        tab.received.clear()
        tab.addresses = []
        tab.update_osc_mappings(controller)
        tab.send('/avatar/parameters/Test')
        self.assertEqual(tab.received, [])

    def test_restart_rebinds_panel_and_sps_to_new_controller(self):
        tab = MappingHarness()
        old, new = object(), object()
        tab.update_osc_mappings(old)
        tab._clear_osc_mappings()
        tab.update_osc_mappings(new)
        for address in ('/avatar/change', '/avatar/parameters/OGB/Pen/Test/Depth',
                        '/avatar/parameters/SoundPad/Volume'):
            tab.send(address)
        self.assertEqual(len(tab.received), 3)
        self.assertTrue(all(controller is new for _, controller in tab.received))

    def test_edit_before_controller_exists_does_not_register_callbacks(self):
        tab = MappingHarness()
        tab.update_osc_mappings()
        self.assertEqual(tab.panel_control_handlers, {})
