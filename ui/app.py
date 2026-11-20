"""Launch the optional Qt console."""

from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtWidgets import QApplication

from app.controller import AppController
from ui.main_window import MainWindow


def run_gui(
    controller: AppController,
    *,
    argv: Sequence[str] | None = None,
    theme: str | None = None,
) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv or sys.argv[:1]))
    app.setApplicationName("Smarti AI Host")
    app.setOrganizationName("Smarti")
    app.setStyle("Fusion")

    window = MainWindow(controller, theme_name=theme)
    window.show()
    try:
        return int(app.exec())
    finally:
        try:
            controller.shutdown()
        except Exception:  # noqa: BLE001 - best effort on exit
            pass
