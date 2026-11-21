"""Flash plan validation and the flash gate (Milestone 7d)."""

from __future__ import annotations

import hashlib
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BenchAttestation,
    FlashAuthorization,
    MemoryRegion,
    bench_flash_policy,
)
from iran_obd_tuner.core.calibration import ChecksumStatus
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, SafetyGuardError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy
from iran_obd_tuner.tuning.demo import load_demo
from iran_obd_tuner.tuning.flash_gate import (
    BACKUP_SPACE_MARGIN,
    authorize_bench_flash,
    authorize_bench_recovery,
    machine_checks,
)
from iran_obd_tuner.tuning.flash_plan import FlashPlanError, build_flash_plan
from iran_obd_tuner.tuning.interfaces import BenchMachineChecks, BenchSafetyChecklist
from iran_obd_tuner.tuning.rom_loader import load_rom, rom_from_bytes

TARGET = SIMULATED_BENCH_TARGET
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def attestation(**overrides: object) -> BenchAttestation:
    values: dict[str, object] = {
        "ecu_on_bench_out_of_vehicle": True,
        "supply_stable_and_current_limited": True,
        "wiring_verified": True,
        "owner_approval_recorded": True,
        "operator": "bench operator",
        "confirmation_phrase": BENCH_CONFIRMATION_PHRASE,
        "attested_at": NOW,
        "simulated": True,
    }
    values.update(overrides)
    return BenchAttestation(**values)  # type: ignore[arg-type]


@pytest.fixture
def demo():
    rom, definitions = load_demo()
    return rom, definitions


@pytest.fixture
def plan(demo):
    rom, definitions = demo
    return build_flash_plan(rom, TARGET, checksum_blocks=definitions.checksums)


# --------------------------------------------------------------------------- plan


def test_the_simulated_demo_is_a_valid_plan(plan, demo) -> None:
    rom, _definitions = demo

    assert plan.image == rom.data and plan.image_sha256 == hashlib.sha256(rom.data).hexdigest()
    assert plan.checksums_valid and not plan.unverified_checksum_override
    assert plan.simulated and plan.image_simulated
    assert plan.region_data(TARGET.regions[0]) == rom.data
    assert len(plan.checksums) == 2
    with pytest.raises(ValueError, match="not one of the target's regions"):
        plan.region_data(MemoryRegion(0x9_0000, 16 * 1024))


def test_a_wrong_size_is_refused(demo) -> None:
    rom, definitions = demo
    small = rom_from_bytes(rom.data[: 32 * 1024], name="half.bin")

    with pytest.raises(FlashPlanError, match="takes exactly 65536"):
        build_flash_plan(small, TARGET, checksum_blocks=())


def test_an_invalid_checksum_is_refused_and_overridable_only_in_simulation(demo) -> None:
    rom, definitions = demo
    data = bytearray(rom.data)
    data[0x2000] ^= 0xFF  # inside a checksummed range
    broken = rom_from_bytes(bytes(data), name="broken.bin")

    with pytest.raises(FlashPlanError, match="not all valid"):
        build_flash_plan(broken, TARGET, checksum_blocks=definitions.checksums)
    overridden = build_flash_plan(
        broken,
        TARGET,
        checksum_blocks=definitions.checksums,
        allow_unverified_checksum_in_simulation=True,
    )
    assert overridden.unverified_checksum_override and not overridden.checksums_valid
    assert {r.status for r in overridden.checksums} >= {ChecksumStatus.INVALID}


def test_no_configured_checksum_is_refused(demo) -> None:
    rom, _definitions = demo

    with pytest.raises(FlashPlanError, match="not_configured"):
        build_flash_plan(rom, TARGET, checksum_blocks=())


def test_the_override_needs_a_simulated_target(demo) -> None:
    rom, _definitions = demo
    # A real target cannot be constructed in this build; a stand-in proves the check exists.
    real_like = SimpleNamespace(
        target_id="real.ecu",
        image_size=TARGET.image_size,
        regions=TARGET.regions,
        simulated=False,
    )

    with pytest.raises(FlashPlanError, match="only be overridden for a simulated target"):
        build_flash_plan(
            rom,
            real_like,  # type: ignore[arg-type]
            checksum_blocks=(),
            allow_unverified_checksum_in_simulation=True,
        )


def test_tampered_bytes_and_a_changed_source_file_are_refused(demo, tmp_path: Path) -> None:
    rom, definitions = demo
    forged = type(rom)(
        data=rom.data, name=rom.name, source_path=None, sha256="0" * 64, crc32=rom.crc32
    )
    with pytest.raises(FlashPlanError, match="SHA-256"):
        build_flash_plan(forged, TARGET, checksum_blocks=definitions.checksums)

    path = tmp_path / "image.bin"
    path.write_bytes(rom.data)
    loaded = load_rom(path)
    path.write_bytes(rom.data[:-1] + b"\x00")  # changed after loading
    with pytest.raises(FlashPlanError, match="changed on disk"):
        build_flash_plan(loaded, TARGET, checksum_blocks=definitions.checksums)


# --------------------------------------------------------------------------- gate


def test_a_complete_session_is_authorised_and_bound(plan, tmp_path: Path) -> None:
    attest = attestation()

    auth = authorize_bench_flash(
        bench_flash_policy(attest),
        attest,
        plan,
        backup_dir=tmp_path / "backups",
        link_simulated=True,
        now=lambda: NOW,
    )

    assert isinstance(auth, FlashAuthorization)
    assert auth.image_sha256 == plan.image_sha256 and auth.target_id == TARGET.target_id
    assert auth.simulated and auth.issued_at == NOW and len(auth.nonce) == 32
    auth.check_for(target_id=TARGET.target_id, image_sha256=plan.image_sha256, simulated=True)


def test_the_gate_refuses_without_the_bench_flash_policy(plan, tmp_path: Path) -> None:
    for policy in (
        DEFAULT_POLICY,
        SafetyPolicy(allowed=frozenset({OperationClass.CALIBRATION_WRITE}), bench_mode=True),
    ):
        with pytest.raises(SafetyGuardError, match="bench_flash"):
            authorize_bench_flash(
                policy, attestation(), plan, backup_dir=tmp_path, link_simulated=True
            )


def test_the_gate_refuses_an_incomplete_attestation(plan, tmp_path: Path) -> None:
    policy = bench_flash_policy(attestation())

    with pytest.raises(SafetyGuardError, match="wiring_verified"):
        authorize_bench_flash(
            policy,
            attestation(wiring_verified=False),
            plan,
            backup_dir=tmp_path,
            link_simulated=True,
        )


@pytest.mark.parametrize("which", ["attestation", "link"])
def test_the_gate_refuses_anything_real(plan, tmp_path: Path, which: str) -> None:
    attest = attestation(simulated=which != "attestation")
    with pytest.raises(BenchTargetNotQualifiedError):
        authorize_bench_flash(
            bench_flash_policy(attest),
            attest,
            plan,
            backup_dir=tmp_path,
            link_simulated=which != "link",
        )


def test_machine_checks_need_a_writable_directory_with_room(plan, tmp_path: Path) -> None:
    attest = attestation()
    policy = bench_flash_policy(attest)
    a_file = tmp_path / "not-a-directory"
    a_file.write_bytes(b"x")

    with pytest.raises(SafetyGuardError, match="not writable"):
        authorize_bench_flash(policy, attest, plan, backup_dir=a_file, link_simulated=True)
    with pytest.raises(SafetyGuardError, match="not enough free space"):
        authorize_bench_flash(
            policy,
            attest,
            plan,
            backup_dir=tmp_path,
            link_simulated=True,
            disk_usage=lambda _path: (10**9, 10**9, 1000),
        )
    checks = machine_checks(plan, tmp_path / "new" / "backups")  # created later by the vault
    assert checks.backup_dir_writable and checks.problems() == ()
    assert checks.required_bytes == 2 * TARGET.image_size + BACKUP_SPACE_MARGIN


def test_a_recovery_is_authorised_for_the_backup_image_only(tmp_path: Path) -> None:
    attest = attestation()
    backup_sha = "c" * 64

    auth = authorize_bench_recovery(
        bench_flash_policy(attest), attest, TARGET, backup_sha, link_simulated=True
    )

    auth.check_for(target_id=TARGET.target_id, image_sha256=backup_sha, simulated=True)
    with pytest.raises(SafetyGuardError, match="different image"):
        auth.check_for(target_id=TARGET.target_id, image_sha256="d" * 64, simulated=True)


# --------------------------------------------------------------------------- checklist split


def test_the_classic_checklist_is_composed_from_attestation_and_machine_checks() -> None:
    names = {f.name for f in fields(BenchSafetyChecklist)}
    assert set(BenchSafetyChecklist.HUMAN_ITEMS) | set(BenchSafetyChecklist.MACHINE_ITEMS) == names
    assert not set(BenchSafetyChecklist.HUMAN_ITEMS) & set(BenchSafetyChecklist.MACHINE_ITEMS)
    machine = BenchMachineChecks(
        backup_dir_writable=True,
        free_bytes=10**9,
        required_bytes=1,
        image_fits_target=True,
        checksums_valid=True,
        target_simulated=True,
        backup_created=True,
        backup_verified=True,
    )

    full = BenchSafetyChecklist.compose(attestation(), machine)
    partial = BenchSafetyChecklist.compose(
        attestation(supply_stable_and_current_limited=False), machine
    )

    assert full.is_satisfied
    assert partial.unmet() == ("supply_voltage_stable",)
    before_backup = BenchSafetyChecklist.compose(
        attestation(), BenchMachineChecks(True, 10**9, 1, True, True, True)
    )
    assert set(before_backup.unmet()) == {"stock_backup_created", "backup_read_back_and_verified"}
