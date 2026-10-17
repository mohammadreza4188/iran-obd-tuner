"""Circular gauge widget for live OBD values."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget


class GaugeWidget(QWidget):
    def __init__(
        self,
        label: str = "",
        unit: str = "",
        maximum: float = 100.0,
        digits: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._unit = unit
        self._max = max(maximum, 1.0)
        self._digits = digits
        self._value = 0.0
        self.setMinimumSize(180, 200)

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.update()

    def set_value(self, value: float) -> None:
        self._value = max(0.0, float(value))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor("#1e293b")
        line = QColor("#334155")
        accent = QColor("#6366f1")
        ink = QColor("#f1f5f9")
        mute = QColor("#94a3b8")

        painter.fillRect(self.rect(), bg)

        # Title
        painter.setPen(ink)
        title_font = QFont(self.font())
        title_font.setPointSize(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            self.rect().adjusted(8, 8, -8, -8),
            Qt.AlignHCenter | Qt.AlignTop,
            self._label,
        )

        side = min(self.width(), self.height() - 36)
        cx = self.width() / 2
        cy = self.height() / 2 + 8
        radius = side * 0.36
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        start_angle = 220 * 16
        span_full = -260 * 16
        pct = min(1.0, self._value / self._max)
        span_value = int(span_full * pct)

        track = QPen(line, 10, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, start_angle, span_full)

        value_pen = QPen(accent, 10, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(value_pen)
        painter.drawArc(rect, start_angle, span_value)

        painter.setPen(accent)
        value_font = QFont(self.font())
        value_font.setPointSize(22)
        value_font.setBold(True)
        painter.setFont(value_font)
        text = f"{self._value:.{self._digits}f}"
        painter.drawText(rect, Qt.AlignCenter, text)

        painter.setPen(mute)
        unit_font = QFont(self.font())
        unit_font.setPointSize(9)
        painter.setFont(unit_font)
        painter.drawText(
            QRectF(cx - radius, cy + radius * 0.15, radius * 2, 24),
            Qt.AlignHCenter | Qt.AlignTop,
            self._unit,
        )


class GaugeCard(QWidget):
    def __init__(self, label: str, unit: str, maximum: float, digits: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            GaugeCard {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)  # Padding inside the card
        self.gauge = GaugeWidget(label=label, unit=unit, maximum=maximum, digits=digits)
        layout.addWidget(self.gauge)

    def set_value(self, value: float) -> None:
        self.gauge.set_value(value)

    def set_label(self, label: str) -> None:
        self.gauge.set_label(label)
