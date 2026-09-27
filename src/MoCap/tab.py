"""Live SteamVR motion capture sensor page."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import math
import threading
import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import translate as _
from .names import load_names, save_names
from .sampling import LatestSensorFrame, sample_timeout
from .rules import (
    BOUNDS,
    CALCULATION_FREQUENCY_MAX_HZ,
    CALCULATION_FREQUENCY_MIN_HZ,
    METRICS,
    MotionRuleEvaluator,
    MotionTrigger,
    default_config,
    default_sensor_rules,
    load_config,
    save_config,
)
from .steamvr import PoseVelocityEstimator, SensorSnapshot, read_sensors, register_steamvr_manifest
from .view3d import SensorView3D


logger = logging.getLogger(__name__)


class ElidedLabel(QLabel):
    """Show a single-line summary and keep its full text in the tooltip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)

    def setText(self, text: str):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._refresh_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self):
        width = max(0, self.contentsRect().width())
        visible_text = QFontMetrics(self.font()).elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width
        )
        QLabel.setText(self, visible_text)


class SensorTableDelegate(QStyledItemDelegate):
    """Keep the name editor at a stable size while table text may shrink to fit."""

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if index.column() == 1 and isinstance(editor, QLineEdit):
            font = self.parent().font()
            font.setPointSizeF(font.pointSizeF() + 2)
            editor.setFont(font)
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return editor


class SteamVRWorker(QThread):
    sensors_updated = Signal()
    state_changed = Signal(str)
    failed = Signal(str)

    def __init__(self, parent, calculation_frequency_hz: int):
        super().__init__(parent)
        self._calculation_frequency_hz = calculation_frequency_hz
        self._poll_wake = threading.Event()
        self.frames = LatestSensorFrame()

    def set_calculation_frequency(self, frequency_hz: int):
        self._calculation_frequency_hz = frequency_hz
        self._poll_wake.set()

    def stop(self):
        self.requestInterruption()
        self._poll_wake.set()

    def run(self):
        vr_system = None
        try:
            try:
                import openvr
            except ImportError as exc:
                raise RuntimeError("未安装 openvr，请先安装项目依赖。") from exc

            if not openvr.isRuntimeInstalled():
                raise RuntimeError("未检测到 SteamVR Runtime。")
            if register_steamvr_manifest():
                self.state_changed.emit("restart")
                return
            if self.isInterruptionRequested():
                return

            vr_system = openvr.init(openvr.VRApplication_Background)
            self.state_changed.emit("connected")
            velocity_estimator = PoseVelocityEstimator()
            event = openvr.VREvent_t()
            quit_events = {
                getattr(openvr, "VREvent_Quit", -1),
                getattr(openvr, "VREvent_ProcessQuit", -1),
                getattr(openvr, "VREvent_DriverRequestedQuit", -1),
            }
            while not self.isInterruptionRequested():
                started_at = time.monotonic()
                while vr_system.pollNextEvent(event):
                    if int(event.eventType) in quit_events:
                        raise RuntimeError("SteamVR 已退出，请重新启动后再连接。")
                sensors = read_sensors(
                    openvr, vr_system, estimator=velocity_estimator, sampled_at=started_at
                )
                if self.frames.publish(sensors, started_at):
                    self.sensors_updated.emit()
                period = 1.0 / self._calculation_frequency_hz
                remaining = period - (time.monotonic() - started_at)
                self._poll_wake.wait(max(0.001, remaining))
                self._poll_wake.clear()
        except Exception as exc:
            logger.exception("SteamVR motion capture failed")
            self.failed.emit(str(exc))
        finally:
            if vr_system is not None:
                try:
                    openvr.shutdown()
                except Exception:
                    logger.exception("Unable to close OpenVR")


class MotionCaptureTab(QWidget):
    COLUMNS = (
        "index", "name", "speed", "speed_rules", "angular_speed", "angular_speed_rules"
    )

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.worker: SteamVRWorker | None = None
        self._state = "disconnected"
        self._error = ""
        self._names_error = ""
        self._rules_error = ""
        self._sensors: list[SensorSnapshot] = []
        self._row_keys: tuple[tuple[str, int], ...] = ()
        self._updating_table = False
        self._loading_rule_controls = False
        self._selected_serial: str | None = None
        self.inline_rule_controls: dict[
            str, dict[str, dict[str, tuple[QDoubleSpinBox, QComboBox, QCheckBox]]]
        ] = {metric: {} for metric in METRICS}
        self.inline_rule_labels: dict[str, dict[str, dict[str, QLabel]]] = {
            metric: {} for metric in METRICS
        }
        self.rule_evaluator = MotionRuleEvaluator()
        self._rule_generation = 0
        self._timed_pending_keys = set()
        self._last_sampled_at: float | None = None
        self._sample_watchdog = QTimer(self)
        self._sample_watchdog.setInterval(100)
        self._sample_watchdog.timeout.connect(self._expire_stale_sample)
        try:
            self.names = load_names()
        except Exception as exc:
            logger.exception("Unable to load sensor names")
            self.names = {}
            self._names_error = str(exc)
        try:
            self.rule_config = load_config()
        except Exception as exc:
            logger.exception("Unable to load motion rules")
            self.rule_config = default_config()
            self._rules_error = str(exc)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.connect_button = QPushButton()
        self.connect_button.setStyleSheet("""
            QPushButton {
                background-color: #DDF2FF;
                color: #174A68;
                border: 1px solid #91C8E8;
                border-radius: 5px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #CDEAFF;
                border-color: #75B8DF;
            }
            QPushButton:pressed {
                background-color: #B9DFF5;
            }
            QPushButton:disabled {
                background-color: #EAF4FA;
                color: #8298A5;
                border-color: #C6DCE8;
            }
        """)
        self.connect_button.clicked.connect(self.connect_steamvr)
        controls.addWidget(self.connect_button)
        self.disconnect_button = QPushButton()
        self.disconnect_button.clicked.connect(self.disconnect_steamvr)
        controls.addWidget(self.disconnect_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        controls.addWidget(self.status_label, 1)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sensor_view = SensorView3D()
        splitter.addWidget(self.sensor_view)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        durations = QHBoxLayout()
        self.low_seconds_label = QLabel()
        durations.addWidget(self.low_seconds_label)
        self.low_seconds_spin = QDoubleSpinBox()
        self.low_seconds_spin.setRange(0.5, 60.0)
        self.low_seconds_spin.setDecimals(1)
        self.low_seconds_spin.setSingleStep(0.5)
        self.low_seconds_spin.setValue(self.rule_config["low_seconds"])
        durations.addWidget(self.low_seconds_spin)
        self.high_seconds_label = QLabel()
        durations.addWidget(self.high_seconds_label)
        self.high_seconds_spin = QDoubleSpinBox()
        self.high_seconds_spin.setRange(0.5, 60.0)
        self.high_seconds_spin.setDecimals(1)
        self.high_seconds_spin.setSingleStep(0.5)
        self.high_seconds_spin.setValue(self.rule_config["high_seconds"])
        durations.addWidget(self.high_seconds_spin)
        durations.addStretch()
        right_layout.addLayout(durations)
        self.low_seconds_spin.valueChanged.connect(self._duration_changed)
        self.high_seconds_spin.valueChanged.connect(self._duration_changed)
        fire_controls = QHBoxLayout()
        self.fire_seconds_label = QLabel()
        fire_controls.addWidget(self.fire_seconds_label)
        self.fire_seconds_spin = QDoubleSpinBox()
        self.fire_seconds_spin.setRange(0.5, 60.0)
        self.fire_seconds_spin.setDecimals(1)
        self.fire_seconds_spin.setSingleStep(0.5)
        self.fire_seconds_spin.setValue(self.rule_config["fire_seconds"])
        fire_controls.addWidget(self.fire_seconds_spin)
        self.continuous_fire_checkbox = QCheckBox()
        self.continuous_fire_checkbox.setChecked(self.rule_config["continuous_fire"])
        self.fire_seconds_spin.setEnabled(not self.rule_config["continuous_fire"])
        fire_controls.addWidget(self.continuous_fire_checkbox)
        fire_controls.addStretch()
        right_layout.addLayout(fire_controls)
        self.fire_seconds_spin.valueChanged.connect(self._fire_setting_changed)
        self.continuous_fire_checkbox.toggled.connect(self._fire_setting_changed)
        calculation_controls = QHBoxLayout()
        self.calculation_frequency_label = QLabel()
        calculation_controls.addWidget(self.calculation_frequency_label)
        self.calculation_frequency_spin = QSpinBox()
        self.calculation_frequency_spin.setRange(
            CALCULATION_FREQUENCY_MIN_HZ, CALCULATION_FREQUENCY_MAX_HZ
        )
        self.calculation_frequency_spin.setValue(self.rule_config["calculation_frequency_hz"])
        calculation_controls.addWidget(self.calculation_frequency_spin)
        calculation_controls.addStretch()
        right_layout.addLayout(calculation_controls)
        self.calculation_frequency_spin.valueChanged.connect(self._calculation_frequency_changed)
        self.count_label = QLabel()
        self.trigger_status_label = ElidedLabel()
        self.trigger_status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.count_label)
        summary_row.addWidget(self.trigger_status_label, 1)
        right_layout.addLayout(summary_row)
        self.sensor_table = QTableWidget(0, len(self.COLUMNS))
        self.sensor_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.sensor_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.sensor_table.setAlternatingRowColors(True)
        self.sensor_table.verticalHeader().setVisible(False)
        header = self.sensor_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, header.fontMetrics().horizontalAdvance("Index") + 18)
        name_font = self.sensor_table.font()
        name_font.setPointSizeF(name_font.pointSizeF() + 2)
        header.resizeSection(1, QFontMetrics(name_font).horizontalAdvance("中" * 10) + 18)
        self.sensor_table.setItemDelegate(SensorTableDelegate(self.sensor_table))
        self.sensor_table.itemChanged.connect(self._name_changed)
        self.sensor_table.currentCellChanged.connect(self._selection_changed)
        right_layout.addWidget(self.sensor_table, 1)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([440, 980])
        layout.addWidget(splitter, 1)
        self.update_ui_texts()

    def connect_steamvr(self):
        if self.worker is not None:
            return
        self._state = "connecting"
        self._error = ""
        self._refresh_controls()
        worker = SteamVRWorker(self, self.rule_config["calculation_frequency_hz"])
        self.worker = worker
        worker.sensors_updated.connect(self._consume_sensor_frame)
        worker.state_changed.connect(self._set_state)
        worker.failed.connect(self._show_error)
        worker.finished.connect(self._worker_finished)
        worker.start()
        self._sample_watchdog.start()

    def disconnect_steamvr(self):
        worker = self.worker
        if worker is not None:
            worker.stop()
            worker.wait()
        self._state = "disconnected"
        self._error = ""
        self._clear_sensors()
        self._refresh_controls()

    def shutdown(self):
        self.disconnect_steamvr()

    def _set_state(self, state: str):
        self._state = state
        self._refresh_controls()

    def _show_error(self, error: str):
        self._state = "error"
        self._error = error
        self._clear_sensors()
        self._refresh_controls()

    def _worker_finished(self):
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        worker.deleteLater()
        if self._state in ("connected", "connecting"):
            self._state = "disconnected"
            self._clear_sensors()
        self._refresh_controls()

    def _clear_sensors(self):
        self._sample_watchdog.stop()
        self._last_sampled_at = None
        controller = getattr(self.main_window, "controller", None)
        if controller is not None and hasattr(controller, "cancel_mocap_triggers"):
            controller.cancel_mocap_triggers()
        self._sensors = []
        self._row_keys = ()
        self._selected_serial = None
        self._reset_rule_evaluator()
        for metric in METRICS:
            self.inline_rule_controls[metric].clear()
            self.inline_rule_labels[metric].clear()
        self.sensor_table.setRowCount(0)
        self.sensor_view.set_sensors([], self.names)

    def _set_cell(self, row: int, column: int, text: str, editable: bool = False):
        item = self.sensor_table.item(row, column)
        created = item is None
        if item is None:
            item = QTableWidgetItem()
            self.sensor_table.setItem(row, column, item)
        if bool(item.flags() & Qt.ItemFlag.ItemIsEditable) != editable:
            flags = item.flags() | Qt.ItemFlag.ItemIsEditable if editable else item.flags() & ~Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
        changed = item.text() != text
        if changed:
            item.setText(text)
        if created or (column == 1 and changed):
            self._style_cell_item(item, column)

    def _style_cell_item(self, item: QTableWidgetItem, column: int):
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if column in (0, 1, 2, 4):
            base_font = self.sensor_table.font()
            font = QFont(base_font)
            font.setPointSizeF(base_font.pointSizeF() + 2)
            if column == 1 and item.text():
                available_width = max(1, self.sensor_table.columnWidth(1) - 18)
                text_width = QFontMetrics(font).horizontalAdvance(item.text())
                if text_width > available_width:
                    minimum_font = QFont(base_font)
                    minimum_text_width = QFontMetrics(minimum_font).horizontalAdvance(
                        "中" * 10
                    )
                    minimum_font_size = base_font.pointSizeF()
                    if minimum_text_width > available_width:
                        minimum_font_size *= available_width / minimum_text_width
                    fitted_size = font.pointSizeF() * available_width / text_width
                    font.setPointSizeF(
                        max(minimum_font_size, fitted_size)
                    )
            item.setFont(font)

    def _create_rule_cell(self, sensor: SensorSnapshot, metric: str) -> QWidget:
        cell = QWidget()
        grid = QGridLayout(cell)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(1)

        labels = {
            "threshold": QLabel(),
            "channel": QLabel(),
            "enabled": QLabel(),
            "low": QLabel(),
            "high": QLabel(),
        }
        labels["threshold"].setAlignment(Qt.AlignmentFlag.AlignCenter)
        labels["channel"].setAlignment(Qt.AlignmentFlag.AlignCenter)
        labels["enabled"].setAlignment(Qt.AlignmentFlag.AlignCenter)
        for key in ("threshold", "channel", "enabled"):
            grid.addWidget(labels[key], 0, {"threshold": 1, "channel": 2, "enabled": 3}[key])
        self.inline_rule_labels[metric][sensor.serial] = labels

        sensor_rules = self.rule_config.get("sensors", {}).get(
            sensor.serial, default_sensor_rules()
        )[metric]
        controls = {}
        for row, bound in enumerate(("high", "low"), start=1):
            labels[bound].setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(labels[bound], row, 0)

            threshold = QDoubleSpinBox()
            threshold.setRange(0.0, 20.0 if metric == "speed" else 2000.0)
            threshold.setDecimals(1)
            threshold.setSingleStep(0.5)
            threshold.setFixedWidth(76)
            threshold.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
            rule = sensor_rules[bound]
            threshold.setValue(rule["threshold"])
            grid.addWidget(threshold, row, 1)

            channel = QComboBox()
            channel.setMinimumWidth(62)
            channel.setMaximumWidth(72)
            channel.addItem("A", "A")
            channel.addItem("B", "B")
            channel.addItem(_("mocap_tab.both"), "both")
            channel.setCurrentIndex(max(0, channel.findData(rule["channel"])))
            channel.setEditable(True)
            channel.lineEdit().setReadOnly(True)
            channel.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(channel, row, 2)

            enabled = QCheckBox()
            enabled.setChecked(rule["enabled"])
            grid.addWidget(enabled, row, 3, Qt.AlignmentFlag.AlignCenter)
            controls[bound] = (threshold, channel, enabled)

            threshold.valueChanged.connect(
                lambda _value, serial=sensor.serial, m=metric, b=bound:
                    self._inline_rule_changed(serial, m, b)
            )
            channel.currentIndexChanged.connect(
                lambda _index, serial=sensor.serial, m=metric, b=bound:
                    self._inline_rule_changed(serial, m, b)
            )
            enabled.toggled.connect(
                lambda _checked, serial=sensor.serial, m=metric, b=bound:
                    self._inline_rule_changed(serial, m, b)
            )

        self.inline_rule_controls[metric][sensor.serial] = controls
        labels["threshold"].setText(_("mocap_tab.rule_headers.threshold"))
        labels["channel"].setText(_("mocap_tab.rule_headers.channel_low"))
        labels["enabled"].setText(_("mocap_tab.rule_headers.enabled_low"))
        labels["low"].setText(_("mocap_tab.rule_headers.low"))
        labels["high"].setText(_("mocap_tab.rule_headers.high"))
        if not sensor.serial or sensor.device_class == "BaseStation":
            cell.setEnabled(False)
        return cell

    def _inline_rule_changed(self, serial: str, metric: str, bound: str):
        controls = self.inline_rule_controls[metric].get(serial)
        if self._loading_rule_controls or not serial or controls is None:
            return
        threshold, channel, enabled = controls[bound]
        rule = self.rule_config["sensors"].setdefault(
            serial, default_sensor_rules()
        )[metric][bound]
        rule["threshold"] = threshold.value()
        rule["channel"] = channel.currentData() or "A"
        rule["enabled"] = enabled.isChecked()

        row = next(
            (i for i, key in enumerate(self._row_keys) if key[0] == serial), None
        )
        if row is not None:
            self.sensor_table.setCurrentCell(row, 3 if metric == "speed" else 5)
        self._reset_rule_evaluator()
        controller = getattr(self.main_window, "controller", None)
        if controller is not None and hasattr(controller, "cancel_mocap_triggers"):
            controller.cancel_mocap_triggers()
        self._save_rule_config()

    def _consume_sensor_frame(self):
        worker = self.sender()
        if worker is not self.worker or self._state != "connected":
            return
        frame = worker.frames.take()
        if frame is None:
            return
        timeout = sample_timeout(self.rule_config["calculation_frequency_hz"])
        if time.monotonic() - frame.sampled_at > timeout:
            self._expire_stale_sample(force=True)
            return
        if frame.interrupted or (
            self._last_sampled_at is not None
            and frame.sampled_at - self._last_sampled_at > timeout
        ):
            self._reset_rule_evaluator()
            controller = getattr(self.main_window, "controller", None)
            if controller is not None:
                controller.cancel_mocap_triggers()
        self._last_sampled_at = frame.sampled_at
        self._show_sensors(frame.sensors, sampled_at=frame.sampled_at)

    def _expire_stale_sample(self, force: bool = False):
        if not force and (
            self._last_sampled_at is None
            or time.monotonic() - self._last_sampled_at
            <= sample_timeout(self.rule_config["calculation_frequency_hz"])
        ):
            return
        self._last_sampled_at = None
        self._reset_rule_evaluator()
        controller = getattr(self.main_window, "controller", None)
        if controller is not None:
            controller.cancel_mocap_triggers()
        self._show_sensors([
            replace(sensor, tracking="NoPose", position=None, forward=None,
                    linear_velocity=None, angular_velocity=None,
                    speed_m_s=None, angular_speed_rad_s=None)
            for sensor in self._sensors
        ])

    def _show_sensors(self, sensors: list[SensorSnapshot], sampled_at: float | None = None):
        if self._state != "connected":
            return
        selected_serial = self._selected_serial
        self._sensors = sorted(sensors, key=lambda sensor: sensor.index)
        row_keys = tuple(
            (sensor.serial, sensor.index) for sensor in self._sensors
        )
        rows_changed = row_keys != self._row_keys
        self._updating_table = True
        try:
            if rows_changed:
                self._row_keys = row_keys
                for metric in METRICS:
                    self.inline_rule_controls[metric].clear()
                    self.inline_rule_labels[metric].clear()
                self.sensor_table.setRowCount(len(self._sensors))
                for row, sensor in enumerate(self._sensors):
                    self._set_cell(row, 0, str(sensor.index))
                    self._set_cell(row, 1, self.names.get(sensor.serial, ""), editable=bool(sensor.serial))
                    speed_rule_cell = self._create_rule_cell(sensor, "speed")
                    angular_rule_cell = self._create_rule_cell(sensor, "angular_speed")
                    self.sensor_table.setCellWidget(row, 3, speed_rule_cell)
                    self.sensor_table.setCellWidget(row, 5, angular_rule_cell)
                    self.sensor_table.setRowHeight(
                        row, max(64, speed_rule_cell.sizeHint().height() + 4,
                                 angular_rule_cell.sizeHint().height() + 4)
                    )
                selected_row = next(
                    (row for row, sensor in enumerate(self._sensors)
                     if sensor.serial == selected_serial),
                    0,
                )
                if self._sensors:
                    self._selected_serial = self._sensors[selected_row].serial
                    self.sensor_table.setCurrentCell(selected_row, 0)
                else:
                    self._selected_serial = None
            for row, sensor in enumerate(self._sensors):
                speed = "—" if sensor.speed_m_s is None else f"{sensor.speed_m_s:.1f}"
                angular_speed = (
                    "—" if sensor.angular_speed_rad_s is None
                    else f"{math.degrees(sensor.angular_speed_rad_s):.1f}"
                )
                self._set_cell(row, 2, speed)
                self._set_cell(row, 4, angular_speed)
        finally:
            self._updating_table = False
        self.sensor_view.set_sensors(self._sensors, self.names)
        controller = getattr(self.main_window, "controller", None)
        if (
            controller is None or not getattr(controller, "app_status_online", False)
            or controller.last_strength is None
        ):
            self._reset_rule_evaluator()
            if controller is not None:
                controller.cancel_mocap_triggers()
        else:
            continuous = self.rule_config["continuous_fire"]
            triggers = self.rule_evaluator.update(
                self._sensors, self.rule_config, now=sampled_at, repeat=continuous,
                max_sample_gap=sample_timeout(self.rule_config["calculation_frequency_hz"]),
            )
            controller.release_mocap_overrides(
                self.rule_evaluator.violating_channels(self.rule_config)
            )
            channels = set()
            for trigger in triggers:
                if trigger.channel in ("A", "both"):
                    channels.add("A")
                if trigger.channel in ("B", "both"):
                    channels.add("B")
            if continuous:
                controller.request_mocap_channels(channels)
            elif triggers:
                keys = {(item.serial, item.metric, item.bound) for item in triggers}
                self._timed_pending_keys.update(keys)
                asyncio.create_task(self._fire_timed_triggers(
                    controller, triggers, self.rule_config["fire_seconds"],
                    self._rule_generation,
                ))
        self._refresh_controls()

    def _reset_rule_evaluator(self):
        self.rule_evaluator.reset()
        self._rule_generation += 1
        self._timed_pending_keys.clear()

    async def _fire_timed_triggers(
        self, controller, triggers: list[MotionTrigger],
        fire_seconds: float, generation: int,
    ):
        if generation != self._rule_generation:
            return
        # A queued task must not start a burst for a rule that has lost tracking.
        pending_keys = {(trigger.serial, trigger.metric, trigger.bound) for trigger in triggers}
        triggers = [trigger for trigger in triggers if (
            self.rule_evaluator.is_current(trigger)
            and (state := self.rule_evaluator.state(trigger)) is not None and state[1]
        )]
        self._timed_pending_keys.difference_update(
            pending_keys - {(trigger.serial, trigger.metric, trigger.bound) for trigger in triggers}
        )
        if not triggers:
            return
        channels = set()
        for trigger in triggers:
            channels.update(("A", "B") if trigger.channel == "both" else (trigger.channel,))
        try:
            accepted = await controller.trigger_mocap_timed(
                channels, fire_seconds,
                is_valid=lambda: generation == self._rule_generation and all(
                    self.rule_evaluator.is_current(trigger) for trigger in triggers
                ),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Unable to start timed motion capture fire")
            accepted = set()
        if generation != self._rule_generation:
            return
        for trigger in triggers:
            key = (trigger.serial, trigger.metric, trigger.bound)
            if not self.rule_evaluator.is_current(trigger):
                if self.rule_evaluator.state(trigger) is None:
                    self._timed_pending_keys.discard(key)
                continue
            self._timed_pending_keys.discard(key)
            requested = {"A", "B"} if trigger.channel == "both" else {trigger.channel}
            if not requested.issubset(accepted) and not requested & controller.mocap_suppressed_channels:
                self.rule_evaluator.retry(trigger)
        self._refresh_controls()

    def _name_changed(self, item: QTableWidgetItem):
        if self._updating_table or item.column() != 1:
            return
        serial = self._row_keys[item.row()][0]
        if not serial:
            return
        name = item.text().strip()
        if name:
            self.names[serial] = name
        else:
            self.names.pop(serial, None)
        if item.text() != name:
            self._updating_table = True
            item.setText(name)
            self._updating_table = False
        self._style_cell_item(item, 1)
        try:
            save_names(self.names)
            self._names_error = ""
        except Exception as exc:
            logger.exception("Unable to save sensor names")
            self._names_error = str(exc)
        self.sensor_view.set_sensors(self._sensors, self.names)
        self._refresh_controls()

    def _selection_changed(self, row: int, _column: int, _previous_row: int, _previous_column: int):
        if self._updating_table:
            return
        self._selected_serial = self._row_keys[row][0] if 0 <= row < len(self._row_keys) else None
        self._refresh_controls()

    def _duration_changed(self):
        self.rule_config["low_seconds"] = self.low_seconds_spin.value()
        self.rule_config["high_seconds"] = self.high_seconds_spin.value()
        self._reset_rule_evaluator()
        controller = getattr(self.main_window, "controller", None)
        if controller is not None:
            controller.cancel_mocap_triggers()
        self._save_rule_config()

    def _fire_setting_changed(self):
        self.rule_config["fire_seconds"] = self.fire_seconds_spin.value()
        self.rule_config["continuous_fire"] = self.continuous_fire_checkbox.isChecked()
        self.fire_seconds_spin.setEnabled(not self.rule_config["continuous_fire"])
        self._reset_rule_evaluator()
        controller = getattr(self.main_window, "controller", None)
        if controller is not None:
            controller.cancel_mocap_triggers()
        self._save_rule_config()

    def _calculation_frequency_changed(self, frequency_hz: int):
        self.rule_config["calculation_frequency_hz"] = frequency_hz
        if self.worker is not None:
            self.worker.set_calculation_frequency(frequency_hz)
        self._reset_rule_evaluator()
        controller = getattr(self.main_window, "controller", None)
        if controller is not None:
            controller.cancel_mocap_triggers()
        self._save_rule_config()

    def _save_rule_config(self):
        try:
            save_config(self.rule_config)
            self._rules_error = ""
        except Exception as exc:
            logger.exception("Unable to save motion rules")
            self._rules_error = str(exc)
        self._refresh_controls()

    def _trigger_status_text(self) -> str:
        if self._state != "connected":
            return str(_("mocap_tab.trigger_disconnected"))
        sensor = next((item for item in self._sensors if item.serial == self._selected_serial), None)
        if sensor is None or not sensor.serial or sensor.device_class == "BaseStation":
            return str(_("mocap_tab.trigger_select_sensor"))
        rules = self.rule_config["sensors"].get(sensor.serial)
        if not rules:
            return str(_("mocap_tab.trigger_no_rules"))
        enabled = [
            (metric, bound, rules[metric][bound])
            for metric in METRICS for bound in BOUNDS
            if rules[metric][bound]["enabled"]
            and (bound != "high" or rules[metric][bound]["threshold"] > 0)
        ]
        if not enabled:
            return str(_("mocap_tab.trigger_no_rules"))

        controller = getattr(self.main_window, "controller", None)
        if controller is None or not getattr(controller, "app_status_online", False) or controller.last_strength is None:
            return str(_("mocap_tab.trigger_no_device"))
        if sensor.tracking != "OK":
            return str(_("mocap_tab.trigger_no_tracking")).format(tracking=sensor.tracking)
        if controller.fire_mode_strength_step <= 0:
            return str(_("mocap_tab.trigger_zero_step"))
        if controller.fire_mode_active:
            return str(_("mocap_tab.trigger_panel_fire"))

        values = {
            "speed": sensor.speed_m_s,
            "angular_speed": math.degrees(sensor.angular_speed_rad_s)
            if sensor.angular_speed_rad_s is not None else None,
        }
        if all(values[metric] is None for metric, _bound, _rule in enabled):
            return str(_("mocap_tab.trigger_no_motion_data"))
        violating = []
        for metric, bound, rule in enabled:
            value = values[metric]
            if value is None:
                continue
            violated = value < rule["threshold"] if bound == "low" else value > rule["threshold"]
            if violated:
                violating.append((metric, bound, rule))
        if not violating:
            return str(_("mocap_tab.trigger_ready"))

        for metric, bound, rule in violating:
            trigger = MotionTrigger(sensor.serial, metric, bound, rule["channel"])
            state = self.rule_evaluator.state(trigger)
            if state is None or not state[1]:
                continue
            requested_for_rule = {"A", "B"} if rule["channel"] == "both" else {rule["channel"]}
            label = str(_(f"mocap_tab.metrics.{metric}")) + " " + str(_(f"mocap_tab.rule_headers.{bound}"))
            if requested_for_rule.issubset(controller.mocap_active_channels):
                key = "trigger_firing" if self.rule_config["continuous_fire"] else "trigger_timed_firing"
                return str(_(f"mocap_tab.{key}")).format(rule=label)
            if requested_for_rule & controller.mocap_suppressed_channels:
                return str(_("mocap_tab.trigger_manual_override"))
            key = (sensor.serial, metric, bound)
            if not self.rule_config["continuous_fire"]:
                if key in self._timed_pending_keys:
                    return str(_("mocap_tab.trigger_starting")).format(rule=label)
                return str(_("mocap_tab.trigger_timed_done")).format(rule=label)

        requested = set()
        for _metric, _bound, rule in violating:
            requested.update(("A", "B") if rule["channel"] == "both" else (rule["channel"],))
        available = set()
        for name in ("A", "B"):
            if name not in requested:
                continue
            strength = controller.last_strength.a if name == "A" else controller.last_strength.b
            limit = controller.last_strength.a_limit if name == "A" else controller.last_strength.b_limit
            if limit > strength:
                available.add(name)
        if not available:
            return str(_("mocap_tab.trigger_at_limit")).format(channels="/".join(sorted(requested)))

        for metric, bound, rule in violating:
            trigger = MotionTrigger(sensor.serial, metric, bound, rule["channel"])
            state = self.rule_evaluator.state(trigger)
            label = str(_(f"mocap_tab.metrics.{metric}")) + " " + str(_(f"mocap_tab.rule_headers.{bound}"))
            if state is not None and state[1]:
                return str(_("mocap_tab.trigger_starting")).format(rule=label)
            duration = self.rule_config[f"{bound}_seconds"]
            elapsed = min(state[0], duration) if state is not None else 0.0
            return str(_("mocap_tab.trigger_counting")).format(
                rule=label, elapsed=elapsed, duration=duration
            )
        return str(_("mocap_tab.trigger_ready"))

    def _refresh_controls(self):
        self.connect_button.setEnabled(self.worker is None)
        self.disconnect_button.setEnabled(self.worker is not None)
        if self._state == "error":
            self.status_label.setText(str(_("mocap_tab.error")).format(error=self._error))
        elif self._names_error:
            self.status_label.setText(str(_("mocap_tab.names_error")).format(error=self._names_error))
        elif self._rules_error:
            self.status_label.setText(str(_("mocap_tab.rules_error")).format(error=self._rules_error))
        else:
            self.status_label.setText(_(f"mocap_tab.{self._state}"))
        self.count_label.setText(
            str(_("mocap_tab.sensor_count")).format(count=self.sensor_table.rowCount())
        )
        self.trigger_status_label.setText(self._trigger_status_text())

    def update_ui_texts(self):
        self.connect_button.setText(_("mocap_tab.connect"))
        self.disconnect_button.setText(_("mocap_tab.disconnect"))
        self.low_seconds_label.setText(_("mocap_tab.low_seconds"))
        self.high_seconds_label.setText(_("mocap_tab.high_seconds"))
        self.fire_seconds_label.setText(_("mocap_tab.fire_seconds"))
        self.continuous_fire_checkbox.setText(_("mocap_tab.continuous_fire"))
        self.calculation_frequency_label.setText(_("mocap_tab.calculation_frequency"))
        calculation_hint = str(_("mocap_tab.calculation_frequency_hint"))
        self.calculation_frequency_label.setToolTip(calculation_hint)
        self.calculation_frequency_spin.setToolTip(calculation_hint)
        self._loading_rule_controls = True
        try:
            for metric in METRICS:
                for labels in self.inline_rule_labels[metric].values():
                    labels["threshold"].setText(_("mocap_tab.rule_headers.threshold"))
                    labels["channel"].setText(_("mocap_tab.rule_headers.channel_low"))
                    labels["enabled"].setText(_("mocap_tab.rule_headers.enabled_low"))
                    labels["low"].setText(_("mocap_tab.rule_headers.low"))
                    labels["high"].setText(_("mocap_tab.rule_headers.high"))
                for sensor_controls in self.inline_rule_controls[metric].values():
                    for _threshold, channel, _enabled in sensor_controls.values():
                        selected = channel.currentData() or "A"
                        channel.clear()
                        channel.addItem("A", "A")
                        channel.addItem("B", "B")
                        channel.addItem(_("mocap_tab.both"), "both")
                        channel.setCurrentIndex(max(0, channel.findData(selected)))
        finally:
            self._loading_rule_controls = False
        self.sensor_table.setHorizontalHeaderLabels(
            [_(f"mocap_tab.columns.{column}") for column in self.COLUMNS]
        )
        self.sensor_view.update_ui_texts()
        self._refresh_controls()
