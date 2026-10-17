"""Safety refusal modal dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from iran_obd_tuner.ui.i18n import COPY, Lang


class SafetyModal(QDialog):
    """Modal shown when a user attempts an action blocked by the safety policy."""

    def __init__(self, lang: Lang, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Safety Warning" if lang == "en" else "هشدار ایمنی")
        self.setFixedSize(400, 200)
        self.setStyleSheet(
            """
            QDialog { background-color: #1e293b; color: #f1f5f9; border: 1px solid #334155; }
            QLabel { color: #f1f5f9; }
            """
        )

        t = COPY[lang]
        layout = QVBoxLayout(self)

        # Icon and Title
        title = QLabel("🛑 " + ("Access Denied" if lang == "en" else "دسترسی مسدود شد"))
        title.setStyleSheet("color: #ef4444; font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel(t.get("safety_refusal_msg", "This operation is disabled in this read-only build."))
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #94a3b8; margin-top: 10px; margin-bottom: 20px;")
        layout.addWidget(desc)

        # OK Button
        btn = QPushButton("Understood" if lang == "en" else "متوجه شدم")
        btn.setMinimumHeight(40)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


