"""UDS ECU identification panel (Identification tab): CAN interface, DIDs and UDS DTCs.

Pure presentation. The window builds a session through the backend, runs it on its worker
thread and hands the results to :meth:`UdsView.show_result`, :meth:`UdsView.show_discovery`
or :meth:`UdsView.show_error`. Every DID shows its own outcome; a value read from the
simulated ECU is always shown under the "simulated" banner, never as vehicle data.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.diagnostics.ecu_identification import EcuIdentification
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.ui.backend import CanDefaults
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["BITRATES", "UdsView"]

#: Common CAN bitrates for OBD-II on CAN (ISO 15765-4: 500 and 250 kbit/s).
BITRATES: tuple[int, ...] = (500_000, 250_000, 125_000)

_BANNER_SIM = "color: #fbbf24; font-weight: bold;"
_BANNER_REAL = "color: #10b981; font-weight: bold;"
_WARNING = "color: #fbbf24;"


class UdsView(QWidget):
    """The UDS part of the Identification tab."""

    discover_requested = Signal()
    read_requested = Signal()

    def __init__(self, defaults: CanDefaults, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lang: Lang = "fa"
        self._defaults = defaults
        # None = nothing read yet; str = the whole run failed; result otherwise
        self._result: EcuIdentification | str | None = None
        self._discovered: tuple[int, ...] | None = None
        self._busy = False

        layout = QVBoxLayout(self)
        self.box = QGroupBox()
        box_layout = QVBoxLayout(self.box)

        form = QFormLayout()
        self.interface_combo = QComboBox()
        for name in defaults.interfaces:
            self.interface_combo.addItem(name, name)
        index = self.interface_combo.findData(defaults.interface)
        self.interface_combo.setCurrentIndex(max(index, 0))
        self.interface_combo.currentIndexChanged.connect(self._on_interface_changed)
        self.channel_edit = QLineEdit(defaults.channel or "")
        self.bitrate_combo = QComboBox()
        for bitrate in dict.fromkeys((defaults.bitrate, *BITRATES)):
            self.bitrate_combo.addItem(f"{bitrate // 1000} kbit/s", bitrate)
        self.interface_label = QLabel()
        self.channel_label = QLabel()
        self.bitrate_label = QLabel()
        for field in (self.interface_combo, self.channel_edit, self.bitrate_combo):
            field.setMinimumHeight(36)
        form.addRow(self.interface_label, self.interface_combo)
        form.addRow(self.channel_label, self.channel_edit)
        form.addRow(self.bitrate_label, self.bitrate_combo)
        box_layout.addLayout(form)

        self.safety_label = QLabel()
        self.safety_label.setWordWrap(True)
        self.safety_label.setStyleSheet(_WARNING)
        box_layout.addWidget(self.safety_label)

        self.extended_check = QCheckBox()
        self.extended_check.setChecked(False)
        self.extended_warning = QLabel()
        self.extended_warning.setWordWrap(True)
        self.extended_warning.setStyleSheet("color: #94a3b8;")
        box_layout.addWidget(self.extended_check)
        box_layout.addWidget(self.extended_warning)

        buttons = QHBoxLayout()
        self.discover_btn = QPushButton()
        self.discover_btn.clicked.connect(self.discover_requested.emit)
        self.read_btn = QPushButton()
        self.read_btn.clicked.connect(self.read_requested.emit)
        buttons.addWidget(self.discover_btn)
        buttons.addWidget(self.read_btn)
        buttons.addStretch()
        box_layout.addLayout(buttons)
        layout.addWidget(self.box)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.did_table = QTableWidget(0, 4)
        self.did_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.did_table.verticalHeader().setVisible(False)
        self.did_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.did_table.setMinimumHeight(210)
        layout.addWidget(self.did_table)

        self.dtc_title = QLabel()
        self.dtc_title.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(self.dtc_title)
        self.dtc_table = QTableWidget(0, 3)
        self.dtc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dtc_table.verticalHeader().setVisible(False)
        self.dtc_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dtc_table.setMinimumHeight(150)
        layout.addWidget(self.dtc_table)

    # ------------------------------------------------------------------ input
    def selection(self) -> tuple[str, str | None, int, bool]:
        """``(interface, channel, bitrate, allow_extended_session)`` chosen by the user."""
        channel = self.channel_edit.text().strip() or None
        return (
            str(self.interface_combo.currentData()),
            channel,
            int(self.bitrate_combo.currentData()),
            self.extended_check.isChecked(),
        )

    @property
    def simulated_selected(self) -> bool:
        return self.interface_combo.currentData() == self._defaults.simulated_interface

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self.discover_btn, self.read_btn, self.interface_combo):
            widget.setEnabled(not busy)
        self._render_status()

    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.box.setTitle(t["uds_title"])
        self.interface_label.setText(t["uds_interface"])
        self.channel_label.setText(t["uds_channel"])
        self.bitrate_label.setText(t["uds_bitrate"])
        self.channel_edit.setPlaceholderText(t["uds_channel_hint"])
        sim_index = self.interface_combo.findData(self._defaults.simulated_interface)
        if sim_index >= 0:
            self.interface_combo.setItemText(sim_index, t["uds_iface_sim"])
        self.safety_label.setText(t["uds_safety"])
        self.extended_check.setText(t["uds_extended"])
        self.extended_warning.setText(t["uds_extended_warning"])
        self.discover_btn.setText(t["uds_discover_btn"])
        self.read_btn.setText(t["uds_read_btn"])
        self.dtc_title.setText(t["uds_dtc_title"])
        cols = t["uds_columns"]
        self.did_table.setHorizontalHeaderLabels(
            [cols["did"], cols["name"], cols["value"], cols["status"]]
        )
        self.dtc_table.setHorizontalHeaderLabels([cols["code"], cols["dtc_status"], cols["flags"]])
        self._render()

    # ------------------------------------------------------------------ results
    def show_discovery(self, response_ids: tuple[int, ...]) -> None:
        self._discovered = response_ids
        self._render_status()

    def show_result(self, result: EcuIdentification) -> None:
        self._result = result
        self._render()

    def show_error(self, detail: str) -> None:
        self._result = detail
        self._render()

    @property
    def result(self) -> EcuIdentification | str | None:
        return self._result

    # ------------------------------------------------------------------ rendering
    def _on_interface_changed(self) -> None:
        self.channel_edit.setEnabled(not self.simulated_selected)
        self._render_banner()

    def _render(self) -> None:
        self.channel_edit.setEnabled(not self.simulated_selected)
        self._render_banner()
        self._render_status()
        self._render_dids()
        self._render_dtcs()

    def _render_banner(self) -> None:
        t = COPY[self._lang]
        result = self._result
        simulated = (
            result.simulated if isinstance(result, EcuIdentification) else self.simulated_selected
        )
        self.banner.setText(t["uds_banner_sim"] if simulated else t["uds_banner_real"])
        self.banner.setStyleSheet(_BANNER_SIM if simulated else _BANNER_REAL)

    def _render_status(self) -> None:
        t = COPY[self._lang]
        parts: list[str] = []
        if self._busy:
            parts.append(t["uds_busy"])
        if self._discovered is not None:
            found = ", ".join(f"{rid:03X}" for rid in self._discovered)
            parts.append(t["uds_discovered"].format(found) if found else t["uds_discovered_none"])
        result = self._result
        if isinstance(result, str):
            parts.append(t["uds_failed"].format(result))
        elif isinstance(result, EcuIdentification):
            parts.append(t["uds_vin_line"].format(result.vin or t["ident_unknown"]))
            if result.extended_session_detail:
                parts.append(t["uds_ext_refused"].format(result.extended_session_detail))
            elif result.extended_session_used:
                parts.append(t["uds_ext_used"])
        self.status_label.setText("\n".join(parts))

    def _outcome_text(self, outcome: ReadOutcome, nrc_name: str | None) -> str:
        text = COPY[self._lang]["outcome"][outcome.value]
        return f"{text} ({nrc_name})" if nrc_name else text

    def _render_dids(self) -> None:
        t = COPY[self._lang]
        result = self._result
        readings = result.dids if isinstance(result, EcuIdentification) else ()
        self.did_table.setRowCount(len(readings))
        for row, reading in enumerate(readings):
            name = t["uds_did_names"].get(reading.name, reading.name)
            if reading.outcome is ReadOutcome.OK and reading.raw is not None:
                value = reading.text if reading.text is not None else reading.raw.hex(" ").upper()
                status = self._outcome_text(reading.outcome, None)
                if reading.session == "extended":
                    status = f"{status} {t['uds_session_ext']}"
            else:
                value = "—"
                status = self._outcome_text(reading.outcome, reading.nrc_name)
            for column, text in enumerate((f"{reading.did:04X}", name, value, status)):
                self.did_table.setItem(row, column, QTableWidgetItem(text))

    def _render_dtcs(self) -> None:
        t = COPY[self._lang]
        result = self._result
        self.dtc_table.setRowCount(0)
        if not isinstance(result, EcuIdentification):
            return
        if result.dtc_outcome is not ReadOutcome.OK:
            self.dtc_title.setText(
                t["uds_dtc_unavailable"].format(self._outcome_text(result.dtc_outcome, None))
            )
            return
        if not result.dtcs:
            self.dtc_title.setText(t["uds_dtc_none"])
            return
        self.dtc_title.setText(t["uds_dtc_title"])
        flags = t["uds_flags"]
        self.dtc_table.setRowCount(len(result.dtcs))
        for row, dtc in enumerate(result.dtcs):
            s = dtc.status
            on = [
                flags[key]
                for key, value in (
                    ("confirmed", s.confirmed),
                    ("pending", s.pending),
                    ("test_failed", s.test_failed),
                    ("warning", s.warning_indicator),
                )
                if value
            ]
            code = dtc.code or dtc.raw.hex().upper()
            texts = (code, f"{s.raw:02X}", t["uds_flag_sep"].join(on) or flags["none"])
            for column, text in enumerate(texts):
                self.dtc_table.setItem(row, column, QTableWidgetItem(text))
