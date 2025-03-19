from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLineEdit, QCheckBox, QLabel, QListWidget, QListWidgetItem, QAbstractItemView)
from PySide6.QtCore import Qt, Signal
import logging
import yaml
import os
import asyncio

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
        # 添加新地址到数据模型
        new_address = {'address': '', 'channels': {'A': False, 'B': False}}
        self.addresses.append(new_address)
        
        # 添加到UI
        item = QListWidgetItem()
        self.address_list_widget.addItem(item)
        widget = OSCAddressWidget()
        item.setSizeHint(widget.sizeHint())
        self.address_list_widget.setItemWidget(item, widget)
        widget.addressChanged.connect(self.on_address_changed)
        widget.channelChanged.connect(self.on_channel_changed)
        self.address_list_widget.setCurrentItem(item)
        
        # 保存并发送更新信号
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
        # 同步 UI 到数据模型，确保二者一致
        self.sync_ui_to_model()
        self.save_addresses()
        self.addresses_updated.emit()

    def on_channel_changed(self):
        # 同步 UI 到数据模型，确保二者一致
        self.sync_ui_to_model()
        self.save_addresses()
        self.addresses_updated.emit()

    def sync_ui_to_model(self):
        """同步 UI 到数据模型，重建 self.addresses 列表"""
        new_addresses = []
        
        for i in range(self.address_list_widget.count()):
            item = self.address_list_widget.item(i)
            widget = self.address_list_widget.itemWidget(item)
            
            if widget:
                address = widget.address_edit.text()
                channels = {
                    'A': widget.channel_a_checkbox.isChecked(),
                    'B': widget.channel_b_checkbox.isChecked()
                }
                new_addresses.append({
                    'address': address,
                    'channels': channels
                })
        
        # 更新数据模型
        self.addresses = new_addresses

    def update_address_list(self):
        """更新 OSC 地址列表"""
        addresses = []
        for i in range(self.address_list_widget.count()):
            widget = self.address_list_widget.itemWidget(self.address_list_widget.item(i))
            if isinstance(widget, OSCAddressWidget):
                address = widget.address_edit.text().strip()
                if address:  # 只添加非空地址
                    channels = []
                    if widget.channel_a_checkbox.isChecked():
                        channels.append("A")
                    if widget.channel_b_checkbox.isChecked():
                        channels.append("B")
                    if channels:  # 至少选择了一个通道
                        addresses.append({
                            'address': address,
                            'channels': channels
                        })
        self.addresses = addresses
        logger.info(f"更新 OSC 地址列表: {len(addresses)} 个地址")
        
        # 如果控制器已初始化，更新 OSC 映射
        if self.main_window.controller:
            asyncio.create_task(self.main_window.network_config_tab._update_osc_mappings(self.main_window.controller))

    def save_addresses(self):
        # Save addresses to a YAML file
        with open('osc_addresses.yml', 'w', encoding='utf-8') as f:
            yaml.dump(self.addresses, f, allow_unicode=True)
        logger.info("OSC addresses saved.")

    def load_addresses(self):
        # 获取绝对路径
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'osc_addresses.yml')
        logger.info(f"尝试从 {config_path} 加载OSC地址配置")
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded_data = yaml.safe_load(f)
                    if loaded_data is None or not isinstance(loaded_data, list):
                        logger.warning(f"加载的配置无效，使用默认地址")
                        self.addresses = self.get_default_addresses()
                    else:
                        self.addresses = loaded_data
                        logger.info(f"从文件加载了 {len(self.addresses)} 个OSC地址")
            else:
                logger.info("配置文件不存在，使用默认地址")
                self.addresses = self.get_default_addresses()
            
            # 将加载的地址更新到UI
            self.populate_address_list()
        except Exception as e:
            logger.error(f"加载OSC地址时出错: {str(e)}")
            self.addresses = self.get_default_addresses()
            self.populate_address_list()

    def get_default_addresses(self):
        """返回默认的OSC地址配置"""
        logger.info("加载默认OSC地址")
        return [
            {'address': '/avatar/parameters/DG-LAB/*', 'channels': {'A': True, 'B': False}},
            {'address': '/avatar/parameters/Tail_Stretch', 'channels': {'A': False, 'B': True}},
        ]

    def populate_address_list(self):
        """将地址列表填充到UI中"""
        # 先清空现有列表
        self.address_list_widget.clear()
        
        # 添加每个地址到列表
        for address_data in self.addresses:
            item = QListWidgetItem()
            self.address_list_widget.addItem(item)
            widget = OSCAddressWidget()
            
            # 设置地址
            widget.address_edit.setText(address_data['address'])
            
            # 处理通道选择状态 - 兼容不同格式的数据
            channels = address_data.get('channels', {})
            if isinstance(channels, dict):
                # 字典格式: {'A': True, 'B': False}
                widget.channel_a_checkbox.setChecked(channels.get('A', False))
                widget.channel_b_checkbox.setChecked(channels.get('B', False))
            elif isinstance(channels, list):
                # 列表格式: ['A', 'B'] 或 ['A']
                widget.channel_a_checkbox.setChecked('A' in channels)
                widget.channel_b_checkbox.setChecked('B' in channels)
            
            # 连接信号
            widget.addressChanged.connect(self.on_address_changed)
            widget.channelChanged.connect(self.on_channel_changed)
            
            item.setSizeHint(widget.sizeHint())
            self.address_list_widget.setItemWidget(item, widget)
        
        logger.info(f"UI已更新，显示 {len(self.addresses)} 个OSC地址")

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
