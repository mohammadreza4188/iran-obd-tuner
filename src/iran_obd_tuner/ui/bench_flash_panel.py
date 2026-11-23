"""Simulated bench flash panel of the Bench tab (Milestone 7f; virtual ECU only).

Pure presentation over a :class:`~iran_obd_tuner.ui.backend.BenchFlashService`: ``ui`` never
sees a capability, the policy or a ``tuning`` type (architecture tests). Everything runs
against the virtual programmable ECU; the ``SIMULATED`` badge is always visible.

* **Activation** - "Enable bench flash mode" opens :class:`BenchAttestationDialog`: the four
  human statements of :class:`~iran_obd_tuner.core.bench.BenchAttestation`, the machine
  checks with live ✓/✗ (journal, backup directory, free space, image, checksums), the
  operator's name and the typed phrase ``BENCH FLASH``. Confirm stays disabled until every
  item holds. The attestation lives only in this panel's memory, for this window session:
  it is never written anywhere (decision D4).
* **Stepper** - Pre-flight -> Mandatory backup -> Programming -> Verify, with the bytes and
  block of the current stage, the backup path and SHA-256 (copyable) and the journal state.
  Cancel works until the erase starts; from the erase report on it is locked and a "do not
  disconnect" warning is shown (decision D3: a half-written ECU is never abandoned).
* **Recovery** - :meth:`BenchFlashPanel.start_recovery` (also used by the window's emergency
  banner) asks for the attestation again, in recovery mode, then restores the verified
  backup recorded in the dirty journal.

Blocking calls run through ``submit`` (the window's bench background runner); progress comes
back through a :class:`~iran_obd_tuner.ui.worker.ProgressRelay` on the GUI thread.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    ERASE_PROGRESS_MESSAGE,
    BenchAttestation,
    FlashProgress,
    FlashStage,
)
from iran_obd_tuner.core.bench_reports import (
    BenchFlashReport,
    BenchJournalReport,
    BenchJournalStatus,
    BenchPreflightReport,
    SimulatedFault,
)
from iran_obd_tuner.core.models import utc_now
from iran_obd_tuner.ui.backend import BenchFlashService
from iran_obd_tuner.ui.i18n import COPY, Lang
from iran_obd_tuner.ui.worker import CancelFlag, ProgressRelay

__all__ = [
    "EMERGENCY_STATUSES",
    "FAULT_PRESETS",
    "STEPS",
    "BenchAttestationDialog",
    "BenchFlashPanel",
    "JournalDialog",
    "journal_text",
    "ltr",
]

Submit = Callable[
    [Callable[[], object], Callable[[object], None], Callable[[BaseException], None]], None
]

#: The four stages of the stepper (a recovery's "recovery" stage is shown as programming).
STEPS: tuple[FlashStage, ...] = (
    FlashStage.PRE_FLIGHT,
    FlashStage.BACKUP,
    FlashStage.PROGRAMMING,
    FlashStage.VERIFY,
)
#: Simulator faults offered for training (every one is deterministic and fires once).
FAULT_PRESETS: tuple[tuple[str, str | None], ...] = (
    ("none", None),
    ("power_loss", "power-loss-at-block=20"),
    ("drop", "drop-at-block=3"),
    ("nrc72", "nrc72-at-block=2"),
    ("bad_key", "bad-key"),
    ("corrupt_readback", "corrupt-readback"),
)
#: Journal statuses for which the window shows its emergency recovery banner.
EMERGENCY_STATUSES = frozenset({BenchJournalStatus.DIRTY, BenchJournalStatus.CORRUPT})
_STEP_ICONS = {"pending": "○", "active": "●", "done": "✓", "failed": "✗"}
_STEP_STYLE = {
    "pending": "color: #64748b;",
    "active": "color: #fbbf24; font-weight: bold;",
    "done": "color: #10b981; font-weight: bold;",
    "failed": "color: #f87171; font-weight: bold;",
}
_SIM_BADGE = (
    "color: #0f172a; background-color: #fbbf24; font-weight: bold; border-radius: 4px; "
    "padding: 2px 8px;"
)
_FRAME = "QFrame#benchFlash { border: 1px solid #334155; border-radius: 8px; }"
_WARNING = (
    "background-color: #450a0a; color: #fecaca; border: 1px solid #b91c1c; border-radius: 6px; "
    "padding: 6px 12px; font-weight: bold;"
)
_OK = "color: #10b981;"
_BAD = "color: #f87171;"
_MUTED = "color: #94a3b8;"


def ltr(text: str) -> str:
    """Isolate a left-to-right fragment (path, hash, technical detail) inside Persian text.

    Without the Unicode isolates (LRI ... PDI) the bidi algorithm reorders the pieces of a
    Windows path or "N free, M needed" around the Persian words.
    """
    return f"\u2066{text}\u2069" if text else text


def _direction(lang: Lang) -> Qt.LayoutDirection:
    return Qt.LayoutDirection.RightToLeft if lang == "fa" else Qt.LayoutDirection.LeftToRight


def _step_of(stage: FlashStage) -> FlashStage:
    return FlashStage.PROGRAMMING if stage is FlashStage.RECOVERY else stage


def journal_text(lang: Lang, report: BenchJournalReport) -> str:
    """The journal as plain text (the inspect dialog and the tests)."""
    t = COPY[lang]
    fields = t["bflash_journal_fields"]
    lines = [
        f"{fields['status']}: {t['bflash_journal_status'][report.status.value]}",
        f"{fields['detail']}: {report.detail}",
        f"{fields['path']}: {report.journal_path}",
    ]
    if report.state is not None:
        lines += [
            f"{fields['state']}: {t['bflash_states'][report.state.value]} ({report.state})",
            f"{fields['transaction']}: {report.transaction_id}",
            f"{fields['target']}: {report.target_id}",
            f"{fields['image']}: {report.image_name or '-'} / {report.image_sha256}",
        ]
    if report.backup_path is not None:
        lines += [
            f"{fields['backup']}: {report.backup_path}",
            f"{fields['backup_sha']}: {report.backup_sha256}",
        ]
    if report.progress:
        progress = report.progress
        lines.append(
            f"{fields['progress']}: {progress.get('stage')} "
            f"{progress.get('done_bytes')}/{progress.get('total_bytes')}"
        )
    if report.abort_reason:
        lines.append(f"{fields['reason']}: {report.abort_reason}")
    if report.history:
        lines.append(f"{fields['history']}: " + " -> ".join(s for s, _at in report.history))
    return "\n".join(lines)


# --------------------------------------------------------------------------- dialogs


class BenchAttestationDialog(QDialog):
    """The session attestation: four statements, machine checks, operator and phrase.

    Opened with :meth:`QDialog.open` (window modal, non-blocking). ``attested`` carries the
    :class:`BenchAttestation` when the operator confirms; nothing is stored anywhere.

    Args:
        lang: UI language.
        mode: ``flash`` or ``recovery``.
        preflight: The machine checks to show (every one must pass).
        parent: Parent widget.
    """

    attested = Signal(object)

    def __init__(
        self, lang: Lang, mode: str, preflight: BenchPreflightReport, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        t = COPY[lang]
        self.mode = mode
        self.preflight = preflight
        self.setWindowTitle(t["bflash_modal_title"][mode])
        self.setMinimumWidth(560)
        self.setLayoutDirection(_direction(lang))
        self.setStyleSheet(
            "QDialog { background-color: #1e293b; color: #f1f5f9; } QLabel { color: #f1f5f9; }"
        )
        layout = QVBoxLayout(self)

        self.sim_badge = QLabel(t["bflash_sim_badge"])
        self.sim_badge.setStyleSheet(_SIM_BADGE)
        layout.addWidget(self.sim_badge, 0, Qt.AlignmentFlag.AlignLeft)
        intro = QLabel(t["bflash_modal_intro"][mode])
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.statements_title = QLabel(t["bflash_statements_title"])
        self.statements_title.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(self.statements_title)
        self.statement_boxes: dict[str, QCheckBox] = {}
        for name in BenchAttestation.STATEMENTS:
            box = QCheckBox(t["bflash_statements"][name])
            box.toggled.connect(self._update)
            self.statement_boxes[name] = box
            layout.addWidget(box)

        self.checks_title = QLabel(t["bflash_checks_title"])
        self.checks_title.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(self.checks_title)
        self.check_labels: dict[str, QLabel] = {}
        for check in preflight.checks:
            label = QLabel()
            label.setWordWrap(True)
            icon = "✓" if check.ok else "✗"
            text = f"{icon}  {t['bflash_checks'][check.key.value]}"
            if check.override:
                text += f" — {t['bflash_check_override']}"
            if check.detail:
                text += f"\n      {ltr(check.detail)}"
            label.setText(text)
            label.setStyleSheet(_OK if check.ok else _BAD)
            self.check_labels[check.key.value] = label
            layout.addWidget(label)

        form = QGridLayout()
        self.operator_label = QLabel(t["bflash_operator"])
        self.operator_edit = QLineEdit()
        self.operator_edit.textChanged.connect(self._update)
        self.phrase_label = QLabel(t["bflash_phrase"].format(BENCH_CONFIRMATION_PHRASE))
        self.phrase_label.setWordWrap(True)
        self.phrase_edit = QLineEdit()
        self.phrase_edit.setPlaceholderText(BENCH_CONFIRMATION_PHRASE)
        self.phrase_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.phrase_edit.textChanged.connect(self._update)
        form.addWidget(self.operator_label, 0, 0)
        form.addWidget(self.operator_edit, 0, 1)
        form.addWidget(self.phrase_label, 1, 0)
        form.addWidget(self.phrase_edit, 1, 1)
        layout.addLayout(form)

        self.missing_label = QLabel()
        self.missing_label.setWordWrap(True)
        self.missing_label.setStyleSheet(_MUTED)
        layout.addWidget(self.missing_label)
        self.session_note = QLabel(t["bflash_session_note"])
        self.session_note.setWordWrap(True)
        self.session_note.setStyleSheet(_MUTED)
        layout.addWidget(self.session_note)

        buttons = QHBoxLayout()
        self.confirm_btn = QPushButton(t["bflash_confirm"][mode])
        self.cancel_btn = QPushButton(t["bflash_dialog_cancel"])
        self.confirm_btn.clicked.connect(self._confirm)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)
        self._t = t
        self._separator = "، " if lang == "fa" else ", "
        self._update()

    def _draft(self) -> BenchAttestation:
        values = {name: box.isChecked() for name, box in self.statement_boxes.items()}
        return BenchAttestation(
            ecu_on_bench_out_of_vehicle=values["ecu_on_bench_out_of_vehicle"],
            supply_stable_and_current_limited=values["supply_stable_and_current_limited"],
            wiring_verified=values["wiring_verified"],
            owner_approval_recorded=values["owner_approval_recorded"],
            operator=self.operator_edit.text().strip(),
            confirmation_phrase=self.phrase_edit.text().strip(),
            attested_at=utc_now(),
            simulated=True,  # only the virtual programmable ECU exists in this build
        )

    def unmet(self) -> tuple[str, ...]:
        """What still blocks the confirmation (attestation items and ``machine_checks``)."""
        missing = list(self._draft().unmet())
        if not self.preflight.ok:
            missing.append("machine_checks")
        return tuple(missing)

    def _update(self) -> None:
        missing = self.unmet()
        self.confirm_btn.setEnabled(not missing)
        names = self._t["bflash_unmet"]
        self.missing_label.setText(
            ""
            if not missing
            else self._t["bflash_missing"].format(self._separator.join(names[m] for m in missing))
        )

    def _confirm(self) -> None:
        if self.unmet():
            return
        self.attested.emit(self._draft())
        self.accept()


class JournalDialog(QDialog):
    """Read-only view of the flash journal (the window's "Inspect journal" action)."""

    def __init__(self, lang: Lang, report: BenchJournalReport, parent: QWidget | None = None):
        super().__init__(parent)
        t = COPY[lang]
        self.setWindowTitle(t["bflash_journal_title"])
        self.setMinimumSize(640, 360)
        self.setLayoutDirection(_direction(lang))
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(journal_text(lang, report))
        self.text.setReadOnly(True)
        self.text.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(self.text)
        self.close_btn = QPushButton(t["bflash_dialog_close"])
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignRight)


# --------------------------------------------------------------------------- the panel


class BenchFlashPanel(QFrame):
    """Simulated bench flash and recovery (see the module docstring).

    Args:
        service: The simulated bench flash service.
        directory: The bench directory (journal, backups, simulated ECU state).
        submit: Runs a job on the bench background thread, calls back on the GUI thread.
    """

    #: Emitted with the new :class:`BenchJournalReport` after every journal check.
    journal_changed = Signal(object)

    def __init__(
        self,
        service: BenchFlashService,
        directory: Path,
        submit: Submit,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("benchFlash")
        self.setStyleSheet(_FRAME)
        self._service = service
        self._directory = Path(directory)
        self._submit = submit
        self._lang: Lang = "fa"
        self._attestation: BenchAttestation | None = None  # this window session only
        self._journal: BenchJournalReport | None = None
        self._running: str | None = None  # "flash", "recovery", "checks", "discard"
        self._rom_path: Path | None = None
        self._maps_path: Path | None = None
        self._steps: dict[FlashStage, str] = dict.fromkeys(STEPS, "pending")
        self._erase_started = False
        self._last_progress: FlashProgress | None = None
        self._report: BenchFlashReport | None = None
        self._result: tuple[str, str] | None = None  # (i18n key, detail)
        self._cancel = CancelFlag()
        self._relay = ProgressRelay(self)
        self._relay.progressed.connect(self._on_progress)
        self.attestation_dialog: BenchAttestationDialog | None = None
        self.journal_dialog: JournalDialog | None = None

        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.sim_badge = QLabel()
        self.sim_badge.setStyleSheet(_SIM_BADGE)
        head.addWidget(self.title_label)
        head.addWidget(self.sim_badge)
        head.addStretch()
        layout.addLayout(head)
        self.intro_label = QLabel()
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet(_MUTED)
        layout.addWidget(self.intro_label)

        mode_row = QHBoxLayout()
        self.mode_label = QLabel()
        self.enable_btn = QPushButton()
        self.enable_btn.clicked.connect(self._toggle_mode)
        mode_row.addWidget(self.mode_label, 1)
        mode_row.addWidget(self.enable_btn)
        layout.addLayout(mode_row)

        source_row = QHBoxLayout()
        self.source_label = QLabel()
        self.choose_rom_btn = QPushButton()
        self.use_demo_btn = QPushButton()
        self.allow_unverified_box = QCheckBox()
        self.choose_rom_btn.clicked.connect(self._choose_rom)
        self.use_demo_btn.clicked.connect(self._use_demo)
        source_row.addWidget(self.source_label, 1)
        source_row.addWidget(self.choose_rom_btn)
        source_row.addWidget(self.use_demo_btn)
        layout.addLayout(source_row)
        layout.addWidget(self.allow_unverified_box)

        fault_row = QHBoxLayout()
        self.fault_label = QLabel()
        self.fault_combo = QComboBox()
        for _key, text in FAULT_PRESETS:
            self.fault_combo.addItem("", text)
        fault_row.addWidget(self.fault_label)
        fault_row.addWidget(self.fault_combo, 1)
        layout.addLayout(fault_row)

        actions = QHBoxLayout()
        self.flash_btn = QPushButton()
        self.cancel_btn = QPushButton()
        self.discard_btn = QPushButton()
        self.inspect_btn = QPushButton()
        self.flash_btn.clicked.connect(self.start_flash)
        self.cancel_btn.clicked.connect(self._request_cancel)
        self.discard_btn.clicked.connect(self._discard_stale)
        self.inspect_btn.clicked.connect(self.inspect_journal)
        for btn in (self.flash_btn, self.cancel_btn, self.discard_btn, self.inspect_btn):
            actions.addWidget(btn)
        actions.addStretch()
        layout.addLayout(actions)

        stepper = QHBoxLayout()
        self.step_labels: dict[FlashStage, QLabel] = {}
        for stage in STEPS:
            label = QLabel()
            self.step_labels[stage] = label
            stepper.addWidget(label)
        stepper.addStretch()
        layout.addLayout(stepper)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet(_MUTED)
        layout.addWidget(self.progress_label)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(_WARNING)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        info = QGridLayout()
        self.journal_title = QLabel()
        self.journal_label = QLabel()
        self.dir_title = QLabel()
        self.dir_edit = QLineEdit(str(self._directory))
        self.backup_title = QLabel()
        self.backup_path_edit = QLineEdit()
        self.sha_title = QLabel()
        self.backup_sha_edit = QLineEdit()
        for edit in (self.dir_edit, self.backup_path_edit, self.backup_sha_edit):
            edit.setReadOnly(True)  # selectable and copyable
            edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        for row, (title, value) in enumerate(
            (
                (self.journal_title, self.journal_label),
                (self.dir_title, self.dir_edit),
                (self.backup_title, self.backup_path_edit),
                (self.sha_title, self.backup_sha_edit),
            )
        ):
            info.addWidget(title, row, 0)
            info.addWidget(value, row, 1)
        layout.addLayout(info)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.set_language(self._lang)

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        """Bench flash mode is enabled for this window session."""
        return self._attestation is not None

    @property
    def running(self) -> str | None:
        return self._running

    @property
    def journal(self) -> BenchJournalReport | None:
        return self._journal

    @property
    def last_report(self) -> BenchFlashReport | None:
        return self._report

    @property
    def erase_started(self) -> bool:
        return self._erase_started

    @property
    def step_states(self) -> dict[FlashStage, str]:
        return dict(self._steps)

    def _selected_fault(self) -> SimulatedFault | None:
        text = self.fault_combo.currentData()
        return None if not text else SimulatedFault.parse(str(text))

    # ------------------------------------------------------------------ language
    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.title_label.setText(t["bflash_title"])
        self.sim_badge.setText(t["bflash_sim_badge"])
        self.intro_label.setText(t["bflash_intro"])
        self.choose_rom_btn.setText(t["bflash_choose_rom"])
        self.use_demo_btn.setText(t["bflash_use_demo"])
        self.allow_unverified_box.setText(t["bflash_allow_unverified"])
        self.fault_label.setText(t["bflash_fault_label"])
        for index, (key, _text) in enumerate(FAULT_PRESETS):
            self.fault_combo.setItemText(index, t["bflash_faults"][key])
        self.flash_btn.setText(t["bflash_start"])
        self.cancel_btn.setText(t["bflash_cancel"])
        self.discard_btn.setText(t["bflash_discard"])
        self.inspect_btn.setText(t["bflash_inspect"])
        self.warning_label.setText(t["bflash_do_not_disconnect"])
        self.journal_title.setText(t["bflash_journal_label"])
        self.dir_title.setText(t["bflash_dir_label"])
        self.backup_title.setText(t["bflash_backup_label"])
        self.sha_title.setText(t["bflash_sha_label"])
        self._render()

    # ------------------------------------------------------------------ rendering
    def _render(self) -> None:
        t = COPY[self._lang]
        running = self._running is not None
        clean = self._journal is not None and self._journal.status is BenchJournalStatus.CLEAN
        stale = self._journal is not None and self._journal.status is BenchJournalStatus.STALE
        if self._attestation is None:
            self.mode_label.setText(t["bflash_mode_inactive"])
            self.mode_label.setStyleSheet(_MUTED)
            self.enable_btn.setText(t["bflash_enable"])
        else:
            self.mode_label.setText(t["bflash_mode_active"].format(self._attestation.operator))
            self.mode_label.setStyleSheet("color: #fbbf24; font-weight: bold;")
            self.enable_btn.setText(t["bflash_disable"])
        self.enable_btn.setEnabled(not running)
        if self._rom_path is None:
            self.source_label.setText(t["bflash_source_demo"])
        else:
            maps = self._maps_path.name if self._maps_path else t["bflash_no_maps"]
            self.source_label.setText(t["bflash_source_file"].format(self._rom_path.name, maps))
        for widget in (
            self.choose_rom_btn,
            self.use_demo_btn,
            self.allow_unverified_box,
            self.fault_combo,
        ):
            widget.setEnabled(not running)
        self.flash_btn.setEnabled(self.active and clean and not running)
        self.cancel_btn.setEnabled(
            self._running == "flash" and not self._erase_started and not self._cancel()
        )
        self.discard_btn.setVisible(stale)
        self.discard_btn.setEnabled(stale and not running)
        self.inspect_btn.setEnabled(self._journal is not None)
        self.warning_label.setVisible(running and self._erase_started)
        for stage, label in self.step_labels.items():
            state = self._steps[stage]
            label.setText(f"{_STEP_ICONS[state]} {t['bflash_steps'][stage.value]}")
            label.setStyleSheet(_STEP_STYLE[state])
        self._render_progress()
        self._render_journal()
        self._render_result()

    def _render_progress(self) -> None:
        t = COPY[self._lang]
        report = self._last_progress
        if report is None:
            self.progress_bar.setValue(0)
            self.progress_label.setText(t["bflash_idle"] if self._running is None else "")
            return
        self.progress_bar.setValue(round(report.fraction * 1000) if report.total_bytes else 0)
        stage = t["bflash_steps"][_step_of(report.stage).value]
        if report.total_bytes:
            text = t["bflash_progress_bytes"].format(
                stage, report.done_bytes, report.total_bytes, round(report.fraction * 100)
            )
            if report.block_sequence is not None:
                text += " — " + t["bflash_progress_block"].format(f"0x{report.block_sequence:02X}")
        else:
            text = f"{stage}: {report.message}" if report.message else stage
        self.progress_label.setText(text)

    def _render_journal(self) -> None:
        t = COPY[self._lang]
        journal = self._journal
        if journal is None:
            self.journal_label.setText(t["bflash_journal_unknown"])
            self.journal_label.setStyleSheet(_MUTED)
            return
        if journal.state is None:  # clean or corrupt: the status says it all
            text = t["bflash_journal_status"][journal.status.value]
        else:  # the state is the more precise of the two
            text = t["bflash_states"][journal.state.value]
        self.journal_label.setText(text)
        bad = journal.status in (BenchJournalStatus.DIRTY, BenchJournalStatus.CORRUPT)
        self.journal_label.setStyleSheet(_BAD if bad else _OK)

    def _render_result(self) -> None:
        t = COPY[self._lang]
        if self._result is None:
            self.result_label.setText("")
            return
        key, detail = self._result
        text = t[key]
        if detail:
            text += f"\n{detail}"
        self.result_label.setText(text)
        good = key in ("bflash_result_ok", "bflash_result_recovered", "bflash_discarded")
        self.result_label.setStyleSheet(_OK if good else _BAD)

    # ------------------------------------------------------------------ journal
    def refresh_journal(self) -> None:
        """Re-assess the journal in the background; emits :attr:`journal_changed`."""
        directory = self._directory
        self._submit(
            lambda: self._service.assess_journal(directory),
            self._on_journal,
            self._on_journal_failed,
        )

    def _on_journal(self, report: object) -> None:
        assert isinstance(report, BenchJournalReport)
        self._journal = report
        self._render()
        self.journal_changed.emit(report)

    def _on_journal_failed(self, error: BaseException) -> None:
        self._result = ("bflash_error", str(error))
        self._render()

    def inspect_journal(self) -> None:
        """Show the journal read-only (refreshed first)."""
        directory = self._directory

        def show(report: object) -> None:
            self._on_journal(report)
            assert isinstance(report, BenchJournalReport)
            self.journal_dialog = JournalDialog(self._lang, report, self)
            self.journal_dialog.open()

        self._submit(lambda: self._service.assess_journal(directory), show, self._on_journal_failed)

    # ------------------------------------------------------------------ activation
    def _toggle_mode(self) -> None:
        if self._attestation is not None:
            self._attestation = None  # leaving bench flash mode needs no confirmation
            self._render()
            return
        self._open_attestation("flash")

    def _open_attestation(self, mode: str) -> None:
        directory = self._directory
        rom, maps = self._rom_path, self._maps_path
        allow = self.allow_unverified_box.isChecked()
        if mode == "flash":

            def job() -> BenchPreflightReport:
                return self._service.preflight_flash(
                    directory,
                    rom_path=rom,
                    maps_path=maps,
                    allow_unverified_checksum_in_simulation=allow,
                )

        else:

            def job() -> BenchPreflightReport:
                return self._service.preflight_recovery(directory)

        self._running = "checks"
        self._render()

        def show(report: object) -> None:
            self._running = None
            assert isinstance(report, BenchPreflightReport)
            dialog = BenchAttestationDialog(self._lang, mode, report, self)
            dialog.attested.connect(lambda att: self._on_attested(mode, att))
            self.attestation_dialog = dialog
            self._render()
            dialog.open()

        def failed(error: BaseException) -> None:
            self._running = None
            self._result = ("bflash_error", str(error))
            self._render()

        self._submit(job, show, failed)

    def _on_attested(self, mode: str, attestation: object) -> None:
        assert isinstance(attestation, BenchAttestation)
        self._attestation = attestation
        self._result = None
        self._render()
        if mode == "recovery":
            self._run_recovery()

    # ------------------------------------------------------------------ source
    def _choose_rom(self) -> None:
        t = COPY[self._lang]
        path, _ = QFileDialog.getOpenFileName(
            self, t["bflash_rom_dialog"], "", t["bench_open_filter"]
        )
        if not path:
            return
        maps, _ = QFileDialog.getOpenFileName(
            self, t["bflash_maps_dialog"], str(Path(path).parent), t["bench_defs_filter"]
        )
        self._rom_path = Path(path)
        self._maps_path = Path(maps) if maps else None
        self._render()

    def _use_demo(self) -> None:
        self._rom_path = self._maps_path = None
        self._render()

    # ------------------------------------------------------------------ runs
    def _begin(self, kind: str) -> None:
        self._running = kind
        self._steps = dict.fromkeys(STEPS, "pending")
        self._steps[FlashStage.PRE_FLIGHT] = "active"
        self._erase_started = False
        self._last_progress = None
        self._report = None
        self._result = None
        self._cancel.reset()
        self.backup_path_edit.clear()
        self.backup_sha_edit.clear()
        self._render()

    def start_flash(self) -> None:
        """Run the four stages (bench flash mode must be active and the journal clean)."""
        if (
            self._attestation is None
            or self._running is not None
            or self._journal is None
            or self._journal.status is not BenchJournalStatus.CLEAN
        ):
            return
        attestation = self._attestation
        directory, rom, maps = self._directory, self._rom_path, self._maps_path
        fault = self._selected_fault()
        allow = self.allow_unverified_box.isChecked()
        self._begin("flash")
        self._submit(
            lambda: self._service.run_simulated_flash(
                directory,
                rom_path=rom,
                maps_path=maps,
                fault=fault,
                allow_unverified_checksum_in_simulation=allow,
                attestation=attestation,
                progress=self._relay.report,
                cancel=self._cancel,
            ),
            self._on_done,
            self._on_failed,
        )

    def start_recovery(self) -> None:
        """Ask for the attestation (recovery mode), then restore the verified backup."""
        if self._running is not None:
            return
        self._open_attestation("recovery")

    def _run_recovery(self) -> None:
        attestation = self._attestation
        directory = self._directory
        self._begin("recovery")
        self._submit(
            lambda: self._service.run_simulated_recovery(
                directory, attestation=attestation, progress=self._relay.report
            ),
            self._on_done,
            self._on_failed,
        )

    def _request_cancel(self) -> None:
        if self._running == "flash" and not self._erase_started:
            self._cancel.request()
            self._render()

    def _on_progress(self, value: object) -> None:
        if not isinstance(value, FlashProgress) or self._running not in ("flash", "recovery"):
            return
        step = _step_of(value.stage)
        if value.message == ERASE_PROGRESS_MESSAGE or step in (
            FlashStage.PROGRAMMING,
            FlashStage.VERIFY,
        ):
            self._erase_started = True
        index = STEPS.index(step)
        for i, stage in enumerate(STEPS):
            if i < index:
                self._steps[stage] = "done"
            elif i == index:
                self._steps[stage] = "active"
        self._last_progress = value
        self._render()

    def _finish(self) -> None:
        self._running = None
        self.refresh_journal()

    def _on_done(self, value: object) -> None:
        assert isinstance(value, BenchFlashReport)
        self._report = value
        if value.backup_path:
            self.backup_path_edit.setText(value.backup_path)
            self.backup_sha_edit.setText(value.backup_sha256 or "")
        if value.ok:
            self._steps = dict.fromkeys(STEPS, "done")
            key = "bflash_result_recovered" if value.mode == "recovery" else "bflash_result_ok"
            self._result = (key, "")
        else:
            self._fail_current_step()
            if value.ecu_untouched:
                key = "bflash_result_cancelled" if self._cancel() else "bflash_result_aborted"
            else:
                key = "bflash_result_recovery_required"
            self._result = (key, value.error or "")
        self._finish()

    def _on_failed(self, error: BaseException) -> None:
        self._fail_current_step()
        key = "bflash_result_cancelled" if self._cancel() else "bflash_refused"
        self._result = (key, str(error))
        self._finish()

    def _fail_current_step(self) -> None:
        for stage in STEPS:
            if self._steps[stage] == "active":
                self._steps[stage] = "failed"

    # ------------------------------------------------------------------ stale journal
    def _confirm_discard(self) -> bool:
        t = COPY[self._lang]
        answer = QMessageBox.question(
            self,
            t["bflash_discard_title"],
            t["bflash_discard_text"],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _discard_stale(self) -> None:
        if self._journal is None or self._journal.status is not BenchJournalStatus.STALE:
            return
        if not self._confirm_discard():  # the operator's explicit acknowledgement
            return
        directory = self._directory
        self._running = "discard"
        self._render()

        def done(archive: object) -> None:
            self._result = ("bflash_discarded", str(archive))
            self._finish()

        def failed(error: BaseException) -> None:
            self._result = ("bflash_error", str(error))
            self._finish()

        self._submit(lambda: self._service.discard_stale_journal(directory), done, failed)
