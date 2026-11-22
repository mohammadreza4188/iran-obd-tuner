"""Milestone 7e.1: backups are bound to the physical unit, not to its software version."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iran_obd_tuner.core.bench import (
    SIMULATED_BENCH_TARGET,
    SOFTWARE_IDENTIFICATION_DIDS,
    EcuIdentity,
)
from iran_obd_tuner.tuning.backup_vault import BackupError, BackupVault, receipt_record

TARGET = SIMULATED_BENCH_TARGET
IMAGE = bytes(range(256)) * (TARGET.image_size // 256)
BEFORE = EcuIdentity(
    {0xF187: "SIM-PART-0001", 0xF189: "SIM-SW-AAAA", 0xF191: "SIM-HW-1.0", 0xF18C: "SIM-SERIAL-42"}
)
AFTER_FLASH = EcuIdentity({**BEFORE.dids, 0xF189: "SIM-SW-BBBB"})
SIBLING = EcuIdentity({**BEFORE.dids, 0xF18C: "SIM-SERIAL-99"})


def _now() -> datetime:
    return datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def test_the_unit_fingerprint_ignores_software_and_needs_every_unit_did() -> None:
    assert BEFORE.fingerprint() != AFTER_FLASH.fingerprint()  # the full record differs
    assert TARGET.unit_fingerprint(BEFORE) == TARGET.unit_fingerprint(AFTER_FLASH)  # same unit
    assert TARGET.unit_fingerprint(BEFORE) != TARGET.unit_fingerprint(SIBLING)
    bootloader = EcuIdentity({0xF191: "SIM-HW-1.0"})  # no serial while in the bootloader
    assert TARGET.unit_fingerprint(bootloader) is None
    assert BEFORE.unit_fingerprint(()) is None
    # order and repetition of the DIDs do not matter
    assert BEFORE.unit_fingerprint((0xF18C, 0xF191, 0xF18C)) == TARGET.unit_fingerprint(BEFORE)


def test_the_simulated_target_binds_to_hardware_number_and_serial() -> None:
    assert set(TARGET.unit_identity_dids) == {0xF191, 0xF18C}
    assert not set(TARGET.unit_identity_dids) & SOFTWARE_IDENTIFICATION_DIDS


@pytest.mark.parametrize(
    ("dids", "message"),
    [
        ((), "needs unit identity DIDs"),
        ((0xF191, 0xF191), "unique"),
        ((0xF191, 0x1_0000), "16-bit"),
        ((0xF191, 0xF189), "identifies software"),
        ((0xF180,), "identifies software"),
    ],
)
def test_a_profile_refuses_unusable_unit_dids(dids: tuple[int, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(TARGET, unit_identity_dids=dids)


def test_the_vault_binds_the_receipt_and_manifest_to_the_unit(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=_now)

    receipt = vault.store(
        IMAGE, IMAGE, target=TARGET, identity=BEFORE, regions=TARGET.regions, simulated=True
    )

    assert receipt.unit_fingerprint == TARGET.unit_fingerprint(BEFORE)
    assert receipt.identity_fingerprint == BEFORE.fingerprint()
    manifest_path = next(tmp_path.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["unit_fingerprint"] == receipt.unit_fingerprint
    assert manifest["unit_identity_dids"] == ["F191", "F18C"]
    # the same unit after a flash is accepted, another unit is not
    receipt.check_for(
        target_id=TARGET.target_id,
        unit_fingerprint=TARGET.unit_fingerprint(AFTER_FLASH),
        simulated=True,
    )
    with pytest.raises(Exception, match="different ECU"):
        receipt.check_for(
            target_id=TARGET.target_id,
            unit_fingerprint=TARGET.unit_fingerprint(SIBLING),
            simulated=True,
        )
    assert vault.receipt_from_record(receipt_record(receipt)) == receipt


def test_the_vault_refuses_an_identity_without_the_unit_dids(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=_now)
    no_serial = EcuIdentity({0xF187: "SIM-PART-0001", 0xF191: "SIM-HW-1.0"})

    with pytest.raises(BackupError, match="unit identity DID"):
        vault.store(
            IMAGE, IMAGE, target=TARGET, identity=no_serial, regions=TARGET.regions, simulated=True
        )
    assert list(tmp_path.iterdir()) == []  # nothing was written


def test_a_backup_from_before_the_unit_binding_no_longer_verifies(tmp_path: Path) -> None:
    """Fail closed: a schema 1 manifest (7d) has no unit fingerprint to bind to."""
    vault = BackupVault(tmp_path, now=_now)
    receipt = vault.store(
        IMAGE, IMAGE, target=TARGET, identity=BEFORE, regions=TARGET.regions, simulated=True
    )
    manifest_path = next(tmp_path.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["schema_version"] = 1
    del manifest["unit_fingerprint"]
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError, match="does not match"):
        vault.verify(receipt)
    old_record = receipt_record(receipt)
    del old_record["unit_fingerprint"]
    with pytest.raises(BackupError, match="incomplete backup record"):
        vault.receipt_from_record(old_record)
