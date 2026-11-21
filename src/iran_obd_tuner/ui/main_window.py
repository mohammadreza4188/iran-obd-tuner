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
after a disconnect are dropped.

The Identification tab also hosts :class:`~iran_obd_tuner.ui.uds_view.UdsView`: UDS over
CAN, independent of the OBD connection. Each click asks the backend for a one-shot
``UdsIdentificationSession`` and runs it on the same background thread.
"""

from __future__ import annotations

import html
import time
from collections import deque
from collections.abc import Callable
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
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner import __version__
from iran_obd_tuner.core.exceptions import (
    EvidenceExportError,
    IranObdTunerError,
    SafetyGuardError,
    UnsupportedOperationError,
    VehicleSilentError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.diagnostics.ecu_identification import EcuIdentification
from iran_obd_tuner.diagnostics.evidence_export import build_evidence_bundle, write_bundle
from iran_obd_tuner.diagnostics.live_data import LiveDataSession, LiveSnapshot, PidStatus
from iran_obd_tuner.diagnostics.models import (
    DtcCategoryResult,
    FreezeFrameSnapshot,
    MonitorStatusResult,
)
from iran_obd_tuner.diagnostics.recorder import CsvRecorder
from iran_obd_tuner.diagnostics.vehicle_matcher import ObservedFacts, rank_candidates
from iran_obd_tuner.ui.backend import DashboardBackend
from iran_obd_tuner.ui.bench_view import BenchView
from iran_obd_tuner.ui.diagnostics_view import DiagnosticsView
from iran_obd_tuner.ui.gauges import GaugeCard
from iran_obd_tuner.ui.i18n import COPY, Lang, format_message
from iran_obd_tuner.ui.icon import build_icon
from iran_obd_tuner.ui.knowledge_view import KnowledgeView
from iran_obd_tuner.ui.performance_view import FAST_POLL_PIDS, PerformanceView
from iran_obd_tuner.ui.safety_modal import SafetyModal
from iran_obd_tuner.ui.traffic_view import TrafficView
from iran_obd_tuner.ui.uds_view import UdsView
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
_MIL_STYLE_BASE = "font-weight: bold; font-size: 11px; border-radius: 4px; padding: 2px 8px; "
_MIL_STYLE_ON = _MIL_STYLE_BASE + "color: #ffffff; background-color: #dc2626;"
_MIL_STYLE_OFF = _MIL_STYLE_BASE + "color: #10b981; border: 1px solid #10b981;"
_MIL_STYLE_UNKNOWN = _MIL_STYLE_BASE + "color: #94a3b8; border: 1px solid #334155;"
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
        # PID 0101 result of the open session; None while disconnected.
        self._mil_state: MonitorStatusResult | None = None
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
        self.mil_badge = QLabel()
        self.mil_badge.setStyleSheet(_MIL_STYLE_UNKNOWN)
        badge_box.addWidget(self.version_label)
        badge_box.addWidget(self.mil_badge)
        badge_box.addWidget(self.guard_badge)
        badge_box.addWidget(self.status_pill)
        header.addLayout(badge_box)
        outer.addLayout(header)
        outer.addSpacing(8)

        # --- Navbar tabs ---
        tab_row = QHBoxLayout()
        tab_row.setSpacing(8)
        self.tab_buttons: list[QPushButton] = []
        self.tab_ids = [
            "dashboard",
            "graph",
            "diag",
            "perf",
            "ident",
            "kb",
            "bench",
            "settings",
            "log",
        ]
        self.tab_icons = {
            "dashboard": "📊",
            "graph": "📈",
            "diag": "⚠️",
            "perf": "⏱️",
            "ident": "📡",
            "kb": "📚",
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

        # Trouble codes (stored / pending / permanent) and freeze frame 00
        self.diag_view = DiagnosticsView()
        self.diag_view.refresh_btn.clicked.connect(self._refresh_dtc)
        self.diag_view.freeze_btn.clicked.connect(self._read_freeze_frame)
        self.diag_view.clear_btn.clicked.connect(self._clear_dtc)
        self.diag_view.set_connected(False)
        self.stack.addWidget(self.diag_view)

        # Performance: 0-100 timer and virtual dyno (passive, estimated)
        self.perf_view = PerformanceView(self._backend.dyno_parameters)
        perf_scroll = QScrollArea()  # the tab is taller than small windows
        perf_scroll.setWidgetResizable(True)
        perf_scroll.setWidget(self.perf_view)
        self.stack.addWidget(perf_scroll)

        # Identification
        ident_page = QWidget()
        ident_layout = QVBoxLayout(ident_page)
        self.ident_obd_title = QLabel()
        self.ident_obd_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        ident_layout.addWidget(self.ident_obd_title)
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
        ident_layout.addSpacing(12)
        ident_layout.addWidget(self.ident_result_label)
        ident_layout.addSpacing(12)
        # UDS over CAN: independent of the OBD connection above (its own one-shot link).
        self.uds_view = UdsView(self._backend.can_defaults)
        self.uds_view.discover_requested.connect(self._discover_ecus)
        self.uds_view.read_requested.connect(self._read_uds_identification)
        ident_layout.addWidget(self.uds_view)
        ident_layout.addStretch()
        ident_scroll = QScrollArea()
        ident_scroll.setWidgetResizable(True)
        ident_scroll.setFrameShape(QFrame.NoFrame)
        ident_scroll.setWidget(ident_page)
        self.stack.addWidget(ident_scroll)

        # Vehicle knowledge base: browse claims, passive match, Field Evidence Bundle export
        self.kb_view = KnowledgeView(
            self._backend.vehicle_profiles,
            self._backend.vehicle_manufacturers,
            self._backend.vehicle_guidance,
            self._backend.vehicle_knowledge_source,
        )
        self.kb_view.match_requested.connect(self._match_vehicle)
        self.kb_view.export_requested.connect(self._export_evidence)
        kb_scroll = QScrollArea()
        kb_scroll.setWidgetResizable(True)
        kb_scroll.setFrameShape(QFrame.NoFrame)
        kb_scroll.setWidget(self.kb_view)
        self.stack.addWidget(kb_scroll)

        # Bench: offline ROM file analysis (Phase 4); ECU access (Phase 5) stays locked
        self.bench_view = BenchView(self._backend.create_bench_workspace(), self._runner.submit)
        self.bench_title = self.bench_view.title_label
        bench_scroll = QScrollArea()
        bench_scroll.setWidgetResizable(True)
        bench_scroll.setFrameShape(QFrame.NoFrame)
        bench_scroll.setWidget(self.bench_view)
        self.stack.addWidget(bench_scroll)

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

        # Log: application events + raw adapter traffic, as two sub-tabs
        self.log_tabs = QTabWidget()
        self.log_list = QListWidget()
        self.traffic_view = TrafficView(self._backend.traffic_log)
        self.log_tabs.addTab(self.log_list, "")
        self.log_tabs.addTab(self.traffic_view, "")
        self.stack.addWidget(self.log_tabs)

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
        self.diag_view.set_language(self.lang)
        self.perf_view.set_language(self.lang)
        self.traffic_view.set_language(self.lang)
        self.log_tabs.setTabText(0, t["log_tab_events"])
        self.log_tabs.setTabText(1, t["log_tab_traffic"])
        self.ident_btn.setText(t["ident_btn"])
        self.ident_obd_title.setText(t["ident_obd_title"])
        self.uds_view.set_language(self.lang)
        self.kb_view.set_language(self.lang)
        self.bench_view.set_language(self.lang)
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
        self._render_mil()
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
        self.diag_view.set_connected(True)
        self.perf_view.set_connected(True)
        self._refresh_kb_availability()
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
        self._mil_state = None
        self._ident_state = None
        self.spark.clear()
        self._restore_idle_controls()
        self._render_snapshot()
        self.diag_view.set_connected(False)
        self.perf_view.set_connected(False)
        self._render_mil()
        self._render_identification()
        self._refresh_kb_availability()
        if session is not None:
            self._append_log("msg.disconnected")

    def _tick(self) -> None:
        session = self._session
        if session is None or self._poll_pending:
            return  # never queue a second poll behind a slow one
        self._poll_pending = True
        generation = self._generation
        pids = FAST_POLL_PIDS if self.perf_view.wants_fast_poll else DASHBOARD_PIDS
        self._runner.submit(
            lambda: session.poll(pids),
            lambda snapshot: self._on_snapshot(generation, snapshot),
            lambda exc: self._on_poll_failed(generation, exc),
        )

    def _on_snapshot(self, generation: int, snapshot: LiveSnapshot) -> None:
        if generation != self._generation:
            return
        self._poll_pending = False
        self._last_snapshot = snapshot
        self._render_snapshot()
        self.perf_view.feed_snapshot(snapshot, time.monotonic())
        self.spark.push(snapshot.value_for("010C"), snapshot.value_for("010D"))
        if self.recorder.recording:
            self.recorder.write(snapshot)
        self._update_recording_label()

    def _on_poll_failed(self, generation: int, error: BaseException) -> None:
        if generation != self._generation:
            return
        self._poll_pending = False
        if isinstance(error, VehicleSilentError):
            # Circuit breaker: the vehicle stopped answering (typically: ignition off).
            self._append_log("msg.vehicle_silent", _describe(error))
            self._disconnect()
            t = COPY[self.lang]
            QMessageBox.warning(self, t["title"], t["vehicle_silent"])
            return
        self._append_log("msg.connection_lost", _describe(error))
        self._disconnect()

    def _render_snapshot(self) -> None:
        t = COPY[self.lang]
        snapshot = self._last_snapshot
        for spec in GAUGES:
            card = self.gauges[spec.key]
            reading = None if snapshot is None else snapshot.reading_for(spec.pid)
            if reading is None:
                if snapshot is None:
                    card.set_unavailable("")
                elif set(FAST_POLL_PIDS) >= {r.pid for r in snapshot.readings}:
                    card.set_unavailable(t["pid_status"]["fast_mode"])  # not polled right now
                else:
                    card.set_unavailable(t["pid_status"]["no_data"])
            elif reading.measurement is not None:
                card.set_value(reading.measurement.value)
            else:
                card.set_unavailable(t["pid_status"][_STATUS_KEYS[reading.status]])

    # ------------------------------------------------------------ trouble codes
    def _refresh_dtc(self) -> None:
        """Read PID 0101 and the three DTC categories on the worker thread."""
        session = self._session
        if session is None:
            return
        generation = self._generation

        def job() -> tuple[MonitorStatusResult, tuple[DtcCategoryResult, ...]]:
            return session.read_monitor_status(), session.read_trouble_code_categories()

        self._runner.submit(
            job,
            lambda result: self._on_dtc(generation, result),
            lambda exc: self._on_dtc(generation, exc),
        )

    def _on_dtc(
        self,
        generation: int,
        result: tuple[MonitorStatusResult, tuple[DtcCategoryResult, ...]] | BaseException,
    ) -> None:
        if generation != self._generation:
            return
        if isinstance(result, BaseException):
            detail = _describe(result)
            self._append_log("msg.dtc_read_error", detail)
            self.diag_view.show_categories(detail)
            return
        monitor, categories = result
        self._mil_state = monitor
        self._render_mil()
        self.diag_view.show_categories(categories)
        self._append_log("msg.dtc_read", sum(len(c.codes) for c in categories))

    def _read_freeze_frame(self) -> None:
        session = self._session
        if session is None:
            return
        generation = self._generation
        self._runner.submit(
            session.read_freeze_frame,
            lambda frame: self._on_freeze_frame(generation, frame),
            lambda exc: self._on_freeze_frame(generation, exc),
        )

    def _on_freeze_frame(
        self, generation: int, result: FreezeFrameSnapshot | BaseException
    ) -> None:
        if generation != self._generation:
            return
        if isinstance(result, BaseException):
            detail = _describe(result)
            self._append_log("msg.ff_error", detail)
            self.diag_view.show_freeze_frame(detail)
            return
        trigger = result.trigger.code if result.trigger is not None else "-"
        self._append_log("msg.ff_read", trigger)
        self.diag_view.show_freeze_frame(result)

    def _render_mil(self) -> None:
        mil = COPY[self.lang]["mil"]
        state = self._mil_state
        tooltip = ""
        if state is None:
            text, style = mil["disconnected"], _MIL_STYLE_UNKNOWN
        elif state.status is None:
            text, style = mil["unknown"], _MIL_STYLE_UNKNOWN
            tooltip = state.detail or ""
        elif state.status.mil_on:
            text, style = mil["on"].format(state.status.confirmed_dtc_count), _MIL_STYLE_ON
        else:
            text, style = mil["off"], _MIL_STYLE_OFF
        if state is not None and state.status is not None:
            incomplete = ", ".join(state.status.incomplete_monitors) or "-"
            tooltip = COPY[self.lang]["mil_incomplete"].format(incomplete)
        self.mil_badge.setText(text)
        self.mil_badge.setStyleSheet(style)
        self.mil_badge.setToolTip(tooltip)

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
        self._refresh_kb_availability()

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

    # ------------------------------------------------------------ UDS identification
    def _start_uds_job(self, job_name: str) -> None:
        """Build a one-shot UDS session from the panel's choices and run ``job_name``."""
        interface, channel, bitrate, extended = self.uds_view.selection()
        try:
            session = self._backend.create_uds_session(
                interface=interface,
                channel=channel,
                bitrate=bitrate,
                allow_extended_session=extended,
            )
        except IranObdTunerError as exc:
            self._on_uds_failed(exc)
            return
        self.uds_view.set_busy(True)
        if job_name == "discover":
            self._runner.submit(session.discover, self._on_ecus_discovered, self._on_uds_failed)
        else:
            self._runner.submit(session.identify, self._on_uds_identified, self._on_uds_failed)

    def _discover_ecus(self) -> None:
        self._start_uds_job("discover")

    def _read_uds_identification(self) -> None:
        self._start_uds_job("identify")

    def _on_ecus_discovered(self, response_ids: tuple[int, ...]) -> None:
        self.uds_view.set_busy(False)
        self.uds_view.show_discovery(response_ids)
        self._append_log(
            "msg.uds_discovered", ", ".join(f"{rid:03X}" for rid in response_ids) or "-"
        )

    def _on_uds_identified(self, result: EcuIdentification) -> None:
        self.uds_view.set_busy(False)
        self.uds_view.show_result(result)
        self._append_log("msg.uds_read", result.interface, result.vin or "-")
        self._refresh_kb_availability()

    def _on_uds_failed(self, error: BaseException) -> None:
        self.uds_view.set_busy(False)
        self.uds_view.show_error(_describe(error))
        self._append_log("msg.uds_error", _describe(error))
        self._refresh_kb_availability()

    # ------------------------------------------------------------ vehicle knowledge
    def _uds_result(self) -> EcuIdentification | None:
        result = self.uds_view.result
        return result if isinstance(result, EcuIdentification) else None

    def _vehicle_info(self) -> VehicleInfo | None:
        state = self._ident_state
        return state if isinstance(state, VehicleInfo) else None

    def _refresh_kb_availability(self) -> None:
        available = (
            self._session is not None
            or self._uds_result() is not None
            or self._vehicle_info() is not None
        )
        self.kb_view.set_session_available(available)

    def _gather_session(
        self, then: Callable[[str | None, tuple[str, ...], bool, str | None], None]
    ) -> None:
        """Read protocol and supported PIDs on the worker (no request is sent), then call back.

        Without an OBD session the callback runs at once with empty OBD facts.
        """
        session = self._session
        if session is None:
            then(None, (), False, None)
            return
        generation = self._generation

        def job() -> tuple[str | None, tuple[str, ...]]:
            try:
                pids = session.supported_pids()
            except IranObdTunerError:
                pids = ()
            return session.active_protocol, pids

        def done(result: tuple[str | None, tuple[str, ...]]) -> None:
            if generation == self._generation:
                then(result[0], result[1], session.simulated, session.adapter_info.name)

        self._runner.submit(job, done, self._on_kb_failed)

    def _match_vehicle(self) -> None:
        self._gather_session(self._on_match_data)

    def _on_match_data(
        self, protocol: str | None, pids: tuple[str, ...], simulated: bool, _adapter: str | None
    ) -> None:
        facts = ObservedFacts.from_session(
            vehicle_info=self._vehicle_info(),
            active_protocol=protocol,
            supported_pids=pids,
            identification=self._uds_result(),
            simulated=simulated,
        )
        report = rank_candidates(
            self._backend.vehicle_profiles,
            facts,
            self._backend.vehicle_manufacturers,
            knowledge_source=self._backend.vehicle_knowledge_source,
        )
        self.kb_view.show_match(report)
        self._append_log("msg.kb_match", len(report.candidates))

    def _on_kb_failed(self, error: BaseException) -> None:
        self.kb_view.show_match_error(_describe(error))
        self._append_log("msg.kb_error", _describe(error))

    def _export_evidence(self) -> None:
        self._gather_session(self._write_evidence)

    def _write_evidence(
        self, protocol: str | None, pids: tuple[str, ...], simulated: bool, adapter: str | None
    ) -> None:
        t = COPY[self.lang]
        uds = self._uds_result()
        any_simulated = simulated or bool(uds is not None and uds.simulated)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = "_simulated" if any_simulated else ""
        default_path = self._backend.output_dir / f"evidence_{stamp}{suffix}.json"
        chosen, _ = QFileDialog.getSaveFileName(
            self, t["kb_export_dialog"], str(default_path), t["kb_export_filter"]
        )
        if not chosen:
            return
        log = self._backend.traffic_log
        try:
            bundle = build_evidence_bundle(
                tool_version=__version__,
                vehicle_info=self._vehicle_info(),
                adapter_name=adapter,
                active_protocol=protocol,
                supported_pids=pids,
                obd_simulated=simulated,
                identification=uds,
                traffic=log.snapshot(),
                dropped_traffic=log.dropped_count,
                operator=self.kb_view.operator_context(),
                knowledge=self._backend.vehicle_profiles,
                redact_vin=self.kb_view.redact_vin(),
            )
            path = write_bundle(Path(chosen), bundle)
        except EvidenceExportError as exc:
            self.kb_view.show_export_error(str(exc))
            self._append_log("msg.kb_export_error", exc)
            return
        self.kb_view.show_export_result(str(path), bundle.simulated)
        self._append_log("msg.kb_export", path)

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
