"""Run controller work off the GUI thread and report back through signals."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class TaskSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class FunctionTask(QRunnable):
    def __init__(self, function: Callable[[Callable[[int, int], None]], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable
        try:
            result = self.function(self.signals.progress.emit)
        except Exception as exc:  # noqa: BLE001 - shown in a dialog
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)
