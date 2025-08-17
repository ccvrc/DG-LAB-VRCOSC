from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLineEdit, QCheckBox, QLabel, QListWidget, QListWidgetItem, QAbstractItemView, QSlider)
from PySide6.QtCore import Qt, Signal, QLocale
import logging
import yaml
import os
import asyncio

from i18n import translate as _
from config import get_config_file_path

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
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.address_list_widget.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.address_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layout.addWidget(self.address_list_widget)

        # Buttons to add and remove addresses
        self.button_layout = QHBoxLayout()
        self.add_button = QPushButton(_("osc_tab.add"))
        self.remove_button = QPushButton(_("osc_tab.remove"))
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
                        'min': widget.get_a_min_value(),
                        'max': widget.get_a_max_value()
                    },
                    'B': {
                        'min': widget.get_b_min_value(),
                        'max': widget.get_b_max_value()
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
        new_addresses = []
        for i in range(self.address_list_widget.count()):
            widget = self.address_list_widget.itemWidget(self.address_list_widget.item(i))
            if isinstance(widget, OSCAddressWidget):
                address = widget.address_edit.text().strip()
                if address:  # 只添加非空地址
                    channels = {
                        'A': widget.channel_a_checkbox.isChecked(),
                        'B': widget.channel_b_checkbox.isChecked()
                    }
                    # 添加映射范围设置
                    mapping_ranges = {
                        'A': {
                            'min': widget.get_a_min_value(),
                            'max': widget.get_a_max_value()
                        },
                        'B': {
                            'min': widget.get_b_min_value(),
                            'max': widget.get_b_max_value()
                        }
                    }
                    if channels['A'] or channels['B']:  # 至少选择了一个通道
                        new_addresses.append({
                            'address': address,
                            'channels': channels,
                            'mapping_ranges': mapping_ranges
                        })
        self.addresses = new_addresses
        logger.info(f"更新 OSC 地址列表: {len(new_addresses)} 个地址")
        
        # 如果控制器已初始化，更新 OSC 映射
        if self.main_window.controller:
            asyncio.create_task(self.main_window.network_config_tab._update_osc_mappings(self.main_window.controller))

    def save_addresses(self):
        # Save addresses to a YAML file using unified config path
        config_path = get_config_file_path('osc_addresses.yml')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.addresses, f, allow_unicode=True)
            logger.info(f"OSC addresses saved to {config_path}")
        except Exception as e:
            logger.error(f"保存OSC地址配置时出错: {str(e)}")

    def load_addresses(self):
        # 使用统一的配置文件路径处理函数
        config_path = get_config_file_path('osc_addresses.yml')
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
            {'address': '/avatar/parameters/DG-LAB/UpperLeg_L', 'channels': {'A': True, 'B': False}},
            {'address': '/avatar/parameters/DG-LAB/UpperLeg_R', 'channels': {'A': False, 'B': True}},
            {'address': '/avatar/parameters/Tail_Stretch', 'channels': {'A': False, 'B': False}},
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
                    widget.set_a_min_value(a_range['min'])
                    widget.set_a_max_value(a_range['max'])
                
                # B 通道映射范围
                if 'B' in mapping_ranges and isinstance(mapping_ranges['B'], dict):
                    b_range = mapping_ranges['B']
                    widget.set_b_min_value(b_range['min'])
                    widget.set_b_max_value(b_range['max'])
            
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

    def update_ui_texts(self):
        """更新所有UI文本为当前语言"""
        # 更新按钮文本
        self.add_button.setText(_("osc_tab.add"))
        self.remove_button.setText(_("osc_tab.remove"))
        
        # 更新各个地址项的UI
        for i in range(self.address_list_widget.count()):
            widget = self.address_list_widget.itemWidget(self.address_list_widget.item(i))
            if isinstance(widget, OSCAddressWidget):
                widget.update_ui_texts()

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
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.address_edit.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.address_edit.setPlaceholderText(_("osc_tab.address_placeholder"))
        self.address_row.addWidget(self.address_edit)

        self.channel_a_checkbox = QCheckBox("A")
        self.address_row.addWidget(self.channel_a_checkbox)

        self.channel_b_checkbox = QCheckBox("B")
        self.address_row.addWidget(self.channel_b_checkbox)
        
        # A 通道映射范围行
        self.a_range_row = QHBoxLayout()
        self.layout.addLayout(self.a_range_row)
        
        self.a_range_label = QLabel(_("osc_tab.channel_range_a") + ":")
        self.a_range_row.addWidget(self.a_range_label)
        
        # A通道最小值和最大值在同一行
        self.a_min_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.a_min_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.a_min_slider.setRange(0, 100)
        self.a_min_slider.setValue(0)
        self.a_min_slider.setFixedWidth(120)
        self.a_range_row.addWidget(self.a_min_slider)
        
        self.a_min_value_label = QLabel(_("osc_tab.min_value") + ":0%")
        self.a_range_row.addWidget(self.a_min_value_label)
        
        self.a_range_row.addSpacing(10)
        
        self.a_max_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.a_max_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.a_max_slider.setRange(0, 100)
        self.a_max_slider.setValue(100)
        self.a_max_slider.setFixedWidth(120)
        self.a_range_row.addWidget(self.a_max_slider)
        
        self.a_max_value_label = QLabel(_("osc_tab.max_value") + ":100%")
        self.a_range_row.addWidget(self.a_max_value_label)
        
        self.a_range_row.addStretch()
        
        # B 通道映射范围行
        self.b_range_row = QHBoxLayout()
        self.layout.addLayout(self.b_range_row)
        
        self.b_range_label = QLabel(_("osc_tab.channel_range_b") + ":")
        self.b_range_row.addWidget(self.b_range_label)
        
        # B通道最小值和最大值在同一行
        self.b_min_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.b_min_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.b_min_slider.setRange(0, 100)
        self.b_min_slider.setValue(0)
        self.b_min_slider.setFixedWidth(120)
        self.b_range_row.addWidget(self.b_min_slider)
        
        self.b_min_value_label = QLabel(_("osc_tab.min_value") + ":0%")
        self.b_range_row.addWidget(self.b_min_value_label)
        
        self.b_range_row.addSpacing(10)
        
        self.b_max_slider = QSlider(Qt.Horizontal)
        # 强制使用英文区域设置，避免数字显示为繁体中文
        self.b_max_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.b_max_slider.setRange(0, 100)
        self.b_max_slider.setValue(100)
        self.b_max_slider.setFixedWidth(120)
        self.b_range_row.addWidget(self.b_max_slider)
        
        self.b_max_value_label = QLabel(_("osc_tab.max_value") + ":100%")
        self.b_range_row.addWidget(self.b_max_value_label)
        
        self.b_range_row.addStretch()
        
        # 连接信号
        self.address_edit.textChanged.connect(self.addressChanged)
        self.channel_a_checkbox.stateChanged.connect(self.on_channel_changed)
        self.channel_b_checkbox.stateChanged.connect(self.on_channel_changed)
        
        self.a_min_slider.valueChanged.connect(self.on_a_min_changed)
        self.a_max_slider.valueChanged.connect(self.on_a_max_changed)
        self.b_min_slider.valueChanged.connect(self.on_b_min_changed)
        self.b_max_slider.valueChanged.connect(self.on_b_max_changed)
        
        # 初始状态更新
        self.update_range_visibility()
    
    def on_channel_changed(self):
        self.update_range_visibility()
        self.channelChanged.emit()
    
    def update_range_visibility(self):
        """根据通道选择状态更新映射范围控件的可见性"""
        is_a_visible = self.channel_a_checkbox.isChecked()
        is_b_visible = self.channel_b_checkbox.isChecked()
        
        # A通道控件可见性
        self.a_range_label.setVisible(is_a_visible)
        self.a_min_slider.setVisible(is_a_visible)
        self.a_min_value_label.setVisible(is_a_visible)
        self.a_max_slider.setVisible(is_a_visible)
        self.a_max_value_label.setVisible(is_a_visible)
        
        # B通道控件可见性
        self.b_range_label.setVisible(is_b_visible)
        self.b_min_slider.setVisible(is_b_visible)
        self.b_min_value_label.setVisible(is_b_visible)
        self.b_max_slider.setVisible(is_b_visible)
        self.b_max_value_label.setVisible(is_b_visible)
        
        # 强制更新布局
        if hasattr(self.layout, 'invalidate') and hasattr(self.layout, 'activate'):
            self.layout.invalidate()
            self.layout.activate()
        
        # 首先更新自身大小
        self.adjustSize()
        
        # 尝试找到父级QListWidget和对应的QListWidgetItem
        # 注意：可能存在多层嵌套的情况
        def find_list_widget_parent(widget):
            if widget is None:
                return None
            if isinstance(widget, QListWidget):
                return widget
            return find_list_widget_parent(widget.parent())
        
        list_widget = find_list_widget_parent(self.parent())
        if list_widget:
            # 查找对应的item并更新其大小
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item and list_widget.itemWidget(item) == self:
                    current_size = self.sizeHint()
                    if current_size.isValid():
                        item.setSizeHint(current_size)
                    # 使用QWidget的标准update()方法，不需要参数
                    list_widget.viewport().update()  # 更新列表视图的可视区域
                    break
        
        # 更新几何属性
        self.updateGeometry()
    
    def on_a_min_changed(self, value):
        """A通道最小值变更处理"""
        # 更新标签显示
        self.a_min_value_label.setText(f"{_('osc_tab.min_value')}:{value}%")
        
        # 确保最小值不大于最大值
        if value > self.a_max_slider.value():
            self.a_max_slider.setValue(value)
        
        self.mapRangeChanged.emit()
    
    def on_a_max_changed(self, value):
        """A通道最大值变更处理"""
        # 更新标签显示
        self.a_max_value_label.setText(f"{_('osc_tab.max_value')}:{value}%")
        
        # 确保最大值不小于最小值
        if value < self.a_min_slider.value():
            self.a_min_slider.setValue(value)
        
        self.mapRangeChanged.emit()
    
    def on_b_min_changed(self, value):
        """B通道最小值变更处理"""
        # 更新标签显示
        self.b_min_value_label.setText(f"{_('osc_tab.min_value')}:{value}%")
        
        # 确保最小值不大于最大值
        if value > self.b_max_slider.value():
            self.b_max_slider.setValue(value)
        
        self.mapRangeChanged.emit()
    
    def on_b_max_changed(self, value):
        """B通道最大值变更处理"""
        # 更新标签显示
        self.b_max_value_label.setText(f"{_('osc_tab.max_value')}:{value}%")
        
        # 确保最大值不小于最小值
        if value < self.b_min_slider.value():
            self.b_min_slider.setValue(value)
        
        self.mapRangeChanged.emit()
    
    def get_a_min_value(self):
        """获取A通道最小值"""
        return self.a_min_slider.value()
    
    def get_a_max_value(self):
        """获取A通道最大值"""
        return self.a_max_slider.value()
    
    def get_b_min_value(self):
        """获取B通道最小值"""
        return self.b_min_slider.value()
    
    def get_b_max_value(self):
        """获取B通道最大值"""
        return self.b_max_slider.value()
    
    def set_a_min_value(self, value):
        """设置A通道最小值"""
        self.a_min_slider.setValue(int(value))
    
    def set_a_max_value(self, value):
        """设置A通道最大值"""
        self.a_max_slider.setValue(int(value))
    
    def set_b_min_value(self, value):
        """设置B通道最小值"""
        self.b_min_slider.setValue(int(value))
    
    def set_b_max_value(self, value):
        """设置B通道最大值"""
        self.b_max_slider.setValue(int(value))

    def update_ui_texts(self):
        """更新所有UI文本为当前语言"""
        # 更新各个地址项的UI
        self.address_edit.setPlaceholderText(_("osc_tab.address_placeholder"))
        self.a_range_label.setText(_("osc_tab.channel_range_a") + ":")
        self.b_range_label.setText(_("osc_tab.channel_range_b") + ":")
        
        # 更新滑块值标签
        a_min_value = self.a_min_slider.value()
        a_max_value = self.a_max_slider.value()
        b_min_value = self.b_min_slider.value()
        b_max_value = self.b_max_slider.value()
        
        self.a_min_value_label.setText(f"{_('osc_tab.min_value')}:{a_min_value}%")
        self.a_max_value_label.setText(f"{_('osc_tab.max_value')}:{a_max_value}%")
        self.b_min_value_label.setText(f"{_('osc_tab.min_value')}:{b_min_value}%")
        self.b_max_value_label.setText(f"{_('osc_tab.max_value')}:{b_max_value}%")
