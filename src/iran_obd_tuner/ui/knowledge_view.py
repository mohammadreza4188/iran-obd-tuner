"""Vehicle Knowledge tab: browse profiles, confidence badges, passive match, evidence export.

Pure presentation. The window hands in the profiles (``core`` models, from the backend), runs
matching / export and passes results back with :meth:`KnowledgeView.show_match`,
:meth:`KnowledgeView.show_export_result` and friends. Every claim shows its confidence as a
text label *and* a colour (never colour alone), its source and, when it is not settled, how
to verify it on a vehicle.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.knowledge import (
    Claim,
    ConfidenceLevel,
    LocalizedText,
    Manufacturer,
    VehicleProfile,
    VehicleVariant,
    claim_rows,
    format_claim_value,
)
from iran_obd_tuner.diagnostics.evidence_export import OperatorContext
from iran_obd_tuner.diagnostics.vehicle_matcher import MatchReport, SignalOutcome
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["CONFIDENCE_COLORS", "KnowledgeView"]

#: Badge colours (Slate + Indigo palette); always shown together with the text label.
CONFIDENCE_COLORS: Mapping[ConfidenceLevel, str] = {
    ConfidenceLevel.VERIFIED: "#10b981",
    ConfidenceLevel.DOCUMENTED: "#6366f1",
    ConfidenceLevel.REPORTED: "#f59e0b",
    ConfidenceLevel.UNVERIFIED: "#f97316",
    ConfidenceLevel.UNKNOWN: "#64748b",
}
_ROLE = Qt.ItemDataRole.UserRole
_MUTED = "color: #94a3b8;"
_WARN = "color: #fbbf24; font-weight: bold;"
_OK = "color: #10b981;"


class KnowledgeView(QWidget):
    """The Vehicle Knowledge tab."""

    match_requested = Signal()
    export_requested = Signal()

    def __init__(
        self,
        vehicles: tuple[VehicleProfile, ...],
        manufacturers: tuple[Manufacturer, ...],
        guidance: Mapping[str, LocalizedText],
        source: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lang: Lang = "fa"
        self._vehicles = vehicles
        self._manufacturers = manufacturers
        self._guidance = guidance
        self._source = source
        self._selected: tuple[str, str] | None = None
        self._report: MatchReport | str | None = None
        self._export: tuple[str, str] | None = None  # (i18n key, detail)
        self._available = False

        layout = QVBoxLayout(self)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.source_label = QLabel()
        self.source_label.setStyleSheet(_MUTED)
        self.source_label.setWordWrap(True)
        self.honesty_label = QLabel()
        self.honesty_label.setWordWrap(True)
        self.honesty_label.setStyleSheet(_MUTED)
        layout.addWidget(self.title_label)
        layout.addWidget(self.source_label)
        layout.addWidget(self.honesty_label)

        legend_row = QHBoxLayout()
        self.legend_label = QLabel()
        legend_row.addWidget(self.legend_label)
        self.legend_badges: dict[ConfidenceLevel, QLabel] = {}
        for level in ConfidenceLevel:
            badge = QLabel()
            badge.setStyleSheet(_badge_style(level))
            self.legend_badges[level] = badge
            legend_row.addWidget(badge)
        legend_row.addStretch()
        layout.addLayout(legend_row)

        # --- browser: manufacturer filter + tree | claims table
        filter_row = QHBoxLayout()
        self.manufacturer_label = QLabel()
        self.manufacturer_combo = QComboBox()
        self.manufacturer_combo.addItem("", None)
        for manufacturer in manufacturers:
            self.manufacturer_combo.addItem(manufacturer.names.en, manufacturer.id)
        self.manufacturer_combo.currentIndexChanged.connect(self._render_tree)
        filter_row.addWidget(self.manufacturer_label)
        filter_row.addWidget(self.manufacturer_combo)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(240)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.tree)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.header_label = QLabel()
        self.header_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.header_label.setWordWrap(True)
        detail_layout.addWidget(self.header_label)
        self.claims_table = QTableWidget(0, 5)
        self.claims_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.claims_table.verticalHeader().setVisible(False)
        self.claims_table.setWordWrap(True)
        header = self.claims_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        for column, width in ((0, 230), (1, 150), (3, 200)):
            self.claims_table.setColumnWidth(column, width)
        self.claims_table.setMinimumHeight(420)
        detail_layout.addWidget(self.claims_table)
        splitter.addWidget(detail)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        # --- passive match
        self.match_box = QGroupBox()
        match_layout = QVBoxLayout(self.match_box)
        match_row = QHBoxLayout()
        self.match_btn = QPushButton()
        self.match_btn.clicked.connect(self.match_requested.emit)
        match_row.addWidget(self.match_btn)
        match_row.addStretch()
        match_layout.addLayout(match_row)
        self.match_status = QLabel()
        self.match_status.setWordWrap(True)
        match_layout.addWidget(self.match_status)
        self.match_table = QTableWidget(0, 5)
        self.match_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.match_table.verticalHeader().setVisible(False)
        self.match_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.match_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.match_table.setMinimumHeight(140)
        match_layout.addWidget(self.match_table)
        layout.addWidget(self.match_box)

        # --- evidence export
        self.export_box = QGroupBox()
        export_layout = QVBoxLayout(self.export_box)
        self.export_note = QLabel()
        self.export_note.setWordWrap(True)
        self.export_note.setStyleSheet(_MUTED)
        export_layout.addWidget(self.export_note)
        form = QFormLayout()
        self.claimed_label = QLabel()
        self.claimed_combo = QComboBox()
        self.claimed_combo.addItem("", None)
        for vehicle in vehicles:
            for variant in vehicle.variants:
                self.claimed_combo.addItem("", f"{vehicle.id}/{variant.id}")
        self.model_year_label = QLabel()
        self.model_year_edit = QLineEdit()
        self.notes_label = QLabel()
        self.notes_edit = QLineEdit()
        form.addRow(self.claimed_label, self.claimed_combo)
        form.addRow(self.model_year_label, self.model_year_edit)
        form.addRow(self.notes_label, self.notes_edit)
        export_layout.addLayout(form)
        self.redact_check = QCheckBox()
        self.redact_check.setChecked(True)  # privacy by default
        export_layout.addWidget(self.redact_check)
        export_row = QHBoxLayout()
        self.export_btn = QPushButton()
        self.export_btn.clicked.connect(self.export_requested.emit)
        export_row.addWidget(self.export_btn)
        export_row.addStretch()
        export_layout.addLayout(export_row)
        self.export_status = QLabel()
        self.export_status.setWordWrap(True)
        export_layout.addWidget(self.export_status)
        layout.addWidget(self.export_box)

        self._render_tree()
        self.set_session_available(False)

    # ------------------------------------------------------------------ input
    @property
    def selected(self) -> tuple[str, str] | None:
        """``(vehicle_id, variant_id)`` shown in the claims table."""
        return self._selected

    def select_variant(self, vehicle_id: str, variant_id: str) -> bool:
        """Select a variant in the tree; ``False`` when it is not listed."""
        for item in self._iter_items():
            if item.data(0, _ROLE) == (vehicle_id, variant_id):
                self.tree.setCurrentItem(item)
                return True
        return False

    def redact_vin(self) -> bool:
        return self.redact_check.isChecked()

    def set_claimed_variant(self, vehicle_id: str | None, variant_id: str | None = None) -> bool:
        """Choose the operator's "this vehicle is" entry; ``None`` clears it."""
        data = None if vehicle_id is None else f"{vehicle_id}/{variant_id}"
        index = self.claimed_combo.findData(data) if data is not None else 0
        if index < 0:
            return False
        self.claimed_combo.setCurrentIndex(index)
        return True

    def operator_context(self) -> OperatorContext:
        claimed = self.claimed_combo.currentData()
        vehicle_id, variant_id = str(claimed).split("/", 1) if claimed else (None, None)
        return OperatorContext(
            vehicle_id=vehicle_id,
            variant_id=variant_id,
            model_year=self.model_year_edit.text().strip() or None,
            notes=self.notes_edit.text().strip() or None,
        )

    def set_session_available(self, available: bool) -> None:
        """Enable matching / export once some session data exists."""
        self._available = available
        self.match_btn.setEnabled(available)
        self.export_btn.setEnabled(available)
        t = COPY[self._lang]
        tip = "" if available else t["kb_match_unavailable"]
        self.match_btn.setToolTip(tip)
        self.export_btn.setToolTip(tip)

    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.title_label.setText(t["kb_title"])
        self.source_label.setText(t["kb_source"].format(self._source))
        self.honesty_label.setText(t["kb_honesty"])
        self.legend_label.setText(t["kb_legend"])
        for level, badge in self.legend_badges.items():
            badge.setText(t["kb_confidence"][level.value])
        self.manufacturer_label.setText(t["kb_manufacturer"])
        self.manufacturer_combo.setItemText(0, t["kb_all_manufacturers"])
        for index, manufacturer in enumerate(self._manufacturers, start=1):
            self.manufacturer_combo.setItemText(index, manufacturer.names.get(lang))
        cols = t["kb_columns"]
        self.claims_table.setHorizontalHeaderLabels(
            [cols["field"], cols["value"], cols["confidence"], cols["source"], cols["notes"]]
        )
        self.match_box.setTitle(t["kb_match_title"])
        self.match_btn.setText(t["kb_match_btn"])
        mcols = t["kb_match_columns"]
        self.match_table.setHorizontalHeaderLabels(
            [mcols["rank"], mcols["vehicle"], mcols["tier"], mcols["score"], mcols["signals"]]
        )
        self.export_box.setTitle(t["kb_export_title"])
        self.export_note.setText(t["kb_export_note"])
        self.claimed_label.setText(t["kb_claimed_vehicle"])
        self.claimed_combo.setItemText(0, t["kb_claimed_none"])
        index = 1
        for vehicle in self._vehicles:
            for variant in vehicle.variants:
                text = f"{vehicle.names.get(lang)} - {variant.names.get(lang)}"
                self.claimed_combo.setItemText(index, text)
                index += 1
        self.model_year_label.setText(t["kb_model_year"])
        self.notes_label.setText(t["kb_operator_notes"])
        self.redact_check.setText(t["kb_redact"])
        self.export_btn.setText(t["kb_export_btn"])
        self.set_session_available(self._available)
        self._render_tree()
        self._render_claims()
        self._render_match()
        self._render_export()

    # ------------------------------------------------------------------ results
    def show_match(self, report: MatchReport) -> None:
        self._report = report
        self._render_match()

    def show_match_error(self, detail: str) -> None:
        self._report = detail
        self._render_match()

    @property
    def match_report(self) -> MatchReport | str | None:
        return self._report

    def show_export_result(self, path: str, simulated: bool) -> None:
        self._export = ("kb_export_done_sim" if simulated else "kb_export_done", path)
        self._render_export()

    def show_export_error(self, detail: str) -> None:
        self._export = ("kb_export_failed", detail)
        self._render_export()

    # ------------------------------------------------------------------ rendering
    def _iter_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                vehicle_item = top.child(j)
                items.extend(vehicle_item.child(k) for k in range(vehicle_item.childCount()))
        return items

    def _render_tree(self) -> None:
        lang = self._lang
        wanted = self.manufacturer_combo.currentData()
        selected = self._selected
        self.tree.blockSignals(True)
        self.tree.clear()
        current: QTreeWidgetItem | None = None
        for manufacturer in self._manufacturers:
            if wanted is not None and manufacturer.id != wanted:
                continue
            top = QTreeWidgetItem([manufacturer.names.get(lang)])
            self.tree.addTopLevelItem(top)
            for vehicle in self._vehicles:
                if vehicle.manufacturer_id != manufacturer.id:
                    continue
                vehicle_item = QTreeWidgetItem([vehicle.names.get(lang)])
                top.addChild(vehicle_item)
                for variant in vehicle.variants:
                    item = QTreeWidgetItem([variant.names.get(lang)])
                    item.setData(0, _ROLE, (vehicle.id, variant.id))
                    vehicle_item.addChild(item)
                    if selected == (vehicle.id, variant.id):
                        current = item
        self.tree.expandAll()
        if current is not None:
            self.tree.setCurrentItem(current)
        self.tree.blockSignals(False)
        if current is None and selected is not None:
            self._selected = None
            self._render_claims()

    def _on_tree_selection(self, item: QTreeWidgetItem | None, _previous: object) -> None:
        data = None if item is None else item.data(0, _ROLE)
        if data:
            self._selected = (str(data[0]), str(data[1]))
            self._render_claims()

    def _lookup(self) -> tuple[VehicleProfile, VehicleVariant] | None:
        if self._selected is None:
            return None
        vehicle = next((v for v in self._vehicles if v.id == self._selected[0]), None)
        variant = None if vehicle is None else vehicle.variant(self._selected[1])
        return None if vehicle is None or variant is None else (vehicle, variant)

    def _render_claims(self) -> None:
        t = COPY[self._lang]
        found = self._lookup()
        if found is None:
            self.header_label.setText(t["kb_select_hint"])
            self.claims_table.setRowCount(0)
            return
        vehicle, variant = found
        self.header_label.setText(
            t["kb_variant_header"].format(
                vehicle.names.get(self._lang), variant.names.get(self._lang)
            )
        )
        rows = claim_rows(variant)
        self.claims_table.setRowCount(len(rows))
        for row, claim_row in enumerate(rows):
            claim = claim_row.claim
            field = t["kb_fields"].get(claim_row.field, claim_row.field)
            section = self._section_name(claim_row.section)
            self._set(row, 0, f"{section} / {field}")
            self._set(row, 1, self._value_text(claim_row.field, claim))
            level = claim_row.confidence
            badge = QTableWidgetItem(t["kb_confidence"][level.value])
            badge.setBackground(QColor(CONFIDENCE_COLORS[level]))
            badge.setForeground(QColor("#ffffff"))
            badge.setData(_ROLE, level.value)
            badge.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.claims_table.setItem(row, 2, badge)
            source_text, source_tip = self._source_text(vehicle, claim)
            self._set(row, 3, source_text, source_tip)
            self._set(row, 4, self._notes_text(claim))
        self.claims_table.resizeRowsToContents()

    def _set(self, row: int, column: int, text: str, tooltip: str | None = None) -> None:
        item = QTableWidgetItem(text)
        item.setToolTip(tooltip or text)
        self.claims_table.setItem(row, column, item)

    def _section_name(self, section: str) -> str:
        sections = COPY[self._lang]["kb_sections"]
        if section.startswith("ecu:"):
            return sections["ecu"].format(section.split(":", 1)[1])
        return sections.get(section, section)

    def _value_text(self, field: str, claim: Claim[object] | None) -> str:
        t = COPY[self._lang]
        if claim is None:
            return t["kb_nothing_recorded"]
        if claim.value is None:
            return t["kb_unknown_value"]
        return format_claim_value(field, claim.value)

    @staticmethod
    def _source_text(vehicle: VehicleProfile, claim: Claim[object] | None) -> tuple[str, str]:
        """Short label for the cell and the full reference (with URL) for the tooltip."""
        if claim is None or claim.source is None:
            return "—", "—"
        source = vehicle.sources.get(claim.source)
        if source is None:
            return claim.source, claim.source
        label = f"{source.title} · {source.publisher}" if source.publisher else source.title
        tip = [label, f"[{source.kind}]"]
        if source.url:
            tip.append(f"{source.url} ({source.accessed})")
        if source.notes:
            tip.append(source.notes)
        return label, "\n".join(tip)

    def _notes_text(self, claim: Claim[object] | None) -> str:
        t = COPY[self._lang]
        if claim is None:
            return ""
        parts: list[str] = []
        if claim.notes:
            parts.append(claim.notes)
        if claim.verify:
            text = self._guidance.get(claim.verify)
            parts.append(
                t["kb_verify_prefix"].format(text.get(self._lang) if text else claim.verify)
            )
        if claim.evidence:
            parts.append(t["kb_evidence"].format(claim.evidence.bundle))
        return "\n".join(parts)

    def _render_match(self) -> None:
        t = COPY[self._lang]
        report = self._report
        self.match_table.setRowCount(0)
        if report is None:
            self.match_status.setText(t["kb_match_disclaimer"])
            self.match_status.setStyleSheet(_MUTED)
            return
        if isinstance(report, str):
            self.match_status.setText(t["kb_match_failed"].format(report))
            self.match_status.setStyleSheet(_WARN)
            return
        lines = []
        if report.simulated:
            lines.append(t["kb_match_simulated"])
        lines.append(
            t["kb_match_summary"].format(
                len(report.candidates), len(report.excluded), report.unmatched_count
            )
        )
        lines.append(t["kb_match_disclaimer"])
        if not report.candidates:
            lines.append(t["kb_match_none"])
        self.match_status.setText("\n".join(lines))
        self.match_status.setStyleSheet(_WARN if report.simulated else "")
        names = {v.id: v for v in self._vehicles}
        rows = [*report.candidates, *report.excluded]
        self.match_table.setRowCount(len(rows))
        for row, candidate in enumerate(rows):
            vehicle = names.get(candidate.vehicle_id)
            variant = None if vehicle is None else vehicle.variant(candidate.variant_id)
            label = (
                f"{vehicle.names.get(self._lang)} - {variant.names.get(self._lang)}"
                if vehicle is not None and variant is not None
                else candidate.key
            )
            signals = "; ".join(
                f"{t['kb_signals'][s.signal.value]}: {t['kb_outcomes'][s.outcome.value]}"
                for s in candidate.signals
                if s.outcome in (SignalOutcome.MATCH, SignalOutcome.CONFLICT)
            )
            if candidate.excluded_reason:
                signals = f"{signals} — {candidate.excluded_reason}"
            texts = (
                str(row + 1),
                label,
                t["kb_tiers"][candidate.tier.value],
                f"{candidate.score:.3f}",
                signals,
            )
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.match_table.setItem(row, column, item)

    def _render_export(self) -> None:
        t = COPY[self._lang]
        if self._export is None:
            self.export_status.setText("")
            return
        key, detail = self._export
        self.export_status.setText(t[key].format(detail))
        self.export_status.setStyleSheet(
            _WARN if key in ("kb_export_failed", "kb_export_done_sim") else _OK
        )


def _badge_style(level: ConfidenceLevel) -> str:
    return (
        f"background-color: {CONFIDENCE_COLORS[level]}; color: #ffffff; border-radius: 4px; "
        "padding: 2px 8px; font-weight: bold; font-size: 11px;"
    )
