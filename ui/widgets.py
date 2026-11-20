"""Small reusable widgets for the console shell."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import Theme

ICONS = {
    "ok": "✓",
    "warn": "!",
    "critical": "✕",
    "unknown": "•",
    "info": "i",
}


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "no frame"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f} s"


def format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"Session {hours:02d}:{minutes:02d}:{secs:02d}"


class StatusChip(QFrame):
    """Icon + word + colour; colour is never the only signal."""

    def __init__(self, word: str = "unknown", tone: str = "unknown") -> None:
        super().__init__()
        self.setObjectName("chip")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 10, 2)
        layout.setSpacing(5)
        self.icon = QLabel(ICONS.get(tone, ICONS["unknown"]))
        self.icon.setObjectName("chipIcon")
        self.text = QLabel(word)
        self.text.setObjectName("chipText")
        layout.addWidget(self.icon)
        layout.addWidget(self.text)
        self.set_state(word, tone)

    def set_state(self, word: str, tone: str = "unknown", tooltip: str = "") -> None:
        tone = tone if tone in ICONS else "unknown"
        self.icon.setText(ICONS[tone])
        self.icon.setProperty("tone", tone)
        self.text.setText(word)
        self.setToolTip(tooltip)
        for widget in (self.icon, self):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class CollapsibleSection(QWidget):
    """A sidebar section with a triangle toggle and persisted state."""

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        settings: QSettings | None = None,
        key: str = "",
        expanded: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._settings = settings
        self._key = f"sidebar/{key or title}"
        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.header.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.body = content
        self.body.setObjectName("sectionBody")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.body)

        self.header.toggled.connect(self._on_toggled)
        state = expanded
        if settings is not None:
            stored = settings.value(self._key, expanded)
            state = stored in (True, "true", "True", "1", 1)
        self.header.setChecked(state)
        self._apply(state)

    def _on_toggled(self, checked: bool) -> None:
        self._apply(checked)
        if self._settings is not None:
            self._settings.setValue(self._key, checked)

    def _apply(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header.setText(("▾  " if expanded else "▸  ") + self._title)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)


class EmptyTile(QFrame):
    """Placeholder before a live camera or schematic is attached."""

    def __init__(self, name: str, message: str = "Waiting for video") -> None:
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.name_label = QLabel(name)
        self.name_label.setObjectName("tileName")
        self.message = QLabel(message)
        self.message.setObjectName("tileEmpty")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)
        layout.addWidget(self.message, 1)


class MetricCard(QFrame):
    """A compact count/label card used by the decision panel."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("helper")
        self.value = QLabel("—")
        self.value.setObjectName("largeReadout")
        self.hint = QLabel("")
        self.hint.setObjectName("helper")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value: str, hint: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)
        self.value.setProperty("tone", tone)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)


def apply_tone(widget: QWidget, theme: Theme, tone: str) -> None:
    """Set a foreground colour from the theme token for a text widget."""
    mapping = {
        "ok": "status-ok",
        "warn": "status-warn",
        "critical": "status-critical-text",
        "unknown": "status-unknown",
        "info": "accent-text",
    }
    widget.setStyleSheet(f"color: {theme.tokens[mapping.get(tone, 'text-primary')]};")
