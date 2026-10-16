"""Phase 1 main window: dashboard, DTC list, settings and log.

Needs the optional ``[gui]`` extra (PySide6). Read-only: reading live data and stored
trouble codes is allowed, while clearing codes is refused by the safety policy and reported
in the UI (see :meth:`MainWindow._clear_dtc`). Constructing the window does not open an
adapter: it only lists serial ports for the port selector, and connects on user request.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.diagnostics.recorder import CsvRecorder
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.diagnostics.obd_service import LiveSample, ObdService
from iran_obd_tuner.ui.gauges import GaugeCard
from iran_obd_tuner.ui.i18n import COPY, Lang


class Sparkline(QWidget):
    """Tiny live chart for RPM / speed history."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rpm: deque[float] = deque(maxlen=60)
        self._speed: deque[float] = deque(maxlen=60)
        self.setMinimumHeight(220)

    def push(self, rpm: float, speed: float) -> None:
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
        painter.fillRect(self.rect(), QColor("#26272c"))

        if len(self._rpm) < 2:
            painter.setPen(QColor("#9b9ca4"))
            painter.drawText(self.rect(), Qt.AlignCenter, "—")
            return

        def draw_series(values: deque[float], color: QColor, vmax: float) -> None:
            if not values:
                return
            w = max(1, self.width() - 20)
            h = max(1, self.height() - 20)
            step = w / max(1, len(values) - 1)
            pen = QPen(color, 2)
            painter.setPen(pen)
            points: list[tuple[float, float]] = []
            for i, v in enumerate(values):
                x = 10 + i * step
                y = 10 + h * (1.0 - min(1.0, v / vmax))
                points.append((x, y))
            for i in range(1, len(points)):
                painter.drawLine(
                    int(points[i - 1][0]),
                    int(points[i - 1][1]),
                    int(points[i][0]),
                    int(points[i][1]),
                )

        draw_series(self._rpm, QColor("#2f86e8"), 7000)
        draw_series(self._speed, QColor("#f3f4f6"), 220)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.lang: Lang = "fa"
        self.service = ObdService()
        self.service.on_log = self._append_log
        self.recorder = CsvRecorder()
        self._page = 0

        self.setMinimumSize(1000, 700)
        self.status_bar = self.statusBar()
        self.recording_label = QLabel("")
        self.status_bar.addPermanentWidget(self.recording_label)
        self._build_ui()
        self._apply_language()
        self._apply_dark_theme()

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        self.edition_label = QLabel()
        self.edition_label.setStyleSheet("color: #2f86e8; font-weight: 600;")
        self.title_label = QLabel()
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        title_box.addWidget(self.edition_label)
        title_box.addWidget(self.title_label)
        header.addLayout(title_box)
        header.addStretch()
        self.status_pill = QLabel()
        self.status_pill.setStyleSheet(
            "background:#313238; color:#9b9ca4; border-radius:12px; padding:6px 12px;"
        )
        header.addWidget(self.status_pill)
        outer.addLayout(header)

        # Tabs
        tab_row = QHBoxLayout()
        self.tab_buttons: list[QPushButton] = []
        self.tab_ids = ["dashboard", "graph", "diag", "settings", "log"]
        for tab_id in self.tab_ids:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            btn.clicked.connect(lambda checked=False, t=tab_id: self._switch_tab(t))
            self.tab_buttons.append(btn)
            tab_row.addWidget(btn)
        tab_row.addStretch()
        outer.addLayout(tab_row)

        # Toolbar: port + connect
        tool = QHBoxLayout()
        self.port_label = QLabel()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(160)
        self.refresh_ports_btn = QPushButton("🔄")
        self.refresh_ports_btn.setFixedSize(40, 40)
        self.refresh_ports_btn.clicked.connect(self._refresh_ports)
        self._reload_ports()
        self.connect_btn = QPushButton()
        self.connect_btn.setMinimumHeight(40)
        self.connect_btn.setMinimumWidth(140)
        self.connect_btn.clicked.connect(self._toggle_connect)

        self.prev_btn = QPushButton("<")
        self.next_btn = QPushButton(">")
        self.page_label = QLabel()
        self.prev_btn.setFixedSize(40, 40)
        self.next_btn.setFixedSize(40, 40)
        self.prev_btn.clicked.connect(lambda: self._set_page(0))
        self.next_btn.clicked.connect(lambda: self._set_page(1))

        tool.addWidget(self.port_label)
        tool.addWidget(self.port_combo)
        tool.addWidget(self.refresh_ports_btn)
        tool.addWidget(self.connect_btn)
        self.record_btn = QPushButton("⏺")
        self.record_btn.setMinimumHeight(40)
        self.record_btn.setMinimumWidth(50)
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self._toggle_recording)
        tool.addWidget(self.record_btn)
        tool.addStretch()
        tool.addWidget(self.prev_btn)
        tool.addWidget(self.page_label)
        tool.addWidget(self.next_btn)
        outer.addLayout(tool)

        # Pages
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # Dashboard gauges
        dash = QWidget()
        self.gauge_grid = QGridLayout(dash)
        self.gauge_grid.setSpacing(10)
        t = COPY[self.lang]
        self.gauges = {
            "rpm": GaugeCard(t["gauges"]["rpm"], t["units"]["rpm"], 7000),
            "speed": GaugeCard(t["gauges"]["speed"], t["units"]["kmh"], 220),
            "coolant": GaugeCard(t["gauges"]["coolant"], t["units"]["c"], 130),
            "voltage": GaugeCard(t["gauges"]["voltage"], t["units"]["v"], 16, digits=1),
            "fuel": GaugeCard(t["gauges"]["fuel"], t["units"]["pct"], 100),
            "throttle": GaugeCard(t["gauges"]["throttle"], t["units"]["pct"], 100),
            "iat": GaugeCard(t["gauges"]["iat"], t["units"]["c"], 80),
            "load": GaugeCard(t["gauges"]["load"], t["units"]["pct"], 100),
        }
        self.stack.addWidget(dash)
        self._layout_gauges()

        # Graph
        graph_page = QWidget()
        graph_layout = QVBoxLayout(graph_page)
        self.spark = Sparkline()
        graph_layout.addWidget(self.spark)
        self.stack.addWidget(graph_page)

        # Diagnostics
        diag_page = QWidget()
        diag_layout = QVBoxLayout(diag_page)
        diag_header = QHBoxLayout()
        self.dtc_title = QLabel()
        self.refresh_dtc_btn = QPushButton()
        self.clear_dtc_btn = QPushButton()
        self.refresh_dtc_btn.clicked.connect(self._refresh_dtc)
        self.clear_dtc_btn.clicked.connect(self._clear_dtc)
        diag_header.addWidget(self.dtc_title)
        diag_header.addStretch()
        diag_header.addWidget(self.refresh_dtc_btn)
        diag_header.addWidget(self.clear_dtc_btn)
        diag_layout.addLayout(diag_header)
        self.dtc_list = QListWidget()
        diag_layout.addWidget(self.dtc_list)
        self.stack.addWidget(diag_page)

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
        self.sim_note.setStyleSheet("color:#9b9ca4;")
        settings_layout.addWidget(self.sim_note)
        settings_layout.addStretch()
        self.stack.addWidget(settings_page)

        # Log
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_list = QListWidget()
        log_layout.addWidget(self.log_list)
        self.stack.addWidget(log_page)

        self._switch_tab("dashboard")

    def _layout_gauges(self) -> None:
        while self.gauge_grid.count():
            item = self.gauge_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        page0 = ["rpm", "speed", "coolant", "voltage", "fuel"]
        page1 = ["throttle", "iat", "load"]
        keys = page0 if self._page == 0 else page1
        for i, key in enumerate(keys):
            self.gauge_grid.addWidget(self.gauges[key], i // 3, i % 3)

        t = COPY[self.lang]
        self.page_label.setText(f"{t['page']} {self._page + 1}/2")

    def _set_page(self, page: int) -> None:
        self._page = page
        self._layout_gauges()

    def _switch_tab(self, tab_id: str) -> None:
        index = self.tab_ids.index(tab_id)
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
            if i == index:
                btn.setStyleSheet(
                    "background:#2f86e8; color:#f5f9ff; border:none; "
                    "border-radius:6px; padding:8px 12px;"
                )
            else:
                btn.setStyleSheet(
                    "background:transparent; color:#9b9ca4; border:none; "
                    "border-radius:6px; padding:8px 12px;"
                )
        # page buttons only useful on dashboard
        visible = tab_id == "dashboard"
        self.prev_btn.setVisible(visible)
        self.next_btn.setVisible(visible)
        self.page_label.setVisible(visible)

    def _apply_language(self) -> None:
        t = COPY[self.lang]
        self.setWindowTitle(t["title"])
        self.edition_label.setText(t["edition"])
        self.title_label.setText(t["title"])
        for btn, tab_id in zip(self.tab_buttons, self.tab_ids, strict=True):
            btn.setText(t["tabs"][tab_id])
        self.port_label.setText(t["port"])
        self.connect_btn.setText(t["connect"] if not self.service.connected else t["disconnect"])
        self.dtc_title.setText(t["dtc_title"])
        self.refresh_dtc_btn.setText(t["refresh_dtc"])
        self.clear_dtc_btn.setText(t["clear_dtc"])
        self.lang_label.setText(t["settings_lang"])
        self.mode_label.setText(t["settings_mode"])
        self.mode_combo.setItemText(0, t["mode_sim"])
        self.mode_combo.setItemText(1, t["mode_real"])
        self.sim_note.setText(t["sim_note"])
        self._update_status_pill()

        # gauge labels
        mapping = {
            "rpm": (t["gauges"]["rpm"], t["units"]["rpm"]),
            "speed": (t["gauges"]["speed"], t["units"]["kmh"]),
            "coolant": (t["gauges"]["coolant"], t["units"]["c"]),
            "voltage": (t["gauges"]["voltage"], t["units"]["v"]),
            "fuel": (t["gauges"]["fuel"], t["units"]["pct"]),
            "throttle": (t["gauges"]["throttle"], t["units"]["pct"]),
            "iat": (t["gauges"]["iat"], t["units"]["c"]),
            "load": (t["gauges"]["load"], t["units"]["pct"]),
        }
        for key, (label, unit) in mapping.items():
            self.gauges[key].set_label(label)
            self.gauges[key].gauge.set_unit(unit)

        # RTL for Persian
        direction = Qt.RightToLeft if self.lang == "fa" else Qt.LeftToRight
        self.setLayoutDirection(direction)
        self._layout_gauges()

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #1a1b1f; color: #f3f4f6; }
            QComboBox, QListWidget {
                background: #313238; border: 1px solid #3d3f46; border-radius: 6px; padding: 6px;
            }
            QPushButton {
                background: #2f86e8; color: #f5f9ff; border: none;
                border-radius: 6px; padding: 8px 14px;
            }
            QPushButton:disabled { background: #3d3f46; color: #9b9ca4; }
            QLabel { color: #f3f4f6; }
            QStatusBar { background: #26272c; color: #9b9ca4; }
            QStatusBar::item { border: none; }
            """
        )

    def _on_lang_changed(self) -> None:
        data = self.lang_combo.currentData()
        if data in ("fa", "en"):
            self.lang = data  # type: ignore[assignment]
            self._apply_language()

    def _reload_ports(self) -> None:
        current = self.port_combo.currentText() if self.port_combo.count() else "Auto"
        self.port_combo.clear()
        for port in self.service.list_ports():
            label = COPY[self.lang]["auto"] if port == "Auto" else port
            self.port_combo.addItem(label, port)
        # restore
        idx = self.port_combo.findData(current if current != COPY[self.lang]["auto"] else "Auto")
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    def _refresh_ports(self) -> None:
        """Re-scan serial ports and update the combo box."""
        prev_count = self.port_combo.count()
        self._reload_ports()
        new_count = self.port_combo.count()
        self._append_log(f"Port scan: {prev_count} -> {new_count} ports")

    def _update_status_pill(self) -> None:
        t = COPY[self.lang]
        if self.service.connected:
            text = t["sim"] if self.service.simulation else t["connected"]
            self.status_pill.setStyleSheet(
                "background:#2f86e8; color:#f5f9ff; border-radius:12px; padding:6px 12px;"
            )
        else:
            text = t["idle"]
            self.status_pill.setStyleSheet(
                "background:#313238; color:#9b9ca4; border-radius:12px; padding:6px 12px;"
            )
        self.status_pill.setText(text)

    # ----------------------------------------------------------- connection
    def _toggle_connect(self) -> None:
        if self.service.connected:
            self.service.disconnect()
            self.timer.stop()
            self.spark.clear()
            self._zero_gauges()
            if self.recorder.recording:
                self._toggle_recording()
            self.record_btn.setEnabled(False)
            self.connect_btn.setText(COPY[self.lang]["connect"])
            self.port_combo.setEnabled(True)
            self.refresh_ports_btn.setEnabled(True)
            self.mode_combo.setEnabled(True)
            self._update_status_pill()
            return

        port = self.port_combo.currentData() or "Auto"
        simulation = self.mode_combo.currentData() == "sim"
        ok = self.service.connect(port=str(port), simulation=simulation)
        if not ok:
            QMessageBox.warning(
                self,
                COPY[self.lang]["title"],
                "Connection failed. Try simulation mode or check the adapter.",
            )
            return

        self.connect_btn.setText(COPY[self.lang]["disconnect"])
        self.port_combo.setEnabled(False)
        self.refresh_ports_btn.setEnabled(False)
        self.record_btn.setEnabled(True)
        self.mode_combo.setEnabled(False)
        self._update_status_pill()
        self._refresh_dtc()
        self.timer.start()

    def _zero_gauges(self) -> None:
        for g in self.gauges.values():
            g.set_value(0)

    def _tick(self) -> None:
        sample = self.service.read_live()
        self._apply_sample(sample)
        if self.recorder.recording:
            self.recorder.write(sample)
            self.recording_label.setText(f"⏺ {self.recorder.count} samples")
        else:
            self.recording_label.setText("")

    def _apply_sample(self, s: LiveSample) -> None:
        self.gauges["rpm"].set_value(s.rpm)
        self.gauges["speed"].set_value(s.speed)
        self.gauges["coolant"].set_value(s.coolant)
        self.gauges["voltage"].set_value(s.voltage)
        self.gauges["fuel"].set_value(s.fuel)
        self.gauges["throttle"].set_value(s.throttle)
        self.gauges["iat"].set_value(s.iat)
        self.gauges["load"].set_value(s.load)
        self.spark.push(s.rpm, s.speed)

    def _refresh_dtc(self) -> None:
        self.dtc_list.clear()
        items = self.service.read_dtc()
        t = COPY[self.lang]
        if not items:
            self.dtc_list.addItem(t["no_dtc"])
            return
        for item in items:
            text = f"{item.code}  —  {item.description}"
            self.dtc_list.addItem(QListWidgetItem(text))

    def _toggle_recording(self) -> None:
        """Start or stop recording live samples to CSV."""
        if self.recorder.recording:
            path = self.recorder.stop()
            count = self.recorder.count
            self.record_btn.setText("⏺")
            self.record_btn.setStyleSheet("")
            self._append_log(f"Recording stopped ({count} samples): {path}")
        else:
            path = self.recorder.start()
            self.record_btn.setText("⏹")
            self.record_btn.setStyleSheet(
                "background:#c0392b; color:#f5f9ff; border:none; border-radius:6px;"
            )
            self._append_log(f"Recording started: {path}")

    def _clear_dtc(self) -> None:
        """Clear stored trouble codes — refused by the safety policy in this build.

        The button stays part of the phase 1 layout, but the refusal is reported to the user
        and the log instead of crashing the window or silently doing nothing.
        """
        try:
            cleared = self.service.clear_dtc()
        except SafetyGuardError as exc:
            self._append_log(str(exc))
            QMessageBox.information(
                self,
                COPY[self.lang]["title"],
                COPY[self.lang]["clear_blocked"],
            )
            return
        if cleared:
            self._refresh_dtc()

    def _append_log(self, message: str) -> None:
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_list.insertItem(0, f"{stamp}  {message}")
        self.status_bar.showMessage(message, 5000)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        self.service.disconnect()
        super().closeEvent(event)
