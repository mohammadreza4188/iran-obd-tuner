"""Dashboard main window: gauges, live graph, trouble codes, identification, settings, log.

Data flow (the only one in the project)::

    MainWindow ─► DashboardBackend.create_session() ─► LiveDataSession ─► ObdAdapter

The window never chooses an adapter and never touches a serial port: it asks the backend
(implemented in the application layer) for a :class:`LiveDataSession` and polls it.

Honesty rules visible here:

* a parameter that could not be read is shown as "—" with its reason, never as ``0``;
* a failed trouble code read is shown as an error, never as "no trouble codes";
* simulated sessions show a permanent banner, and recordings carry a ``simulated`` column;
* "clear codes" is forwarded to the adapter's safety gate, refused, and explained.

Every adapter call (connect, poll, trouble codes, identification) runs on one background
thread (:class:`~iran_obd_tuner.ui.worker.BackgroundRunner`), so a slow serial adapter
never freezes the window; results are applied on the GUI thread, and results that arrive
after a disconnect are dropped. Not implemented: freeze frames, reports.
"""

from __future__ import annotations

import html
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner import __version__
from iran_obd_tuner.core.exceptions import (
    IranObdTunerError,
    SafetyGuardError,
    UnsupportedOperationError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.diagnostics.live_data import LiveDataSession, LiveSnapshot, PidStatus
from iran_obd_tuner.diagnostics.models import TroubleCode
from iran_obd_tuner.diagnostics.recorder import CsvRecorder
from iran_obd_tuner.ui.backend import DashboardBackend
from iran_obd_tuner.ui.gauges import GaugeCard
from iran_obd_tuner.ui.i18n import COPY, Lang, format_message
from iran_obd_tuner.ui.icon import build_icon
from iran_obd_tuner.ui.safety_modal import SafetyModal
from iran_obd_tuner.ui.worker import BackgroundRunner

__all__ = ["DASHBOARD_PIDS", "GAUGES", "GaugeSpec", "MainWindow"]

_LOG = get_logger("ui.main_window")

#: Port selector entry meaning "let the adapter choose".
AUTO_PORT = "auto"

POLL_INTERVAL_MS = 250


@dataclass(frozen=True, slots=True)
class GaugeSpec:
    """One dashboard gauge and the OBD-II parameter it shows."""

    key: str
    pid: str
    unit_key: str
    minimum: float
    maximum: float
    digits: int = 0


#: Dashboard gauges, in display order. Temperature scales start at -40 °C, the lowest value
#: the OBD-II temperature PIDs can encode.
GAUGES: tuple[GaugeSpec, ...] = (
    GaugeSpec("rpm", "010C", "rpm", 0, 7000),
    GaugeSpec("speed", "010D", "kmh", 0, 220),
    GaugeSpec("coolant", "0105", "c", -40, 130),
    GaugeSpec("voltage", "0142", "v", 0, 16, digits=1),
    GaugeSpec("fuel", "012F", "pct", 0, 100),
    GaugeSpec("throttle", "0111", "pct", 0, 100),
    GaugeSpec("iat", "010F", "c", -40, 80),
    GaugeSpec("load", "0104", "pct", 0, 100),
)

#: Parameters polled for the dashboard (and recorded to CSV), in gauge order.
DASHBOARD_PIDS: tuple[str, ...] = tuple(spec.pid for spec in GAUGES)

_PAGES: tuple[tuple[str, ...], ...] = (
    ("rpm", "speed", "coolant", "voltage", "fuel"),
    ("throttle", "iat", "load"),
)

_STATUS_KEYS = {
    PidStatus.UNSUPPORTED: "unsupported",
    PidStatus.NO_RESPONSE: "no_response",
    PidStatus.MALFORMED: "malformed",
}

_TAB_STYLE_ACTIVE = (
    "background-color: #6366f1; color: #ffffff; border: none; border-radius: 8px; "
    "padding: 8px 16px; font-weight: bold;"
)
_TAB_STYLE_IDLE = (
    "background-color: transparent; color: #94a3b8; border: none; border-radius: 8px; "
    "padding: 8px 16px;"
)
_PILL_STYLE_ACTIVE = (
    "background:#6366f1; color:#ffffff; border-radius:12px; padding:6px 16px; font-weight:bold;"
)
_PILL_STYLE_IDLE = (
    "background:#1e293b; color:#94a3b8; border-radius:12px; padding:6px 16px; "
    "font-weight:bold; border:1px solid #334155;"
)
_RECORD_STYLE_ACTIVE = (
    "background-color: #ef4444; color: #ffffff; border: none; border-radius: 8px; "
    "padding: 8px 16px; font-weight: bold;"
)


def _describe(error: BaseException) -> str:
    """Text for an error raised by a background adapter job."""
    if isinstance(error, IranObdTunerError):
        return str(error)
    _LOG.error("unexpected error in adapter job", exc_info=error)
    return f"{type(error).__name__}: {error}"


def _ignore(_result: object) -> None:
    """Callback for jobs whose result is not needed."""


def _log_background_error(error: BaseException) -> None:
    _LOG.warning("background adapter job failed: %s", error)


class Sparkline(QWidget):
    """Rolling RPM/speed graph. Missing samples leave a gap instead of a fake zero."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rpm: deque[float | None] = deque(maxlen=60)
        self._speed: deque[float | None] = deque(maxlen=60)
        self.setMinimumHeight(220)

    @property
    def sample_count(self) -> int:
        """Number of samples currently held."""
        return len(self._rpm)

    def push(self, rpm: float | None, speed: float | None) -> None:
        self._rpm.append(rpm)
        self._speed.append(speed)
        self.update()

    def clear(self) -> None:
        self._rpm.clear()
        self._speed.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f172a"))

        if len(self._rpm) < 2:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(self.rect(), Qt.AlignCenter, "—")
            return

        def draw_series(values: deque[float | None], color: QColor, vmax: float) -> None:
            w = max(1, self.width() - 20)
            h = max(1, self.height() - 20)
            step = w / max(1, len(values) - 1)
            painter.setPen(QPen(color, 2))
            previous: tuple[float, float] | None = None
            for i, value in enumerate(values):
                if value is None:
                    previous = None
                    continue
                point = (10 + i * step, 10 + h * (1.0 - min(1.0, max(0.0, value / vmax))))
                if previous is not None:
                    painter.drawLine(
                        int(previous[0]), int(previous[1]), int(point[0]), int(point[1])
                    )
                previous = point

        draw_series(self._rpm, QColor("#6366f1"), 7000)
        draw_series(self._speed, QColor("#f1f5f9"), 220)


class MainWindow(QMainWindow):
    """The dashboard window.

    Args:
        backend: Application services (session factory, port list, output directory).
    """

    def __init__(self, backend: DashboardBackend) -> None:
        super().__init__()
        self.lang: Lang = "fa"
        self._backend = backend
        self._session: LiveDataSession | None = None
        self.recorder = CsvRecorder()
        self._page = 0
        self._last_snapshot: LiveSnapshot | None = None
        self._dtc_state: tuple[TroubleCode, ...] | str | None = None
        # Vehicle identification, or (i18n key, error detail) when it is not available.
        self._ident_state: VehicleInfo | tuple[str, str] | None = None
        self._safety_modal: SafetyModal | None = None
        # Adapter I/O runs on one background thread; results come back as callbacks.
        self._runner = BackgroundRunner(self)
        self._connecting = False
        self._poll_pending = False
        self._generation = 0  # bumped on disconnect; stale results are dropped

        self.setMinimumSize(1100, 800)
        self.setWindowIcon(build_icon())
        self.status_bar = self.statusBar()
        self.recording_label = QLabel("")
        self.status_bar.addPermanentWidget(self.recording_label)

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)

        self._build_ui()
        self._apply_language()
        self._apply_theme()

    # ------------------------------------------------------------------ state
    @property
    def session(self) -> LiveDataSession | None:
        """The open session, or ``None`` when disconnected."""
        return self._session

    @property
    def connected(self) -> bool:
        """Whether a session is open."""
        return self._session is not None

    @property
    def busy(self) -> bool:
        """Whether adapter jobs are still running or queued (used by tests)."""
        return self._runner.pending > 0

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        # --- Header ---
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        self.edition_label = QLabel()
        self.edition_label.setStyleSheet("color: #6366f1; font-weight: 600; font-size: 12px;")
        self.title_label = QLabel()
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        title_box.addWidget(self.edition_label)
        title_box.addWidget(self.title_label)
        header.addLayout(title_box)
        header.addStretch()

        badge_box = QHBoxLayout()
        badge_box.setSpacing(8)
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setStyleSheet("color: #94a3b8; font-family: monospace; font-size: 12px;")
        self.guard_badge = QLabel()
        self.guard_badge.setStyleSheet(
            "color: #10b981; font-weight: bold; font-size: 11px; border: 1px solid #10b981; "
            "border-radius: 4px; padding: 2px 8px;"
        )
        self.status_pill = QLabel()
        badge_box.addWidget(self.version_label)
        badge_box.addWidget(self.guard_badge)
        badge_box.addWidget(self.status_pill)
        header.addLayout(badge_box)
        outer.addLayout(header)
        outer.addSpacing(8)

        # --- Navbar tabs ---
        tab_row = QHBoxLayout()
        tab_row.setSpacing(8)
        self.tab_buttons: list[QPushButton] = []
        self.tab_ids = ["dashboard", "graph", "diag", "ident", "bench", "settings", "log"]
        self.tab_icons = {
            "dashboard": "📊",
            "graph": "📈",
            "diag": "⚠️",
            "ident": "📡",
            "bench": "🛠️",
            "settings": "⚙️",
            "log": "📝",
        }
        for tab_id in self.tab_ids:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setMinimumHeight(38)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, t=tab_id: self._switch_tab(t))
            self.tab_buttons.append(btn)
            tab_row.addWidget(btn)
        tab_row.addStretch()
        outer.addLayout(tab_row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #334155;")
        outer.addWidget(line)
        outer.addSpacing(8)

        # --- Toolbar (port, connect, record, pager) ---
        tool_frame = QFrame()
        tool_frame.setObjectName("tool_frame")
        tool_layout = QHBoxLayout(tool_frame)
        tool_layout.setContentsMargins(16, 12, 16, 12)

        self.port_label = QLabel()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(160)
        self.refresh_ports_btn = QPushButton("🔄")
        self.refresh_ports_btn.setFixedSize(38, 38)
        self.refresh_ports_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_ports_btn.clicked.connect(self._refresh_ports)

        self.connect_btn = QPushButton()
        self.connect_btn.setMinimumHeight(38)
        self.connect_btn.setMinimumWidth(120)
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(self._toggle_connect)

        self.record_btn = QPushButton("⏺")
        self.record_btn.setMinimumHeight(38)
        self.record_btn.setMinimumWidth(46)
        self.record_btn.setEnabled(False)
        self.record_btn.setCursor(Qt.PointingHandCursor)
        self.record_btn.clicked.connect(self._toggle_recording)

        self.prev_btn = QPushButton("<")
        self.next_btn = QPushButton(">")
        self.page_label = QLabel()
        self.prev_btn.setFixedSize(38, 38)
        self.next_btn.setFixedSize(38, 38)
        self.prev_btn.clicked.connect(lambda: self._set_page(0))
        self.next_btn.clicked.connect(lambda: self._set_page(1))

        tool_layout.addWidget(self.port_label)
        tool_layout.addWidget(self.port_combo)
        tool_layout.addWidget(self.refresh_ports_btn)
        tool_layout.addWidget(self.connect_btn)
        tool_layout.addWidget(self.record_btn)
        tool_layout.addStretch()
        tool_layout.addWidget(self.prev_btn)
        tool_layout.addWidget(self.page_label)
        tool_layout.addWidget(self.next_btn)
        outer.addWidget(tool_frame)

        # --- Simulation banner (visible only while a simulated session is open) ---
        self.sim_banner = QLabel()
        self.sim_banner.setWordWrap(True)
        self.sim_banner.setStyleSheet(
            "background-color: #422006; color: #fbbf24; border: 1px solid #b45309; "
            "border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.sim_banner.setVisible(False)
        outer.addWidget(self.sim_banner)

        # --- Pages ---
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # Dashboard
        dash = QWidget()
        self.gauge_grid = QGridLayout(dash)
        self.gauge_grid.setSpacing(16)
        self.gauges: dict[str, GaugeCard] = {
            spec.key: GaugeCard("", "", spec.minimum, spec.maximum, digits=spec.digits)
            for spec in GAUGES
        }
        self.stack.addWidget(dash)

        # Graph
        graph_page = QWidget()
        graph_layout = QVBoxLayout(graph_page)
        self.spark = Sparkline()
        graph_layout.addWidget(self.spark)
        self.stack.addWidget(graph_page)

        # Trouble codes
        diag_page = QWidget()
        diag_layout = QVBoxLayout(diag_page)
        diag_header = QHBoxLayout()
        self.dtc_title = QLabel()
        self.dtc_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.refresh_dtc_btn = QPushButton()
        self.clear_dtc_btn = QPushButton()
        self.refresh_dtc_btn.clicked.connect(self._refresh_dtc)
        self.clear_dtc_btn.clicked.connect(self._clear_dtc)
        diag_header.addWidget(self.dtc_title)
        diag_header.addStretch()
        diag_header.addWidget(self.refresh_dtc_btn)
        diag_header.addWidget(self.clear_dtc_btn)
        diag_layout.addLayout(diag_header)
        self.dtc_note = QLabel()
        self.dtc_note.setStyleSheet("color: #94a3b8;")
        diag_layout.addWidget(self.dtc_note)
        self.dtc_list = QListWidget()
        diag_layout.addWidget(self.dtc_list)
        self.stack.addWidget(diag_page)

        # Identification
        ident_page = QWidget()
        ident_layout = QVBoxLayout(ident_page)
        self.ident_btn = QPushButton()
        self.ident_btn.setMinimumHeight(40)
        self.ident_btn.setMaximumWidth(340)
        self.ident_btn.clicked.connect(self._read_identification)
        self.ident_result_label = QLabel("")
        self.ident_result_label.setStyleSheet("font-size: 14px;")
        self.ident_result_label.setWordWrap(True)
        self.ident_result_label.setTextFormat(Qt.RichText)
        self.ident_result_label.setVisible(False)
        ident_layout.addWidget(self.ident_btn, alignment=Qt.AlignCenter)
        ident_layout.addSpacing(20)
        ident_layout.addWidget(self.ident_result_label)
        ident_layout.addStretch()
        self.stack.addWidget(ident_page)

        # Bench (not implemented: disabled placeholders that say so)
        bench_page = QWidget()
        bench_layout = QVBoxLayout(bench_page)
        self.bench_title = QLabel()
        self.bench_title.setStyleSheet("color: #ef4444; font-size: 20px; font-weight: bold;")
        self.bench_title.setAlignment(Qt.AlignCenter)
        self.bench_warning = QLabel()
        self.bench_warning.setWordWrap(True)
        self.bench_warning.setAlignment(Qt.AlignCenter)
        self.bench_warning.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; padding: 16px; border-radius: 8px; "
            "border: 1px solid #334155; margin-top: 10px; margin-bottom: 20px;"
        )
        btn_layout = QVBoxLayout()
        self.bench_btn_read = QPushButton()
        self.bench_btn_write = QPushButton()
        self.bench_btn_unlock = QPushButton()
        for btn in (self.bench_btn_read, self.bench_btn_write, self.bench_btn_unlock):
            btn.setEnabled(False)
            btn.setMinimumHeight(40)
            btn_layout.addWidget(btn)
        bench_layout.addWidget(self.bench_title)
        bench_layout.addWidget(self.bench_warning)
        bench_layout.addLayout(btn_layout)
        bench_layout.addStretch()
        self.stack.addWidget(bench_page)

        # Settings
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        lang_row = QHBoxLayout()
        self.lang_label = QLabel()
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("فارسی", "fa")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lang_row.addWidget(self.lang_label)
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch()
        settings_layout.addLayout(lang_row)

        mode_row = QHBoxLayout()
        self.mode_label = QLabel()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("", "sim")
        self.mode_combo.addItem("", "real")
        mode_row.addWidget(self.mode_label)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        settings_layout.addLayout(mode_row)

        self.sim_note = QLabel()
        self.sim_note.setWordWrap(True)
        self.sim_note.setStyleSheet("color:#94a3b8;")
        settings_layout.addWidget(self.sim_note)
        settings_layout.addStretch()
        self.stack.addWidget(settings_page)

        # Log
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_list = QListWidget()
        log_layout.addWidget(self.log_list)
        self.stack.addWidget(log_page)

        self._reload_ports()
        self._switch_tab("dashboard")

    def _layout_gauges(self) -> None:
        while self.gauge_grid.count():
            item = self.gauge_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for i, key in enumerate(_PAGES[self._page]):
            self.gauge_grid.addWidget(self.gauges[key], i // 3, i % 3)
        self.page_label.setText(f"{COPY[self.lang]['page']} {self._page + 1}/{len(_PAGES)}")

    def _set_page(self, page: int) -> None:
        self._page = page
        self._layout_gauges()

    def _switch_tab(self, tab_id: str) -> None:
        index = self.tab_ids.index(tab_id)
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
            btn.setStyleSheet(_TAB_STYLE_ACTIVE if i == index else _TAB_STYLE_IDLE)
        visible = tab_id == "dashboard"
        self.prev_btn.setVisible(visible)
        self.next_btn.setVisible(visible)
        self.page_label.setVisible(visible)

    def _apply_language(self) -> None:
        t = COPY[self.lang]
        self.setWindowTitle(t["title"])
        self.edition_label.setText(t["edition"])
        self.title_label.setText(t["title"])
        self.guard_badge.setText(t["read_only_badge"])
        for btn, tab_id in zip(self.tab_buttons, self.tab_ids, strict=True):
            btn.setText(f"{self.tab_icons.get(tab_id, '')}  {t['tabs'][tab_id]}")

        self.port_label.setText(t["port"])
        self.refresh_ports_btn.setToolTip(t["refresh_ports"])
        self.connect_btn.setText(t["disconnect"] if self.connected else t["connect"])
        self.record_btn.setToolTip(
            t["record_stop"] if self.recorder.recording else t["record_start"]
        )
        self.sim_banner.setText(t["sim_banner"])
        self.dtc_title.setText(t["dtc_title"])
        self.dtc_note.setText(t["dtc_note"])
        self.refresh_dtc_btn.setText(t["refresh_dtc"])
        self.clear_dtc_btn.setText(t["clear_dtc"])
        self.ident_btn.setText(t["ident_btn"])
        self.bench_title.setText(t["bench_title"])
        self.bench_warning.setText(t["bench_warning"])
        self.bench_btn_read.setText(t["bench_btn_read"])
        self.bench_btn_write.setText(t["bench_btn_write"])
        self.bench_btn_unlock.setText(t["bench_btn_unlock"])
        self.lang_label.setText(t["settings_lang"])
        self.mode_label.setText(t["settings_mode"])
        self.mode_combo.setItemText(0, t["mode_sim"])
        self.mode_combo.setItemText(1, t["mode_real"])
        self.sim_note.setText(t["sim_note"])

        for spec in GAUGES:
            self.gauges[spec.key].set_label(t["gauges"][spec.key])
            self.gauges[spec.key].set_unit(t["units"][spec.unit_key])

        self._update_status_pill()
        self._render_snapshot()
        self._render_dtc()
        self._render_identification()
        self._update_recording_label()

        self.setLayoutDirection(Qt.RightToLeft if self.lang == "fa" else Qt.LeftToRight)
        self._layout_gauges()

    def _apply_theme(self) -> None:
        font_id = QFontDatabase.addApplicationFont("Vazirmatn-Regular.ttf")
        font_family = "Vazirmatn" if font_id != -1 else "Segoe UI"
        self.setStyleSheet(
            f"""
            * {{ font-family: '{font_family}', 'Tahoma', sans-serif; }}
            QMainWindow, QWidget {{ background-color: #0f172a; color: #f1f5f9; }}
            QFrame#tool_frame {{
                background-color: #1e293b; border: 1px solid #334155; border-radius: 12px;
            }}
            QComboBox, QListWidget, QLineEdit {{
                background-color: #1e293b; border: 1px solid #334155; border-radius: 8px;
                padding: 6px 12px; color: #f1f5f9; selection-background-color: #6366f1;
            }}
            QComboBox:hover, QListWidget:hover {{ border: 1px solid #475569; }}
            QComboBox:focus {{ border: 1px solid #6366f1; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QPushButton {{
                background-color: #6366f1; color: #ffffff; border: none;
                border-radius: 8px; padding: 8px 16px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #4f46e5; }}
            QPushButton:pressed {{ background-color: #4338ca; }}
            QPushButton:disabled {{ background-color: #334155; color: #64748b; }}
            QLabel {{ color: #f1f5f9; }}
            QStatusBar {{
                background-color: #1e293b; color: #94a3b8; border-top: 1px solid #334155;
                padding: 4px;
            }}
            QStatusBar::item {{ border: none; }}
            """
        )

    def _on_lang_changed(self) -> None:
        data = self.lang_combo.currentData()
        if data in ("fa", "en"):
            self.lang = data  # type: ignore[assignment]
            self._reload_ports()
            self._apply_language()

    def _reload_ports(self) -> None:
        current = self.port_combo.currentData() if self.port_combo.count() else AUTO_PORT
        self.port_combo.clear()
        self.port_combo.addItem(COPY[self.lang]["auto"], AUTO_PORT)
        for port in self._backend.list_serial_ports():
            self.port_combo.addItem(port, port)
        index = self.port_combo.findData(current)
        self.port_combo.setCurrentIndex(max(0, index))

    def _refresh_ports(self) -> None:
        previous = self.port_combo.count()
        self._reload_ports()
        self._append_log("msg.port_scan", previous, self.port_combo.count())

    def _update_status_pill(self) -> None:
        t = COPY[self.lang]
        if self.connected and self._session is not None:
            text = t["sim"] if self._session.simulated else t["connected"]
            self.status_pill.setStyleSheet(_PILL_STYLE_ACTIVE)
        else:
            text = t["idle"]
            self.status_pill.setStyleSheet(_PILL_STYLE_IDLE)
        self.status_pill.setText(text)

    # ----------------------------------------------------------- connection
    def _toggle_connect(self) -> None:
        if self._connecting:
            return
        if self._session is not None:
            self._disconnect()
            return

        t = COPY[self.lang]
        port_data = self.port_combo.currentData()
        port = None if port_data in (None, AUTO_PORT) else str(port_data)
        simulation = self.mode_combo.currentData() == "sim"

        try:
            session = self._backend.create_session(simulation=simulation, port=port)
        except IranObdTunerError as exc:
            self._append_log("msg.adapter_unavailable", exc)
            QMessageBox.warning(self, t["title"], t["adapter_unavailable"].format(exc))
            return

        self._connecting = True
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText(t["connecting"])
        self.port_combo.setEnabled(False)
        self.refresh_ports_btn.setEnabled(False)
        self.mode_combo.setEnabled(False)
        generation = self._generation
        self._runner.submit(
            session.connect,
            lambda _result: self._on_connected(generation, session),
            lambda exc: self._on_connect_failed(generation, exc),
        )

    def _on_connected(self, generation: int, session: LiveDataSession) -> None:
        if generation != self._generation:  # the window was closed meanwhile
            self._runner.submit(session.disconnect, _ignore, _log_background_error)
            return
        self._connecting = False
        self._session = session
        t = COPY[self.lang]
        self._append_log("msg.connected", session.adapter_info.name, session.active_protocol or "?")
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText(t["disconnect"])
        self.record_btn.setEnabled(True)
        self.sim_banner.setVisible(session.simulated)
        self._update_status_pill()
        self._refresh_dtc()
        self._tick()
        self.timer.start()

    def _on_connect_failed(self, generation: int, error: BaseException) -> None:
        if generation != self._generation:
            return
        self._connecting = False
        self._restore_idle_controls()
        t = COPY[self.lang]
        detail = _describe(error)
        self._append_log("msg.connect_error", detail)
        QMessageBox.warning(self, t["title"], t["connection_failed"].format(detail))

    def _restore_idle_controls(self) -> None:
        t = COPY[self.lang]
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText(t["connect"])
        self.port_combo.setEnabled(True)
        self.refresh_ports_btn.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.record_btn.setEnabled(False)
        self.sim_banner.setVisible(False)
        self._update_status_pill()

    def _disconnect(self) -> None:
        """Close the session (on the worker thread) and reset the window."""
        self._generation += 1  # results of jobs still in flight are ignored from now on
        self._connecting = False
        self.timer.stop()
        self._poll_pending = False
        if self.recorder.recording:
            self._stop_recording()
        session, self._session = self._session, None
        if session is not None:
            self._runner.submit(session.disconnect, _ignore, _log_background_error)
        self._last_snapshot = None
        self._dtc_state = None
        self._ident_state = None
        self.spark.clear()
        self._restore_idle_controls()
        self._render_snapshot()
        self._render_dtc()
        self._render_identification()
        if session is not None:
            self._append_log("msg.disconnected")

    def _tick(self) -> None:
        session = self._session
        if session is None or self._poll_pending:
            return  # never queue a second poll behind a slow one
        self._poll_pending = True
        generation = self._generation
        self._runner.submit(
            lambda: session.poll(DASHBOARD_PIDS),
            lambda snapshot: self._on_snapshot(generation, snapshot),
            lambda exc: self._on_poll_failed(generation, exc),
        )

    def _on_snapshot(self, generation: int, snapshot: LiveSnapshot) -> None:
        if generation != self._generation:
            return
        self._poll_pending = False
        self._last_snapshot = snapshot
        self._render_snapshot()
        self.spark.push(snapshot.value_for("010C"), snapshot.value_for("010D"))
        if self.recorder.recording:
            self.recorder.write(snapshot)
        self._update_recording_label()

    def _on_poll_failed(self, generation: int, error: BaseException) -> None:
        if generation != self._generation:
            return
        self._poll_pending = False
        self._append_log("msg.connection_lost", _describe(error))
        self._disconnect()

    def _render_snapshot(self) -> None:
        t = COPY[self.lang]
        snapshot = self._last_snapshot
        for spec in GAUGES:
            card = self.gauges[spec.key]
            reading = None if snapshot is None else snapshot.reading_for(spec.pid)
            if reading is None:
                card.set_unavailable("" if snapshot is None else t["pid_status"]["no_data"])
            elif reading.measurement is not None:
                card.set_value(reading.measurement.value)
            else:
                card.set_unavailable(t["pid_status"][_STATUS_KEYS[reading.status]])

    # ------------------------------------------------------------ trouble codes
    def _refresh_dtc(self) -> None:
        session = self._session
        if session is None:
            self._dtc_state = None
            self._render_dtc()
            return
        generation = self._generation
        self._runner.submit(
            session.read_trouble_codes,
            lambda codes: self._on_dtc(generation, codes),
            lambda exc: self._on_dtc(generation, exc),
        )

    def _on_dtc(self, generation: int, result: tuple[TroubleCode, ...] | BaseException) -> None:
        if generation != self._generation:
            return
        if isinstance(result, BaseException):
            self._dtc_state = _describe(result)
            self._append_log("msg.dtc_read_error", self._dtc_state)
        else:
            self._dtc_state = result
            self._append_log("msg.dtc_read", len(result))
        self._render_dtc()

    def _render_dtc(self) -> None:
        t = COPY[self.lang]
        self.dtc_list.clear()
        state = self._dtc_state
        if state is None:
            self.dtc_list.addItem(t["dtc_not_connected"])
        elif isinstance(state, str):
            self.dtc_list.addItem(t["dtc_read_failed"].format(state))
        elif not state:
            self.dtc_list.addItem(t["no_dtc"])
        else:
            for code in state:
                description = code.description or t["dtc_unknown_description"]
                self.dtc_list.addItem(f"{code.code}  —  {description}")

    def _clear_dtc(self) -> None:
        """Forward the request to the adapter's safety gate and explain the refusal.

        Runs on the GUI thread on purpose: the gate refuses before any I/O happens.
        """
        if self._session is not None:
            try:
                self._session.request_clear_trouble_codes()
            except (SafetyGuardError, UnsupportedOperationError) as exc:
                _LOG.info("clear trouble codes refused: %s", exc)
        self._append_log("msg.clear_dtc_refused")
        self._show_safety_modal(COPY[self.lang]["safety_clear_dtc_msg"])

    def _show_safety_modal(self, message: str) -> None:
        if self._safety_modal is not None:
            self._safety_modal.close()
        self._safety_modal = SafetyModal(self.lang, message, self)
        self._safety_modal.open()

    # ------------------------------------------------------------ identification
    def _read_identification(self) -> None:
        session = self._session
        if session is None:
            self._ident_state = ("ident_not_connected", "")
            self._render_identification()
            return
        generation = self._generation
        self._runner.submit(
            session.read_vehicle_info,
            lambda info: self._on_identification(generation, info),
            lambda exc: self._on_identification(generation, exc),
        )

    def _on_identification(self, generation: int, result: VehicleInfo | BaseException) -> None:
        if generation != self._generation:
            return
        if isinstance(result, BaseException):
            self._ident_state = ("ident_failed", _describe(result))
            self._append_log("msg.ident_error", _describe(result))
        else:
            self._ident_state = result
            self._append_log("msg.ident_read", result.vin or "-")
        self._render_identification()

    def _render_identification(self) -> None:
        t = COPY[self.lang]
        state = self._ident_state
        if state is None:
            self.ident_result_label.setVisible(False)
            return
        if isinstance(state, tuple):
            key, detail = state
            text = html.escape(t[key].format(detail))
        else:
            unknown = t["ident_unknown"]
            source = (
                f"<span style='color: #fbbf24;'>{html.escape(t['ident_simulated'])}</span>"
                if state.simulated
                else html.escape(t["ident_real"])
            )
            text = (
                f"<b>{html.escape(t['ident_vin'])}</b> "
                f"<code>{html.escape(state.vin or unknown)}</code><br><br>"
                f"<b>{html.escape(t['ident_protocol'])}</b> "
                f"{html.escape(state.protocol or unknown)}<br><br>"
                f"<b>{html.escape(t['ident_adapter'])}</b> "
                f"{html.escape(state.adapter_name or unknown)}<br><br>"
                f"{source}"
            )
        self.ident_result_label.setText(text)
        self.ident_result_label.setVisible(True)

    # ------------------------------------------------------------ recording
    def _toggle_recording(self) -> None:
        if self.recorder.recording:
            self._stop_recording()
            return
        if self._session is None:
            return
        t = COPY[self.lang]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = "_simulated" if self._session.simulated else ""
        default_path = self._backend.output_dir / f"live_{stamp}{suffix}.csv"
        chosen, _ = QFileDialog.getSaveFileName(
            self, t["save_dialog_title"], str(default_path), t["save_dialog_filter"]
        )
        if not chosen:
            return
        try:
            path = self.recorder.start(Path(chosen), DASHBOARD_PIDS)
        except OSError as exc:
            self._append_log("msg.recording_error", exc)
            QMessageBox.warning(self, t["title"], t["recording_failed"].format(exc))
            return
        self.record_btn.setText("⏹")
        self.record_btn.setStyleSheet(_RECORD_STYLE_ACTIVE)
        self.record_btn.setToolTip(t["record_stop"])
        self._append_log("msg.recording_started", path)
        self._update_recording_label()

    def _stop_recording(self) -> None:
        count = self.recorder.count
        path = self.recorder.stop()
        self.record_btn.setText("⏺")
        self.record_btn.setStyleSheet("")
        self.record_btn.setToolTip(COPY[self.lang]["record_start"])
        self._append_log("msg.recording_stopped", count, path)
        self._update_recording_label()

    def _update_recording_label(self) -> None:
        if self.recorder.recording:
            text = COPY[self.lang]["recording_samples"].format(self.recorder.count)
        else:
            text = ""
        self.recording_label.setText(text)

    # ------------------------------------------------------------ log
    def _append_log(self, key: str, *args: object) -> None:
        text = format_message(self.lang, key, *args)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_list.insertItem(0, f"{stamp}  {text}")
        self.status_bar.showMessage(text, 5000)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._disconnect()
        # Stops the worker thread and runs the queued disconnect, so no port stays open.
        self._runner.shutdown()
        super().closeEvent(event)
