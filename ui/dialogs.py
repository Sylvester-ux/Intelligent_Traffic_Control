"""Dialog helpers. Consequence-bearing actions default to Cancel."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def build_confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    confirm_label: str = "Continue",
    informative: str = "",
) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    confirm = box.addButton(confirm_label, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box._confirm_button = confirm  # type: ignore[attr-defined]
    box._cancel_button = cancel  # type: ignore[attr-defined]
    return box


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    confirm_label: str = "Continue",
    informative: str = "",
) -> bool:
    box = build_confirm(
        parent,
        title,
        text,
        confirm_label=confirm_label,
        informative=informative,
    )
    box.exec()
    return box.clickedButton() is getattr(box, "_confirm_button", None)


def about_text() -> str:
    return (
        "Smarti AI Host — operator console\n\n"
        "Advisory research prototype. Not validated on hardware or approved "
        "for public roads.\n\n"
        "The host recommends which serving unit should receive green and for "
        "how long. The Arduino Uno remains the authority for phase order, "
        "yellow, all-red and the lamps. The console never draws a lit lamp as "
        "physical state; it shows the last recommendation, the controller's "
        "reply and a derived expected red.\n\n"
        "No video is recorded. Snapshots happen only on explicit click."
    )
