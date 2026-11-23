"""Bench tab: offline ROM file analysis and table editing (Phase 4), plus the simulated bench
flash panel (Milestone 7f, virtual ECU only).

Pure presentation over a :class:`~iran_obd_tuner.ui.backend.BenchWorkspace` (implemented in
the application layer; ``ui`` never imports ``tuning``). File work (open, definitions, export)
runs through the window's background runner; table edits are fast in-memory operations.

Honesty rules visible here: a permanent banner says that nothing is sent to any ECU; the
SIMULATED demo is labelled; checksum "valid" carries its caveat; values outside a map's bounds
are refused (never clamped); exporting needs an explicit acknowledgement and is refused for
the source file or while a configured checksum is invalid. The workbench itself never talks
to an ECU; the old locked Phase 5 buttons were replaced in Milestone 7f by
:class:`~iran_obd_tuner.ui.bench_flash_panel.BenchFlashPanel`, which only reaches the
virtual programmable ECU.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.calibration import (
    ChecksumStatus,
    MapCategory,
    TableSnapshot,
    TableViewMode,
)
from iran_obd_tuner.core.exceptions import IranObdTunerError
from iran_obd_tuner.ui.backend import BenchWorkspace
from iran_obd_tuner.ui.bench_flash_panel import BenchFlashPanel
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["CHECKSUM_COLORS", "BenchView", "heat_color", "parse_number"]

Submit = Callable[
    [Callable[[], object], Callable[[object], None], Callable[[BaseException], None]], None
]

CHECKSUM_COLORS = {
    ChecksumStatus.VALID: "#10b981",
    ChecksumStatus.INVALID: "#dc2626",
    ChecksumStatus.NOT_CONFIGURED: "#64748b",
}
_ROLE = Qt.ItemDataRole.UserRole
_BANNER = (
    "background-color: #450a0a; color: #fecaca; border: 1px solid #b91c1c; border-radius: 8px; "
    "padding: 8px 16px; font-weight: bold;"
)
_SIM_BADGE = "color: #fbbf24; font-weight: bold;"
_ERROR = "color: #f87171;"
_OK = "color: #94a3b8;"
_MAX_STRINGS_SHOWN = 500
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


def parse_number(text: str) -> float:
    """A number typed by the user (Persian / Arabic digits and the Persian decimal mark too).

    Raises:
        ValueError: Not a finite number.
    """
    cleaned = text.strip().translate(_PERSIAN_DIGITS).replace(",", ".").replace("−", "-")
    value = float(cleaned)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def heat_color(value: float, low: float, high: float) -> QColor:
    """Blue (low) -> amber -> red (high) on a fixed scale; mid-grey when the scale is flat."""
    if high <= low:
        return QColor("#334155")
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    return _mix("#1d4ed8", "#b45309", t * 2) if t < 0.5 else _mix("#b45309", "#b91c1c", t * 2 - 1)


def _delta_color(delta: float | None, span: float) -> QColor:
    if delta is None or delta == 0 or span <= 0:
        return QColor("#1e293b")
    t = min(1.0, abs(delta) / span)
    return _mix("#1e293b", "#b91c1c" if delta > 0 else "#1d4ed8", 0.35 + 0.65 * t)


def _decimals(step: float) -> int:
    if step <= 0:
        return 3
    return min(4, max(0, -math.floor(math.log10(step) + 1e-9)))


class BenchView(QWidget):
    """The Bench tab.

    Args:
        workspace: The offline session (from ``DashboardBackend.create_bench_workspace``).
        submit: Runs ``job`` on the background thread and calls back on the GUI thread.
    """

    def __init__(
        self,
        workspace: BenchWorkspace,
        submit: Submit,
        parent: QWidget | None = None,
        *,
        flash_panel: BenchFlashPanel | None = None,
    ):
        super().__init__(parent)
        self.flash_panel = flash_panel
        self._ws = workspace
        self._submit = submit
        self._lang: Lang = "fa"
        self._map_id: str | None = None
        self._mode = TableViewMode.MODIFIED
        self._busy = False
        self._rendering = False
        self._status: tuple[str, tuple[object, ...], bool] | None = None  # key, args, is_error

        layout = QVBoxLayout(self)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.title_label)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(_BANNER)
        layout.addWidget(self.banner)

        toolbar = QHBoxLayout()
        self.open_btn = QPushButton()
        self.demo_btn = QPushButton()
        self.defs_btn = QPushButton()
        self.undo_btn = QPushButton()
        self.redo_btn = QPushButton()
        self.recalc_btn = QPushButton()
        self.export_btn = QPushButton()
        self.undo_btn.setShortcut("Ctrl+Z")
        self.redo_btn.setShortcut("Ctrl+Y")
        for btn, slot in (
            (self.open_btn, self._open_rom),
            (self.demo_btn, self._open_demo),
            (self.defs_btn, self._load_definitions),
            (self.undo_btn, self._undo),
            (self.redo_btn, self._redo),
            (self.recalc_btn, self._recalculate),
            (self.export_btn, self._export),
        ):
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.splitter = splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.info_label)
        self.sim_label = QLabel()
        self.sim_label.setStyleSheet(_SIM_BADGE)
        self.sim_label.setWordWrap(True)
        left_layout.addWidget(self.sim_label)
        self.maps_title = QLabel()
        self.maps_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(self.maps_title)
        self.map_list = QListWidget()
        self.map_list.setMinimumHeight(120)
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        left_layout.addWidget(self.map_list)
        self.strings_title = QLabel()
        self.strings_title.setWordWrap(True)
        left_layout.addWidget(self.strings_title)
        self.strings_filter = QLineEdit()
        self.strings_filter.textChanged.connect(self._render_strings)
        left_layout.addWidget(self.strings_filter)
        self.strings_table = QTableWidget(0, 2)
        self.strings_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.strings_table.verticalHeader().setVisible(False)
        self.strings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.strings_table.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        left_layout.addWidget(self.strings_table)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        head = QHBoxLayout()
        self.table_title = QLabel()
        self.table_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        head.addWidget(self.table_title, 1)
        self.view_label = QLabel()
        self.mode_combo = QComboBox()
        for mode in TableViewMode:
            self.mode_combo.addItem("", mode.value)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(self._mode.value))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        head.addWidget(self.view_label)
        head.addWidget(self.mode_combo)
        right_layout.addLayout(head)
        self.grid = QTableWidget(0, 0)
        self.grid.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # axes read left to right
        self.grid.setMinimumHeight(300)
        self.grid.itemChanged.connect(self._on_cell_edited)
        right_layout.addWidget(self.grid, 1)
        edit_row = QHBoxLayout()
        self.value_label = QLabel()
        self.value_edit = QLineEdit()
        self.value_edit.setMaximumWidth(120)
        self.set_btn = QPushButton()
        self.set_btn.clicked.connect(self._set_selection)
        self.percent_label = QLabel()
        self.percent_edit = QLineEdit()
        self.percent_edit.setMaximumWidth(90)
        self.percent_btn = QPushButton()
        self.percent_btn.clicked.connect(self._add_percent)
        for widget in (
            self.value_label,
            self.value_edit,
            self.set_btn,
            self.percent_label,
            self.percent_edit,
            self.percent_btn,
        ):
            edit_row.addWidget(widget)
        edit_row.addStretch()
        right_layout.addLayout(edit_row)
        self.checksums_title = QLabel()
        self.checksums_title.setWordWrap(True)
        right_layout.addWidget(self.checksums_title)
        self.checksum_table = QTableWidget(0, 5)
        self.checksum_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.checksum_table.verticalHeader().setVisible(False)
        self.checksum_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.checksum_table.setMaximumHeight(130)
        self.checksum_table.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        right_layout.addWidget(self.checksum_table)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        if flash_panel is not None:  # Milestone 7f: simulated bench flash (virtual ECU only)
            layout.addWidget(flash_panel)
        self.refresh()

    # ------------------------------------------------------------------ helpers
    @property
    def map_id(self) -> str | None:
        return self._map_id

    @property
    def mode(self) -> TableViewMode:
        return self._mode

    def _say(self, key: str, *args: object, error: bool = False) -> None:
        self._status = (key, args, error)
        self._render_status()

    def _render_status(self) -> None:
        if self._status is None:
            self.status_label.setText("")
            return
        key, args, error = self._status
        self.status_label.setText(COPY[self._lang][key].format(*args))
        self.status_label.setStyleSheet(_ERROR if error else _OK)

    def _run(self, job: Callable[[], object], done: Callable[[object], None]) -> None:
        self._busy = True
        self._update_buttons()
        self._say("bench_busy")

        def finished(result: object) -> None:
            self._busy = False
            done(result)
            self.refresh()

        def failed(error: BaseException) -> None:
            self._busy = False
            self._say("bench_failed", error, error=True)
            self.refresh()

        self._submit(job, finished, failed)

    def _update_buttons(self) -> None:
        ws = self._ws
        loaded = ws.loaded and not self._busy
        self.open_btn.setEnabled(not self._busy)
        self.demo_btn.setEnabled(not self._busy)
        self.defs_btn.setEnabled(loaded and not ws.is_modified)
        self.undo_btn.setEnabled(loaded and ws.can_undo)
        self.redo_btn.setEnabled(loaded and ws.can_redo)
        has_checksums = any(c.block_id is not None for c in ws.checksums())
        self.recalc_btn.setEnabled(loaded and has_checksums)
        self.export_btn.setEnabled(loaded and ws.is_modified)
        editable = loaded and self._map_id is not None and self._mode is TableViewMode.MODIFIED
        for widget in (self.set_btn, self.percent_btn, self.value_edit, self.percent_edit):
            widget.setEnabled(editable)

    # ------------------------------------------------------------------ actions
    def _open_rom(self) -> None:
        t = COPY[self._lang]
        path, _ = QFileDialog.getOpenFileName(
            self, t["bench_open_dialog"], "", t["bench_open_filter"]
        )
        if path:
            self._map_id = None
            self._run(lambda: self._ws.open_rom(path), lambda s: self._say("bench_opened", s.name))

    def _open_demo(self) -> None:
        self._map_id = None
        self._run(self._ws.open_demo, lambda s: self._say("bench_opened", s.name))

    def _load_definitions(self) -> None:
        t = COPY[self._lang]
        path, _ = QFileDialog.getOpenFileName(
            self, t["bench_defs_dialog"], "", t["bench_defs_filter"]
        )
        if path:
            self._map_id = None
            self._run(
                lambda: self._ws.load_definitions(path),
                lambda _report: self._say("bench_defs_loaded"),
            )

    def _undo(self) -> None:
        label = self._ws.undo() if self._ws.loaded else None
        if label:
            self._say("bench_undone", label)
        self.refresh()

    def _redo(self) -> None:
        label = self._ws.redo() if self._ws.loaded else None
        if label:
            self._say("bench_redone", label)
        self.refresh()

    def _recalculate(self) -> None:
        count = self._ws.recalculate_checksums()
        self._say("bench_recalc_done", count)
        self.refresh()

    def confirm_export(self) -> bool:
        """Ask for the acknowledgement (replaced in tests)."""
        t = COPY[self._lang]
        box = QMessageBox(
            QMessageBox.Icon.Warning, t["bench_confirm_title"], t["bench_confirm_text"], parent=self
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        check = QCheckBox(t["bench_confirm_check"])
        box.setCheckBox(check)
        return box.exec() == QMessageBox.StandardButton.Yes and check.isChecked()

    def _export(self) -> None:
        t = COPY[self._lang]
        if not self._ws.is_modified:
            return
        acknowledged = self.confirm_export()
        if not acknowledged:
            self._say("bench_export_cancelled")
            return
        default = str(self._ws.default_export_path())
        path, _ = QFileDialog.getSaveFileName(
            self, t["bench_export_dialog"], default, t["bench_export_filter"]
        )
        if not path:
            self._say("bench_export_cancelled")
            return
        # the save dialog already asked before replacing another file
        self._run(
            lambda: self._ws.export(path, acknowledged=True, overwrite=True),
            lambda r: self._say("bench_exported", r.path, r.sha256[:16], r.manifest_path),
        )

    def _cells(self) -> list[tuple[int, int]]:
        return sorted({(i.row(), i.column()) for i in self.grid.selectedIndexes()})

    def _edit(self, action: Callable[[], object]) -> None:
        try:
            outcome = action()
        except (IranObdTunerError, ValueError) as exc:
            self._say("bench_edit_refused", exc, error=True)
        else:
            self._say(
                "bench_edit_done",
                outcome.changed_cells,  # type: ignore[attr-defined]
                f"{outcome.max_quantisation_error:.4g}",  # type: ignore[attr-defined]
            )
        self.refresh()

    def _set_selection(self) -> None:
        cells = self._cells()
        if self._map_id is None or not cells:
            return
        map_id = self._map_id
        self._edit(lambda: self._ws.set_cells(map_id, cells, parse_number(self.value_edit.text())))

    def _add_percent(self) -> None:
        cells = self._cells()
        if self._map_id is None or not cells:
            return
        map_id = self._map_id
        self._edit(
            lambda: self._ws.scale_cells(map_id, cells, parse_number(self.percent_edit.text()))
        )

    def _on_cell_edited(self, item: QTableWidgetItem) -> None:
        if self._rendering or self._map_id is None or self._mode is not TableViewMode.MODIFIED:
            return
        map_id, row, col, text = self._map_id, item.row(), item.column(), item.text()
        self._edit(lambda: self._ws.set_cell(map_id, row, col, parse_number(text)))

    def _on_map_selected(self, item: QListWidgetItem | None, _previous: object) -> None:
        if self._rendering or item is None:
            return
        self._map_id = item.data(_ROLE)
        self._render_table()
        self._update_buttons()

    def _on_mode_changed(self) -> None:
        self._mode = TableViewMode(self.mode_combo.currentData())
        self._render_table()
        self._update_buttons()

    def select_map(self, map_id: str) -> bool:
        for i in range(self.map_list.count()):
            if self.map_list.item(i).data(_ROLE) == map_id:
                self.map_list.setCurrentRow(i)
                return True
        return False

    def set_mode(self, mode: TableViewMode) -> None:
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode.value))

    # ------------------------------------------------------------------ rendering
    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.title_label.setText(t["bench_title"])
        direction = (
            Qt.LayoutDirection.RightToLeft if lang == "fa" else Qt.LayoutDirection.LeftToRight
        )
        self.splitter.setLayoutDirection(direction)  # QSplitter does not follow the window
        self.banner.setText(t["bench_warning"])
        self.open_btn.setText(t["bench_open"])
        self.demo_btn.setText(t["bench_open_demo"])
        self.defs_btn.setText(t["bench_load_defs"])
        self.undo_btn.setText(t["bench_undo"])
        self.redo_btn.setText(t["bench_redo"])
        self.recalc_btn.setText(t["bench_recalc"])
        self.export_btn.setText(t["bench_export"])
        self.maps_title.setText(t["bench_maps_title"])
        self.strings_filter.setPlaceholderText(t["bench_strings_filter"])
        cols = t["bench_strings_columns"]
        self.strings_table.setHorizontalHeaderLabels([cols["offset"], cols["text"]])
        self.view_label.setText(t["bench_view_label"])
        for i, mode in enumerate(TableViewMode):
            self.mode_combo.setItemText(i, t["bench_view_modes"][mode.value])
        self.value_label.setText(t["bench_value_label"])
        self.set_btn.setText(t["bench_set_btn"])
        self.percent_label.setText(t["bench_percent_label"])
        self.percent_btn.setText(t["bench_add_pct_btn"])
        self.checksums_title.setText(t["bench_checksums_title"])
        ccols = t["bench_checksum_columns"]
        self.checksum_table.setHorizontalHeaderLabels(
            [ccols["id"], ccols["algorithm"], ccols["range"], ccols["values"], ccols["status"]]
        )
        if self.flash_panel is not None:
            self.flash_panel.set_language(lang)
        self.refresh()

    def refresh(self) -> None:
        self._render_info()
        self._render_maps()
        self._render_strings()
        self._render_table()
        self._render_checksums()
        self._render_status()
        self._update_buttons()

    def _render_info(self) -> None:
        t = COPY[self._lang]
        summary = self._ws.summary()
        if summary is None:
            self.info_label.setText(t["bench_no_rom"])
            self.sim_label.setText("")
            return
        lines = [
            t["bench_rom_line"].format(summary.name, summary.size),
            f"SHA-256: {summary.sha256}",
            f"CRC32: {summary.crc32:08X}",
        ]
        defs = self._ws.definitions()
        if defs is None:
            lines.append(t["bench_defs_none"])
        else:
            kind = t["bench_defs_simulated"] if defs.simulated else t["bench_defs_user"]
            confidence = t["kb_confidence"][defs.confidence.value]
            lines.append(
                t["bench_defs_line"].format(
                    defs.name, kind, confidence, defs.map_count, defs.checksum_count
                )
            )
            report = self._ws.compatibility()
            for warning in () if report is None else report.warnings:
                lines.append(t["bench_warning_line"].format(warning))
        self.info_label.setText("\n".join(lines))
        simulated = summary.simulated or bool(defs and defs.simulated)
        self.sim_label.setText(t["bench_simulated_badge"] if simulated else "")

    def _render_maps(self) -> None:
        t = COPY[self._lang]
        self._rendering = True
        self.map_list.clear()
        maps = self._ws.maps()
        ids = [m.id for m in maps]
        if self._map_id not in ids:
            self._map_id = ids[0] if ids else None
        for m in maps:
            category = t["bench_categories"][MapCategory(m.category).value]
            changed = f" ✎{m.changed_cells}" if m.changed_cells else ""
            item = QListWidgetItem(f"[{category}] {m.names.get(self._lang)}{changed}")
            item.setData(_ROLE, m.id)
            self.map_list.addItem(item)
            if m.id == self._map_id:
                self.map_list.setCurrentItem(item)
        self._rendering = False

    def _render_strings(self) -> None:
        t = COPY[self._lang]
        scan = self._ws.strings()
        if scan is None:
            self.strings_title.setText("")
            self.strings_table.setRowCount(0)
            return
        self.strings_title.setText(t["bench_strings_title"].format(scan.total_found))
        needle = self.strings_filter.text().strip().lower()
        shown = [s for s in scan.strings if needle in s.text.lower()][:_MAX_STRINGS_SHOWN]
        self.strings_table.setRowCount(len(shown))
        for row, item in enumerate(shown):
            self.strings_table.setItem(row, 0, QTableWidgetItem(f"0x{item.offset:06X}"))
            self.strings_table.setItem(row, 1, QTableWidgetItem(item.text))

    def _format(self, value: float | None, table: TableSnapshot) -> str:
        if value is None:
            return COPY[self._lang]["bench_na"]
        if self._mode is TableViewMode.DELTA_PERCENT:
            return f"{value:+.1f}%"
        digits = _decimals(table.step)
        text = f"{value:.{digits}f}"
        return f"{value:+.{digits}f}" if self._mode is TableViewMode.DELTA else text

    def _render_table(self) -> None:
        t = COPY[self._lang]
        self._rendering = True
        try:
            if self._map_id is None or not self._ws.loaded:
                self.table_title.setText(t["bench_table_none"] if self._ws.loaded else "")
                self.grid.setRowCount(0)
                self.grid.setColumnCount(0)
                return
            table = self._ws.table(self._map_id)
            self.table_title.setText(
                t["bench_table_title"].format(
                    table.names.get(self._lang), table.unit, table.rows, table.cols
                )
            )
            self.grid.setRowCount(table.rows)
            self.grid.setColumnCount(table.cols)
            if table.x_axis is not None:
                labels = [f"{v:g}" for v in table.x_axis.values]
                labels[0] = f"{labels[0]} {table.x_axis.unit}"
            else:
                labels = [str(c) for c in range(table.cols)]
            self.grid.setHorizontalHeaderLabels(labels)
            if table.y_axis is not None:
                rows = [f"{v:g} {table.y_axis.unit}" for v in table.y_axis.values]
            else:
                rows = [str(r) for r in range(table.rows)]
            self.grid.setVerticalHeaderLabels(rows)
            originals = [v for row in table.original for v in row]
            low, high = min(originals), max(originals)
            span = max(
                (abs(table.delta(r, c)) for r in range(table.rows) for c in range(table.cols)),
                default=0.0,
            )
            pct_span = max(
                (
                    abs(p)
                    for r in range(table.rows)
                    for c in range(table.cols)
                    if (p := table.delta_percent(r, c)) is not None
                ),
                default=0.0,
            )
            editable = self._mode is TableViewMode.MODIFIED
            for r in range(table.rows):
                for c in range(table.cols):
                    value = table.value(r, c, self._mode)
                    item = QTableWidgetItem(self._format(value, table))
                    if self._mode in (TableViewMode.ORIGINAL, TableViewMode.MODIFIED):
                        background = heat_color(value if value is not None else low, low, high)
                    elif self._mode is TableViewMode.DELTA:
                        background = _delta_color(value, span)
                    else:
                        background = _delta_color(value, pct_span)
                    item.setBackground(background)
                    item.setForeground(QColor("#ffffff"))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if table.changed(r, c):
                        font = QFont()
                        font.setBold(True)
                        font.setUnderline(True)
                        item.setFont(font)
                    item.setToolTip(
                        t["bench_cell_tip"].format(
                            f"{table.original[r][c]:g}",
                            f"{table.current[r][c]:g}",
                            f"{table.edit_min:g}",
                            f"{table.edit_max:g}",
                            table.unit,
                        )
                    )
                    flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                    if editable:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    self.grid.setItem(r, c, item)
            self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        finally:
            self._rendering = False

    def _render_checksums(self) -> None:
        t = COPY[self._lang]
        results = self._ws.checksums() if self._ws.loaded else ()
        self.checksum_table.setRowCount(len(results))
        for row, result in enumerate(results):
            data = result.as_dict()
            texts = (
                result.block_id or "—",
                "—" if result.algorithm is None else result.algorithm.value,
                "—" if data["range"] is None else f"{data['range'][0]}..{data['range'][1]}",
                "—" if data["stored"] is None else f"{data['stored']} / {data['computed']}",
                t["bench_checksum_status"][result.status.value],
            )
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                if column == 4:
                    item.setBackground(QColor(CHECKSUM_COLORS[result.status]))
                    item.setForeground(QColor("#ffffff"))
                    item.setData(_ROLE, result.status.value)
                self.checksum_table.setItem(row, column, item)
