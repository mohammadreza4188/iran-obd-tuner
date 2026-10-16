"""Programmatic window icon for the dashboard (no external asset files)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap


def _make_pixmap(size: int) -> QPixmap:
    """Draw one icon variant at the given square size."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    # Rounded dark background
    bg = QColor("#1a1b1f")
    painter.setBrush(QBrush(bg))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)

    # Gauge arc (blue)
    margin = size * 0.16
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    start_angle = 220 * 16
    span = -260 * 16

    track_pen = QPen(QColor("#3d3f46"), size * 0.11, Qt.SolidLine, Qt.RoundCap)
    painter.setPen(track_pen)
    painter.drawArc(rect, start_angle, span)

    accent_pen = QPen(QColor("#2f86e8"), size * 0.11, Qt.SolidLine, Qt.RoundCap)
    painter.setPen(accent_pen)
    painter.drawArc(rect, start_angle, int(span * 0.68))

    # Center needle dot
    center = QPointF(size / 2, size / 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor("#f3f4f6")))
    painter.drawEllipse(center, size * 0.06, size * 0.06)

    # Small "OBD" text
    painter.setPen(QColor("#9b9ca4"))
    f = QFont()
    f.setBold(True)
    f.setPointSizeF(size * 0.12)
    painter.setFont(f)
    painter.drawText(
        QRectF(0, size * 0.70, size, size * 0.25),
        Qt.AlignHCenter | Qt.AlignTop,
        "OBD",
    )

    painter.end()
    return pix


def build_icon() -> QIcon:
    """Build a multi-resolution window icon."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_make_pixmap(size))
    return icon
