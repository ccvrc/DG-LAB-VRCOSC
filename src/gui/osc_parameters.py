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
        new_address = {
            'address': '', 
            'channels': {'A': False, 'B': False},
            'mapping_ranges': {
                'A': {'min': 0, 'max': 100},
                'B': {'min': 0, 'max': 100}
            }
        }
        self.addresses.append(new_address)
        
        # 添加到UI
        item = QListWidgetItem()
        self.address_list_widget.addItem(item)
        widget = OSCAddressWidget()
        item.setSizeHint(widget.sizeHint())
        self.address_list_widget.setItemWidget(item, widget)
        widget.addressChanged.connect(self.on_address_changed)
        widget.channelChanged.connect(self.on_channel_changed)
        widget.mapRangeChanged.connect(self.on_map_range_changed)
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

    def on_map_range_changed(self):
        """处理映射范围变更事件"""
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
                # 添加映射范围设置
                mapping_ranges = {
                    'A': {
                        'min': int(widget.a_min_edit.text() or "0"),
                        'max': int(widget.a_max_edit.text() or "100")
                    },
                    'B': {
                        'min': int(widget.b_min_edit.text() or "0"),
                        'max': int(widget.b_max_edit.text() or "100")
                    }
                }
                new_addresses.append({
                    'address': address,
                    'channels': channels,
                    'mapping_ranges': mapping_ranges
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
            
            # 处理映射范围设置 - 如果数据中有映射范围信息，则应用它
            mapping_ranges = address_data.get('mapping_ranges', {})
            if mapping_ranges and isinstance(mapping_ranges, dict):
                # A 通道映射范围
                if 'A' in mapping_ranges and isinstance(mapping_ranges['A'], dict):
                    a_range = mapping_ranges['A']
                    widget.a_min_edit.setText(str(a_range.get('min', 0)))
                    widget.a_max_edit.setText(str(a_range.get('max', 100)))
                
                # B 通道映射范围
                if 'B' in mapping_ranges and isinstance(mapping_ranges['B'], dict):
                    b_range = mapping_ranges['B']
                    widget.b_min_edit.setText(str(b_range.get('min', 0)))
                    widget.b_max_edit.setText(str(b_range.get('max', 100)))
            
            # 连接信号
            widget.addressChanged.connect(self.on_address_changed)
            widget.channelChanged.connect(self.on_channel_changed)
            widget.mapRangeChanged.connect(self.on_map_range_changed)
            
            item.setSizeHint(widget.sizeHint())
            self.address_list_widget.setItemWidget(item, widget)
            
            # 更新映射范围控件的可见性
            widget.update_range_visibility()
        
        logger.info(f"UI已更新，显示 {len(self.addresses)} 个OSC地址")

    def get_addresses(self):
        # Return the list of addresses
        return self.addresses

class OSCAddressWidget(QWidget):
    addressChanged = Signal()
    channelChanged = Signal()
    mapRangeChanged = Signal()

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        
        # 地址和通道选择行
        self.address_row = QHBoxLayout()
        self.layout.addLayout(self.address_row)

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("OSC 地址")
        self.address_row.addWidget(self.address_edit)

        self.channel_a_checkbox = QCheckBox("A")
        self.address_row.addWidget(self.channel_a_checkbox)

        self.channel_b_checkbox = QCheckBox("B")
        self.address_row.addWidget(self.channel_b_checkbox)
        
        # A 通道映射范围行
        self.a_range_row = QHBoxLayout()
        self.layout.addLayout(self.a_range_row)
        
        self.a_range_label = QLabel("A通道映射范围:")
        self.a_range_row.addWidget(self.a_range_label)
        
        self.a_min_edit = QLineEdit()
        self.a_min_edit.setPlaceholderText("最小值(%)")
        self.a_min_edit.setText("0")
        self.a_min_edit.setFixedWidth(80)
        self.a_range_row.addWidget(self.a_min_edit)
        
        self.a_range_row.addWidget(QLabel("-"))
        
        self.a_max_edit = QLineEdit()
        self.a_max_edit.setPlaceholderText("最大值(%)")
        self.a_max_edit.setText("100")
        self.a_max_edit.setFixedWidth(80)
        self.a_range_row.addWidget(self.a_max_edit)
        
        self.a_range_row.addStretch()
        
        # B 通道映射范围行
        self.b_range_row = QHBoxLayout()
        self.layout.addLayout(self.b_range_row)
        
        self.b_range_label = QLabel("B通道映射范围:")
        self.b_range_row.addWidget(self.b_range_label)
        
        self.b_min_edit = QLineEdit()
        self.b_min_edit.setPlaceholderText("最小值(%)")
        self.b_min_edit.setText("0")
        self.b_min_edit.setFixedWidth(80)
        self.b_range_row.addWidget(self.b_min_edit)
        
        self.b_range_row.addWidget(QLabel("-"))
        
        self.b_max_edit = QLineEdit()
        self.b_max_edit.setPlaceholderText("最大值(%)")
        self.b_max_edit.setText("100")
        self.b_max_edit.setFixedWidth(80)
        self.b_range_row.addWidget(self.b_max_edit)
        
        self.b_range_row.addStretch()
        
        # 连接信号
        self.address_edit.textChanged.connect(self.addressChanged)
        self.channel_a_checkbox.stateChanged.connect(self.on_channel_changed)
        self.channel_b_checkbox.stateChanged.connect(self.on_channel_changed)
        
        self.a_min_edit.textChanged.connect(self.validate_a_range)
        self.a_max_edit.textChanged.connect(self.validate_a_range)
        self.b_min_edit.textChanged.connect(self.validate_b_range)
        self.b_max_edit.textChanged.connect(self.validate_b_range)
        
        # 初始状态更新
        self.update_range_visibility()
    
    def on_channel_changed(self):
        self.update_range_visibility()
        self.channelChanged.emit()
    
    def update_range_visibility(self):
        """根据通道选择状态更新映射范围控件的可见性"""
        self.a_range_label.setVisible(self.channel_a_checkbox.isChecked())
        self.a_min_edit.setVisible(self.channel_a_checkbox.isChecked())
        self.a_max_edit.setVisible(self.channel_a_checkbox.isChecked())
        
        self.b_range_label.setVisible(self.channel_b_checkbox.isChecked())
        self.b_min_edit.setVisible(self.channel_b_checkbox.isChecked())
        self.b_max_edit.setVisible(self.channel_b_checkbox.isChecked())
        
        # 发出布局变更信号
        self.updateGeometry()
    
    def validate_a_range(self):
        """验证A通道的映射范围值"""
        try:
            min_val = float(self.a_min_edit.text() or "0")
            max_val = float(self.a_max_edit.text() or "100")
            
            # 确保最小值不大于最大值
            if min_val > max_val:
                self.a_min_edit.setText(str(max_val))
                min_val = max_val
                
            # 确保值在0-100范围内
            min_val = max(0, min(100, min_val))
            max_val = max(0, min(100, max_val))
            
            self.a_min_edit.setText(str(int(min_val)))
            self.a_max_edit.setText(str(int(max_val)))
            
            self.mapRangeChanged.emit()
        except ValueError:
            # 如果输入的不是有效数字，则重置为默认值
            self.a_min_edit.setText("0")
            self.a_max_edit.setText("100")
    
    def validate_b_range(self):
        """验证B通道的映射范围值"""
        try:
            min_val = float(self.b_min_edit.text() or "0")
            max_val = float(self.b_max_edit.text() or "100")
            
            # 确保最小值不大于最大值
            if min_val > max_val:
                self.b_min_edit.setText(str(max_val))
                min_val = max_val
                
            # 确保值在0-100范围内
            min_val = max(0, min(100, min_val))
            max_val = max(0, min(100, max_val))
            
            self.b_min_edit.setText(str(int(min_val)))
            self.b_max_edit.setText(str(int(max_val)))
            
            self.mapRangeChanged.emit()
        except ValueError:
            # 如果输入的不是有效数字，则重置为默认值
            self.b_min_edit.setText("0")
            self.b_max_edit.setText("100")
