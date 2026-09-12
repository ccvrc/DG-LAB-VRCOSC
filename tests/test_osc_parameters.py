import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtWidgets import QApplication

from gui.osc_parameters import OSCParametersTab


class OSCParametersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name) / 'osc_addresses.yml'
        self.path_patch = patch('gui.osc_parameters.get_config_file_path', return_value=str(self.config_path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def create_tab(self):
        tab = OSCParametersTab(SimpleNamespace(controller=None))
        self.addCleanup(tab.close)
        return tab

    def read_saved_addresses(self):
        return yaml.safe_load(self.config_path.read_text(encoding='utf-8'))

    def test_default_disabled_row_can_be_removed(self):
        tab = self.create_tab()
        self.assertEqual(tab.address_list_widget.count(), 3)
        self.assertEqual(len(tab.addresses), 3)
        self.assertEqual(len(tab.get_addresses()), 2)

        tab.address_list_widget.setCurrentRow(2)
        tab.remove_address()

        self.assertEqual(tab.address_list_widget.count(), 2)
        self.assertEqual(len(tab.addresses), 2)
        self.assertEqual(self.read_saved_addresses(), tab.addresses)

    def test_disabled_and_incomplete_rows_survive_add_save_reload(self):
        tab = self.create_tab()
        tab.add_address()

        saved = self.read_saved_addresses()
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[2]['address'], '/avatar/parameters/Tail_Stretch')
        self.assertEqual(saved[3]['address'], '')
        self.assertEqual(len(tab.get_addresses()), 2)

        reloaded = self.create_tab()
        self.assertEqual(reloaded.address_list_widget.count(), 4)
        self.assertEqual(reloaded.addresses, saved)
        self.assertEqual(len(reloaded.get_addresses()), 2)

    def test_disabled_middle_row_does_not_shift_deletion_target(self):
        entries = [
            {'address': '/first', 'channels': {'A': True, 'B': False}},
            {'address': '/disabled', 'channels': {'A': False, 'B': False}},
            {'address': '/last', 'channels': {'A': False, 'B': True}},
        ]
        self.config_path.write_text(yaml.safe_dump(entries), encoding='utf-8')
        tab = self.create_tab()

        tab.address_list_widget.setCurrentRow(1)
        tab.remove_address()

        self.assertEqual([entry['address'] for entry in tab.addresses], ['/first', '/last'])
        self.assertEqual(self.read_saved_addresses(), tab.addresses)


if __name__ == '__main__':
    unittest.main()
