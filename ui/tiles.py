"""Live camera tile with painted telemetry, health words and tile actions."""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QMenu

from decision.models import ApproachMetrics
from ui.theme import MONO_FONT, Theme

STATE_TONE = {
    "LIVE": "status-ok",
    "STALE": "status-warn",
    "CONNECTING": "status-unknown",
    "DISCONNECTED": "status-critical-text",
}
STATE_ICON = {
    "LIVE": "✓",
    "STALE": "!",
    "CONNECTING": "•",
    "DISCONNECTED": "✕",
}


class CameraTile(QFrame):
    """A camera input styled like the reference tile, with honest status."""

    clicked = Signal(str)
    double_clicked = Signal(str)
    snapshot_requested = Signal(str)
    flip_requested = Signal(str)
    fullscreen_requested = Signal(str)
    overlays_requested = Signal(str, bool, bool)

    def __init__(
        self,
        camera_id: str,
        approach: str,
        source: str,
        *,
        theme: Theme,
        font_family: str = "Cascadia Mono",
    ) -> None:
        super().__init__()
        self.setObjectName("tile")
        self.camera_id = camera_id
        self.approach = approach
        self.source = source
        self.theme = theme
        self._mono = font_family
        self._image: Optional[QImage] = None
        self._state = "CONNECTING"
        self._fps = 0.0
        self._age: Optional[float] = None
        self._reconnects = 0
        self._metrics: Optional[ApproachMetrics] = None
        self._started = False
        self._focused = False
        self._overlays = True
        self._show_polygon = True
        self._show_line = True
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    # -- data --------------------------------------------------------------

    def set_frame(self, frame: np.ndarray) -> None:
        """Store a downscaled BGR copy as a QImage."""
        if frame is None or frame.size == 0:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        image = QImage(
            rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888
        ).copy()
        self._image = image
        self.update()

    def set_health(
        self,
        state: str,
        fps: float,
        frame_age_s: Optional[float],
        reconnects: int,
        metrics: Optional[ApproachMetrics] = None,
        *,
        started: bool = True,
    ) -> None:
        self._state = state
        self._fps = fps
        self._age = frame_age_s
        self._reconnects = reconnects
        self._metrics = metrics
        self._started = started
        self.update()

    def set_focused(self, focused: bool) -> None:
        self._focused = focused
        self.update()

    def set_overlays_visible(self, visible: bool) -> None:
        self._overlays = bool(visible)
        self.update()

    def set_overlay_state(self, polygon: bool, count_line: bool) -> None:
        self._show_polygon = bool(polygon)
        self._show_line = bool(count_line)

    def status_word(self) -> str:
        if not self._started:
            return "STOPPED"
        return self._state

    def has_frame(self) -> bool:
        return self._image is not None

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit(self.camera_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.camera_id)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.camera_id)
            return
        super().keyPressEvent(event)

    def _context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        polygon = menu.addAction("Show polygon")
        polygon.setCheckable(True)
        polygon.setChecked(self._show_polygon)
        line = menu.addAction("Show counting line")
        line.setCheckable(True)
        line.setChecked(self._show_line)
        menu.addSeparator()
        snapshot = menu.addAction("Snapshot this tile…")
        flip = menu.addAction("Flip horizontally")
        fullscreen = menu.addAction("Fullscreen")
        chosen = menu.exec(self.mapToGlobal(position))
        if chosen is None:
            return
        if chosen is polygon:
            self._show_polygon = polygon.isChecked()
            self.overlays_requested.emit(
                self.camera_id, self._show_polygon, self._show_line
            )
        elif chosen is line:
            self._show_line = line.isChecked()
            self.overlays_requested.emit(
                self.camera_id, self._show_polygon, self._show_line
            )
        elif chosen is snapshot:
            self.snapshot_requested.emit(self.camera_id)
        elif chosen is flip:
            self.flip_requested.emit(self.camera_id)
        elif chosen is fullscreen:
            self.fullscreen_requested.emit(self.camera_id)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(self.theme.tokens["bg-field"]))

        if self._image is not None:
            scaled = self._image.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
            if self.status_word() != "LIVE":
                painter.fillRect(rect, QColor(self.theme.tokens["tile-dim"]))
        else:
            painter.fillRect(rect, QColor(self.theme.tokens["bg-field"]))

        if self.status_word() != "LIVE" or self._image is None:
            self._paint_no_signal(painter, rect)

        self._paint_name_strip(painter, rect)
        self._paint_health(painter, rect)
        if self._overlays:
            self._paint_telemetry(painter, rect)

        border = QColor(self.theme.tokens["border"])
        painter.setPen(QPen(border, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self._focused:
            painter.setPen(QPen(QColor(self.theme.tokens["focus-ring"]), 2))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))

    def _paint_name_strip(self, painter: QPainter, rect: QRect) -> None:
        text = f"{self.approach}   {self.camera_id}"
        font = QFont(self._mono)
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        strip = QRect(8, 8, width, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.tokens["overlay-panel"]))
        painter.drawRoundedRect(strip, 4, 4)
        painter.setPen(QColor(self.theme.tokens["text-primary"]))
        painter.drawText(strip.adjusted(8, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_health(self, painter: QPainter, rect: QRect) -> None:
        word = self.status_word()
        tone = STATE_TONE.get(word)
        if word == "STOPPED":
            tone = "text-secondary"
        icon = STATE_ICON.get(word, "•")
        text = f"{icon} {word}"
        font = QFont(self._mono)
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        badge = QRect(rect.width() - width - 8, 8, width, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.tokens["overlay-panel"]))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor(self.theme.tokens[tone or "text-secondary"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_no_signal(self, painter: QPainter, rect: QRect) -> None:
        word = self.status_word()
        if word == "LIVE":
            return
        if word == "CONNECTING" and self._image is None and self._started:
            headline = "CONNECTING"
        elif word == "CONNECTING":
            headline = "STOPPED"
        elif word == "DISCONNECTED":
            headline = "NO SIGNAL"
        elif word == "STALE":
            headline = "NO SIGNAL"
        else:
            headline = word

        age = "no frame yet" if self._age is None else f"last frame {self._age:.1f} s ago"
        detail = f"{word} · {age}"
        if word == "STOPPED":
            detail = "Press START to begin analysis"

        font = QFont()
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self.theme.tokens["text-primary"]))
        painter.drawText(
            rect.adjusted(0, 0, 0, 14),
            Qt.AlignmentFlag.AlignCenter,
            headline,
        )
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(self.theme.tokens["text-secondary"]))
        painter.drawText(
            rect.adjusted(0, 28, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            detail,
        )

    def _paint_telemetry(self, painter: QPainter, rect: QRect) -> None:
        metrics = self._metrics
        left = 10
        right = rect.width() - 10
        top = 42

        vehicles = metrics.vehicle_count_in_region if metrics else 0
        stationary = metrics.stationary_count if metrics else 0
        congestion = metrics.congestion_level if metrics else "unknown"
        self._text(painter, left, top, f"VEH {vehicles}   STOP {stationary}")
        self._text(painter, left, top + 15, f"CONGESTION {congestion.upper()}")

        age = "n/a" if self._age is None else f"{self._age:.1f}s"
        resolution = (
            f"{self._image.width()}x{self._image.height()}"
            if self._image is not None
            else "no frame"
        )
        self._text(painter, right, top, f"{self._fps:.1f} fps", align_right=True)
        self._text(painter, right, top + 15, f"AGE {age}   {resolution}", align_right=True)

        bottom = rect.height() - 12
        if self.source == "file" and metrics is not None:
            source_text = f"FILE FRAME {metrics.frame_index}"
        else:
            source_text = self.source.upper()
        self._text(painter, left, bottom, source_text)
        latency = f"{metrics.latency_s * 1000:.0f} ms" if metrics else "n/a"
        self._text(painter, right, bottom, f"PIPELINE {latency}", align_right=True)

    def _text(
        self,
        painter: QPainter,
        x: int,
        baseline: int,
        text: str,
        *,
        align_right: bool = False,
    ) -> None:
        font = QFont(self._mono)
        font.setPixelSize(10)
        painter.setFont(font)
        if align_right:
            x -= painter.fontMetrics().horizontalAdvance(text)
        painter.setPen(QColor(self.theme.tokens["text-outline"]))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(x + dx, baseline + dy, text)
        painter.setPen(QColor(self.theme.tokens["overlay-text"]))
        painter.drawText(x, baseline, text)
