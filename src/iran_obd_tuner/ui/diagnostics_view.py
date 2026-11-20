"""Trouble codes tab: stored / pending / permanent codes and freeze frame 00.

Pure presentation: the window reads the data on its background thread and hands the results
to :meth:`DiagnosticsView.show_categories` and :meth:`DiagnosticsView.show_freeze_frame`.
Every category and every freeze-frame parameter shows its own outcome: "unsupported" or "no
answer" is displayed as such and never as "no codes" or as a number.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.dtc import DtcStatus
from iran_obd_tuner.diagnostics.models import (
    DtcCategoryResult,
    FreezeFrameSnapshot,
    PidStatus,
    ReadOutcome,
)
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["DiagnosticsView", "display_unit"]

_FILTER_ALL = "all"

#: Units as shown to the user (the catalogue stores ASCII symbols).
_UNIT_SYMBOLS = {"degC": "°C"}


def display_unit(unit: str) -> str:
    """Return the unit symbol shown in the UI, e.g. ``"°C"`` for ``"degC"``."""
    return _UNIT_SYMBOLS.get(unit, unit)


class DiagnosticsView(QWidget):
    """The trouble codes tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lang: Lang = "fa"
        # None = not connected; str = the whole read failed; tuple = per-category results
        self._categories: tuple[DtcCategoryResult, ...] | str | None = None
        # None = not read yet; str = the whole read failed; snapshot otherwise
        self._freeze: FreezeFrameSnapshot | str | None = None
        self._connected = False

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.filter_combo = QComboBox()
        for key in (_FILTER_ALL, *(status.value for status in DtcStatus)):
            self.filter_combo.addItem("", key)
        self.filter_combo.currentIndexChanged.connect(self._render_categories)
        self.refresh_btn = QPushButton()
        self.freeze_btn = QPushButton()
        self.clear_btn = QPushButton()
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.filter_combo)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.freeze_btn)
        header.addWidget(self.clear_btn)
        layout.addLayout(header)

        self.note = QLabel()
        self.note.setStyleSheet("color: #94a3b8;")
        layout.addWidget(self.note)
        self.dtc_list = QListWidget()
        layout.addWidget(self.dtc_list, 3)

        self.freeze_title = QLabel()
        self.freeze_title.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(self.freeze_title)
        self.freeze_status = QLabel()
        self.freeze_status.setWordWrap(True)
        layout.addWidget(self.freeze_status)
        self.freeze_table = QTableWidget(0, 4)
        self.freeze_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.freeze_table.verticalHeader().setVisible(False)
        self.freeze_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.freeze_table, 2)

    # ------------------------------------------------------------------ input
    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.title.setText(t["dtc_title"])
        self.note.setText(t["dtc_note"])
        self.refresh_btn.setText(t["refresh_dtc"])
        self.freeze_btn.setText(t["ff_read_btn"])
        self.clear_btn.setText(t["clear_dtc"])
        self.freeze_title.setText(t["ff_title"])
        self.filter_combo.setItemText(0, t["dtc_filter_all"])
        for index, status in enumerate(DtcStatus, start=1):
            self.filter_combo.setItemText(index, t["dtc_category"][status.value])
        columns = t["ff_columns"]
        self.freeze_table.setHorizontalHeaderLabels(
            [columns["param"], columns["value"], columns["unit"], columns["status"]]
        )
        self._render_categories()
        self._render_freeze_frame()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.freeze_btn.setEnabled(connected)
        if not connected:
            self._categories = None
            self._freeze = None
        self._render_categories()
        self._render_freeze_frame()

    def show_categories(self, result: tuple[DtcCategoryResult, ...] | str) -> None:
        self._categories = result
        self._render_categories()

    def show_freeze_frame(self, result: FreezeFrameSnapshot | str) -> None:
        self._freeze = result
        self._render_freeze_frame()

    @property
    def category_filter(self) -> str:
        return str(self.filter_combo.currentData())

    def item_texts(self) -> list[str]:
        """Visible lines of the code list (used by tests)."""
        return [self.dtc_list.item(i).text() for i in range(self.dtc_list.count())]

    # ------------------------------------------------------------------ rendering
    def _add_header(self, text: str) -> None:
        item = QListWidgetItem(text)
        font = QFont(self.dtc_list.font())
        font.setBold(True)
        item.setFont(font)
        item.setFlags(Qt.ItemIsEnabled)
        self.dtc_list.addItem(item)

    def _render_categories(self) -> None:
        t = COPY[self._lang]
        self.dtc_list.clear()
        state = self._categories
        if state is None:
            self.dtc_list.addItem(t["dtc_not_connected"])
            return
        if isinstance(state, str):
            self.dtc_list.addItem(t["dtc_read_failed"].format(state))
            return
        wanted = self.category_filter
        for category in state:
            if wanted not in (_FILTER_ALL, category.status.value):
                continue
            name = t["dtc_category"][category.status.value]
            service = category.status.service
            if category.outcome is not ReadOutcome.OK:
                outcome = t["outcome"][category.outcome.value]
                self._add_header(t["dtc_category_unavailable"].format(name, service, outcome))
                continue
            self._add_header(t["dtc_category_header"].format(name, service, len(category.codes)))
            if not category.codes:
                self.dtc_list.addItem("    " + t["no_dtc"])
            for code in category.codes:
                description = code.description or t["dtc_unknown_description"]
                self.dtc_list.addItem(f"    {code.code}  —  {description}")

    def _render_freeze_frame(self) -> None:
        t = COPY[self._lang]
        state = self._freeze
        self.freeze_table.setRowCount(0)
        if state is None:
            self.freeze_status.setText(
                t["ff_not_read"] if self._connected else t["ff_not_connected"]
            )
            return
        if isinstance(state, str):
            self.freeze_status.setText(t["ff_unavailable"].format(state))
            return
        if state.outcome is not ReadOutcome.OK:
            reason = t["outcome"][state.outcome.value]
            self.freeze_status.setText(t["ff_unavailable"].format(reason))
            return
        if not state.present:
            self.freeze_status.setText(t["ff_none_stored"])
            return
        trigger = state.trigger
        if trigger is None:  # pragma: no cover - present frames always carry a trigger
            self.freeze_status.setText("")
        else:
            description = trigger.description or t["dtc_unknown_description"]
            self.freeze_status.setText(t["ff_trigger"].format(trigger.code, description))
        self._fill_table(state)

    def _fill_table(self, state: FreezeFrameSnapshot) -> None:
        t = COPY[self._lang]
        self.freeze_table.setRowCount(len(state.readings))
        for row, reading in enumerate(state.readings):
            name = t["ff_params"].get(reading.pid, reading.pid)
            m = reading.measurement
            value = "—" if m is None else f"{m.value:.1f}"
            unit = "" if m is None else display_unit(str(m.unit))
            status = t["pid_status"][_status_key(reading.status)]
            for column, text in enumerate((name, value, unit, status)):
                self.freeze_table.setItem(row, column, QTableWidgetItem(text))

    def freeze_rows(self) -> list[Sequence[str]]:
        """Table contents (used by tests)."""
        rows = []
        for row in range(self.freeze_table.rowCount()):
            rows.append([self.freeze_table.item(row, col).text() for col in range(4)])
        return rows


def _status_key(status: PidStatus) -> str:
    return "ok" if status is PidStatus.OK else status.value
