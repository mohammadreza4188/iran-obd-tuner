"""Bench flash orchestrator (Milestone 7d) against the virtual programmable ECU.

Deterministic: fake bus, fake clock, no thread. Every capability is obtained through the
real gate (``authorize_bench_flash`` / ``authorize_bench_recovery``) and the real vault.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BenchAttestation,
    FlashProgress,
    FlashStage,
    JournalState,
    bench_flash_policy,
)
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.hardware.bench_programmer import BenchProgrammer
from iran_obd_tuner.hardware.uds import UdsNoTrafficError
from iran_obd_tuner.hardware.uds_sim_programmable import (
    FaultPlan,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    default_simulated_image,
)
from iran_obd_tuner.tuning.backup_vault import BackupError, BackupVault
from iran_obd_tuner.tuning.demo import load_demo
from iran_obd_tuner.tuning.flash_gate import authorize_bench_flash, authorize_bench_recovery
from iran_obd_tuner.tuning.flash_journal import (
    AssessmentStatus,
    FlashJournal,
    JournalInspector,
    JournalLock,
    JournalLockedError,
)
from iran_obd_tuner.tuning.flash_orchestrator import (
    BenchFlashOrchestrator,
    FlashBlockedError,
    FlashOutcome,
)
from iran_obd_tuner.tuning.flash_plan import build_flash_plan

TARGET = SIMULATED_BENCH_TARGET
REGION = TARGET.regions[0]
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
ATTESTATION = BenchAttestation(
    ecu_on_bench_out_of_vehicle=True,
    supply_stable_and_current_limited=True,
    wiring_verified=True,
    owner_approval_recorded=True,
    operator="bench operator",
    confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
    attested_at=NOW,
    simulated=True,
)
POLICY = bench_flash_policy(ATTESTATION)
FACTORY = default_simulated_image()
S = JournalState


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Bench:
    """A journal + vault + plan, and fresh "links" (programmers) to a simulated ECU."""

    def __init__(self, root: Path, fake_can_bus, responder, fake_clock, vault=None) -> None:
        self.root = root
        self._fake_can_bus, self._responder, self.clock = fake_can_bus, responder, fake_clock
        self.vault = vault or BackupVault(root / "backups", now=Clock())
        self.orchestrator = BenchFlashOrchestrator(root / "journal", self.vault, now=Clock())
        rom, definitions = load_demo()
        self.plan = build_flash_plan(rom, TARGET, checksum_blocks=definitions.checksums)

    def ecu(self, **faults: object) -> VirtualProgrammableEcu:
        return VirtualProgrammableEcu(faults=FaultPlan(**faults), clock=self.clock)  # type: ignore[arg-type]

    def port(self, ecu: VirtualProgrammableEcu) -> BenchProgrammer:
        bus = self._fake_can_bus(self._responder(ecu))
        return BenchProgrammer(bus, TARGET, clock=bus.clock)

    def authorization(self, plan=None):
        return authorize_bench_flash(
            POLICY,
            ATTESTATION,
            plan or self.plan,
            backup_dir=self.root / "backups",
            link_simulated=True,
        )

    def recovery_authorization(self):
        assessment = self.orchestrator.assess()
        assert assessment.backup_sha256 is not None
        return authorize_bench_recovery(
            POLICY, ATTESTATION, TARGET, assessment.backup_sha256, link_simulated=True
        )

    def flash(self, ecu, progress=None) -> FlashOutcome:
        return self.orchestrator.flash(
            self.port(ecu),
            self.plan,
            self.authorization(),
            SimulatedXorSeedKey(),
            progress=progress,
        )

    def recover(self, ecu, progress=None) -> FlashOutcome:
        return self.orchestrator.recover(
            self.port(ecu), self.recovery_authorization(), SimulatedXorSeedKey(), progress=progress
        )

    def backups(self) -> list[Path]:
        return sorted((self.root / "backups").glob("*.bin"))


@pytest.fixture
def bench(tmp_path: Path, fake_can_bus, iso_tp_ecu_responder, fake_clock) -> Bench:
    return Bench(tmp_path, fake_can_bus, iso_tp_ecu_responder, fake_clock)


def requests_after(ecu: VirtualProgrammableEcu, start: int, prefix: bytes) -> list[bytes]:
    return [r for r in ecu.requests[start:] if r.startswith(prefix)]


def first_erase_index(ecu: VirtualProgrammableEcu) -> int:
    return next(i for i, r in enumerate(ecu.requests) if r[:4] == b"\x31\x01\xff\x00")


# --------------------------------------------------------------------------- happy path


def test_a_full_flash_runs_the_four_stages_and_archives_the_journal(bench: Bench) -> None:
    ecu = bench.ecu()
    reports: list[FlashProgress] = []

    outcome = bench.flash(ecu, progress=reports.append)

    assert outcome.ok and outcome.state is S.COMPLETED and outcome.simulated
    assert outcome.states == (
        S.PENDING, S.BACKUP_VERIFIED, S.ERASING, S.TRANSFERRING, S.VERIFYING, S.COMPLETED
    )  # fmt: skip
    assert ecu.flash == bench.plan.image and ecu.application_valid
    assert outcome.identity_after is not None
    assert outcome.identity_after.mismatches(TARGET.identity_patterns) == ()
    stages = [r.stage for r in reports]
    assert stages[0] is FlashStage.PRE_FLIGHT
    order = [s for i, s in enumerate(stages) if i == 0 or s is not stages[i - 1]]
    assert order == [
        FlashStage.PRE_FLIGHT,
        FlashStage.BACKUP,
        FlashStage.PROGRAMMING,
        FlashStage.VERIFY,
    ]
    # backup: the factory content, kept once, verified; three uploads = 2 backup + read-back
    assert [p.read_bytes() for p in bench.backups()] == [FACTORY]
    assert outcome.backup is not None and Path(outcome.backup.path) == bench.backups()[0]
    assert len(requests_after(ecu, 0, b"\x35")) == 3
    archived = json.loads(outcome.archive_path.read_text("utf-8"))  # type: ignore[union-attr]
    assert (
        archived["state"] == "completed" and archived["backup"]["sha256"] == outcome.backup.sha256
    )
    assert (
        archived["progress"]["done_bytes"]
        == archived["progress"]["total_bytes"]
        == TARGET.image_size
    )
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN


def test_a_slow_erase_is_waited_for(bench: Bench) -> None:
    outcome = bench.flash(bench.ecu(slow_erase_pending=3))

    assert outcome.state is S.COMPLETED


def test_journal_progress_is_saved_every_k_blocks(bench: Bench, monkeypatch) -> None:
    saved: list[int] = []
    real = FlashJournal.update

    def spy(self, **fields):
        if fields.get("progress"):
            saved.append(fields["progress"]["done_bytes"])
        return real(self, **fields)

    monkeypatch.setattr(FlashJournal, "update", spy)
    bench.flash(bench.ecu())

    assert saved == [16 * 256 * n for n in range(1, 17)]  # 256 blocks, every 16th


# --------------------------------------------------------------------------- refusals, no journal


def test_an_authorisation_for_another_image_is_refused(bench: Bench) -> None:
    rom, definitions = load_demo()
    other = build_flash_plan(
        type(rom)(
            data=FACTORY,
            name="factory.bin",
            source_path=None,
            sha256=__import__("hashlib").sha256(FACTORY).hexdigest(),
            crc32=0,
        ),
        TARGET,
        allow_unverified_checksum_in_simulation=True,
    )
    ecu = bench.ecu()

    with pytest.raises(SafetyGuardError, match="different image"):
        bench.orchestrator.flash(
            bench.port(ecu), bench.plan, bench.authorization(other), SimulatedXorSeedKey()
        )
    assert ecu.requests == []  # nothing was sent
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN


def test_an_ecu_in_its_bootloader_is_not_flashed(bench: Bench) -> None:
    ecu = bench.ecu()
    ecu.handle(b"\x10\x02")  # the bootloader answers (programming session)

    with pytest.raises(FlashBlockedError, match="bootloader"):
        bench.flash(ecu)
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN  # no journal begun


def test_an_ecu_that_never_answers_creates_no_journal(bench: Bench) -> None:
    ecu = bench.ecu()
    ecu.powered = False

    with pytest.raises(UdsNoTrafficError):
        bench.flash(ecu)
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN


def test_another_session_holding_the_lock_blocks(bench: Bench) -> None:
    with JournalLock(bench.orchestrator.journal_dir), pytest.raises(JournalLockedError):
        bench.flash(bench.ecu())


# --------------------------------------------------------------------------- D3 before the erase


@pytest.mark.parametrize(
    "faults",
    [
        {"refuse_upload": True},
        {"corrupt_readback_at_block": 3},  # the two read-outs differ
        {"bad_key": True},
    ],
)
def test_a_failure_before_the_erase_aborts_cleanly(bench: Bench, faults) -> None:
    ecu = bench.ecu(**faults)

    outcome = bench.flash(ecu)

    assert outcome.state is S.ABORTED and outcome.ecu_untouched and outcome.error
    assert outcome.states[-1] is S.ABORTED and S.ERASING not in outcome.states
    assert ecu.flash == FACTORY and ecu.application_valid  # untouched
    assert ecu.requests[-1] == b"\x10\x01"  # D3: back to the default session
    assert not requests_after(ecu, 0, b"\x11") and not requests_after(ecu, 0, b"\x31")
    assert outcome.archive_path is not None and outcome.archive_path.exists()
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN


def test_a_backup_changed_on_disk_before_the_erase_is_caught(
    tmp_path: Path, fake_can_bus, iso_tp_ecu_responder, fake_clock
) -> None:
    class TamperingVault(BackupVault):
        def store(self, *args, **kwargs):
            receipt = super().store(*args, **kwargs)
            path = Path(receipt.path)
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            data = bytearray(path.read_bytes())
            data[0] ^= 0xFF
            path.write_bytes(bytes(data))  # changed after the receipt, before the erase
            return receipt

    bench = Bench(
        tmp_path,
        fake_can_bus,
        iso_tp_ecu_responder,
        fake_clock,
        vault=TamperingVault(tmp_path / "backups", now=Clock()),
    )
    ecu = bench.ecu()

    outcome = bench.flash(ecu)

    assert outcome.state is S.ABORTED and "changed on disk" in (outcome.error or "")
    assert not requests_after(ecu, 0, b"\x31\x01\xff\x00")  # no erase was ever sent
    assert ecu.flash == FACTORY


# --------------------------------------------------------------------------- D3 after the erase


@pytest.mark.parametrize(
    "faults",
    [
        {"drop_link_at_block": 5},
        {"nrc_at_block": (5, 0x72)},
        {"nrc_at_block": (200, 0x71)},
        {"power_loss_at_block": 5},
    ],
)
def test_a_failure_after_the_erase_is_interrupted_without_a_blind_reset(
    bench: Bench, faults
) -> None:
    ecu = bench.ecu(**faults)

    outcome = bench.flash(ecu)

    assert outcome.state is S.INTERRUPTED and outcome.states[-1] is S.INTERRUPTED
    erase = first_erase_index(ecu)
    assert not requests_after(ecu, erase, b"\x11")  # no ECUReset of a half-written ECU
    assert not requests_after(ecu, erase, b"\x10")  # and no session change
    assessment = bench.orchestrator.assess()
    assert assessment.status is AssessmentStatus.DIRTY and assessment.can_recover
    assert outcome.archive_path is None and outcome.backup is not None


def test_a_wrong_read_back_is_interrupted(bench: Bench) -> None:
    class CorruptsReadBack(VirtualProgrammableEcu):
        uploads = 0

        def handle(self, request: bytes) -> list[bytes]:
            if request[:1] == b"\x35":
                self.uploads += 1
                if self.uploads == 3:  # the read-back after the download
                    self.faults.corrupt_readback_at_block = 10
            return super().handle(request)

    ecu = CorruptsReadBack(clock=bench.clock)

    outcome = bench.flash(ecu)

    assert outcome.state is S.INTERRUPTED and "read-back" in (outcome.error or "")
    assert not requests_after(ecu, first_erase_index(ecu), b"\x11")


def test_a_failed_check_routine_is_interrupted(bench: Bench) -> None:
    class FailsCheck(VirtualProgrammableEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request[:4] == b"\x31\x01\xff\x01":
                self.requests.append(request)
                return [b"\x71\x01\xff\x01\x01"]
            return super().handle(request)

    outcome = bench.flash(FailsCheck(clock=bench.clock))

    assert outcome.state is S.INTERRUPTED and "check routine" in (outcome.error or "")


def test_an_operator_interrupt_is_journaled_and_re_raised(bench: Bench) -> None:
    def stop_at_block_ten(report: FlashProgress) -> None:
        if report.stage is FlashStage.PROGRAMMING and report.block_sequence == 10:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        bench.flash(bench.ecu(), progress=stop_at_block_ten)

    assert bench.orchestrator.assess().state is S.INTERRUPTED


def test_a_dirty_or_stale_journal_blocks_the_next_flash(bench: Bench) -> None:
    bench.flash(bench.ecu(power_loss_at_block=5))
    with pytest.raises(FlashBlockedError, match="dirty"):
        bench.flash(bench.ecu())


# --------------------------------------------------------------------------- recovery


def interrupted_by_power_loss(bench: Bench, block: int = 5) -> VirtualProgrammableEcu:
    ecu = bench.ecu(power_loss_at_block=block)
    assert bench.flash(ecu).state is S.INTERRUPTED
    ecu.power_on()
    assert ecu.in_bootloader
    return ecu


def test_recovery_restores_the_verified_backup(bench: Bench) -> None:
    ecu = interrupted_by_power_loss(bench)
    start = len(ecu.requests)
    reports: list[FlashProgress] = []

    outcome = bench.recover(ecu, progress=reports.append)

    assert outcome.state is S.RECOVERED and outcome.mode == "recovery" and outcome.ok
    assert outcome.states == (S.INTERRUPTED, S.RECOVERING, S.RECOVERED)
    assert ecu.flash == FACTORY and ecu.application_valid and not ecu.in_bootloader
    assert len(requests_after(ecu, start, b"\x35")) == 1  # the read-back only: no new backup
    assert len(bench.backups()) == 1  # the original backup, untouched
    assert {r.stage for r in reports} >= {FlashStage.RECOVERY, FlashStage.VERIFY}
    assert bench.orchestrator.assess().status is AssessmentStatus.CLEAN
    archived = json.loads(outcome.archive_path.read_text("utf-8"))  # type: ignore[union-attr]
    assert [h["state"] for h in archived["history"]][-3:] == [
        "interrupted",
        "recovering",
        "recovered",
    ]
    assert len(archived["recovery"]) == 1


def test_recovery_is_deterministic(
    tmp_path: Path, fake_can_bus, iso_tp_ecu_responder, fake_clock
) -> None:
    results = []
    for run in ("a", "b"):
        bench = Bench(tmp_path / run, fake_can_bus, iso_tp_ecu_responder, fake_clock)
        ecu = interrupted_by_power_loss(bench, block=40)
        outcome = bench.recover(ecu)
        results.append((outcome.states, ecu.flash, bench.backups()[0].read_bytes()))

    assert results[0] == results[1]


def test_recovery_needs_the_backup_bound_authorisation(bench: Bench) -> None:
    ecu = interrupted_by_power_loss(bench)
    start = len(ecu.requests)

    with pytest.raises(SafetyGuardError, match="different image"):
        bench.orchestrator.recover(
            bench.port(ecu), bench.authorization(), SimulatedXorSeedKey()
        )  # bound to the new image, not to the backup
    assert ecu.requests[start:] == []
    assert bench.orchestrator.assess().status is AssessmentStatus.DIRTY


def test_recovery_refuses_a_foreign_ecu(bench: Bench) -> None:
    interrupted_by_power_loss(bench)

    class Stranger(VirtualProgrammableEcu):
        def _did_values(self) -> dict[int, bytes]:
            return {0xF187: b"OTHER-PART", 0xF191: b"OTHER-HW", 0xF189: b"OTHER-SW"}

    stranger = Stranger(clock=bench.clock)
    with pytest.raises(FlashBlockedError, match="neither the target's bootloader"):
        bench.recover(stranger)
    assert not requests_after(stranger, 0, b"\x10\x02")  # nothing that changes it was sent


def test_recovery_refuses_a_backup_changed_on_disk(bench: Bench) -> None:
    ecu = interrupted_by_power_loss(bench)
    backup = bench.backups()[0]
    backup.chmod(stat.S_IRUSR | stat.S_IWUSR)
    backup.write_bytes(b"\x00" * len(FACTORY))
    start = len(ecu.requests)

    with pytest.raises(BackupError, match="changed on disk"):
        bench.orchestrator.recover(
            bench.port(ecu),
            authorize_bench_recovery(
                POLICY,
                ATTESTATION,
                TARGET,
                bench.orchestrator.assess().backup_sha256,  # type: ignore[arg-type]
                link_simulated=True,
            ),
            SimulatedXorSeedKey(),
        )
    assert ecu.requests[start:] == []


def test_a_failed_recovery_can_be_retried(bench: Bench) -> None:
    ecu = interrupted_by_power_loss(bench)
    ecu.faults = FaultPlan(nrc_at_block=(3, 0x72))

    failed = bench.recover(ecu)

    assert failed.state is S.RECOVERY_FAILED and not failed.ok
    assert bench.orchestrator.assess().status is AssessmentStatus.DIRTY
    retried = bench.recover(ecu)
    assert retried.state is S.RECOVERED
    assert retried.states == (S.RECOVERY_FAILED, S.RECOVERING, S.RECOVERED)
    assert ecu.flash == FACTORY and ecu.application_valid


def test_recovery_after_the_process_died_mid_transfer(bench: Bench, monkeypatch) -> None:
    def die(*_args, **_kwargs):
        raise RuntimeError("the process died before it could journal the failure")

    monkeypatch.setattr(BenchFlashOrchestrator, "_fail_flash", die)
    ecu = bench.ecu(power_loss_at_block=8)
    with pytest.raises(RuntimeError, match="process died"):
        bench.flash(ecu)
    monkeypatch.undo()
    assert bench.orchestrator.assess().state is S.TRANSFERRING  # dirty, never marked
    ecu.power_on()

    outcome = bench.recover(ecu)

    assert outcome.states == (S.TRANSFERRING, S.INTERRUPTED, S.RECOVERING, S.RECOVERED)
    assert ecu.flash == FACTORY


def test_nothing_to_recover_and_corrupt_journals_are_refused(bench: Bench) -> None:
    ecu = bench.ecu()
    with pytest.raises(FlashBlockedError, match="nothing to recover"):
        bench.orchestrator.recover(bench.port(ecu), bench.authorization(), SimulatedXorSeedKey())

    journal_dir = bench.orchestrator.journal_dir
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "flash-journal.json").write_text("{broken", encoding="utf-8")
    assert bench.orchestrator.assess().status is AssessmentStatus.CORRUPT
    with pytest.raises(FlashBlockedError, match="corrupt"):
        bench.orchestrator.recover(bench.port(ecu), bench.authorization(), SimulatedXorSeedKey())
    with pytest.raises(FlashBlockedError, match="corrupt"):
        bench.flash(ecu)
    assert ecu.requests == []


def test_a_stale_journal_blocks_until_it_is_discarded(bench: Bench) -> None:
    ecu = bench.ecu()
    assert bench.flash(ecu).state is S.COMPLETED
    FlashJournal.begin(
        bench.orchestrator.journal_dir,
        transaction_id="f" * 32,
        target_id=TARGET.target_id,
        image_sha256=bench.plan.image_sha256,
        image_name="left over",
        simulated=True,
        attestation=ATTESTATION,
        authorization_nonce="n" * 32,
        identity=bench.port(ecu).read_identity([0xF187, 0xF191]),
        unverified_checksum_override=False,
    )  # a session that stopped right after pre-flight
    with pytest.raises(FlashBlockedError, match="stale"):
        bench.flash(bench.ecu())

    JournalInspector.discard_stale(bench.orchestrator.journal_dir)

    assert bench.flash(bench.ecu()).state is S.COMPLETED
