"""Raw adapter traffic panel (TX / RX / INFO lines) with pause, clear and copy.

Reads the shared :class:`~iran_obd_tuner.core.traffic.TrafficLog` on the GUI thread every
:data:`REFRESH_MS` milliseconds, appending only entries it has not shown yet. "Pause" only
freezes the view; recording continues in the bounded buffer, and on resume the view catches
up with whatever is still in it. Simulated lines carry a visible tag.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["REFRESH_MS", "TrafficView"]

REFRESH_MS = 200


class TrafficView(QWidget):
    """The "raw traffic" sub-tab."""

    def __init__(self, log: TrafficLog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log = log
        self._lang: Lang = "fa"
        self._last_seq = 0
        self._paused = False

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.pause_btn = QPushButton()
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self.clear)
        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color: #94a3b8;")
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.clear_btn)
        controls.addWidget(self.copy_btn)
        controls.addStretch()
        controls.addWidget(self.stats_label)
        layout.addLayout(controls)

        self.privacy_note = QLabel()
        self.privacy_note.setWordWrap(True)
        self.privacy_note.setStyleSheet("color: #fbbf24;")
        layout.addWidget(self.privacy_note)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(log.capacity)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.text, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.pause_btn.setText(t["traffic_resume"] if self._paused else t["traffic_pause"])
        self.clear_btn.setText(t["traffic_clear"])
        self.copy_btn.setText(t["traffic_copy"])
        self.privacy_note.setText(t["traffic_privacy"])
        self._update_stats()

    def refresh(self) -> None:
        """Append entries recorded since the last refresh (no-op while paused)."""
        if self._paused:
            return
        entries = self._log.entries_since(self._last_seq)
        if entries:
            tag = COPY[self._lang]["traffic_sim_tag"]
            self.text.appendPlainText("\n".join(entry.format_line(tag) for entry in entries))
            self._last_seq = entries[-1].seq
        self._update_stats()

    def clear(self) -> None:
        """Forget the recorded lines and empty the view."""
        self._log.clear()
        self._last_seq = self._log.last_seq
        self.text.clear()
        self._update_stats()

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def lines(self) -> list[str]:
        """Visible lines (used by tests)."""
        content = self.text.toPlainText()
        return content.splitlines() if content else []

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
        self.set_language(self._lang)
        if not paused:
            self.refresh()

    def _update_stats(self) -> None:
        self.stats_label.setText(
            COPY[self._lang]["traffic_stats"].format(
                len(self._log), self._log.dropped_count, self._log.capacity
            )
        )
