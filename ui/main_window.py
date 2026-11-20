"""The operator console shell.

The window only reads `AppController.snapshot()` and calls controller commands.
It never imports the core packages directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
from PySide6.QtCore import Qt, QSettings, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.controller import AppController
from app.exporters import config_yaml
from app.snapshots import UiSnapshot
from ui.dialogs import about_text, confirm
from ui.schematic import SchematicTile
from ui.theme import DARK, THEMES, stylesheet
from ui.tiles import CameraTile
from ui.widgets import (
    CollapsibleSection,
    StatusChip,
    format_uptime,
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: AppController,
        *,
        settings: QSettings | None = None,
        theme_name: str | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.settings = settings or QSettings("Smarti", "AIHost")
        stored_theme = theme_name or str(self.settings.value("theme", "dark"))
        self.theme = THEMES.get(stored_theme, DARK)
        self._snapshot: UiSnapshot | None = None
        self._focused_camera: str | None = None
        self._focus_mode = False
        self._playback_paused = False
        self._snapshot_warned = False
        self._tiles: dict[str, CameraTile] = {}
        self._flip_state = {
            camera.id: camera.flip for camera in controller.config.cameras
        }

        self.setWindowTitle("Smarti AI Host — Traffic Control Console")
        self.setMinimumSize(1280, 720)
        self.resize(1440, 900)

        self._build_actions()
        self._build_ui()
        self._apply_theme()
        self._restore_layout()

        self._panel_timer = QTimer(self)
        self._panel_timer.setInterval(250)
        self._panel_timer.timeout.connect(self._refresh_panels)
        self._panel_timer.start()

        self._video_timer = QTimer(self)
        self._video_timer.setInterval(100)
        self._video_timer.timeout.connect(self._refresh_video)
        self._video_timer.start()

        self._refresh_status()

    # -- construction ------------------------------------------------------

    def _build_actions(self) -> None:
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.exit_action.triggered.connect(self.close)

        self.start_action = QAction("Start / Stop", self)
        self.start_action.setShortcut(QKeySequence("F5"))
        self.start_action.triggered.connect(self._toggle_started)

        self.focus_action = QAction("Focus mode", self)
        self.focus_action.setCheckable(True)
        self.focus_action.setShortcut(QKeySequence("F11"))
        self.focus_action.toggled.connect(self._set_focus_mode)

        self.overlays_action = QAction("Show tile overlays", self)
        self.overlays_action.setCheckable(True)
        self.overlays_action.setChecked(True)
        self.overlays_action.toggled.connect(self._toggle_overlays)

        self.logs_action = QAction("Show log panel", self)
        self.logs_action.setCheckable(True)
        self.logs_action.setChecked(True)
        self.logs_action.toggled.connect(self._set_logs_visible)

        self.theme_action = QAction("Light theme", self)
        self.theme_action.setCheckable(True)
        self.theme_action.setChecked(self.theme.name == "light")
        self.theme_action.toggled.connect(self._theme_toggled)

        self.open_logs_action = QAction("Open logs folder", self)
        self.open_logs_action.triggered.connect(self._open_logs_folder)

        self.open_config_action = QAction("View configuration…", self)
        self.open_config_action.triggered.connect(self._open_config)

        self.about_action = QAction("About and limits", self)
        self.about_action.triggered.connect(self._show_about)

        self.camera_actions: dict[str, QAction] = {}
        for camera in self.controller.config.cameras:
            action = QAction(f"Focus {camera.approach} ({camera.id})", self)
            action.triggered.connect(
                lambda _checked=False, camera_id=camera.id: self._focus_camera(camera_id)
            )
            self.camera_actions[camera.id] = action

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_title_bar())
        self._build_menus()

        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_video_area())
        self.splitter.addWidget(self._build_sidebar())
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([1120, 320])
        outer.addWidget(self.splitter, 1)

        self.setCentralWidget(central)
        self._build_status_bar()

    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(38)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)
        icon = QLabel("S")
        icon.setObjectName("appIcon")
        title = QLabel("SMARTI AI HOST — TRAFFIC CONTROL CONSOLE")
        title.setObjectName("appTitle")
        layout.addWidget(icon)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addStretch(1)
        spacer = QLabel("  ")
        spacer.setFixedWidth(icon.sizeHint().width())
        layout.addWidget(spacer)
        return bar

    def _build_menus(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("File")
        file_menu.addAction(self.start_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu = menu.addMenu("View")
        view_menu.addAction(self.focus_action)
        view_menu.addAction(self.overlays_action)
        view_menu.addAction(self.theme_action)
        view_menu.addSeparator()
        view_menu.addAction(self.logs_action)

        cameras_menu = menu.addMenu("Cameras")
        for action in self.camera_actions.values():
            cameras_menu.addAction(action)

        log_menu = menu.addMenu("Log")
        log_menu.addAction(self.open_logs_action)

        settings_menu = menu.addMenu("Settings")
        settings_menu.addAction(self.open_config_action)
        settings_menu.addAction(self.theme_action)

        help_menu = menu.addMenu("Help")
        help_menu.addAction(self.about_action)

        self.mode_badge = QLabel("COMPAT TWO PHASE")
        self.mode_badge.setObjectName("modeBadge")
        menu.setCornerWidget(self.mode_badge, Qt.Corner.TopRightCorner)

    def _build_video_area(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(2)
        for column in range(2):
            self.grid_layout.setColumnStretch(column, 1)
        for row in range(2):
            self.grid_layout.setRowStretch(row, 1)

        self._build_tiles()
        layout.addWidget(self.grid_host, 1)

        self.log_dock = QFrame()
        self.log_dock.setObjectName("card")
        self.log_dock.setMinimumHeight(120)
        dock_layout = QVBoxLayout(self.log_dock)
        dock_layout.setContentsMargins(10, 6, 10, 6)
        dock_layout.addWidget(QLabel("Live log and decisions arrive with the log panel."))
        layout.addWidget(self.log_dock, 0)
        return holder

    def _build_tiles(self) -> None:
        self.schematic_tile = SchematicTile(
            theme=self.theme,
            approaches=tuple(
                camera.approach for camera in self.controller.config.cameras
            ),
        )
        for camera in self.controller.config.cameras:
            tile = CameraTile(
                camera.id,
                camera.approach,
                camera.source,
                theme=self.theme,
            )
            tile.clicked.connect(self._focus_camera)
            tile.double_clicked.connect(self._fullscreen_camera)
            tile.snapshot_requested.connect(self._snapshot_camera)
            tile.flip_requested.connect(self._toggle_flip)
            tile.fullscreen_requested.connect(self._fullscreen_camera)
            tile.overlays_requested.connect(self._set_tile_overlays)
            self._tiles[camera.id] = tile
        self._relayout_tiles()

    def _relayout_tiles(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self.grid_host)
        items: list[QWidget] = list(self._tiles.values()) + [self.schematic_tile]
        if self._focus_mode and self._focused_camera in self._tiles:
            big = self._tiles[self._focused_camera]
            self.grid_layout.addWidget(big, 0, 0, 2, 2)
            others = [tile for tile in items if tile is not big]
            for index, tile in enumerate(others[:4]):
                self.grid_layout.addWidget(tile, index, 2)
        else:
            for index, tile in enumerate(items[:4]):
                self.grid_layout.addWidget(tile, index // 2, index % 2)

    def _build_sidebar(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(430)
        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        system = QLabel("Waiting for the first tick.")
        system.setObjectName("mono")
        system.setWordWrap(True)
        system.setContentsMargins(10, 8, 10, 8)
        system.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.system_label = system
        layout.addWidget(
            CollapsibleSection(
                "System", system, settings=self.settings, key="system"
            )
        )

        self.events_table = QTableWidget(0, 4)
        self.events_table.setHorizontalHeaderLabels(
            ["Time", "Unit / camera", "Event", "Result"]
        )
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.events_table.setMinimumHeight(140)
        layout.addWidget(
            CollapsibleSection(
                "Live Events", self.events_table, settings=self.settings, key="events"
            )
        )

        playback = QWidget()
        playback_layout = QVBoxLayout(playback)
        playback_layout.setContentsMargins(10, 8, 10, 8)
        playback_layout.setSpacing(6)
        note = QLabel("Testing aid: file sources only. Speed changes the feed, not decision timing.")
        note.setObjectName("helper")
        note.setWordWrap(True)
        playback_layout.addWidget(note)
        row = QHBoxLayout()
        self.play_button = QPushButton("Pause")
        self.play_button.clicked.connect(self._toggle_playback)
        restart = QPushButton("Restart")
        restart.clicked.connect(self.controller.restart_playback)
        self.speed_box = QComboBox()
        for label, value in (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)):
            self.speed_box.addItem(label, value)
        self.speed_box.setCurrentIndex(1)
        self.speed_box.currentIndexChanged.connect(
            lambda _index: self.controller.set_playback_speed(
                float(self.speed_box.currentData())
            )
        )
        row.addWidget(self.play_button)
        row.addWidget(restart)
        row.addWidget(self.speed_box)
        playback_layout.addLayout(row)
        self.playback_section = CollapsibleSection(
            "Playback", playback, settings=self.settings, key="playback"
        )
        self.playback_section.setVisible(bool(self.controller.file_camera_ids()))
        layout.addWidget(self.playback_section)

        self.camera_tree = QTreeWidget()
        self.camera_tree.setHeaderLabels(["Approach / camera", "Source"])
        self.camera_tree.setMinimumHeight(150)
        root = QTreeWidgetItem(["Junction (T)", ""])
        self.camera_tree.addTopLevelItem(root)
        for camera in self.controller.config.cameras:
            item = QTreeWidgetItem([f"{camera.approach}  {camera.id}", camera.source])
            item.setData(0, Qt.ItemDataRole.UserRole, camera.id)
            root.addChild(item)
        root.setExpanded(True)
        self.camera_tree.itemClicked.connect(self._tree_item_clicked)
        layout.addWidget(
            CollapsibleSection(
                "Camera List", self.camera_tree, settings=self.settings, key="cameras"
            )
        )

        controls = QLabel("Controls are added with the operator panel.")
        controls.setObjectName("helper")
        controls.setWordWrap(True)
        controls.setContentsMargins(10, 8, 10, 8)
        self.controls_section = CollapsibleSection(
            "Controls", controls, settings=self.settings, key="controls"
        )
        layout.addWidget(self.controls_section)
        layout.addStretch(1)

        scroll.setWidget(sidebar)
        return scroll

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.start_button = QPushButton("START")
        self.start_button.setProperty("variant", "primary")
        self.start_button.setMinimumWidth(112)
        self.start_button.clicked.connect(self._toggle_started)
        bar.addWidget(self.start_button)

        self.session_label = QLabel("Session 00:00:00")
        self.session_label.setObjectName("session")
        bar.addWidget(self.session_label, 1)

        self.camera_chip = StatusChip("0/0 cameras", "unknown")
        self.gateway_chip = StatusChip("Gateway unknown", "unknown")
        self.gate_chip = StatusChip("Gate CLOSED", "critical")
        self.dry_chip = StatusChip("DRY RUN", "info")
        self.dry_chip.setVisible(False)
        self.clock_chip = StatusChip("--:--:--", "info")
        for chip in (
            self.camera_chip,
            self.gateway_chip,
            self.gate_chip,
            self.dry_chip,
            self.clock_chip,
        ):
            bar.addPermanentWidget(chip)
        self.setStatusBar(bar)

    # -- theme and layout --------------------------------------------------

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.theme))
        if hasattr(self, "mode_badge"):
            self.mode_badge.setStyleSheet("")

    def switch_theme(self, name: str) -> None:
        theme = THEMES.get(name)
        if theme is None:
            return
        self.theme = theme
        self.settings.setValue("theme", name)
        self.theme_action.blockSignals(True)
        self.theme_action.setChecked(name == "light")
        self.theme_action.blockSignals(False)
        self._apply_theme()
        for tile in self._tiles.values():
            tile.theme = self.theme
            tile.update()
        self.schematic_tile.set_theme(self.theme)
        self._refresh_status()

    def _theme_toggled(self, light: bool) -> None:
        self.switch_theme("light" if light else "dark")

    def _set_focus_mode(self, enabled: bool) -> None:
        self._focus_mode = bool(enabled)
        self.settings.setValue("view/focus", self._focus_mode)
        if self._focus_mode and self._focused_camera is None:
            cameras = list(self._tiles)
            if cameras:
                self._focused_camera = cameras[0]
        self._relayout_tiles()
        self._update_tile_focus()

    def _focus_camera(self, camera_id: str) -> None:
        self._focused_camera = camera_id
        if not self._focus_mode:
            self.focus_action.setChecked(True)
        else:
            self._relayout_tiles()
            self._update_tile_focus()

    def _update_tile_focus(self) -> None:
        for camera_id, tile in self._tiles.items():
            tile.set_focused(self._focus_mode and camera_id == self._focused_camera)

    def _tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        camera_id = item.data(0, Qt.ItemDataRole.UserRole)
        if camera_id:
            self._focus_camera(str(camera_id))

    def _toggle_overlays(self, enabled: bool) -> None:
        self.controller.set_overlays(polygon=enabled, count_line=enabled)
        for tile in self._tiles.values():
            tile.set_overlays_visible(enabled)
            tile.set_overlay_state(enabled, enabled)
        self.settings.setValue("view/overlays", enabled)

    def _set_tile_overlays(
        self, camera_id: str, polygon: bool, count_line: bool
    ) -> None:
        self.controller.set_camera_overlays(
            camera_id, polygon=polygon, count_line=count_line
        )
        tile = self._tiles.get(camera_id)
        if tile is not None:
            tile.set_overlay_state(polygon, count_line)

    def _set_logs_visible(self, visible: bool) -> None:
        self.log_dock.setVisible(bool(visible))
        self.settings.setValue("view/logs", bool(visible))

    def _restore_layout(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter = self.settings.value("window/splitter")
        if splitter:
            self.splitter.restoreState(splitter)
        overlays = self.settings.value("view/overlays", True)
        enabled = overlays in (True, "true", "True", "1", 1)
        self.overlays_action.blockSignals(True)
        self.overlays_action.setChecked(enabled)
        self.overlays_action.blockSignals(False)
        self.controller.set_overlays(polygon=enabled, count_line=enabled)
        for tile in self._tiles.values():
            tile.set_overlays_visible(enabled)
            tile.set_overlay_state(enabled, enabled)
        focused = self.settings.value("view/focus", False) in (
            True,
            "true",
            "True",
            "1",
            1,
        )
        if focused:
            self.focus_action.setChecked(True)
        logs = self.settings.value("view/logs", True) in (
            True,
            "true",
            "True",
            "1",
            1,
        )
        self.logs_action.blockSignals(True)
        self.logs_action.setChecked(logs)
        self.logs_action.blockSignals(False)
        self.log_dock.setVisible(logs)

    def _save_layout(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/splitter", self.splitter.saveState())
        self.settings.setValue("theme", self.theme.name)
        self.settings.setValue("view/focus", self._focus_mode)
        self.settings.setValue("view/overlays", self.overlays_action.isChecked())
        self.settings.setValue("view/logs", self.logs_action.isChecked())

    # -- live updates ------------------------------------------------------

    def _refresh_video(self) -> None:
        snapshot = self._snapshot
        if snapshot is None or not snapshot.started:
            return
        for camera in snapshot.cameras:
            tile = self._tiles.get(camera.camera_id)
            if tile is None:
                continue
            frame = self.controller.frame(camera.camera_id, max_size=960)
            if frame is not None:
                tile.set_frame(frame)

    def _refresh_panels(self) -> None:
        self._refresh_status()
        self._refresh_events()

    def _refresh_events(self) -> None:
        # Filled by the log panel phase; keeping the table selection intact.
        return

    def _refresh_status(self) -> None:
        snapshot = self.controller.snapshot()
        self._snapshot = snapshot

        self.start_button.setText("STOP" if snapshot.started else "START")
        self.session_label.setText(format_uptime(snapshot.uptime_s))

        if snapshot.fresh_cameras == snapshot.required_cameras and snapshot.required_cameras:
            tone = "ok"
        elif snapshot.fresh_cameras == 0:
            tone = "critical"
        else:
            tone = "warn"
        self.camera_chip.set_state(
            f"{snapshot.fresh_cameras}/{snapshot.required_cameras} cameras", tone
        )

        gateway_tone = {
            "CONNECTED": "ok",
            "CONNECTING": "unknown",
            "OFFLINE": "critical",
            "DRY RUN": "info",
            "OFF": "unknown",
        }.get(snapshot.gateway_state, "unknown")
        self.gateway_chip.set_state(
            f"Gateway {snapshot.gateway_state.title()}",
            gateway_tone,
            f"{snapshot.gateway_host}:{snapshot.gateway_port}",
        )

        if snapshot.gate_open:
            gate_tone = "ok"
        elif snapshot.released:
            gate_tone = "warn"
        else:
            gate_tone = "critical"
        self.gate_chip.set_state(
            "Gate OPEN" if snapshot.gate_open else "Gate CLOSED",
            gate_tone,
            snapshot.gate_reason,
        )
        self.dry_chip.setVisible(bool(snapshot.dry_run))
        self.clock_chip.set_state(datetime.now().strftime("%H:%M:%S"), "info")

        if snapshot.simulation:
            parts = []
            if snapshot.dry_run:
                parts.append("dry run")
            if snapshot.mock_gateway:
                parts.append("mock gateway")
            if snapshot.three_phase:
                parts.append("three-phase proposal")
            detail = ", ".join(parts) or "simulation"
            self.banner.setText(
                f"SIMULATION: {detail}. Not connected to real hardware."
            )
            self.banner.setVisible(True)
            self.mode_badge.setText("SIMULATION")
        else:
            self.banner.setVisible(False)
            self.mode_badge.setText(snapshot.mode.replace("_", " ").upper())

        fps = ", ".join(
            f"{camera.approach} {camera.fps:.1f}" for camera in snapshot.cameras
        )
        self.system_label.setText(
            "\n".join(
                [
                    f"Mode: {snapshot.mode}",
                    f"Gateway: {snapshot.gateway_host}:{snapshot.gateway_port} "
                    f"({snapshot.gateway_state})",
                    f"Heartbeat gate: {'OPEN' if snapshot.gate_open else 'CLOSED'}",
                    f"Reason: {snapshot.gate_reason}",
                    f"Device: {self.controller.config.detection.device}",
                    f"Camera fps: {fps or 'no data'}",
                    f"CPU: {_percent(snapshot.cpu_percent)}   "
                    f"Memory: {_percent(snapshot.memory_percent)}",
                    f"Uptime: {format_uptime(snapshot.uptime_s)}",
                ]
            )
        )

        for camera in snapshot.cameras:
            tile = self._tiles.get(camera.camera_id)
            if tile is not None:
                tile.set_health(
                    camera.state,
                    camera.fps,
                    camera.frame_age_s,
                    camera.reconnects,
                    snapshot.approach(camera.approach),
                    started=snapshot.started,
                )
        self.schematic_tile.set_snapshot(snapshot)

    # -- commands ----------------------------------------------------------

    def _toggle_started(self) -> None:
        if self.controller.running:
            if not confirm(
                self,
                "Stop the host?",
                "Stop the pipelines and the advisory link now?",
                confirm_label="Stop",
                informative=(
                    "The controller will run autonomously; heartbeats stop and "
                    "logs are flushed."
                ),
            ):
                return
            self.controller.stop()
        else:
            try:
                self.controller.start()
            except Exception as exc:  # noqa: BLE001 - shown to the operator
                QMessageBox.critical(self, "Could not start", str(exc))
        self._refresh_status()

    def _snapshot_camera(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None:
            return
        if not self._snapshot_warned:
            QMessageBox.information(
                self,
                "Snapshot privacy",
                "Camera frames may show people and number plates. "
                "A snapshot is saved only when you choose to, and exports "
                "never contain images.",
            )
            self._snapshot_warned = True
        default = f"snapshot_{tile.approach}_{datetime.now():%Y%m%d_%H%M%S}.png"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save tile snapshot", default, "PNG image (*.png)"
        )
        if not path:
            return
        if not tile.grab().save(path):
            QMessageBox.warning(self, "Snapshot failed", f"Could not write {path}")

    def _toggle_flip(self, camera_id: str) -> None:
        new_state = not self._flip_state.get(camera_id, False)
        self._flip_state[camera_id] = new_state
        self.controller.set_flip(camera_id, new_state)

    def _fullscreen_camera(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{tile.approach} — {camera_id}")
        dialog.setWindowState(Qt.WindowState.WindowFullScreen)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"background-color: {self.theme.tokens['bg-field']};"
        )
        layout.addWidget(label)

        def update() -> None:
            frame = self.controller.frame(camera_id, max_size=1600)
            if frame is None:
                return
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            image = QImage(
                rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888
            ).copy()
            pixmap = QPixmap.fromImage(image).scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setPixmap(pixmap)

        timer = QTimer(dialog)
        timer.setInterval(100)
        timer.timeout.connect(update)
        timer.start()
        update()
        dialog.exec()
        timer.stop()

    def _toggle_playback(self) -> None:
        self._playback_paused = not self._playback_paused
        if self._playback_paused:
            self.controller.pause_playback()
            self.play_button.setText("Play")
        else:
            self.controller.resume_playback()
            self.play_button.setText("Pause")

    def _open_logs_folder(self) -> None:
        path = Path(self.controller.config.logging.decision_log).resolve().parent
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_config(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Configuration (read-only, secrets redacted)")
        dialog.resize(760, 640)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(config_yaml(self.controller.config))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(self, "About Smarti AI Host", about_text())

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.controller.running:
            if not confirm(
                self,
                "Shutdown the host?",
                "Shut down the host and close the console?",
                confirm_label="Shutdown",
                informative=(
                    "Requests and heartbeats stop, the controller runs "
                    "autonomously, logs are flushed and the window closes."
                ),
            ):
                event.ignore()
                return
        try:
            self.controller.shutdown()
        except Exception:  # noqa: BLE001 - closing must still proceed
            pass
        self._save_layout()
        super().closeEvent(event)


def _percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"
