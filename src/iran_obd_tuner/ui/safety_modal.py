"""Safety refusal dialog, shown when a blocked (state-changing) action is requested.

The window opens it with :meth:`QDialog.open` (window modal, non-blocking) rather than
``exec()``, so it never blocks the event loop and stays testable headless. All text comes
from :mod:`iran_obd_tuner.ui.i18n`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["SafetyModal"]


class SafetyModal(QDialog):
    """Modal explaining why an action was refused.

    Args:
        lang: UI language.
        message: Translated explanation of the refused action.
        parent: Parent window.
    """

    def __init__(self, lang: Lang, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        t = COPY[lang]
        self.setWindowTitle(t["safety_title"])
        self.setMinimumWidth(420)
        self.setLayoutDirection(Qt.RightToLeft if lang == "fa" else Qt.LeftToRight)
        self.setStyleSheet(
            """
            QDialog { background-color: #1e293b; color: #f1f5f9; border: 1px solid #334155; }
            QLabel { color: #f1f5f9; }
            """
        )

        layout = QVBoxLayout(self)

        self.heading_label = QLabel(t["safety_heading"])
        self.heading_label.setStyleSheet("color: #ef4444; font-size: 18px; font-weight: bold;")
        self.heading_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.heading_label)

        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setStyleSheet("color: #94a3b8; margin-top: 10px; margin-bottom: 20px;")
        layout.addWidget(self.message_label)

        self.ack_button = QPushButton(t["safety_ack"])
        self.ack_button.setMinimumHeight(40)
        self.ack_button.clicked.connect(self.accept)
        layout.addWidget(self.ack_button)
