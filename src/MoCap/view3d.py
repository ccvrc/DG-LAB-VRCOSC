"""Lightweight orbit view of SteamVR Standing-space sensor poses."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from i18n import translate as _
from .steamvr import SensorSnapshot


SENSOR_COLORS = {
    "HMD": QColor("#55c7ff"),
    "Controller": QColor("#ffb65e"),
    "Tracker": QColor("#74db96"),
    "BaseStation": QColor("#c6a2ff"),
}


class SensorView3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 230)
        self._sensors: list[SensorSnapshot] = []
        self._names: dict[str, str] = {}
        self._yaw = math.radians(35)
        self._pitch = math.radians(25)
        self._zoom = 1.0
        self._view_target = (0.0, 1.0, 0.0)
        self._last_pointer = None
        self.update_ui_texts()

    def update_ui_texts(self):
        self._hint = str(_("mocap_tab.view_hint"))
        self._no_poses = str(_("mocap_tab.no_poses"))
        self.update()

    def set_sensors(self, sensors: list[SensorSnapshot], names: dict[str, str]):
        self._sensors = sensors
        self._names = names.copy()
        hmd = next(
            (sensor for sensor in sensors
             if sensor.device_class == "HMD" and sensor.position is not None),
            None,
        )
        self._view_target = hmd.position if hmd is not None else (0.0, 1.0, 0.0)
        self.update()

    def _project(self, point: tuple[float, float, float]) -> tuple[QPointF, float]:
        x, y, z = point
        target_x, target_y, target_z = self._view_target
        relative_x, relative_y, relative_z = x - target_x, y - target_y, z - target_z
        horizontal = math.cos(self._yaw) * relative_x - math.sin(self._yaw) * relative_z
        depth = math.sin(self._yaw) * relative_x + math.cos(self._yaw) * relative_z
        vertical = math.cos(self._pitch) * relative_y + math.sin(self._pitch) * depth
        scale = min(self.width() / 7.5, self.height() / 5.5) * self._zoom
        return (
            QPointF(self.width() * 0.5 + scale * horizontal, self.height() / 3 - scale * vertical),
            math.cos(self._pitch) * depth - math.sin(self._pitch) * relative_y,
        )

    def _line(self, painter: QPainter, start, end, color: QColor, width: float = 1.0):
        painter.setPen(QPen(color, width))
        painter.drawLine(self._project(start)[0], self._project(end)[0])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#191e28"))

        grid_color = QColor("#394251")
        target_x, _target_y, target_z = self._view_target
        for coordinate in range(-3, 4):
            self._line(
                painter, (target_x + coordinate, 0, target_z - 3),
                (target_x + coordinate, 0, target_z + 3), grid_color,
            )
            self._line(
                painter, (target_x - 3, 0, target_z + coordinate),
                (target_x + 3, 0, target_z + coordinate), grid_color,
            )

        visible = [sensor for sensor in self._sensors if sensor.position is not None]
        visible.sort(key=lambda sensor: self._project(sensor.position)[1], reverse=True)
        painter.setFont(self.font())
        for sensor in visible:
            center = self._project(sensor.position)[0]
            color = SENSOR_COLORS.get(sensor.device_class, QColor("#eeeeee"))
            painter.setPen(QPen(QColor("#f6f8fb"), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(center, 6.0, 6.0)
            painter.setPen(QColor("#f6f8fb"))
            label = self._names.get(sensor.serial) or (
                "HMD" if sensor.device_class == "HMD" else str(sensor.index)
            )
            painter.drawText(center + QPointF(10, -8), label)

        painter.setPen(QColor("#bac4d1"))
        painter.drawText(12, 22, self._hint)
        if not visible:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._no_poses)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_pointer = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._last_pointer is not None:
            delta = event.position() - self._last_pointer
            self._yaw += delta.x() * 0.01
            self._pitch = max(math.radians(-75), min(math.radians(75), self._pitch - delta.y() * 0.01))
            self._last_pointer = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_pointer = None
            self.unsetCursor()

    def wheelEvent(self, event):
        self._zoom = max(0.35, min(3.0, self._zoom * 1.15 ** (event.angleDelta().y() / 120)))
        self.update()
