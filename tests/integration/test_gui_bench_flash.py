"""Milestone 7f: the simulated bench flash panel and the emergency recovery banner (headless).

The window runs over the production wiring (:class:`AppDashboardBackend` ->
``app/bench_flash.py`` -> gate, vault, journal, orchestrator -> bench link -> virtual
programmable ECU on python-can's in-process ``virtual`` bus). Skipped without the ``[gui]``
or the ``[can]`` extra. Bench jobs run on the window's bench thread; the tests wait for them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the dashboard needs the optional [gui] extra")
pytest.importorskip("can", reason="the simulated bench link needs the optional [can] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iran_obd_tuner.app.bench_flash import SimulatedEcuStore, simulated_attestation  # noqa: E402
from iran_obd_tuner.app.dashboard import AppDashboardBackend  # noqa: E402
from iran_obd_tuner.core.bench import (  # noqa: E402
    BENCH_CONFIRMATION_PHRASE,
    ERASE_PROGRESS_MESSAGE,
    SIMULATED_BENCH_TARGET,
    BenchAttestation,
    EcuIdentity,
    FlashProgress,
    FlashStage,
)
from iran_obd_tuner.core.bench_reports import BenchJournalStatus  # noqa: E402
from iran_obd_tuner.core.config import AppSettings  # noqa: E402
from iran_obd_tuner.database.repository import JsonKnowledgeRepository  # noqa: E402
from iran_obd_tuner.hardware.uds_sim_programmable import default_simulated_image  # noqa: E402
from iran_obd_tuner.tuning.demo import DEMO_ROM_NAME, build_demo_rom  # noqa: E402
from iran_obd_tuner.tuning.flash_journal import JOURNAL_NAME, FlashJournal  # noqa: E402
from iran_obd_tuner.ui.bench_flash_panel import (  # noqa: E402
    FAULT_PRESETS,
    STEPS,
    BenchAttestationDialog,
    BenchFlashPanel,
    journal_text,
)
from iran_obd_tuner.ui.i18n import COPY  # noqa: E402
from iran_obd_tuner.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def wait_until(condition: Callable[[], bool], timeout_s: float = 20.0) -> None:
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        app.processEvents()
        time.sleep(0.002)


def settle(window: MainWindow) -> None:
    wait_until(lambda: not window.busy)


class Backend(AppDashboardBackend):
    __test__ = False

    def __init__(self, output_dir: Path) -> None:
        super().__init__(AppSettings(output_dir=output_dir), JsonKnowledgeRepository())

    def list_serial_ports(self) -> tuple[str, ...]:
        return ()


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def backend(tmp_path: Path) -> Backend:
    return Backend(tmp_path / "output")


def open_window(qt_app: QApplication, backend: Backend) -> MainWindow:
    window = MainWindow(backend)
    window.lang_combo.setCurrentIndex(1)  # English
    qt_app.processEvents()
    settle(window)
    return window


@pytest.fixture
def window(qt_app: QApplication, backend: Backend) -> Iterator[MainWindow]:
    win = open_window(qt_app, backend)
    yield win
    settle(win)
    win.close()


def panel_of(window: MainWindow) -> BenchFlashPanel:
    window._switch_tab("bench")
    return window.bench_flash_panel


def open_dialog(window: MainWindow, button) -> BenchAttestationDialog:  # noqa: ANN001
    panel = window.bench_flash_panel
    panel.attestation_dialog = None
    button.click()
    settle(window)
    wait_until(lambda: panel.attestation_dialog is not None)
    dialog = panel.attestation_dialog
    assert dialog is not None
    return dialog


def fill(dialog: BenchAttestationDialog, *, phrase: str = BENCH_CONFIRMATION_PHRASE) -> None:
    for box in dialog.statement_boxes.values():
        box.setChecked(True)
    dialog.operator_edit.setText("GUI test")
    dialog.phrase_edit.setText(phrase)


def activate(window: MainWindow) -> BenchFlashPanel:
    panel = panel_of(window)
    dialog = open_dialog(window, panel.enable_btn)
    fill(dialog)
    dialog.confirm_btn.click()
    assert panel.active
    return panel


def run_flash(window: MainWindow, fault: str = "none") -> BenchFlashPanel:
    panel = window.bench_flash_panel
    keys = [key for key, _ in FAULT_PRESETS]
    panel.fault_combo.setCurrentIndex(keys.index(fault))
    assert panel.flash_btn.isEnabled()
    panel.flash_btn.click()
    assert panel.running == "flash"
    wait_until(lambda: panel.running is None)
    settle(window)
    return panel


# --------------------------------------------------------------------------- lock states


def test_the_panel_starts_off_with_a_clean_journal(window: MainWindow) -> None:
    panel = panel_of(window)
    t = COPY["en"]
    assert panel.sim_badge.text() == t["bflash_sim_badge"] and not panel.sim_badge.isHidden()
    assert panel.journal is not None and panel.journal.status is BenchJournalStatus.CLEAN
    assert not panel.active and panel.mode_label.text() == t["bflash_mode_inactive"]
    assert panel.enable_btn.isEnabled()
    for button in (panel.flash_btn, panel.cancel_btn):
        assert not button.isEnabled()
    assert panel.discard_btn.isHidden() and panel.warning_label.isHidden()
    assert window.recovery_banner.isHidden()
    assert [panel.step_labels[s].text() for s in STEPS] == [
        f"○ {t['bflash_steps'][s.value]}" for s in STEPS
    ]
    assert panel.dir_edit.isReadOnly() and panel.dir_edit.text().endswith("bench-flash-sim")


def test_the_fault_presets_are_all_valid_and_translated() -> None:
    from iran_obd_tuner.core.bench_reports import SimulatedFault

    for key, text in FAULT_PRESETS:
        if text is not None:
            SimulatedFault.parse(text)
        for lang in ("fa", "en"):
            assert COPY[lang]["bflash_faults"][key]


# --------------------------------------------------------------------------- the modal


def test_the_attestation_modal_needs_every_item(window: MainWindow) -> None:
    panel = panel_of(window)
    dialog = open_dialog(window, panel.enable_btn)

    assert dialog.mode == "flash" and dialog.preflight.ok
    assert all(label.text().startswith("✓") for label in dialog.check_labels.values())
    assert set(dialog.check_labels) == {
        "journal_clean",
        "image_valid",
        "checksums_valid",
        "backup_dir_writable",
        "free_space",
        "target_simulated",
    }
    assert not dialog.confirm_btn.isEnabled()
    assert set(dialog.unmet()) == {*BenchAttestation.STATEMENTS, "operator", "confirmation_phrase"}
    boxes = list(dialog.statement_boxes.values())
    for box in boxes:
        box.setChecked(True)
        assert not dialog.confirm_btn.isEnabled()
    dialog.operator_edit.setText("   ")
    assert "operator" in dialog.unmet()
    dialog.operator_edit.setText("GUI test")
    for wrong in ("bench flash", "BENCH  FLASH", "BENCH", "BENCH FLASH!"):
        dialog.phrase_edit.setText(wrong)
        assert not dialog.confirm_btn.isEnabled(), wrong
    dialog.phrase_edit.setText(BENCH_CONFIRMATION_PHRASE)
    assert dialog.confirm_btn.isEnabled() and dialog.unmet() == ()
    boxes[2].setChecked(False)  # unticking any statement locks it again
    assert not dialog.confirm_btn.isEnabled()
    assert COPY["en"]["bflash_unmet"]["wiring_verified"] in dialog.missing_label.text()
    boxes[2].setChecked(True)

    dialog.confirm_btn.click()

    assert panel.active and panel.flash_btn.isEnabled()
    assert "GUI test" in panel.mode_label.text()


def test_a_failed_machine_check_blocks_the_modal(window: MainWindow, tmp_path: Path) -> None:
    panel = panel_of(window)
    bad = tmp_path / "too_small.bin"
    bad.write_bytes(b"\x00" * 32 * 1024)  # half the simulated target's image size
    panel._rom_path = bad

    dialog = open_dialog(window, panel.enable_btn)
    fill(dialog)

    assert not dialog.preflight.ok
    assert dialog.check_labels["image_valid"].text().startswith("✗")
    assert "machine_checks" in dialog.unmet() and not dialog.confirm_btn.isEnabled()
    dialog.confirm_btn.click()  # a disabled button does nothing
    assert not panel.active


def test_an_unverified_checksum_needs_the_simulation_override(
    window: MainWindow, tmp_path: Path
) -> None:
    rom = tmp_path / DEMO_ROM_NAME
    rom.write_bytes(build_demo_rom())  # no maps: checksums not configured
    panel = panel_of(window)
    panel._rom_path = rom

    refused = open_dialog(window, panel.enable_btn)
    assert not refused.preflight.ok and refused.check_labels["image_valid"].text().startswith("✗")
    refused.reject()

    panel.allow_unverified_box.setChecked(True)
    allowed = open_dialog(window, panel.enable_btn)
    assert allowed.preflight.ok
    assert COPY["en"]["bflash_check_override"] in allowed.check_labels["checksums_valid"].text()


def test_bench_flash_mode_is_never_persisted(
    qt_app: QApplication, window: MainWindow, backend: Backend
) -> None:
    activate(window)
    other = open_window(qt_app, backend)
    try:
        assert not other.bench_flash_panel.active  # a new window starts locked again
    finally:
        other.close()
    window.bench_flash_panel.enable_btn.click()  # "Leave bench flash mode"
    assert not window.bench_flash_panel.active
    assert not window.bench_flash_panel.flash_btn.isEnabled()


# --------------------------------------------------------------------------- flash


def test_a_full_simulated_flash_walks_the_four_stages(window: MainWindow, backend: Backend) -> None:
    panel = activate(window)
    seen: list[tuple[FlashStage, str, bool, bool, bool]] = []

    def record(value: object) -> None:
        assert isinstance(value, FlashProgress)
        seen.append(
            (
                value.stage,
                value.message,
                panel.erase_started,
                panel.cancel_btn.isEnabled(),
                panel.warning_label.isVisible() or not panel.warning_label.isHidden(),
            )
        )

    panel._relay.progressed.connect(record)
    run_flash(window)

    report = panel.last_report
    assert report is not None and report.ok and report.mode == "flash"
    assert panel.step_states == dict.fromkeys(STEPS, "done")
    assert panel.result_label.text() == COPY["en"]["bflash_result_ok"]
    stages = [stage for stage, *_ in seen]
    assert stages[0] is FlashStage.PRE_FLIGHT
    first = {stage: stages.index(stage) for stage in STEPS}
    assert first[FlashStage.PRE_FLIGHT] < first[FlashStage.BACKUP]
    assert first[FlashStage.BACKUP] < first[FlashStage.PROGRAMMING] < first[FlashStage.VERIFY]
    erase = next(
        i for i, (_s, message, *_r) in enumerate(seen) if message == ERASE_PROGRESS_MESSAGE
    )
    assert all(cancel for _s, _m, _e, cancel, _w in seen[:erase] if _s is FlashStage.BACKUP)
    assert all(erased and not cancel and warned for _s, _m, erased, cancel, warned in seen[erase:])
    # the backup is shown, copyable, and is what the vault wrote
    path = Path(panel.backup_path_edit.text())
    assert panel.backup_path_edit.isReadOnly() and path.is_file()
    assert panel.backup_sha_edit.text() == hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.read_bytes() == default_simulated_image()
    state = SimulatedEcuStore(backend.bench_flash_dir).load()
    assert state is not None and state.flash == build_demo_rom()
    assert panel.journal is not None and panel.journal.status is BenchJournalStatus.CLEAN
    assert window.recovery_banner.isHidden() and panel.warning_label.isHidden()
    assert panel.flash_btn.isEnabled()  # ready for the next one


def test_cancel_before_the_erase_leaves_the_ecu_untouched(
    window: MainWindow, backend: Backend
) -> None:
    panel = activate(window)
    panel.flash_btn.click()
    assert panel.cancel_btn.isEnabled()
    panel.cancel_btn.click()  # honoured at the next pre-flight / backup report
    assert not panel.cancel_btn.isEnabled()
    wait_until(lambda: panel.running is None)
    settle(window)

    assert panel.result_label.text().startswith(COPY["en"]["bflash_result_cancelled"])
    assert not panel.erase_started
    assert panel.journal is not None and panel.journal.status is BenchJournalStatus.CLEAN
    state = SimulatedEcuStore(backend.bench_flash_dir).load()
    assert state is None or state.flash == default_simulated_image()
    assert window.recovery_banner.isHidden()


# --------------------------------------------------------------------------- recovery


def test_power_loss_raises_the_banner_and_the_recovery_rolls_back(
    qt_app: QApplication, window: MainWindow, backend: Backend
) -> None:
    panel = activate(window)
    run_flash(window, "power_loss")

    report = panel.last_report
    assert report is not None and report.recovery_required
    assert panel.result_label.text().startswith(COPY["en"]["bflash_result_recovery_required"])
    assert FlashStage.PROGRAMMING in [
        s for s, state in panel.step_states.items() if state == "failed"
    ]
    assert panel.journal is not None and panel.journal.status is BenchJournalStatus.DIRTY
    assert not panel.flash_btn.isEnabled()  # a dirty journal blocks every new flash
    assert not window.recovery_banner.isHidden()
    assert window.recovery_btn.isEnabled()
    assert str(backend.bench_flash_dir) in window.recovery_label.text()
    torn = SimulatedEcuStore(backend.bench_flash_dir).load()
    assert torn is not None and torn.flash not in (default_simulated_image(), build_demo_rom())

    # a window opened later sees the dirty journal at start-up
    later = open_window(qt_app, backend)
    try:
        assert not later.recovery_banner.isHidden() and later.recovery_btn.isEnabled()
        assert not later.bench_flash_panel.active
    finally:
        later.close()

    # inspect the journal from the banner
    window.inspect_journal_btn.click()
    settle(window)
    wait_until(lambda: panel.journal_dialog is not None)
    text = panel.journal_dialog.text.toPlainText()  # type: ignore[union-attr]
    assert "interrupted" in text and report.backup_sha256 in text  # type: ignore[operator]
    panel.journal_dialog.accept()  # type: ignore[union-attr]

    # "Start recovery" asks for the attestation again, in recovery mode
    window._switch_tab("dashboard")
    dialog = open_dialog(window, window.recovery_btn)
    assert window.stack.currentIndex() == window.tab_ids.index("bench")
    assert dialog.mode == "recovery" and dialog.preflight.ok
    assert set(dialog.check_labels) == {
        "journal_recoverable",
        "backup_verified",
        "target_simulated",
    }
    assert not dialog.confirm_btn.isEnabled()
    fill(dialog)
    dialog.confirm_btn.click()
    assert panel.running == "recovery"
    assert not panel.cancel_btn.isEnabled()  # a recovery is never abandoned
    wait_until(lambda: panel.running is None)
    settle(window)

    done = panel.last_report
    assert done is not None and done.mode == "recovery" and done.ok
    assert panel.result_label.text() == COPY["en"]["bflash_result_recovered"]
    assert panel.journal is not None and panel.journal.status is BenchJournalStatus.CLEAN
    assert window.recovery_banner.isHidden()
    restored = SimulatedEcuStore(backend.bench_flash_dir).load()
    assert restored is not None and restored.flash == default_simulated_image()


def test_a_corrupt_journal_shows_the_banner_without_a_recovery(
    qt_app: QApplication, backend: Backend
) -> None:
    backend.bench_flash_dir.mkdir(parents=True)
    (backend.bench_flash_dir / JOURNAL_NAME).write_text("{broken", encoding="utf-8")
    window = open_window(qt_app, backend)
    try:
        assert not window.recovery_banner.isHidden()
        assert not window.recovery_btn.isEnabled()
        assert JOURNAL_NAME in window.recovery_label.text()
        panel = panel_of(window)
        assert not panel.flash_btn.isEnabled()
        dialog = open_dialog(window, panel.enable_btn)  # the modal refuses a corrupt journal
        fill(dialog)
        assert dialog.check_labels["journal_clean"].text().startswith("✗")
        assert not dialog.confirm_btn.isEnabled()
        dialog.reject()
        assert (backend.bench_flash_dir / JOURNAL_NAME).read_text("utf-8") == "{broken"
    finally:
        settle(window)
        window.close()


def test_a_stale_journal_is_discarded_only_after_confirmation(
    qt_app: QApplication, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    FlashJournal.begin(
        backend.bench_flash_dir,
        transaction_id=uuid.uuid4().hex,
        target_id=SIMULATED_BENCH_TARGET.target_id,
        image_sha256=hashlib.sha256(build_demo_rom()).hexdigest(),
        image_name=DEMO_ROM_NAME,
        simulated=True,
        attestation=simulated_attestation(),
        authorization_nonce=uuid.uuid4().hex,
        identity=EcuIdentity({0xF191: "SIM-HW-1.0", 0xF18C: "SIM-SERIAL-42"}),
        unverified_checksum_override=False,
    )
    window = open_window(qt_app, backend)
    try:
        panel = panel_of(window)
        assert panel.journal is not None and panel.journal.status is BenchJournalStatus.STALE
        assert window.recovery_banner.isHidden()  # stale is not an emergency
        assert not panel.discard_btn.isHidden() and panel.discard_btn.isEnabled()

        monkeypatch.setattr(panel, "_confirm_discard", lambda: False)
        panel.discard_btn.click()
        settle(window)
        assert (backend.bench_flash_dir / JOURNAL_NAME).exists()

        monkeypatch.setattr(panel, "_confirm_discard", lambda: True)
        panel.discard_btn.click()
        settle(window)
        wait_until(lambda: panel.running is None)
        settle(window)
        assert panel.journal is not None and panel.journal.status is BenchJournalStatus.CLEAN
        assert not (backend.bench_flash_dir / JOURNAL_NAME).exists()
        assert panel.result_label.text().startswith(COPY["en"]["bflash_discarded"])
        archived = list((backend.bench_flash_dir / "journal-archive").glob("*.json"))
        assert [json.loads(p.read_text("utf-8"))["state"] for p in archived] == ["aborted"]
    finally:
        settle(window)
        window.close()


# --------------------------------------------------------------------------- language


def test_the_panel_and_the_modal_follow_the_language(window: MainWindow) -> None:
    panel = panel_of(window)
    window.lang_combo.setCurrentIndex(0)  # Persian
    t = COPY["fa"]
    assert panel.title_label.text() == t["bflash_title"]
    assert panel.enable_btn.text() == t["bflash_enable"]
    assert re.search(r"[؀-ۿ]", panel.intro_label.text())
    dialog = open_dialog(window, panel.enable_btn)
    assert dialog.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert dialog.windowTitle() == t["bflash_modal_title"]["flash"]
    assert dialog.phrase_edit.layoutDirection() == Qt.LayoutDirection.LeftToRight
    dialog.reject()
    window.lang_combo.setCurrentIndex(1)
    assert panel.title_label.text() == COPY["en"]["bflash_title"]


def test_the_journal_text_is_translated_in_both_languages(
    window: MainWindow, backend: Backend
) -> None:
    activate(window)
    run_flash(window, "nrc72")
    journal = window.bench_flash_panel.journal
    assert journal is not None and journal.status is BenchJournalStatus.DIRTY
    for lang in ("fa", "en"):
        text = journal_text(lang, journal)  # type: ignore[arg-type]
        fields = COPY[lang]["bflash_journal_fields"]
        for key in ("status", "state", "backup", "backup_sha", "reason", "history"):
            assert fields[key] in text, (lang, key)
