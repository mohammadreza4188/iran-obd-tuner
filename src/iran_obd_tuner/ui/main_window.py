"""Phase 1 main window: dashboard, DTC list, settings and log. (Tailwind-inspired UI)"""

from __future__ import annotations

from collections import deque
from datetime import datetime
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
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.diagnostics.obd_service import LiveSample, ObdService
from iran_obd_tuner.diagnostics.recorder import CsvRecorder
from iran_obd_tuner.tuning.ecu_id import MockEcuIdentifier
from iran_obd_tuner.ui.gauges import GaugeCard
from iran_obd_tuner.ui.i18n import COPY, Lang
from iran_obd_tuner.ui.icon import build_icon
from iran_obd_tuner.ui.safety_modal import SafetyModal


class Sparkline(QWidget):
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
        painter.fillRect(self.rect(), QColor("#0f172a"))

        if len(self._rpm) < 2:
            painter.setPen(QColor("#94a3b8"))
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
            points = []
            for i, v in enumerate(values):
                x = 10 + i * step
                y = 10 + h * (1.0 - min(1.0, v / vmax))
                points.append((x, y))
            for i in range(1, len(points)):
                painter.drawLine(int(points[i - 1][0]), int(points[i - 1][1]), int(points[i][0]), int(points[i][1]))

        draw_series(self._rpm, QColor("#6366f1"), 7000)
        draw_series(self._speed, QColor("#f1f5f9"), 220)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.lang: Lang = "fa"
        self.service = ObdService()
        self.service.on_log = self._append_log
        self.recorder = CsvRecorder()
        self._page = 0

        self.setMinimumSize(1100, 800)
        self.setWindowIcon(build_icon())
        self.status_bar = self.statusBar()
        self.recording_label = QLabel("")
        self.status_bar.addPermanentWidget(self.recording_label)
        
        self._build_ui()
        self._apply_language()
        self._apply_theme()

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        # --- Header ---
        header = QHBoxLayout()
        
        # Icon & Title
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

        # Version & Badges
        badge_box = QHBoxLayout()
        badge_box.setSpacing(8)
        
        self.version_label = QLabel("v0.2.0-phase2")
        self.version_label.setStyleSheet("color: #94a3b8; font-family: monospace; font-size: 12px;")
        
        self.guard_badge = QLabel("READ-ONLY GUARD 🛡️")
        self.guard_badge.setStyleSheet(
            "color: #10b981; font-weight: bold; font-size: 11px; border: 1px solid #10b981; border-radius: 4px; padding: 2px 8px;"
        )
        
        self.status_pill = QLabel()
        self.status_pill.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; border-radius: 12px; padding: 6px 16px; font-weight: bold; border: 1px solid #334155;"
        )
        
        badge_box.addWidget(self.version_label)
        badge_box.addWidget(self.guard_badge)
        badge_box.addWidget(self.status_pill)
        header.addLayout(badge_box)
        
        outer.addLayout(header)
        outer.addSpacing(8)

        # --- Navbar Tabs ---
        tab_row = QHBoxLayout()
        tab_row.setSpacing(8)
        self.tab_buttons: list[QPushButton] = []
        self.tab_ids = ["dashboard", "graph", "diag", "ecu", "bench", "settings", "log"]
        self.tab_icons = {
            "dashboard": "📊", "graph": "📈", "diag": "⚠️", 
            "ecu": "📡", "bench": "🛠️", "settings": "⚙️", "log": "📝"
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
        
        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #334155;")
        outer.addWidget(line)
        outer.addSpacing(8)

        # --- Toolbar (Port, Connect, Record) ---
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
        self._reload_ports()
        
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

        # --- Pages ---
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # Dashboard
        dash = QWidget()
        self.gauge_grid = QGridLayout(dash)
        self.gauge_grid.setSpacing(16)
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
        self.dtc_list = QListWidget()
        diag_layout.addWidget(self.dtc_list)
        self.stack.addWidget(diag_page)

        # ECU Identification
        ecu_page = QWidget()
        ecu_layout = QVBoxLayout(ecu_page)
        
        self.ecu_id_btn = QPushButton()
        self.ecu_id_btn.setMinimumHeight(40)
        self.ecu_id_btn.setMaximumWidth(300)
        self.ecu_id_btn.clicked.connect(self._identify_ecu)
        
        self.ecu_result_frame = QFrame()
        self.ecu_result_frame.setVisible(False)
        result_layout = QVBoxLayout(self.ecu_result_frame)
        self.ecu_result_label = QLabel("")
        self.ecu_result_label.setStyleSheet("font-size: 14px; line-height: 1.8;")
        self.ecu_result_label.setWordWrap(True)
        result_layout.addWidget(self.ecu_result_label)

        ecu_layout.addWidget(self.ecu_id_btn, alignment=Qt.AlignCenter)
        ecu_layout.addSpacing(20)
        ecu_layout.addWidget(self.ecu_result_frame)
        ecu_layout.addStretch()
        self.stack.addWidget(ecu_page)

        # Bench
        bench_page = QWidget()
        bench_layout = QVBoxLayout(bench_page)
        
        self.bench_title = QLabel()
        self.bench_title.setStyleSheet("color: #ef4444; font-size: 20px; font-weight: bold;")
        self.bench_title.setAlignment(Qt.AlignCenter)
        
        self.bench_warning = QLabel()
        self.bench_warning.setWordWrap(True)
        self.bench_warning.setAlignment(Qt.AlignCenter)
        self.bench_warning.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; padding: 16px; border-radius: 8px; border: 1px solid #334155; margin-top: 10px; margin-bottom: 20px;"
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
                    "background-color: #6366f1; color: #ffffff; border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
                )
            else:
                btn.setStyleSheet(
                    "background-color: transparent; color: #94a3b8; border: none; border-radius: 8px; padding: 8px 16px;"
                )
        
        visible = tab_id == "dashboard"
        self.prev_btn.setVisible(visible)
        self.next_btn.setVisible(visible)
        self.page_label.setVisible(visible)

    def _apply_language(self) -> None:
        t = COPY[self.lang]
        self.setWindowTitle(t["title"])
        self.edition_label.setText(t["edition"])
        self.title_label.setText(t["title"])
        for btn, tab_id in zip(self.tab_buttons, self.tab_ids):
            icon = self.tab_icons.get(tab_id, "")
            btn.setText(f"{icon}  {t['tabs'][tab_id]}")
            
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
        
        self.ecu_id_btn.setText(t["ecu_id_btn"])
        self.bench_title.setText(t["bench_title"])
        self.bench_warning.setText(t["bench_warning"])
        self.bench_btn_read.setText(t["bench_btn_read"])
        self.bench_btn_write.setText(t["bench_btn_write"])
        self.bench_btn_unlock.setText(t["bench_btn_unlock"])
        
        self._update_status_pill()

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

        direction = Qt.RightToLeft if self.lang == "fa" else Qt.LeftToRight
        self.setLayoutDirection(direction)
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
            QStatusBar {{ background-color: #1e293b; color: #94a3b8; border-top: 1px solid #334155; padding: 4px; }}
            QStatusBar::item {{ border: none; }}
            """
        )

    def _on_lang_changed(self) -> None:
        data = self.lang_combo.currentData()
        if data in ("fa", "en"):
            self.lang = data  # type: ignore[assignment]
            self._apply_language()
            self._reload_ports()

    def _reload_ports(self) -> None:
        current = self.port_combo.currentText() if self.port_combo.count() else "Auto"
        self.port_combo.clear()
        for port in self.service.list_ports():
            label = COPY[self.lang]["auto"] if port == "Auto" else port
            self.port_combo.addItem(label, port)
        idx = self.port_combo.findData(current if current != COPY[self.lang]["auto"] else "Auto")
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    def _refresh_ports(self) -> None:
        prev_count = self.port_combo.count()
        self._reload_ports()
        new_count = self.port_combo.count()
        self._append_log("msg.port_scan", prev_count, new_count)

    def _update_status_pill(self) -> None:
        t = COPY[self.lang]
        if self.service.connected:
            text = t["sim"] if self.service.simulation else t["connected"]
            self.status_pill.setStyleSheet(
                "background:#6366f1; color:#ffffff; border-radius:12px; padding:6px 16px; font-weight:bold;"
            )
        else:
            text = t["idle"]
            self.status_pill.setStyleSheet(
                "background:#1e293b; color:#94a3b8; border-radius:12px; padding:6px 16px; font-weight:bold; border:1px solid #334155;"
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
            QMessageBox.warning(self, COPY[self.lang]["title"], COPY[self.lang]["connection_failed"])
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

    def _clear_dtc(self) -> None:
        try:
            cleared = self.service.clear_dtc()
        except SafetyGuardError:
            self._append_log("msg.safety_violation")
            modal = SafetyModal(self.lang, self)
            modal.exec()
            return
        if cleared:
            self._refresh_dtc()

    def _toggle_recording(self) -> None:
        if self.recorder.recording:
            path = self.recorder.stop()
            count = self.recorder.count
            self.record_btn.setText("⏺")
            self.record_btn.setStyleSheet("")
            self._append_log("msg.recording_stopped", count, str(path))
        else:
            default_name = f"live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            file_path_str, _ = QFileDialog.getSaveFileName(
                self, "Save Live Data / ذخیره داده‌ها", default_name, "CSV Files (*.csv)"
            )
            if not file_path_str:
                return
                
            custom_path = Path(file_path_str)
            path = self.recorder.start(custom_path)
            
            self.record_btn.setText("⏹")
            self.record_btn.setStyleSheet(
                "background-color: #ef4444; color: #ffffff; border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
            )
            self._append_log("msg.recording_started", str(path))

    def _identify_ecu(self) -> None:
        identifier = MockEcuIdentifier()
        result = identifier.identify()
        self._append_log("msg.ecu_identified", result.ecu_family)
        
        t = COPY[self.lang]
        html = (
            f"<b>{t['ecu_id_vin']}</b> <code style='color: #38bdf8;'>{result.vin}</code><br><br>"
            f"<b>{t['ecu_id_family']}</b> <code style='color: #f59e0b;'>{result.ecu_family}</code><br><br>"
            f"<b>{t['ecu_id_status']}</b> {result.status}<br><br>"
            f"<span style='color: #ef4444;'>{t['ecu_id_simulated']}</span>"
        )
        self.ecu_result_label.setText(html)
        self.ecu_result_frame.setVisible(True)

    def _translate_log(self, message: str, args: tuple) -> str:
        t = COPY[self.lang]
        messages = t.get("messages", {})
        if message in messages:
            template = messages[message]
            try:
                return template.format(*args) if args else template
            except (IndexError, KeyError):
                return template
        if args:
            try:
                return message.format(*args)
            except (IndexError, KeyError):
                return message
        return message

    def _append_log(self, message: str, *args: object) -> None:
        text = self._translate_log(message, args)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_list.insertItem(0, f"{stamp}  {text}")
        self.status_bar.showMessage(text, 5000)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        self.service.disconnect()
        super().closeEvent(event)
