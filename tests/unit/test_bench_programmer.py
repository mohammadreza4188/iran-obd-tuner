"""Bench programmer (Milestone 7c) against the virtual programmable ECU on a fake bus.

Fully deterministic: the programmer, ISO-TP and the simulated ECU share one fake clock and
nothing runs in a thread. Capabilities are minted here with the private token on purpose
(an architecture test keeps that token out of every package module but its minters).
"""

from __future__ import annotations

import hashlib
import uuid
import zlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from iran_obd_tuner.core import bench
from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BackupReceipt,
    BenchAttestation,
    BenchProgrammerPort,
    EcuIdentity,
    FlashAuthorization,
    FlashStage,
    MemoryRegion,
    bench_flash_policy,
)
from iran_obd_tuner.core.exceptions import (
    AdapterTimeoutError,
    BenchTargetNotQualifiedError,
    SafetyGuardError,
)
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.bench_programmer import (
    BENCH_ALLOWED_REQUESTS,
    BENCH_FORBIDDEN_SERVICES,
    BENCH_P2_STAR_CEILING_S,
    BenchNegativeResponseError,
    BenchProgrammer,
    BenchTiming,
    BenchTransferError,
    check_bench_request,
)
from iran_obd_tuner.hardware.uds import UdsNoTrafficError
from iran_obd_tuner.hardware.uds_sim_programmable import (
    DID_BOOT_STATUS,
    FaultPlan,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    default_simulated_image,
)

TARGET = SIMULATED_BENCH_TARGET
REGION = TARGET.regions[0]
SIZE = TARGET.image_size
BLOCK = 256
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
IDENTITY_DIDS = (0xF187, 0xF189, 0xF191, DID_BOOT_STATUS)


def new_image(seed: int = 3) -> bytes:
    return bytes((index * 11 + seed) & 0xFF for index in range(SIZE))


def authorization(image: bytes, **overrides: object) -> FlashAuthorization:
    attest = BenchAttestation(
        ecu_on_bench_out_of_vehicle=True,
        supply_stable_and_current_limited=True,
        wiring_verified=True,
        owner_approval_recorded=True,
        operator="test",
        confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
        attested_at=NOW,
        simulated=True,
    )
    values: dict[str, object] = {
        "policy": bench_flash_policy(attest),
        "attestation": attest,
        "target_id": TARGET.target_id,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "simulated": True,
        "nonce": uuid.uuid4().hex,
        "issued_at": NOW,
        "_token": bench._MINT,
    }
    values.update(overrides)
    return FlashAuthorization(**values)  # type: ignore[arg-type]


def receipt(identity: EcuIdentity, **overrides: object) -> BackupReceipt:
    values: dict[str, object] = {
        "path": "backups/SIMULATED-backup.bin",
        "sha256": hashlib.sha256(default_simulated_image()).hexdigest(),
        "size": SIZE,
        "regions": TARGET.regions,
        "target_id": TARGET.target_id,
        "identity_fingerprint": identity.fingerprint(),
        "simulated": True,
        "created_at": NOW,
        "_token": bench._MINT,
    }
    values.update(overrides)
    return BackupReceipt(**values)  # type: ignore[arg-type]


@pytest.fixture
def rig(fake_can_bus, iso_tp_ecu_responder, fake_clock):
    """Factory: ``rig(ecu=None, **programmer_kwargs)`` -> (programmer, ecu, bus, log)."""

    def build(ecu: VirtualProgrammableEcu | None = None, target=TARGET, **kwargs):
        ecu = ecu or VirtualProgrammableEcu(clock=fake_clock)
        bus = fake_can_bus(iso_tp_ecu_responder(ecu))
        log = TrafficLog()
        programmer = BenchProgrammer(bus, target, traffic_log=log, clock=bus.clock, **kwargs)
        return programmer, ecu, bus, log

    return build


def sent_requests(ecu: VirtualProgrammableEcu, prefix: bytes) -> list[bytes]:
    return [r for r in ecu.requests if r.startswith(prefix)]


def prepared(rig, image: bytes, ecu: VirtualProgrammableEcu | None = None, **kwargs):
    """Programmer in the programming session, unlocked, with identity and backup."""
    programmer, ecu, bus, log = rig(ecu, **kwargs)
    auth = authorization(image)
    identity = programmer.read_identity(IDENTITY_DIDS)
    backup = receipt(identity)
    programmer.enter_programming_session(authorization=auth)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
    return programmer, ecu, bus, log, auth, backup


# --------------------------------------------------------------------------- allowlist


def test_every_two_byte_request_against_the_bench_allowlist() -> None:
    """All 65 536 (SID, sub-function) pairs: exactly the expected ones pass."""
    expected = {(0x10, s) for s in (0x01, 0x02, 0x03)} | {(0x11, 0x01)}
    expected |= {(0x27, 0x01), (0x3E, 0x00), (0x3E, 0x80)}
    expected |= {(0x36, s) for s in range(256)}  # an upload block request: 36 <counter>
    accepted = set()
    for sid in range(256):
        for sub in range(256):
            try:
                check_bench_request(bytes([sid, sub]), TARGET)
            except SafetyGuardError:
                continue
            accepted.add((sid, sub))
    assert accepted == expected


@pytest.mark.parametrize("sid", sorted(BENCH_FORBIDDEN_SERVICES))
def test_forbidden_services_are_refused_by_name(sid: int) -> None:
    with pytest.raises(SafetyGuardError, match=BENCH_FORBIDDEN_SERVICES[sid]):
        check_bench_request(bytes([sid, 0x01, 0x00, 0x00]), TARGET)


ADDR_SIZE = b"\x44" + REGION.start_address.to_bytes(4, "big") + SIZE.to_bytes(4, "big")


@pytest.mark.parametrize(
    ("request_bytes", "allowed"),
    [
        (b"\x22\xf1\x90", True),
        (b"\x22\xf1", False),
        (b"\x22\xf1\x90\xf1\x89", False),  # one DID per request
        (b"\x27\x02\x01\x02\x03\x04", True),
        (b"\x27\x02", False),  # a key needs key bytes
        (b"\x27\x01\x00", False),
        (b"\x27\x03", False),  # another level
        (b"\x31\x01\xff\x00" + ADDR_SIZE, True),
        (b"\x31\x01\xff\x01" + ADDR_SIZE + b"\x00\x00\x00\x00", True),
        (b"\x31\x01\xff\x00" + ADDR_SIZE + b"\x00", False),
        (b"\x31\x01\x12\x34" + ADDR_SIZE, False),  # not a routine of the target
        (b"\x31\x02\xff\x00" + ADDR_SIZE, False),  # stopRoutine
        (b"\x31\x03\xff\x01", False),  # requestRoutineResults
        (b"\x34\x00" + ADDR_SIZE, True),
        (b"\x35\x00" + ADDR_SIZE, True),
        (b"\x34\x11" + ADDR_SIZE, False),  # compression/encryption
        (b"\x34\x00\x24" + bytes(6), False),  # other address/length format
        (b"\x36\x01" + b"\x00" * 256, True),
        (b"\x37", True),
        (b"\x37\x00", False),
        (b"\x10\x02\x00", False),
        (b"\x11\x02", False),  # keyOffOnReset
        (b"\x23\x44" + bytes(8), False),  # ReadMemoryByAddress: not needed, not allowed
        (b"\x19\x02\xff", False),
        (b"", False),
    ],
)
def test_long_requests_against_the_bench_allowlist(request_bytes: bytes, allowed: bool) -> None:
    if allowed:
        check_bench_request(request_bytes, TARGET)
    else:
        with pytest.raises(SafetyGuardError):
            check_bench_request(request_bytes, TARGET)


def test_allowlist_tables_are_consistent() -> None:
    assert not set(BENCH_ALLOWED_REQUESTS) & set(BENCH_FORBIDDEN_SERVICES)
    assert BENCH_ALLOWED_REQUESTS[0x11] == frozenset({0x01})


# --------------------------------------------------------------------------- construction


def test_only_simulated_single_region_targets_on_simulated_buses(fake_can_bus) -> None:
    with pytest.raises(BenchTargetNotQualifiedError):
        BenchProgrammer(fake_can_bus(simulated=False), TARGET)
    half = 32 * 1024
    two_regions = replace(
        TARGET,
        regions=(MemoryRegion(0x8_0000, half, 0), MemoryRegion(0x9_0000, half, half)),
    )
    with pytest.raises(BenchTargetNotQualifiedError, match="multi-region"):
        BenchProgrammer(fake_can_bus(), two_regions)
    programmer = BenchProgrammer(fake_can_bus(), TARGET)
    assert isinstance(programmer, BenchProgrammerPort) and programmer.simulated


def test_timing_bounds() -> None:
    for bad in (
        {"p2_s": 0},
        {"p2_star_s": BENCH_P2_STAR_CEILING_S + 1},
        {"max_block_retries": 3},
        {"max_transfer_retries": 9},
        {"max_block_retries": -1},
    ):
        with pytest.raises(ValueError):
            BenchTiming(**bad)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- capabilities


@pytest.mark.parametrize(
    "call",
    [
        lambda p, a, b: p.enter_programming_session(authorization=a),
        lambda p, a, b: p.unlock(authorization=a, provider=SimulatedXorSeedKey()),
        lambda p, a, b: p.upload_region(REGION, authorization=a),
        lambda p, a, b: p.erase_region(REGION, authorization=a, backup=b),
        lambda p, a, b: p.download_region(REGION, new_image(), authorization=a, backup=b),
        lambda p, a, b: p.check_memory(REGION, 0, authorization=a),
        lambda p, a, b: p.hard_reset(authorization=a),
    ],
)
def test_nothing_is_sent_without_an_authorisation(rig, call) -> None:
    programmer, ecu, bus, _log = rig()
    identity = programmer.read_identity(IDENTITY_DIDS)
    before = len(bus.sent)

    for fake in (None, "authorised", object()):
        with pytest.raises(SafetyGuardError, match="FlashAuthorization"):
            call(programmer, fake, receipt(identity))

    assert len(bus.sent) == before


@pytest.mark.parametrize(
    ("backup_overrides", "message"),
    [
        ({"target_id": "simulated.other"}, "another target"),
        ({"identity_fingerprint": "f" * 64}, "different ECU"),
        (
            {"regions": (MemoryRegion(0x8_0000, 16 * 1024),), "size": 16 * 1024},
            "not covered",
        ),
    ],
)
def test_memory_changes_need_the_matching_backup(rig, backup_overrides, message) -> None:
    image = new_image()
    programmer, ecu, bus, _log, auth, backup = prepared(rig, image)
    wrong = receipt(programmer.read_identity(IDENTITY_DIDS), **backup_overrides)
    before = len(bus.sent)

    for call in (
        lambda: programmer.erase_region(REGION, authorization=auth, backup=wrong),
        lambda: programmer.download_region(REGION, image, authorization=auth, backup=wrong),
    ):
        with pytest.raises(SafetyGuardError, match=message):
            call()
    with pytest.raises(SafetyGuardError, match="BackupReceipt"):
        programmer.erase_region(REGION, authorization=auth, backup=None)  # type: ignore[arg-type]

    assert len(bus.sent) == before
    assert ecu.flash == default_simulated_image()


def test_a_bootloader_read_never_weakens_the_application_binding(rig) -> None:
    """Regression: re-reading the identity inside the programming session (bootloader) must
    not replace the application binding, or a backup of another ECU would be accepted."""
    image = new_image()
    programmer, _ecu, bus, _log, auth, _backup = prepared(rig, image)
    again = programmer.read_identity(IDENTITY_DIDS)
    assert again.dids[DID_BOOT_STATUS] == "BOOTLOADER"
    foreign = receipt(EcuIdentity({0xF187: "SIM-PART-9999", 0xF191: "SIM-HW-1.0"}))
    before = len(bus.sent)

    with pytest.raises(SafetyGuardError, match="different ECU"):
        programmer.erase_region(REGION, authorization=auth, backup=foreign)
    assert len(bus.sent) == before


def test_an_unknown_identity_blocks_memory_changes(rig) -> None:
    class Stranger(VirtualProgrammableEcu):
        def _did_values(self) -> dict[int, bytes]:
            return {0xF187: b"OTHER-PART", 0xF191: b"OTHER-HW", DID_BOOT_STATUS: b"APPLICATION"}

    image = new_image()
    programmer, _ecu, _bus, _log = rig(Stranger())
    auth = authorization(image)
    stranger = programmer.read_identity(IDENTITY_DIDS)
    programmer.enter_programming_session(authorization=auth)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())

    with pytest.raises(SafetyGuardError, match="read the ECU identity"):
        programmer.erase_region(REGION, authorization=auth, backup=receipt(stranger))


def test_memory_changes_need_an_identity_read_on_this_link(rig) -> None:
    image = new_image()
    programmer, ecu, bus, _log = rig()
    auth = authorization(image)
    identity = EcuIdentity({0xF187: "SIM-PART-0001", 0xF191: "SIM-HW-1.0"})
    programmer.enter_programming_session(authorization=auth)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())

    with pytest.raises(SafetyGuardError, match="read the ECU identity"):
        programmer.erase_region(REGION, authorization=auth, backup=receipt(identity))


def test_the_first_authorisation_is_pinned_to_the_link(rig) -> None:
    image = new_image()
    programmer, _ecu, bus, _log, _auth, _backup = prepared(rig, image)
    before = len(bus.sent)

    with pytest.raises(SafetyGuardError, match="another authorisation"):
        programmer.check_memory(REGION, 0, authorization=authorization(image))
    assert len(bus.sent) == before


def test_only_the_authorised_image_can_be_downloaded(rig) -> None:
    image = new_image()
    programmer, ecu, bus, _log, auth, backup = prepared(rig, image)
    programmer.erase_region(REGION, authorization=auth, backup=backup)
    before = len(bus.sent)

    with pytest.raises(SafetyGuardError, match="not the authorised image"):
        programmer.download_region(REGION, new_image(seed=9), authorization=auth, backup=backup)
    with pytest.raises(SafetyGuardError, match="size"):
        programmer.download_region(REGION, image[:-1], authorization=auth, backup=backup)
    assert len(bus.sent) == before


def test_a_bootloader_identity_allows_recovery_writes(rig, fake_clock) -> None:
    """After an interrupted flash only the bootloader answers; the orchestrator binds the
    backup to the journal (7d) - the programmer accepts the bootloader identity."""
    ecu = VirtualProgrammableEcu(clock=fake_clock)
    ecu.handle(b"\x10\x02")
    ecu.handle(b"\x11\x01")  # still valid: back in the application
    image = default_simulated_image()
    programmer, ecu, _bus, _log = rig(ecu)
    auth = authorization(image)
    application = programmer.read_identity(IDENTITY_DIDS)
    backup = receipt(application)
    programmer.enter_programming_session(authorization=auth)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
    programmer.erase_region(REGION, authorization=auth, backup=backup)
    programmer.hard_reset(authorization=auth)  # the ECU boots into its bootloader

    bootloader = programmer.read_identity(IDENTITY_DIDS)
    assert bootloader.dids[DID_BOOT_STATUS] == "BOOTLOADER"
    programmer.enter_programming_session(authorization=auth)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
    programmer.download_region(REGION, image, authorization=auth, backup=backup)

    assert ecu.flash == image


# --------------------------------------------------------------------------- full flow


def test_full_flow_backup_erase_download_verify_reset(rig) -> None:
    image = new_image()
    programmer, ecu, _bus, log = rig()
    assert programmer.check_link(3) == 0.0  # fake clock: instant answers
    identity = programmer.read_identity(IDENTITY_DIDS)
    assert identity.mismatches(TARGET.identity_patterns) == ()
    auth = authorization(image)

    programmer.enter_programming_session(authorization=auth)
    assert programmer.p2_star_s == 5.0  # announced by the simulator (500 x 10 ms)
    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
    backup_progress: list = []
    backup_data = programmer.upload_region(
        REGION, authorization=auth, progress=backup_progress.append
    )
    assert backup_data == default_simulated_image()
    backup = receipt(identity, sha256=hashlib.sha256(backup_data).hexdigest())
    programmer.erase_region(REGION, authorization=auth, backup=backup)
    progress: list = []
    programmer.download_region(
        REGION, image, authorization=auth, backup=backup, progress=progress.append
    )
    assert programmer.check_memory(REGION, zlib.crc32(image), authorization=auth) is True
    programmer.hard_reset(authorization=auth)
    after = programmer.read_identity(IDENTITY_DIDS)

    assert ecu.flash == image
    assert after.dids[0xF189] == f"SIM-SW-{zlib.crc32(image):08X}"
    assert after.dids[DID_BOOT_STATUS] == "APPLICATION"
    assert len(progress) == SIZE // BLOCK and progress[-1].fraction == 1.0
    assert {p.stage for p in progress} == {FlashStage.PROGRAMMING}
    assert {p.stage for p in backup_progress} == {FlashStage.BACKUP}
    sequences = [p.block_sequence for p in progress]
    assert sequences[254:256] == [0xFF, 0x00]  # wrap-around after 255 blocks
    texts = [entry.text for entry in log.snapshot()]
    assert "36 FF [256 data bytes]" in texts and "36 00 [256 data bytes]" in texts


def test_the_traffic_log_carries_headers_only_and_no_key(rig) -> None:
    image = new_image()
    programmer, _ecu, _bus, log, auth, backup = prepared(rig, image)
    programmer.upload_region(REGION, authorization=auth)
    programmer.erase_region(REGION, authorization=auth, backup=backup)
    programmer.download_region(REGION, image, authorization=auth, backup=backup)

    entries = log.snapshot()
    assert entries and all(entry.simulated for entry in entries)
    assert max(len(entry.text) for entry in entries) < 60
    assert "27 02 [key redacted]" in [e.text for e in entries if e.direction is Direction.TX]
    joined = " ".join(entry.text for entry in entries).replace(" ", "")
    assert image[1000:1016].hex().upper() not in joined  # no payload ever reaches the log
    assert default_simulated_image()[1000:1016].hex().upper() not in joined


# --------------------------------------------------------------------------- security access


def test_unlock_refuses_foreign_providers_before_sending(rig) -> None:
    class RealProvider:
        provider_id = "simulated-xor"
        simulated = False

        def compute_key(self, level: int, seed: bytes) -> bytes:
            return seed

    class OtherSimulated(RealProvider):
        provider_id = "simulated-other"
        simulated = True

    image = new_image()
    programmer, ecu, _bus, _log = rig()
    auth = authorization(image)
    programmer.enter_programming_session(authorization=auth)

    with pytest.raises(BenchTargetNotQualifiedError):
        programmer.unlock(authorization=auth, provider=RealProvider())
    with pytest.raises(SafetyGuardError, match="not the target"):
        programmer.unlock(authorization=auth, provider=OtherSimulated())
    assert sent_requests(ecu, b"\x27") == []


def test_a_refused_key_is_never_retried(rig, fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(bad_key=True), clock=fake_clock)
    image = new_image()
    programmer, ecu, _bus, _log = rig(ecu)
    auth = authorization(image)
    programmer.enter_programming_session(authorization=auth)

    with pytest.raises(BenchNegativeResponseError) as refused:
        programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())

    assert refused.value.nrc == 0x35
    assert len(sent_requests(ecu, b"\x27\x02")) == 1  # exactly one key attempt
    assert ecu.failed_key_attempts == 1


def test_an_already_unlocked_ecu_gets_no_key(rig) -> None:
    image = new_image()
    programmer, ecu, _bus, _log, auth, _backup = prepared(rig, image)
    keys_before = len(sent_requests(ecu, b"\x27\x02"))

    programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())  # all-zero seed

    assert len(sent_requests(ecu, b"\x27\x02")) == keys_before


# --------------------------------------------------------------------------- retries and aborts


class LosesAnswers(VirtualProgrammableEcu):
    """For each listed block sequence counter, the first download attempt goes wrong once.

    ``busy=False``: the block is programmed but its answer is lost (the retry is a repeat
    the ECU confirms). ``busy=True``: ``0x21`` busyRepeatRequest, nothing programmed.
    """

    def __init__(self, sequences: set[int], *, busy: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sequences, self.busy, self.done = sequences, busy, set()

    def handle(self, request: bytes) -> list[bytes]:
        is_download_block = request[:1] == b"\x36" and len(request) > 2
        sequence = request[1] if is_download_block else None
        if sequence in self.sequences and sequence not in self.done:
            self.done.add(sequence)
            if self.busy:
                self.requests.append(request)
                return [b"\x7f\x36\x21"]  # busy: not processed
            super().handle(request)
            return []  # processed, answer lost
        return super().handle(request)


@pytest.mark.parametrize("busy", [False, True])
def test_a_lost_answer_or_busy_is_retried_with_the_same_counter(rig, fake_clock, busy) -> None:
    image = new_image()
    ecu = LosesAnswers({3}, busy=busy, clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu)
    programmer.erase_region(REGION, authorization=auth, backup=backup)

    programmer.download_region(REGION, image, authorization=auth, backup=backup)

    assert ecu.flash == image
    assert len(sent_requests(ecu, b"\x36\x03")) == 2  # once, then the retry


def test_a_block_is_retried_at_most_twice(rig, fake_clock) -> None:
    image = new_image()
    ecu = VirtualProgrammableEcu(faults=FaultPlan(drop_link_at_block=3), clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu)
    programmer.erase_region(REGION, authorization=auth, backup=backup)

    with pytest.raises(BenchTransferError, match="retry budget"):
        programmer.download_region(REGION, image, authorization=auth, backup=backup)

    assert len(sent_requests(ecu, b"\x36\x03")) == 3  # 1 + 2 retries
    assert not sent_requests(ecu, b"\x36\x04")


def test_a_transfer_is_retried_at_most_eight_times(rig, fake_clock) -> None:
    image = new_image()
    ecu = LosesAnswers(set(range(1, 12)), clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu)
    programmer.erase_region(REGION, authorization=auth, backup=backup)

    with pytest.raises(BenchTransferError, match="retry budget"):
        programmer.download_region(REGION, image, authorization=auth, backup=backup)

    retried_blocks = [s for s in range(1, 12) if len(sent_requests(ecu, bytes([0x36, s]))) == 2]
    assert retried_blocks == list(range(1, 9))  # 8 retries, the 9th lost answer aborts
    assert len(sent_requests(ecu, b"\x36\x09")) == 1


@pytest.mark.parametrize("nrc", [0x70, 0x71, 0x72, 0x73])
def test_transfer_refusals_abort_at_once(rig, fake_clock, nrc) -> None:
    image = new_image()
    ecu = VirtualProgrammableEcu(faults=FaultPlan(nrc_at_block=(2, nrc)), clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu)
    programmer.erase_region(REGION, authorization=auth, backup=backup)

    with pytest.raises(BenchNegativeResponseError) as refused:
        programmer.download_region(REGION, image, authorization=auth, backup=backup)

    assert refused.value.nrc == nrc
    assert len(sent_requests(ecu, b"\x36\x02")) == 1  # never retried
    assert sent_requests(ecu, b"\x37") == []  # no transfer exit after an abort


def test_power_loss_mid_transfer_aborts_after_the_bounded_retries(rig, fake_clock) -> None:
    image = new_image()
    ecu = VirtualProgrammableEcu(faults=FaultPlan(power_loss_at_block=4), clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu)
    programmer.erase_region(REGION, authorization=auth, backup=backup)
    started = fake_clock()

    with pytest.raises(BenchTransferError):
        programmer.download_region(REGION, image, authorization=auth, backup=backup)

    assert not ecu.powered
    assert fake_clock() - started <= 3 * BenchTiming().p2_s + 0.01  # three bounded waits


def test_a_link_that_never_answered_raises_no_traffic(rig, fake_clock) -> None:
    ecu = VirtualProgrammableEcu(clock=fake_clock)
    ecu.powered = False
    programmer, ecu, bus, _log = rig(ecu)

    with pytest.raises(UdsNoTrafficError):
        programmer.check_link(3)

    assert len(bus.sent) == 1  # one TesterPresent, nothing retried


def test_an_upload_block_with_a_lost_answer_is_retried(rig, fake_clock) -> None:
    class LosesUploadAnswer(VirtualProgrammableEcu):
        lost = False

        def handle(self, request: bytes) -> list[bytes]:
            answers = super().handle(request)
            if request == b"\x36\x02" and not self.lost:
                self.lost = True
                return []
            return answers

    image = new_image()
    ecu = LosesUploadAnswer(clock=fake_clock)
    programmer, ecu, _bus, _log, auth, _backup = prepared(rig, image, ecu)

    assert programmer.upload_region(REGION, authorization=auth) == default_simulated_image()
    assert len(sent_requests(ecu, b"\x36\x02")) == 2


# --------------------------------------------------------------------------- keepalive, timing


def test_keepalive_only_outside_the_default_session(rig, fake_clock) -> None:
    image = new_image()
    programmer, ecu, _bus, _log = rig()
    fake_clock.advance(5)
    programmer.read_identity([0xF191])
    assert sent_requests(ecu, b"\x3e\x80") == []  # default session: no keepalive

    programmer.enter_programming_session(authorization=authorization(image))
    fake_clock.advance(1.5)
    programmer.read_identity([0xF191])
    assert sent_requests(ecu, b"\x3e\x80") == []  # not idle long enough
    fake_clock.advance(2.5)
    programmer.read_identity([0xF191])
    assert len(sent_requests(ecu, b"\x3e\x80")) == 1
    position = ecu.requests.index(b"\x3e\x80")
    assert ecu.requests[position + 1] == b"\x22\xf1\x91"  # right before the request


def test_an_announced_p2_star_is_capped(rig, fake_clock) -> None:
    class Slow(VirtualProgrammableEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request == b"\x10\x02":
                super().handle(request)
                return [b"\x50\x02\x00\x32\xff\xff"]  # P2* 655.35 s
            return super().handle(request)

    programmer, _ecu, _bus, _log = rig(Slow(clock=fake_clock))
    programmer.enter_programming_session(authorization=authorization(new_image()))

    assert programmer.p2_star_s == BENCH_P2_STAR_CEILING_S


def test_an_erase_that_stays_pending_ends_at_the_target_ceiling(rig, fake_clock) -> None:
    class NeverDone(VirtualProgrammableEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request[:4] == b"\x31\x01\xff\x00":
                self.requests.append(request)
                return [b"\x7f\x31\x78"]  # pending, then silence
            return super().handle(request)

    image = new_image()
    target = replace(TARGET, max_erase_s=3.0)
    ecu = NeverDone(clock=fake_clock)
    programmer, ecu, _bus, _log, auth, backup = prepared(rig, image, ecu, target=target)
    started = fake_clock()

    with pytest.raises(AdapterTimeoutError):
        programmer.erase_region(REGION, authorization=auth, backup=backup)

    assert fake_clock() - started <= 3.0 + 0.01


def test_leaving_the_session_needs_no_capability_and_reset_forgets_the_identity(rig) -> None:
    image = new_image()
    programmer, _ecu, _bus, _log, auth, backup = prepared(rig, image)

    programmer.return_to_default_session()
    assert programmer.session == 0x01
    programmer.enter_programming_session(authorization=auth)
    programmer.hard_reset(authorization=auth)

    with pytest.raises(SafetyGuardError, match="read the ECU identity"):
        programmer.erase_region(REGION, authorization=auth, backup=backup)
