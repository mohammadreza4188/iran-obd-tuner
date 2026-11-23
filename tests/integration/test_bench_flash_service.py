"""Milestone 7f service additions: machine-check preflights, cancellation, the erase report."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.app.bench_flash import (  # noqa: E402
    CANCELLABLE_STAGES,
    BenchFlashCancelled,
    BenchFlashService,
)
from iran_obd_tuner.core.bench import (  # noqa: E402
    ERASE_PROGRESS_MESSAGE,
    FlashProgress,
    FlashStage,
    JournalState,
)
from iran_obd_tuner.core.bench_reports import (  # noqa: E402
    BenchCheckKey,
    BenchJournalStatus,
    SimulatedFault,
)
from iran_obd_tuner.tuning.flash_journal import JOURNAL_NAME  # noqa: E402

K = BenchCheckKey


@pytest.fixture
def service() -> BenchFlashService:
    return BenchFlashService()


def test_the_flash_preflight_passes_on_a_clean_bench(
    service: BenchFlashService, tmp_path: Path
) -> None:
    report = service.preflight_flash(tmp_path)

    assert report.ok and report.mode == "flash" and report.image_simulated
    assert [c.key for c in report.checks] == [
        K.JOURNAL_CLEAN,
        K.IMAGE_VALID,
        K.CHECKSUMS_VALID,
        K.BACKUP_DIR_WRITABLE,
        K.FREE_SPACE,
        K.TARGET_SIMULATED,
    ]
    assert list(tmp_path.iterdir()) == []  # read-only: nothing was written


def test_the_flash_preflight_reports_a_blocking_journal_and_a_refused_image(
    service: BenchFlashService, tmp_path: Path
) -> None:
    (tmp_path / JOURNAL_NAME).write_text("{broken", encoding="utf-8")
    small = tmp_path / "small.bin"
    small.write_bytes(b"\xff" * 32 * 1024)

    report = service.preflight_flash(tmp_path, rom_path=small)

    assert not report.ok
    journal = report.check(K.JOURNAL_CLEAN)
    image = report.check(K.IMAGE_VALID)
    assert journal is not None and not journal.ok and "corrupt" in journal.detail
    assert image is not None and not image.ok and "65536" in image.detail
    assert report.check(K.FREE_SPACE) is None  # the plan was refused, nothing more to check


def test_the_override_is_visible_in_the_preflight(
    service: BenchFlashService, tmp_path: Path
) -> None:
    from iran_obd_tuner.tuning.demo import build_demo_rom

    rom = tmp_path / "demo.bin"
    rom.write_bytes(build_demo_rom())

    refused = service.preflight_flash(tmp_path, rom_path=rom)
    allowed = service.preflight_flash(
        tmp_path, rom_path=rom, allow_unverified_checksum_in_simulation=True
    )

    assert not refused.ok and "not_configured" in refused.check(K.IMAGE_VALID).detail  # type: ignore[union-attr]
    checksums = allowed.check(K.CHECKSUMS_VALID)
    assert allowed.ok and checksums is not None and checksums.ok and checksums.override


def test_the_recovery_preflight_reverifies_the_backup(
    service: BenchFlashService, tmp_path: Path
) -> None:
    assert not service.preflight_recovery(tmp_path).ok  # clean: nothing to recover
    service.run_simulated_flash(tmp_path, fault=SimulatedFault.parse("power-loss-at-block=5"))

    report = service.preflight_recovery(tmp_path)
    assert report.ok and [c.key for c in report.checks] == [
        K.JOURNAL_RECOVERABLE,
        K.BACKUP_VERIFIED,
        K.TARGET_SIMULATED,
    ]
    backup = Path(service.assess_journal(tmp_path).backup_path)  # type: ignore[arg-type]
    os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
    backup.write_bytes(b"\x00" * backup.stat().st_size)  # tampered after the receipt

    tampered = service.preflight_recovery(tmp_path)
    verified = tampered.check(K.BACKUP_VERIFIED)
    assert not tampered.ok and verified is not None and "changed on disk" in verified.detail


def test_the_erase_report_marks_the_point_of_no_return(
    service: BenchFlashService, tmp_path: Path
) -> None:
    reports: list[FlashProgress] = []

    outcome = service.run_simulated_flash(tmp_path, progress=reports.append)

    assert outcome.ok
    erases = [i for i, r in enumerate(reports) if r.message == ERASE_PROGRESS_MESSAGE]
    assert len(erases) == 1
    before, after = reports[: erases[0]], reports[erases[0] + 1 :]
    assert {r.stage for r in before} <= CANCELLABLE_STAGES
    assert reports[erases[0]].stage is FlashStage.PROGRAMMING
    assert after and all(r.stage not in CANCELLABLE_STAGES for r in after)


def test_a_cancel_during_the_backup_aborts_with_the_ecu_untouched(
    service: BenchFlashService, tmp_path: Path
) -> None:
    def cancel_in_backup(report: FlashProgress) -> None:
        if report.stage is FlashStage.BACKUP:
            flag[0] = True

    flag = [False]
    report = service.run_simulated_flash(
        tmp_path, progress=cancel_in_backup, cancel=lambda: flag[0]
    )

    assert report.state is JournalState.ABORTED and report.ecu_untouched
    assert "cancelled by the operator" in (report.error or "")
    assert service.assess_journal(tmp_path).status is BenchJournalStatus.CLEAN


def test_a_cancel_in_the_preflight_raises_before_anything_is_journaled(
    service: BenchFlashService, tmp_path: Path
) -> None:
    with pytest.raises(BenchFlashCancelled):
        service.run_simulated_flash(tmp_path, cancel=lambda: True)
    assert service.assess_journal(tmp_path).status is BenchJournalStatus.CLEAN
    assert not (tmp_path / "journal-archive").exists()


def test_a_cancel_after_the_erase_is_ignored(service: BenchFlashService, tmp_path: Path) -> None:
    flag = [False]

    def cancel_once_erasing(report: FlashProgress) -> None:
        if report.message == ERASE_PROGRESS_MESSAGE:
            flag[0] = True  # too late: a half-written ECU is never abandoned

    report = service.run_simulated_flash(
        tmp_path, progress=cancel_once_erasing, cancel=lambda: flag[0]
    )

    assert report.state is JournalState.COMPLETED
