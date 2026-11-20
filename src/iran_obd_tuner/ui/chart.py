"""Small dual-axis line chart (QPainter, no extra dependency).

Used for the virtual dyno: one x axis (rpm), a left series (power) and a right series
(torque), each with its own scale. Points are drawn as given; nothing is smoothed here.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

__all__ = ["DualAxisChart"]

_LEFT_COLOR = QColor("#6366f1")
_RIGHT_COLOR = QColor("#f59e0b")
_GRID = QColor("#334155")
_TEXT = QColor("#94a3b8")


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    for step in (1, 2, 2.5, 5, 10):
        magnitude = 10 ** (len(str(int(value))) - 1)
        if value <= step * magnitude:
            return step * magnitude
    return value  # pragma: no cover


class DualAxisChart(QWidget):
    """Two series sharing an x axis, with independent y scales."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._x: list[float] = []
        self._left: list[float] = []
        self._right: list[float] = []
        self._titles = ("", "", "")
        self.setMinimumHeight(240)

    @property
    def point_count(self) -> int:
        return len(self._x)

    def set_titles(self, x_title: str, left_title: str, right_title: str) -> None:
        self._titles = (x_title, left_title, right_title)
        self.update()

    def set_series(self, x: Sequence[float], left: Sequence[float], right: Sequence[float]) -> None:
        if not len(x) == len(left) == len(right):
            raise ValueError("all series must have the same length")
        self._x, self._left, self._right = list(x), list(left), list(right)
        self.update()

    def clear(self) -> None:
        self.set_series([], [], [])

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f172a"))
        plot = QRectF(56, 16, max(10, self.width() - 112), max(10, self.height() - 52))
        painter.setPen(QPen(_GRID, 1))
        painter.drawRect(plot)
        x_title, left_title, right_title = self._titles
        painter.setPen(_TEXT)
        painter.drawText(QRectF(0, self.height() - 22, self.width(), 20), Qt.AlignHCenter, x_title)
        painter.setPen(_LEFT_COLOR)
        painter.drawText(QRectF(4, 0, 200, 16), Qt.AlignLeft, left_title)
        painter.setPen(_RIGHT_COLOR)
        painter.drawText(QRectF(self.width() - 204, 0, 200, 16), Qt.AlignRight, right_title)
        if len(self._x) < 2:
            painter.setPen(_TEXT)
            painter.drawText(plot, Qt.AlignCenter, "—")
            return

        x_min, x_max = min(self._x), max(self._x)
        x_span = (x_max - x_min) or 1.0
        left_max = _nice_max(max(self._left))
        right_max = _nice_max(max(self._right))

        def to_point(x: float, y: float, y_max: float) -> QPointF:
            return QPointF(
                plot.left() + (x - x_min) / x_span * plot.width(),
                plot.bottom() - max(0.0, y) / y_max * plot.height(),
            )

        for i in range(5):  # grid and axis labels
            fraction = i / 4
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QPen(_GRID, 1, Qt.DotLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(_LEFT_COLOR)
            painter.drawText(QRectF(0, y - 8, 52, 16), Qt.AlignRight, f"{left_max * fraction:g}")
            painter.setPen(_RIGHT_COLOR)
            painter.drawText(
                QRectF(plot.right() + 4, y - 8, 52, 16), Qt.AlignLeft, f"{right_max * fraction:g}"
            )
            painter.setPen(_TEXT)
            x_value = x_min + fraction * x_span
            painter.drawText(
                QRectF(plot.left() + fraction * plot.width() - 30, plot.bottom() + 2, 60, 16),
                Qt.AlignHCenter,
                f"{x_value:.0f}",
            )

        for series, color, y_max in (
            (self._left, _LEFT_COLOR, left_max),
            (self._right, _RIGHT_COLOR, right_max),
        ):
            path = QPainterPath(to_point(self._x[0], series[0], y_max))
            for x, y in zip(self._x[1:], series[1:], strict=True):
                path.lineTo(to_point(x, y, y_max))
            painter.setPen(QPen(color, 2))
            painter.drawPath(path)
