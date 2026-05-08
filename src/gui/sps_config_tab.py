from PySide6.QtCore import QLocale, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
import asyncio
import logging
import os
import yaml

from config import get_config_file_path
from i18n import translate as _, language_signals
from services.vrchat_oscquery_inspector import extract_avatar_id, fetch_vrchat_osc_nodes
from sps_processor import PLUG_SOURCE_KEYS, SOCKET_SOURCE_KEYS, SPSProcessor

logger = logging.getLogger(__name__)


class SPSConfigTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.zones: list[dict[str, str]] = []
        self.bindings: list[dict] = []
        self.avatar_bindings: dict[str, list[dict]] = {}
        self.legacy_bindings: list[dict] = []
        self.current_avatar_id: str | None = None
        self.visible_zone_keys: set[tuple[str, str]] | None = None
        self.refresh_task: asyncio.Task | None = None
        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.setSingleShot(True)
        self.auto_refresh_timer.timeout.connect(lambda: self.refresh_zones(manual=False))

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.toolbar_layout = QHBoxLayout()
        self.refresh_button = QPushButton(_("sps_tab.refresh"))
        self.refresh_button.clicked.connect(lambda: self.refresh_zones(manual=True))
        self.toolbar_layout.addWidget(self.refresh_button)

        self.status_label = QLabel(_("sps_tab.not_scanned"))
        self.toolbar_layout.addWidget(self.status_label)
        self.toolbar_layout.addStretch()
        self.layout.addLayout(self.toolbar_layout)

        self.zone_list_widget = QListWidget()
        self.zone_list_widget.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.zone_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layout.addWidget(self.zone_list_widget)

        self.load_bindings()
        self.update_zone_list()
        language_signals.language_changed.connect(self.update_ui_texts)
        self.schedule_auto_refresh("startup", delay_ms=2500)

    def config_path(self):
        return get_config_file_path("sps_bindings.yml")

    def load_bindings(self):
        path = self.config_path()
        if not os.path.exists(path):
            self.bindings = []
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
                self.load_bindings_data(data)
        except Exception as e:
            logger.error(f"加载 SPS 绑定配置失败: {e}")
            self.bindings = []

    def load_bindings_data(self, data):
        self.avatar_bindings = {}
        self.legacy_bindings = []
        self.current_avatar_id = None

        if isinstance(data, list):
            self.legacy_bindings = self.normalize_bindings(data)
            self.bindings = list(self.legacy_bindings)
            return

        if not isinstance(data, dict):
            self.bindings = []
            return

        self.current_avatar_id = data.get("current_avatar_id") if isinstance(data.get("current_avatar_id"), str) else None
        self.legacy_bindings = self.normalize_bindings(data.get("legacy_bindings", []))

        avatars = data.get("avatars", {})
        if isinstance(avatars, dict):
            for avatar_id, avatar_data in avatars.items():
                if not isinstance(avatar_id, str) or not avatar_id.startswith("avtr_"):
                    continue
                raw_bindings = avatar_data.get("bindings", []) if isinstance(avatar_data, dict) else avatar_data
                self.avatar_bindings[avatar_id] = self.normalize_bindings(raw_bindings if isinstance(raw_bindings, list) else [])

        if self.current_avatar_id and self.current_avatar_id in self.avatar_bindings:
            self.bindings = list(self.avatar_bindings[self.current_avatar_id])
        else:
            self.bindings = list(self.legacy_bindings)

    def normalize_bindings(self, bindings: list[dict]):
        normalized_bindings = []
        seen = set()
        for binding in bindings:
            kind = binding.get("kind")
            zone_id = SPSProcessor.normalize_zone_id(binding.get("zone_id", ""))
            if kind not in {"Orf", "Pen"} or not zone_id:
                continue
            key = (kind, zone_id)
            if key in seen:
                continue
            seen.add(key)
            normalized_bindings.append(
                {
                    "kind": kind,
                    "zone_id": zone_id,
                    "channels": {
                        "A": bool(binding.get("channels", {}).get("A", False)),
                        "B": bool(binding.get("channels", {}).get("B", False)),
                    },
                    "min_strength": {
                        "A": int(binding.get("min_strength", {}).get("A", 0)),
                        "B": int(binding.get("min_strength", {}).get("B", 0)),
                    },
                    "max_strength": {
                        "A": int(binding.get("max_strength", {}).get("A", 100)),
                        "B": int(binding.get("max_strength", {}).get("B", 100)),
                    },
                    "sources": SPSProcessor.normalize_sources(kind, binding.get("sources")),
                }
            )
        return normalized_bindings

    def save_bindings(self):
        self.sync_ui_to_model()
        self.bindings = self.normalize_bindings(self.bindings)
        if self.current_avatar_id:
            self.avatar_bindings[self.current_avatar_id] = list(self.bindings)
        else:
            self.legacy_bindings = list(self.bindings)
        path = self.config_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(self.bindings_data(), f, allow_unicode=True, sort_keys=False)
            logger.info(f"SPS bindings saved to {path}")
        except Exception as e:
            logger.error(f"保存 SPS 绑定配置失败: {e}")
        self.apply_bindings_to_controller()

    def bindings_data(self):
        data = {
            "version": 2,
            "current_avatar_id": self.current_avatar_id,
            "avatars": {},
        }
        for avatar_id, bindings in sorted(self.avatar_bindings.items()):
            data["avatars"][avatar_id] = {"bindings": self.normalize_bindings(bindings)}
        if self.legacy_bindings and not self.current_avatar_id:
            data["legacy_bindings"] = self.normalize_bindings(self.legacy_bindings)
        return data

    def apply_bindings_to_controller(self):
        self.bindings = self.normalize_bindings(self.bindings)
        if self.main_window.controller and hasattr(self.main_window.controller, "set_sps_bindings"):
            self.main_window.controller.set_sps_bindings(self.bindings)

    def get_binding_for_zone(self, kind: str, zone_id: str) -> dict:
        zone_id = SPSProcessor.normalize_zone_id(zone_id)
        for binding in self.bindings:
            if binding.get("kind") == kind and binding.get("zone_id") == zone_id:
                return binding
        return {
            "kind": kind,
            "zone_id": zone_id,
            "channels": {"A": False, "B": False},
            "min_strength": {"A": 0, "B": 0},
            "max_strength": {"A": 100, "B": 100},
            "sources": SPSProcessor.normalize_sources(kind, None),
        }

    def schedule_auto_refresh(self, reason="auto", delay_ms=1000):
        logger.info(f"安排 SPS 自动探测: reason={reason}, delay={delay_ms}ms")
        self.auto_refresh_timer.start(delay_ms)

    def refresh_zones(self, manual=True):
        if self.refresh_task and not self.refresh_task.done():
            logger.info("SPS 自动探测已在进行中，忽略重复触发")
            if manual:
                self.status_label.setText(_("sps_tab.scanning"))
            return

        if manual:
            self.status_label.setText(_("sps_tab.scanning"))
            self.refresh_button.setEnabled(False)

        self.refresh_task = asyncio.create_task(self.refresh_zones_async(manual=manual))
        self.refresh_task.add_done_callback(self.on_refresh_task_done)

    async def refresh_zones_async(self, manual=True):
        try:
            nodes, host_info = await asyncio.to_thread(fetch_vrchat_osc_nodes, timeout=2.0)
            self.switch_avatar(extract_avatar_id(nodes))
            self.zones = SPSProcessor.discover_zones_from_nodes(nodes)
            if not self.zones:
                logger.info("当前 Avatar 未发现 OGB/SPS 参数")
                self.visible_zone_keys = set()
                self.update_zone_list()
                if manual:
                    self.status_label.setText(_("sps_tab.found").format(count=0, host=host_info.get("NAME", "VRChat")))
                return
            self.visible_zone_keys = {
                (zone["kind"], SPSProcessor.normalize_zone_id(zone["zone_id"]))
                for zone in self.zones
            }
            self.merge_discovered_zones()
            self.update_zone_list()
            self.save_bindings()
            self.status_label.setText(
                _("sps_tab.found").format(count=len(self.zones), host=host_info.get("NAME", "VRChat"))
            )
        except Exception as e:
            if manual:
                logger.error(f"SPS 自动探测失败: {e}", exc_info=True)
                self.status_label.setText(_("sps_tab.scan_failed").format(error=e))
            else:
                logger.info(f"SPS 自动探测暂不可用: {e}")
                self.schedule_auto_refresh("retry_after_failure", delay_ms=5000)
        finally:
            self.refresh_button.setEnabled(True)

    def on_refresh_task_done(self, task: asyncio.Task):
        try:
            task.result()
        except asyncio.CancelledError:
            logger.info("SPS 自动探测任务已取消")
        except Exception as e:
            logger.error(f"SPS 自动探测任务异常结束: {e}", exc_info=True)

    def merge_discovered_zones(self):
        self.bindings = self.normalize_bindings(self.bindings)
        existing_keys = {(binding.get("kind"), binding.get("zone_id")) for binding in self.bindings}
        for zone in self.zones:
            zone_id = SPSProcessor.normalize_zone_id(zone["zone_id"])
            key = (zone["kind"], zone_id)
            if key in existing_keys:
                continue
            self.bindings.append(self.get_binding_for_zone(zone["kind"], zone_id))
            existing_keys.add(key)

    def switch_avatar(self, avatar_id: str | None):
        if avatar_id == self.current_avatar_id:
            return

        if self.current_avatar_id:
            self.sync_ui_to_model()
            self.avatar_bindings[self.current_avatar_id] = self.normalize_bindings(self.bindings)
        elif self.bindings and not self.legacy_bindings:
            self.legacy_bindings = self.normalize_bindings(self.bindings)

        self.current_avatar_id = avatar_id
        if avatar_id:
            if avatar_id not in self.avatar_bindings:
                self.avatar_bindings[avatar_id] = self.normalize_bindings(self.legacy_bindings)
                self.legacy_bindings = []
            self.bindings = list(self.avatar_bindings[avatar_id])
        else:
            self.bindings = list(self.legacy_bindings)
        self.apply_bindings_to_controller()

    def update_zone_list(self):
        self.zone_list_widget.clear()

        self.bindings = self.normalize_bindings(self.bindings)
        visible_bindings = list(self.bindings)
        if self.visible_zone_keys is not None:
            visible_bindings = [
                binding
                for binding in visible_bindings
                if (binding.get("kind"), SPSProcessor.normalize_zone_id(binding.get("zone_id", ""))) in self.visible_zone_keys
            ]
        visible_bindings.sort(key=lambda item: (item.get("kind", ""), item.get("zone_id", "")))

        for binding in visible_bindings:
            item = QListWidgetItem()
            self.zone_list_widget.addItem(item)
            widget = SPSZoneWidget(binding)
            widget.changed.connect(self.save_bindings)
            self.zone_list_widget.setItemWidget(item, widget)
            widget.refresh_list_item_size()

        if not visible_bindings:
            self.status_label.setText(_("sps_tab.not_scanned"))

    def sync_ui_to_model(self):
        visible_updates = []
        for i in range(self.zone_list_widget.count()):
            item = self.zone_list_widget.item(i)
            widget = self.zone_list_widget.itemWidget(item)
            if isinstance(widget, SPSZoneWidget):
                visible_updates.append(widget.to_binding())

        if self.visible_zone_keys is None:
            self.bindings = self.normalize_bindings(visible_updates)
            return

        preserved = [
            binding
            for binding in self.bindings
            if (binding.get("kind"), SPSProcessor.normalize_zone_id(binding.get("zone_id", ""))) not in self.visible_zone_keys
        ]
        self.bindings = self.normalize_bindings(preserved + visible_updates)

    def get_bindings(self):
        self.sync_ui_to_model()
        return self.bindings

    def update_ui_texts(self):
        self.refresh_button.setText(_("sps_tab.refresh"))
        if not self.bindings:
            self.status_label.setText(_("sps_tab.not_scanned"))
        for i in range(self.zone_list_widget.count()):
            widget = self.zone_list_widget.itemWidget(self.zone_list_widget.item(i))
            if isinstance(widget, SPSZoneWidget):
                widget.update_ui_texts()


class SPSZoneWidget(QWidget):
    changed = Signal()

    def __init__(self, binding: dict):
        super().__init__()
        self.binding = binding
        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.header_layout = QHBoxLayout()
        self.layout.addLayout(self.header_layout)

        self.kind_combo = QComboBox()
        self.kind_combo.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.kind_combo.addItem("Socket / Orf", "Orf")
        self.kind_combo.addItem("Plug / Pen", "Pen")
        index = self.kind_combo.findData(binding.get("kind", "Orf"))
        self.kind_combo.setCurrentIndex(max(0, index))
        self.kind_combo.setEnabled(False)
        self.header_layout.addWidget(self.kind_combo)

        self.zone_label = QLabel(str(binding.get("zone_id", "")))
        self.zone_label.setMinimumWidth(160)
        self.header_layout.addWidget(self.zone_label)

        self.channel_a_checkbox = QCheckBox("A")
        self.channel_a_checkbox.setChecked(bool(binding.get("channels", {}).get("A", False)))
        self.header_layout.addWidget(self.channel_a_checkbox)

        self.channel_b_checkbox = QCheckBox("B")
        self.channel_b_checkbox.setChecked(bool(binding.get("channels", {}).get("B", False)))
        self.header_layout.addWidget(self.channel_b_checkbox)
        self.header_layout.addStretch()

        self.a_range_row = RangeRowWidget(
            _("sps_tab.channel_range_a"),
            int(binding.get("min_strength", {}).get("A", 0)),
            int(binding.get("max_strength", {}).get("A", 100)),
        )
        self.layout.addWidget(self.a_range_row)

        self.b_range_row = RangeRowWidget(
            _("sps_tab.channel_range_b"),
            int(binding.get("min_strength", {}).get("B", 0)),
            int(binding.get("max_strength", {}).get("B", 100)),
        )
        self.layout.addWidget(self.b_range_row)

        self.sources_row = SourcesRowWidget(binding.get("kind", "Orf"), binding.get("sources", {}))
        self.layout.addWidget(self.sources_row)

        self.kind_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.channel_a_checkbox.stateChanged.connect(self.on_channel_changed)
        self.channel_b_checkbox.stateChanged.connect(self.on_channel_changed)
        self.a_range_row.changed.connect(lambda: self.changed.emit())
        self.b_range_row.changed.connect(lambda: self.changed.emit())
        self.sources_row.changed.connect(lambda: self.changed.emit())
        self.update_range_visibility()

    def to_binding(self):
        return {
            "kind": self.kind_combo.currentData(),
            "zone_id": self.zone_label.text(),
            "channels": {
                "A": self.channel_a_checkbox.isChecked(),
                "B": self.channel_b_checkbox.isChecked(),
            },
            "min_strength": {
                "A": self.a_range_row.min_value(),
                "B": self.b_range_row.min_value(),
            },
            "max_strength": {
                "A": self.a_range_row.max_value(),
                "B": self.b_range_row.max_value(),
            },
            "sources": self.sources_row.sources(),
        }

    def on_channel_changed(self):
        self.update_range_visibility()
        self.changed.emit()

    def update_range_visibility(self):
        self.a_range_row.setVisible(self.channel_a_checkbox.isChecked())
        self.b_range_row.setVisible(self.channel_b_checkbox.isChecked())

        if hasattr(self.layout, "invalidate") and hasattr(self.layout, "activate"):
            self.layout.invalidate()
            self.layout.activate()

        self.adjustSize()
        self.updateGeometry()
        self.refresh_list_item_size()

    def refresh_list_item_size(self):
        list_widget = self.find_list_widget_parent(self.parent())
        if not list_widget:
            return

        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item and list_widget.itemWidget(item) == self:
                current_size = self.sizeHint()
                if current_size.isValid():
                    item.setSizeHint(current_size)
                list_widget.doItemsLayout()
                list_widget.viewport().update()
                break

    def find_list_widget_parent(self, widget):
        if widget is None:
            return None
        if isinstance(widget, QListWidget):
            return widget
        return self.find_list_widget_parent(widget.parent())

    def update_ui_texts(self):
        self.a_range_row.set_title(_("sps_tab.channel_range_a"))
        self.b_range_row.set_title(_("sps_tab.channel_range_b"))
        self.sources_row.update_ui_texts()


class SourcesRowWidget(QWidget):
    changed = Signal()

    def __init__(self, kind: str, sources: dict):
        super().__init__()
        self.kind = kind
        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.title_label = QLabel()
        self.layout.addWidget(self.title_label)

        self.checkbox_layout = QHBoxLayout()
        self.layout.addLayout(self.checkbox_layout)

        self.checkboxes: dict[str, QCheckBox] = {}
        normalized_sources = SPSProcessor.normalize_sources(kind, sources)
        for key in self.source_keys():
            checkbox = QCheckBox()
            checkbox.setChecked(bool(normalized_sources.get(key, False)))
            checkbox.stateChanged.connect(lambda *_: self.changed.emit())
            self.checkbox_layout.addWidget(checkbox)
            self.checkboxes[key] = checkbox
        self.checkbox_layout.addStretch()
        self.update_ui_texts()

    def source_keys(self):
        return SOCKET_SOURCE_KEYS if self.kind == "Orf" else PLUG_SOURCE_KEYS

    def sources(self):
        return {key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()}

    def update_ui_texts(self):
        self.title_label.setText(_("sps_tab.sources") + ":")
        for key, checkbox in self.checkboxes.items():
            checkbox.setText(_(f"sps_tab.source_{key}"))


class RangeRowWidget(QWidget):
    changed = Signal()

    def __init__(self, title: str, min_value: int, max_value: int):
        super().__init__()
        self.layout = QHBoxLayout(self)
        self.setLayout(self.layout)

        self.title_label = QLabel(title + ":")
        self.title_label.setMinimumWidth(110)
        self.layout.addWidget(self.title_label)

        self.min_slider = QSlider()
        self.min_slider.setOrientation(Qt.Horizontal)
        self.min_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.min_slider.setRange(0, 100)
        self.min_slider.setValue(max(0, min(100, min_value)))
        self.min_slider.setFixedWidth(120)
        self.layout.addWidget(self.min_slider)

        self.min_label = QLabel()
        self.min_label.setMinimumWidth(70)
        self.layout.addWidget(self.min_label)

        self.max_slider = QSlider()
        self.max_slider.setOrientation(Qt.Horizontal)
        self.max_slider.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        self.max_slider.setRange(0, 100)
        self.max_slider.setValue(max(0, min(100, max_value)))
        self.max_slider.setFixedWidth(120)
        self.layout.addWidget(self.max_slider)

        self.max_label = QLabel()
        self.max_label.setMinimumWidth(70)
        self.layout.addWidget(self.max_label)
        self.layout.addStretch()

        self.min_slider.valueChanged.connect(self.on_min_changed)
        self.max_slider.valueChanged.connect(self.on_max_changed)
        self.update_labels()

    def set_title(self, title: str):
        self.title_label.setText(title + ":")
        self.update_labels()

    def min_value(self):
        return self.min_slider.value()

    def max_value(self):
        return self.max_slider.value()

    def on_min_changed(self, value):
        if value > self.max_slider.value():
            self.max_slider.setValue(value)
        self.update_labels()
        self.changed.emit()

    def on_max_changed(self, value):
        if value < self.min_slider.value():
            self.min_slider.setValue(value)
        self.update_labels()
        self.changed.emit()

    def update_labels(self):
        self.min_label.setText(f"{_('osc_tab.min_value')}: {self.min_slider.value()}%")
        self.max_label.setText(f"{_('osc_tab.max_value')}: {self.max_slider.value()}%")
