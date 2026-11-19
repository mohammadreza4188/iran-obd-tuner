"""Circular gauge widget for live OBD values.

A gauge shows either a value or an explicit "no value" state (``—`` plus a short caption
such as "Not supported"). It never invents a number: a parameter that could not be read is
shown as unavailable, not as ``0``. Values below the scale minimum (e.g. coolant at -10 °C
with a scale starting at -40) are displayed as they are; only the arc is clamped.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

__all__ = ["GaugeCard", "GaugeWidget"]

NO_VALUE_TEXT = "—"


class GaugeWidget(QWidget):
    """Painted circular gauge."""

    def __init__(
        self,
        label: str = "",
        unit: str = "",
        minimum: float = 0.0,
        maximum: float = 100.0,
        digits: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if maximum <= minimum:
            raise ValueError("maximum must be greater than minimum")
        self._label = label
        self._unit = unit
        self._min = minimum
        self._max = maximum
        self._digits = digits
        self._value: float | None = None
        self._caption = ""
        self.setMinimumSize(180, 200)

    @property
    def value(self) -> float | None:
        """Displayed value, or ``None`` when the gauge shows "no value"."""
        return self._value

    @property
    def caption(self) -> str:
        """Reason shown when no value is available (empty otherwise)."""
        return self._caption

    @property
    def display_text(self) -> str:
        """The text in the centre of the gauge."""
        if self._value is None:
            return NO_VALUE_TEXT
        return f"{self._value:.{self._digits}f}"

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.update()

    def set_value(self, value: float) -> None:
        """Show ``value`` (any real number, including negative values)."""
        self._value = float(value)
        self._caption = ""
        self.update()

    def set_unavailable(self, caption: str = "") -> None:
        """Show "no value", with an optional reason such as "Not supported"."""
        self._value = None
        self._caption = caption
        self.update()

    def _fraction(self) -> float:
        if self._value is None:
            return 0.0
        span = self._max - self._min
        return min(1.0, max(0.0, (self._value - self._min) / span))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor("#1e293b")
        line = QColor("#334155")
        accent = QColor("#6366f1")
        ink = QColor("#f1f5f9")
        mute = QColor("#94a3b8")

        painter.fillRect(self.rect(), bg)

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

        painter.setPen(QPen(line, 10, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, start_angle, span_full)

        if self._value is not None:
            painter.setPen(QPen(accent, 10, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, start_angle, int(span_full * self._fraction()))

        painter.setPen(accent if self._value is not None else mute)
        value_font = QFont(self.font())
        value_font.setPointSize(22)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.drawText(rect, Qt.AlignCenter, self.display_text)

        painter.setPen(mute)
        unit_font = QFont(self.font())
        unit_font.setPointSize(9)
        painter.setFont(unit_font)
        painter.drawText(
            QRectF(cx - radius, cy + radius * 0.15, radius * 2, 24),
            Qt.AlignHCenter | Qt.AlignTop,
            self._unit,
        )
        if self._caption:
            painter.drawText(
                QRectF(0, cy + radius * 0.75, self.width(), 24),
                Qt.AlignHCenter | Qt.AlignTop,
                self._caption,
            )


class GaugeCard(QWidget):
    """A gauge inside a rounded card."""

    def __init__(
        self,
        label: str,
        unit: str,
        minimum: float,
        maximum: float,
        digits: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            GaugeCard {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.gauge = GaugeWidget(
            label=label, unit=unit, minimum=minimum, maximum=maximum, digits=digits
        )
        layout.addWidget(self.gauge)

    def set_value(self, value: float) -> None:
        self.gauge.set_value(value)

    def set_unavailable(self, caption: str = "") -> None:
        self.gauge.set_unavailable(caption)

    def set_label(self, label: str) -> None:
        self.gauge.set_label(label)

    def set_unit(self, unit: str) -> None:
        self.gauge.set_unit(unit)
