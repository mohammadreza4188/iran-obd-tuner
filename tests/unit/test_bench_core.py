"""Bench programming contract (Milestone 7a): state machine, targets, policy, capabilities.

Nothing here sends a frame or writes a file: ``core.bench`` only describes bench work. The
tests import the private mint token on purpose - outside ``tests/`` an architecture test
allows exactly one ``tuning`` module per capability to do so.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from iran_obd_tuner.core import bench
from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    DIRTY_JOURNAL_STATES,
    JOURNAL_TRANSITIONS,
    SIMULATED_BENCH_TARGET,
    TERMINAL_JOURNAL_STATES,
    BackupReceipt,
    BenchAttestation,
    BenchProgrammerPort,
    BenchTargetProfile,
    EcuIdentity,
    FlashAuthorization,
    FlashProgress,
    FlashStage,
    JournalState,
    MemoryRegion,
    SeedKeyProvider,
    bench_flash_policy,
    check_journal_transition,
)
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, SafetyGuardError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, require
from iran_obd_tuner.tuning.demo import DEMO_ROM_SIZE

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


def attestation(**overrides: object) -> BenchAttestation:
    values: dict[str, object] = {
        "ecu_on_bench_out_of_vehicle": True,
        "supply_stable_and_current_limited": True,
        "wiring_verified": True,
        "owner_approval_recorded": True,
        "operator": "test operator",
        "confirmation_phrase": BENCH_CONFIRMATION_PHRASE,
        "attested_at": NOW,
        "simulated": True,
    }
    values.update(overrides)
    return BenchAttestation(**values)  # type: ignore[arg-type]


def authorization(**overrides: object) -> FlashAuthorization:
    attest = attestation()
    values: dict[str, object] = {
        "policy": bench_flash_policy(attest),
        "attestation": attest,
        "target_id": SIMULATED_BENCH_TARGET.target_id,
        "image_sha256": SHA_A,
        "simulated": True,
        "nonce": uuid.uuid4().hex,
        "issued_at": NOW,
        "_token": bench._MINT,
    }
    values.update(overrides)
    return FlashAuthorization(**values)  # type: ignore[arg-type]


def receipt(**overrides: object) -> BackupReceipt:
    values: dict[str, object] = {
        "path": "backups/SIMULATED-backup.bin",
        "sha256": SHA_B,
        "size": SIMULATED_BENCH_TARGET.image_size,
        "regions": SIMULATED_BENCH_TARGET.regions,
        "target_id": SIMULATED_BENCH_TARGET.target_id,
        "identity_fingerprint": SHA_A,
        "simulated": True,
        "created_at": NOW,
        "_token": bench._MINT,
    }
    values.update(overrides)
    return BackupReceipt(**values)  # type: ignore[arg-type]


def profile(**overrides: object) -> BenchTargetProfile:
    return replace(SIMULATED_BENCH_TARGET, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- journal states


def test_every_state_has_a_transition_entry_and_targets_are_states() -> None:
    assert set(JOURNAL_TRANSITIONS) == set(JournalState)
    for targets in JOURNAL_TRANSITIONS.values():
        assert targets <= set(JournalState)


def test_the_happy_path_and_the_recovery_path_are_allowed() -> None:
    happy = [
        JournalState.PENDING,
        JournalState.BACKUP_VERIFIED,
        JournalState.ERASING,
        JournalState.TRANSFERRING,
        JournalState.VERIFYING,
        JournalState.COMPLETED,
    ]
    for current, new in zip(happy, happy[1:], strict=False):
        check_journal_transition(current, new)
    recovery = [
        JournalState.TRANSFERRING,
        JournalState.INTERRUPTED,
        JournalState.RECOVERING,
        JournalState.RECOVERY_FAILED,
        JournalState.RECOVERING,
        JournalState.RECOVERED,
    ]
    for current, new in zip(recovery, recovery[1:], strict=False):
        check_journal_transition(current, new)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (JournalState.PENDING, JournalState.ERASING),  # erasing without a verified backup
        (JournalState.PENDING, JournalState.TRANSFERRING),
        (JournalState.BACKUP_VERIFIED, JournalState.TRANSFERRING),  # skipping the erase
        (JournalState.ERASING, JournalState.ABORTED),  # a touched ECU is never "clean"
        (JournalState.TRANSFERRING, JournalState.ABORTED),
        (JournalState.VERIFYING, JournalState.ABORTED),
        (JournalState.INTERRUPTED, JournalState.ERASING),  # recovery, not a fresh flash
        (JournalState.ABORTED, JournalState.ERASING),
        (JournalState.COMPLETED, JournalState.ERASING),
        (JournalState.RECOVERED, JournalState.RECOVERING),
    ],
)
def test_unsafe_transitions_are_refused(current: JournalState, new: JournalState) -> None:
    with pytest.raises(SafetyGuardError, match="not allowed"):
        check_journal_transition(current, new)


def test_dirty_and_terminal_states() -> None:
    assert {JournalState.ABORTED, JournalState.COMPLETED, JournalState.RECOVERED} == set(
        TERMINAL_JOURNAL_STATES
    )
    clean = {JournalState.PENDING, JournalState.BACKUP_VERIFIED} | TERMINAL_JOURNAL_STATES
    assert clean.isdisjoint(DIRTY_JOURNAL_STATES)
    assert clean | DIRTY_JOURNAL_STATES == set(JournalState)
    # every state reachable after the erase started is dirty until recovered or completed
    assert {JournalState.ERASING, JournalState.INTERRUPTED} <= DIRTY_JOURNAL_STATES


def test_four_stages_plus_recovery() -> None:
    assert [stage.value for stage in FlashStage] == [
        "pre_flight",
        "backup",
        "programming",
        "verify",
        "recovery",
    ]


# --------------------------------------------------------------------------- regions, targets


def test_memory_region_geometry_and_validation() -> None:
    a = MemoryRegion(0x1000, 0x100, image_offset=0)
    assert (a.end_address, a.image_end) == (0x1100, 0x100)
    assert a.overlaps(MemoryRegion(0x10FF, 1)) and not a.overlaps(MemoryRegion(0x1100, 1))
    assert a.contains(MemoryRegion(0x1010, 0x10)) and not a.contains(MemoryRegion(0x10F0, 0x20))
    for bad in ({"size": 0}, {"start_address": -1}, {"start_address": 0xFFFF_FFFF, "size": 2}):
        with pytest.raises(ValueError):
            MemoryRegion(**{"start_address": 0, "size": 1, **bad})  # type: ignore[arg-type]


def test_the_simulated_target_matches_the_simulated_demo_rom() -> None:
    target = SIMULATED_BENCH_TARGET
    assert target.simulated is True and "SIMULATED" in target.display_name
    assert target.image_size == DEMO_ROM_SIZE
    assert target.evidence is None  # nothing about it is a fact about a real ECU


def test_a_real_bench_target_cannot_be_constructed_in_this_build() -> None:
    with pytest.raises(BenchTargetNotQualifiedError, match="not simulated") as refused:
        profile(target_id="ikco.some_ecu", simulated=False, evidence="anything")
    assert isinstance(refused.value, SafetyGuardError)


@pytest.mark.parametrize(
    "overrides",
    [
        {"regions": ()},
        {"regions": (MemoryRegion(0x8_0000, 64 * 1024, image_offset=16),)},  # gap in the image
        {"regions": (MemoryRegion(0x8_0100, 16 * 1024),)},  # not sector aligned
        {
            "regions": (
                MemoryRegion(0x8_0000, 32 * 1024, 0),
                MemoryRegion(0x8_4000, 32 * 1024, 32 * 1024),  # overlaps the first
            )
        },
        {"sector_size": 0},
        {"security_level": 0x02},  # even: that is sendKey
        {"erase_routine_id": 0x1_0000},
        {"check_routine_id": 0xFF00},  # same as erase
        {"address_and_length_format": 0x24},
        {"max_erase_s": 0.0},
        {"seed_key_provider_id": " "},
    ],
)
def test_invalid_target_profiles_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        profile(**overrides)


def test_identity_fingerprint_and_pattern_matching() -> None:
    one = EcuIdentity({0xF191: "SIM-HW-1.0", 0xF187: "SIM-PART-0001"})
    same = EcuIdentity({0xF187: "SIM-PART-0001", 0xF191: "SIM-HW-1.0"})
    other = EcuIdentity({0xF187: "SIM-PART-0002", 0xF191: "SIM-HW-1.0"})

    assert one.fingerprint() == same.fingerprint() != other.fingerprint()
    assert one.mismatches(SIMULATED_BENCH_TARGET.identity_patterns) == ()
    problems = EcuIdentity({0xF187: "REAL-123"}).mismatches(
        SIMULATED_BENCH_TARGET.identity_patterns
    )
    assert any("F187" in p and "REAL-123" in p for p in problems)
    assert any("F191" in p and "not read" in p for p in problems)


# --------------------------------------------------------------------------- attestation, policy


def test_a_complete_attestation_grants_bench_flash_and_nothing_else() -> None:
    policy = bench_flash_policy(attestation())

    assert policy.allows(OperationClass.BENCH_FLASH) and policy.allows(OperationClass.READ_ONLY)
    assert not policy.allows(OperationClass.CALIBRATION_WRITE)
    assert not policy.allows(OperationClass.STATE_CHANGING)
    assert policy.bench_mode is True


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"ecu_on_bench_out_of_vehicle": False}, "ecu_on_bench_out_of_vehicle"),
        ({"supply_stable_and_current_limited": False}, "supply_stable_and_current_limited"),
        ({"wiring_verified": False}, "wiring_verified"),
        ({"owner_approval_recorded": False}, "owner_approval_recorded"),
        ({"owner_approval_recorded": 1}, "owner_approval_recorded"),  # truthy is not "yes"
        ({"operator": "  "}, "operator"),
        ({"confirmation_phrase": "bench flash"}, "confirmation_phrase"),
        ({"confirmation_phrase": "BENCH FLASH "}, "confirmation_phrase"),
    ],
)
def test_an_incomplete_attestation_grants_nothing(
    overrides: dict[str, object], missing: str
) -> None:
    incomplete = attestation(**overrides)

    assert missing in incomplete.unmet() and not incomplete.is_complete
    with pytest.raises(SafetyGuardError, match=missing):
        bench_flash_policy(incomplete)


def test_attestation_time_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone aware"):
        attestation(attested_at=datetime(2026, 11, 22, 12, 0))  # noqa: DTZ001


def test_the_default_policy_never_allows_bench_flash() -> None:
    assert not DEFAULT_POLICY.allows(OperationClass.BENCH_FLASH)
    assert OperationClass.BENCH_FLASH.is_destructive
    with pytest.raises(SafetyGuardError, match="bench_flash"):
        require(OperationClass.BENCH_FLASH)


# --------------------------------------------------------------------------- FlashAuthorization


def test_an_authorisation_needs_the_mint_token() -> None:
    with pytest.raises(SafetyGuardError, match="minting module"):
        authorization(_token=object())
    with pytest.raises(TypeError):
        FlashAuthorization(  # type: ignore[call-arg]
            policy=bench_flash_policy(attestation()),
            attestation=attestation(),
            target_id="x",
            image_sha256=SHA_A,
            simulated=True,
            nonce=uuid.uuid4().hex,
            issued_at=NOW,
        )


def test_an_authorisation_revalidates_everything_it_carries() -> None:
    with pytest.raises(SafetyGuardError, match="does not allow"):
        authorization(policy=DEFAULT_POLICY)
    with pytest.raises(SafetyGuardError, match="incomplete"):
        authorization(attestation=attestation(wiring_verified=False))
    with pytest.raises(SafetyGuardError, match="disagree"):
        authorization(simulated=False)
    real_attestation = attestation(simulated=False)
    with pytest.raises(BenchTargetNotQualifiedError):
        authorization(
            attestation=real_attestation,
            policy=bench_flash_policy(real_attestation),
            simulated=False,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        authorization(image_sha256="ABC")
    with pytest.raises(ValueError, match="nonce"):
        authorization(nonce="short")


def test_an_authorisation_is_bound_to_target_image_and_link() -> None:
    auth = authorization()
    target = SIMULATED_BENCH_TARGET.target_id

    auth.check_for(target_id=target, image_sha256=SHA_A, simulated=True)
    with pytest.raises(SafetyGuardError, match="target"):
        auth.check_for(target_id="other", image_sha256=SHA_A, simulated=True)
    with pytest.raises(SafetyGuardError, match="different image"):
        auth.check_for(target_id=target, image_sha256=SHA_B, simulated=True)
    with pytest.raises(SafetyGuardError, match="simulated link"):
        auth.check_for(target_id=target, image_sha256=SHA_A, simulated=False)
    assert "_token" not in repr(auth)


# --------------------------------------------------------------------------- BackupReceipt


def test_a_receipt_needs_the_mint_token_and_consistent_contents() -> None:
    with pytest.raises(SafetyGuardError, match="minting module"):
        receipt(_token=None)
    with pytest.raises(ValueError, match="size"):
        receipt(size=10)
    with pytest.raises(ValueError, match="SHA-256"):
        receipt(sha256="0" * 63)
    with pytest.raises(ValueError, match="region"):
        receipt(regions=(), size=0)


def test_a_receipt_only_covers_what_it_contains() -> None:
    backup = receipt()
    inside = MemoryRegion(0x8_4000, 16 * 1024)
    outside = MemoryRegion(0x9_0000, 16 * 1024)
    straddling = MemoryRegion(0x8_C000, 32 * 1024)

    assert backup.covers(inside) and not backup.covers(outside)
    backup.check_covers([inside, *SIMULATED_BENCH_TARGET.regions])
    for region in (outside, straddling):
        with pytest.raises(SafetyGuardError, match="cannot be restored"):
            backup.check_covers([inside, region])


def test_a_receipt_is_bound_to_target_ecu_and_link() -> None:
    backup = receipt()
    target = SIMULATED_BENCH_TARGET.target_id

    backup.check_for(target_id=target, identity_fingerprint=SHA_A, simulated=True)
    with pytest.raises(SafetyGuardError, match="target"):
        backup.check_for(target_id="other", identity_fingerprint=SHA_A, simulated=True)
    with pytest.raises(SafetyGuardError, match="different ECU"):
        backup.check_for(target_id=target, identity_fingerprint=SHA_B, simulated=True)
    with pytest.raises(SafetyGuardError, match="simulation"):
        backup.check_for(target_id=target, identity_fingerprint=SHA_A, simulated=False)


# --------------------------------------------------------------------------- progress and ports


def test_progress_validation_and_fraction() -> None:
    assert FlashProgress(FlashStage.PRE_FLIGHT).fraction == 1.0
    assert FlashProgress(FlashStage.PROGRAMMING, 512, 2048, block_sequence=4).fraction == 0.25
    with pytest.raises(ValueError):
        FlashProgress(FlashStage.BACKUP, 10, 5)


def test_ports_are_structural_protocols() -> None:
    class Provider:
        provider_id = "test"
        simulated = True

        def compute_key(self, level: int, seed: bytes) -> bytes:
            return seed

    assert isinstance(Provider(), SeedKeyProvider)
    assert not isinstance(object(), BenchProgrammerPort)
