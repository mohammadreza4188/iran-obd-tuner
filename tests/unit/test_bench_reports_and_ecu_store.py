"""Milestone 7e: shared bench reports, fault parsing, simulator persistence (no bus needed)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from iran_obd_tuner.app.bench_flash import (
    ECU_STATE_NAME,
    BenchFlashService,
    SimulatedBenchStateError,
    SimulatedEcuStore,
    simulated_attestation,
)
from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET, JournalState
from iran_obd_tuner.core.bench_reports import (
    BenchFlashReport,
    BenchJournalStatus,
    SimulatedFault,
    SimulatedFaultKind,
)
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, ConfigurationError
from iran_obd_tuner.hardware.uds_sim_programmable import (
    ProgrammableEcuConfig,
    SimulatedEcuState,
    VirtualProgrammableEcu,
    default_simulated_image,
)
from iran_obd_tuner.tuning.flash_journal import AssessmentStatus

# --------------------------------------------------------------------------- faults


@pytest.mark.parametrize(
    ("text", "kind", "block"),
    [
        ("drop-at-block=3", SimulatedFaultKind.DROP_AT_BLOCK, 3),
        ("nrc72-at-block=1", SimulatedFaultKind.NRC72_AT_BLOCK, 1),
        ("POWER-LOSS-AT-BLOCK=64", SimulatedFaultKind.POWER_LOSS_AT_BLOCK, 64),
        ("bad-key", SimulatedFaultKind.BAD_KEY, None),
        (" corrupt-readback ", SimulatedFaultKind.CORRUPT_READBACK, None),
    ],
)
def test_faults_parse(text: str, kind: SimulatedFaultKind, block: int | None) -> None:
    fault = SimulatedFault.parse(text)
    assert (fault.kind, fault.block) == (kind, block)
    assert SimulatedFault.parse(fault.describe()) == fault


@pytest.mark.parametrize(
    "text",
    [
        "",
        "explode",
        "drop-at-block",
        "drop-at-block=",
        "drop-at-block=0",
        "drop-at-block=-1",
        "drop-at-block=x",
        "bad-key=2",
        "power-loss-at-block=3=4",
    ],
)
def test_bad_faults_are_refused(text: str) -> None:
    with pytest.raises(ValueError):
        SimulatedFault.parse(text)


def test_a_block_beyond_the_transfer_is_refused_before_anything_starts(tmp_path: Path) -> None:
    service = BenchFlashService()
    assert service.blocks_per_transfer == 64  # 64 KiB in 1 KiB blocks
    with pytest.raises(ConfigurationError, match=r"N = 1\.\.64"):
        service.run_simulated_flash(tmp_path, fault=SimulatedFault.parse("drop-at-block=65"))
    assert list(tmp_path.iterdir()) == []  # nothing was written


# --------------------------------------------------------------------------- reports


def test_the_journal_status_mirrors_the_tuning_assessment() -> None:
    assert [s.value for s in BenchJournalStatus] == [s.value for s in AssessmentStatus]


def test_flash_report_flags() -> None:
    def report(state: JournalState) -> BenchFlashReport:
        return BenchFlashReport("flash", state, "0" * 32, "d", True, "img", "a" * 64)

    assert report(JournalState.COMPLETED).ok and report(JournalState.RECOVERED).ok
    assert report(JournalState.ABORTED).ecu_untouched
    assert report(JournalState.INTERRUPTED).recovery_required
    assert report(JournalState.RECOVERY_FAILED).recovery_required
    data = report(JournalState.INTERRUPTED).as_dict()
    assert data["state"] == "interrupted" and data["recovery_required"] is True
    json.dumps(data)  # JSON-serialisable


def test_the_service_refuses_a_non_simulated_attestation(tmp_path: Path) -> None:
    real = dataclasses.replace(simulated_attestation(), simulated=False)
    with pytest.raises(BenchTargetNotQualifiedError):
        BenchFlashService().run_simulated_flash(tmp_path, attestation=real)


def test_maps_without_a_rom_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="--maps needs --rom"):
        BenchFlashService().run_simulated_flash(tmp_path, maps_path=tmp_path / "x.json")


def test_a_clean_directory_assesses_clean(tmp_path: Path) -> None:
    report = BenchFlashService().assess_journal(tmp_path / "nothing-here")
    assert report.status is BenchJournalStatus.CLEAN and not report.blocks_new_flash
    assert report.as_dict()["history"] == []


# --------------------------------------------------------------------------- simulator state


def test_the_simulator_boots_from_its_persistent_state() -> None:
    ecu = VirtualProgrammableEcu()
    torn = bytearray(default_simulated_image())
    torn[100:200] = b"\xff" * 100
    state = SimulatedEcuState(bytes(torn), None, 2)

    rebooted = VirtualProgrammableEcu(state=state)

    assert rebooted.flash == bytes(torn)
    assert not rebooted.application_valid and rebooted.in_bootloader
    assert rebooted.failed_key_attempts == 2
    assert rebooted.persistent_state() == state
    assert ecu.persistent_state().valid_crc is not None and ecu.application_valid


def test_the_simulator_state_is_validated() -> None:
    with pytest.raises(ValueError):
        SimulatedEcuState(b"", None, -1)
    with pytest.raises(ValueError):
        SimulatedEcuState(b"", 1 << 33, 0)
    with pytest.raises(ValueError, match="image size"):
        VirtualProgrammableEcu(state=SimulatedEcuState(b"\x00" * 10, None, 0))
    image = default_simulated_image()
    with pytest.raises(ValueError, match="not both"):
        VirtualProgrammableEcu(
            ProgrammableEcuConfig(initial_image=image), state=SimulatedEcuState(image, None, 0)
        )


def test_the_ecu_store_round_trips_and_labels_the_file(tmp_path: Path) -> None:
    store = SimulatedEcuStore(tmp_path)
    assert store.load() is None  # factory fresh
    state = SimulatedEcuState(default_simulated_image(), 0x1234, 1)

    store.save(state)
    store.save(state)  # replaces atomically

    assert store.load() == state
    document = json.loads((tmp_path / ECU_STATE_NAME).read_text("utf-8"))
    assert document["simulated"] is True and "SIMULATED" in document["note"]
    assert document["target_id"] == SIMULATED_BENCH_TARGET.target_id
    assert not (tmp_path / (ECU_STATE_NAME + ".tmp")).exists()


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: d.update(flash_sha256="0" * 64),
        lambda d: d.update(simulated=False),
        lambda d: d.update(target_id="other"),
        lambda d: d.update(flash_base64="***"),
        lambda d: d.pop("failed_key_attempts"),
        lambda d: d.update(schema_version=99),
    ],
)
def test_a_damaged_ecu_state_fails_closed(tmp_path: Path, damage: Callable[[dict], object]) -> None:
    store = SimulatedEcuStore(tmp_path)
    store.save(SimulatedEcuState(default_simulated_image(), None, 0))
    path = tmp_path / ECU_STATE_NAME
    document = json.loads(path.read_text("utf-8"))
    damage(document)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SimulatedBenchStateError, match="delete it"):
        store.load()


def test_unreadable_ecu_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ECU_STATE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(SimulatedBenchStateError):
        SimulatedEcuStore(tmp_path).load()
