from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLineEdit, QCheckBox, QLabel, QListWidget, QListWidgetItem, QAbstractItemView)
from PySide6.QtCore import Qt, Signal
import logging
import yaml
import os

logger = logging.getLogger(__name__)

class OSCParametersTab(QWidget):
    addresses_updated = Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        # List to display OSC addresses
        self.address_list_widget = QListWidget()
        self.address_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layout.addWidget(self.address_list_widget)

        # Buttons to add and remove addresses
        self.button_layout = QHBoxLayout()
        self.add_button = QPushButton("添加地址")
        self.remove_button = QPushButton("移除地址")
        self.button_layout.addWidget(self.add_button)
        self.button_layout.addWidget(self.remove_button)
        self.layout.addLayout(self.button_layout)

        self.add_button.clicked.connect(self.add_address)
        self.remove_button.clicked.connect(self.remove_address)

        # Load existing addresses
        self.addresses = []
        self.load_addresses()

        # Update the UI
        self.update_address_list()

    def add_address(self):
        item = QListWidgetItem()
        self.address_list_widget.addItem(item)
        widget = OSCAddressWidget()
        item.setSizeHint(widget.sizeHint())
        self.address_list_widget.setItemWidget(item, widget)
        self.addresses.append({'address': '', 'channels': {'A': False, 'B': False}})
        widget.addressChanged.connect(self.on_address_changed)
        widget.channelChanged.connect(self.on_channel_changed)
        self.address_list_widget.setCurrentItem(item)
        self.save_addresses()
        self.addresses_updated.emit()

    def remove_address(self):
        current_row = self.address_list_widget.currentRow()
        if current_row >= 0:
            self.address_list_widget.takeItem(current_row)
            del self.addresses[current_row]
            self.save_addresses()
            self.addresses_updated.emit()

    def on_address_changed(self):
        # Update the address in self.addresses
        for i in range(self.address_list_widget.count()):
            item = self.address_list_widget.item(i)
            widget = self.address_list_widget.itemWidget(item)
            self.addresses[i]['address'] = widget.address_edit.text()
        self.save_addresses()
        self.addresses_updated.emit()

    def on_channel_changed(self):
        # Update the channels in self.addresses
        for i in range(self.address_list_widget.count()):
            item = self.address_list_widget.item(i)
            widget = self.address_list_widget.itemWidget(item)
            self.addresses[i]['channels']['A'] = widget.channel_a_checkbox.isChecked()
            self.addresses[i]['channels']['B'] = widget.channel_b_checkbox.isChecked()
        self.save_addresses()
        self.addresses_updated.emit()

    def update_address_list(self):
        self.address_list_widget.clear()
        for addr in self.addresses:
            item = QListWidgetItem()
            self.address_list_widget.addItem(item)
            widget = OSCAddressWidget()
            widget.address_edit.setText(addr['address'])
            widget.channel_a_checkbox.setChecked(addr['channels'].get('A', False))
            widget.channel_b_checkbox.setChecked(addr['channels'].get('B', False))
            item.setSizeHint(widget.sizeHint())
            self.address_list_widget.setItemWidget(item, widget)
            widget.addressChanged.connect(self.on_address_changed)
            widget.channelChanged.connect(self.on_channel_changed)

    def save_addresses(self):
        # Save addresses to a YAML file
        with open('osc_addresses.yml', 'w', encoding='utf-8') as f:
            yaml.dump(self.addresses, f, allow_unicode=True)
        logger.info("OSC addresses saved.")

    def load_addresses(self):
        # Load addresses from a YAML file
        if os.path.exists('osc_addresses.yml'):
            with open('osc_addresses.yml', 'r', encoding='utf-8') as f:
                self.addresses = yaml.safe_load(f)
            if self.addresses is None:
                self.addresses = []
            logger.info("OSC addresses loaded.")
        else:
            # Load default addresses
            self.addresses = [
                {'address': '/avatar/parameters/DG-LAB/*', 'channels': {'A': True}},
                {'address': '/avatar/parameters/Tail_Stretch', 'channels': {'B': True}},
            ]

    def get_addresses(self):
        # Return the list of addresses
        return self.addresses

class OSCAddressWidget(QWidget):
    addressChanged = Signal()
    channelChanged = Signal()

    def __init__(self):
        super().__init__()
        self.layout = QHBoxLayout()
        self.setLayout(self.layout)

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("OSC 地址")
        self.layout.addWidget(self.address_edit)

        self.channel_a_checkbox = QCheckBox("A")
        self.layout.addWidget(self.channel_a_checkbox)

        self.channel_b_checkbox = QCheckBox("B")
        self.layout.addWidget(self.channel_b_checkbox)

        self.address_edit.textChanged.connect(self.addressChanged)
        self.channel_a_checkbox.stateChanged.connect(self.channelChanged)
        self.channel_b_checkbox.stateChanged.connect(self.channelChanged)
