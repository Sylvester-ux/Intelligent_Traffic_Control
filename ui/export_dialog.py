"""Save-as dialog for decision exports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

FORMATS = (("JSONL decisions", "jsonl"), ("CSV (flattened)", "csv"), ("Filtered log text", "text"))
RANGES = (("Last 5 minutes", "5m"), ("Last hour", "1h"), ("All decisions", "all"))
SUFFIX = {"jsonl": ".jsonl", "csv": ".csv", "text": ".log"}


class ExportLogDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        default_dir: str | Path,
        has_selection: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export log")
        self.resize(560, 220)
        self._default_dir = Path(default_dir)
        self._has_selection = has_selection

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.format_box = QComboBox()
        for label, value in FORMATS:
            self.format_box.addItem(label, value)
        form.addRow("Format", self.format_box)

        self.range_box = QComboBox()
        for label, value in RANGES:
            self.range_box.addItem(label, value)
        if has_selection:
            self.range_box.addItem("Selected decision rows", "selected")
        form.addRow("Range", self.range_box)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(self._default_path("jsonl"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        form.addRow("Save to", path_widget)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("Export")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.format_box.currentIndexChanged.connect(self._format_changed)
        self._format_changed()

    def _format_changed(self) -> None:
        fmt = str(self.format_box.currentData())
        if fmt == "text":
            self.range_box.setEnabled(False)
            self.range_box.setToolTip(
                "Text exports the current filtered log view, not a time range."
            )
        else:
            self.range_box.setEnabled(True)
            self.range_box.setToolTip("")
        current = Path(self.path_edit.text())
        if current.suffix in (".jsonl", ".csv", ".log"):
            self.path_edit.setText(str(current.with_suffix(SUFFIX[fmt])))

    def _default_path(self, fmt: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(self._default_dir / f"decisions_{stamp}{SUFFIX[fmt]}")

    def _browse(self) -> None:
        fmt = str(self.format_box.currentData())
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Export log",
            self.path_edit.text(),
            "All files (*)",
        )
        if not path:
            return
        if not Path(path).suffix:
            path += SUFFIX[fmt]
        self.path_edit.setText(path)

    def values(self) -> tuple[str, str, str]:
        return (
            self.path_edit.text().strip(),
            str(self.format_box.currentData()),
            str(self.range_box.currentData()),
        )
