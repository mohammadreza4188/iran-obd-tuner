"""Bench flash orchestrator (Milestone 7d): the four-stage pipeline and its recovery.

:class:`BenchFlashOrchestrator` drives a :class:`~iran_obd_tuner.core.bench.
BenchProgrammerPort` only - it imports no ``hardware`` module (architecture test) and
mints no capability. Every step is journaled **before** it is taken.

Normal flash (:meth:`BenchFlashOrchestrator.flash`):

1. **Pre-flight** - no journal may block (dirty, stale or corrupt), the authorisation must
   be bound to this plan, the target and the link, the link must answer within the latency
   bound, and the ECU must show the target's *application* identity (an ECU sitting in its
   bootloader needs a recovery, not a flash). Then the journal starts (``pending``).
2. **Mandatory backup** - programming session, one unlock, two independent read-outs of
   every region; the vault keeps them only if they are identical, written once and read
   back from disk with the same SHA-256 -> ``backup_verified``.
3. **Erase and transfer** - the backup file is re-verified on disk immediately before the
   erase (check-to-use); ``erasing`` then ``transferring`` (progress saved every
   ``progress_every_blocks`` blocks).
4. **Verify** - ``verifying``: the check routine, a read-back whose SHA-256 must equal the
   image, a hard reset and the application identity again -> ``completed`` (archived).

Failures (decision D3 of the Milestone 7 plan): before the erase, the ECU is untouched - the
default session is requested and the journal ends ``aborted`` (archived). From the erase on,
the journal becomes ``interrupted`` and **nothing else is sent**: no blind reset of a
half-written ECU. A ``KeyboardInterrupt`` is journaled the same way and re-raised.

Recovery (:meth:`BenchFlashOrchestrator.recover`) needs a dirty journal with a backup and an
authorisation bound to **that backup's** SHA-256. It never takes a backup (that would read a
half-written ECU over the good copy): it re-verifies the recorded backup, accepts the
target's bootloader identity (or the backed-up application identity), and runs stages 3-4
with the backup image -> ``recovered``, or ``recovery_failed`` (which may be retried).
"""

from __future__ import annotations

import hashlib
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.bench import (
    BackupReceipt,
    BenchProgrammerPort,
    BenchTargetProfile,
    EcuIdentity,
    FlashAuthorization,
    FlashProgress,
    FlashStage,
    JournalState,
    MemoryRegion,
    ProgressSink,
    SeedKeyProvider,
)
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import utc_now
from iran_obd_tuner.tuning.backup_vault import BackupVault, receipt_record
from iran_obd_tuner.tuning.flash_journal import (
    AssessmentStatus,
    FlashJournal,
    JournalInspector,
    JournalLock,
    RecoveryAssessment,
)
from iran_obd_tuner.tuning.flash_plan import FlashPlan

__all__ = [
    "FlashBlockedError",
    "FlashOutcome",
    "FlashVerificationError",
    "BenchFlashOrchestrator",
    "identity_dids",
]

_LOG = get_logger("tuning.flash_orchestrator")

#: Standard DID of the ECU software version, read for the record (not for matching).
_SOFTWARE_VERSION_DID = 0xF189


class FlashBlockedError(SafetyGuardError):
    """A flash or recovery may not start (journal state, binding or identity)."""


class FlashVerificationError(SafetyGuardError):
    """Stage 4 found the ECU content or identity different from what was written."""


@dataclass(frozen=True, slots=True)
class FlashOutcome:
    """How a flash or recovery ended.

    Attributes:
        mode: ``flash`` or ``recovery``.
        state: Final journal state (``completed``, ``aborted``, ``interrupted``,
            ``recovered`` or ``recovery_failed``).
        transaction_id: The journal's transaction.
        simulated: Always ``True`` in this build.
        backup: The receipt of the verified backup, once there is one.
        identity_after: The ECU identity read after the reset (success only).
        archive_path: Where the journal was archived (terminal states only).
        error: Why it stopped, for the non-success states.
        states: The journal states this run went through, in order.
    """

    mode: str
    state: JournalState
    transaction_id: str
    simulated: bool
    backup: BackupReceipt | None = None
    identity_after: EcuIdentity | None = None
    archive_path: Path | None = None
    error: str | None = None
    states: tuple[JournalState, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.state in (JournalState.COMPLETED, JournalState.RECOVERED)

    @property
    def ecu_untouched(self) -> bool:
        """``True`` when the run stopped before any erase (``aborted``)."""
        return self.state is JournalState.ABORTED


def identity_dids(target: BenchTargetProfile) -> tuple[int, ...]:
    """The DIDs read for identification: the target's patterns plus the software version."""
    return tuple(
        sorted(
            set(target.identity_patterns)
            | set(target.bootloader_identity_patterns)
            | {_SOFTWARE_VERSION_DID}
        )
    )


@dataclass
class _Run:
    """Mutable bookkeeping of one run (journal, states seen, progress counters)."""

    journal: FlashJournal
    states: list[JournalState]
    blocks: int = 0

    def transition(self, new: JournalState, **fields: Any) -> None:
        self.journal.transition(new, **fields)
        self.states.append(new)


class BenchFlashOrchestrator:
    """Runs bench flashes and recoveries against a :class:`BenchProgrammerPort`.

    Args:
        journal_dir: Where the flash journal, its lock and its archive live.
        vault: Where backups are kept (write-once).
        progress_every_blocks: Journal progress is saved every that many transfer blocks.
        link_round_trips / max_link_latency_s: Pre-flight link stability check.
        now: Clock for journal timestamps (aware UTC).
    """

    def __init__(
        self,
        journal_dir: Path | str,
        vault: BackupVault,
        *,
        progress_every_blocks: int = 16,
        link_round_trips: int = 5,
        max_link_latency_s: float = 0.5,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if progress_every_blocks < 1 or link_round_trips < 1 or max_link_latency_s <= 0:
            raise ValueError("invalid orchestrator settings")
        self._journal_dir = Path(journal_dir)
        self._vault = vault
        self._every = progress_every_blocks
        self._round_trips = link_round_trips
        self._max_latency = max_link_latency_s
        self._now = now

    @property
    def journal_dir(self) -> Path:
        return self._journal_dir

    def assess(self) -> RecoveryAssessment:
        """The start-up verdict on the journal directory (read-only)."""
        return JournalInspector.inspect(self._journal_dir)

    # ------------------------------------------------------------------ helpers
    def _emit(self, progress: ProgressSink | None, report: FlashProgress) -> None:
        if progress is not None:
            progress(report)

    def _verify_link(self, port: BenchProgrammerPort) -> None:
        slowest = port.check_link(self._round_trips)
        if slowest > self._max_latency:
            raise FlashBlockedError(
                f"the bench link is too slow or unstable ({slowest:.3f}s > "
                f"{self._max_latency:.3f}s per round trip)"
            )

    def _stage_sink(
        self, run: _Run, stage: FlashStage, progress: ProgressSink | None, *, journal: bool
    ) -> ProgressSink:
        def sink(report: FlashProgress) -> None:
            restamped = FlashProgress(
                stage, report.done_bytes, report.total_bytes, report.message, report.block_sequence
            )
            if journal:
                run.blocks += 1
                if run.blocks % self._every == 0 or report.done_bytes == report.total_bytes:
                    run.journal.update(
                        progress={
                            "stage": str(stage),
                            "done_bytes": report.done_bytes,
                            "total_bytes": report.total_bytes,
                            "block_sequence": report.block_sequence,
                        }
                    )
            self._emit(progress, restamped)

        return sink

    def _program_and_verify(
        self,
        run: _Run,
        port: BenchProgrammerPort,
        authorization: FlashAuthorization,
        receipt: BackupReceipt,
        regions: tuple[MemoryRegion, ...],
        image_of: Callable[[MemoryRegion], bytes],
        image_sha256: str,
        progress: ProgressSink | None,
        *,
        stage: FlashStage,
    ) -> EcuIdentity:
        """Stages 3 and 4 (shared by flash and recovery); returns the identity after reset."""
        self._vault.verify(receipt)  # check-to-use: the backup is still intact on disk
        if run.journal.state is not JournalState.RECOVERING:
            run.transition(JournalState.ERASING)
        for region in regions:
            port.erase_region(region, authorization=authorization, backup=receipt)
        if run.journal.state is not JournalState.RECOVERING:
            run.transition(JournalState.TRANSFERRING)
        sink = self._stage_sink(run, stage, progress, journal=True)
        for region in regions:
            port.download_region(
                region, image_of(region), authorization=authorization, backup=receipt, progress=sink
            )
        if run.journal.state is not JournalState.RECOVERING:
            run.transition(JournalState.VERIFYING)
        self._emit(progress, FlashProgress(FlashStage.VERIFY, message="check routine"))
        for region in regions:
            crc = zlib.crc32(image_of(region))
            if not port.check_memory(region, crc, authorization=authorization):
                raise FlashVerificationError(
                    f"the ECU check routine failed for {region.describe()}"
                )
        read_sink = self._stage_sink(run, FlashStage.VERIFY, progress, journal=False)
        read_back = b"".join(
            port.upload_region(region, authorization=authorization, progress=read_sink)
            for region in regions
        )
        if hashlib.sha256(read_back).hexdigest() != image_sha256:
            raise FlashVerificationError("the read-back does not match the written image")
        port.hard_reset(authorization=authorization)
        identity = port.read_identity(identity_dids(port.target))
        problems = identity.mismatches(port.target.identity_patterns)
        if problems:
            raise FlashVerificationError(
                "after the reset the ECU does not run the application: " + "; ".join(problems)
            )
        return identity

    # ------------------------------------------------------------------ flash
    def flash(
        self,
        port: BenchProgrammerPort,
        plan: FlashPlan,
        authorization: FlashAuthorization,
        provider: SeedKeyProvider,
        *,
        progress: ProgressSink | None = None,
    ) -> FlashOutcome:
        """Run the four stages; see the module docstring.

        Raises:
            FlashBlockedError / SafetyGuardError: Refusals before the journal starts (nothing
                was changed). After it started, failures are returned as the outcome.
        """
        target = plan.target
        if port.target.target_id != target.target_id:
            raise FlashBlockedError("the programmer is connected to another target than the plan")
        authorization.check_for(
            target_id=target.target_id, image_sha256=plan.image_sha256, simulated=port.simulated
        )
        assessment = self.assess()
        if assessment.blocks_new_flash:
            raise FlashBlockedError(
                f"the flash journal is {assessment.status}: {assessment.detail}"
            )
        with JournalLock(self._journal_dir):
            self._emit(progress, FlashProgress(FlashStage.PRE_FLIGHT, message="link check"))
            self._verify_link(port)
            identity = port.read_identity(identity_dids(target))
            problems = identity.mismatches(target.identity_patterns)
            if problems:
                raise FlashBlockedError(
                    "the ECU does not show the target's application identity (if it sits in "
                    "its bootloader, recover it instead): " + "; ".join(problems)
                )
            journal = FlashJournal.begin(
                self._journal_dir,
                transaction_id=uuid.uuid4().hex,
                target_id=target.target_id,
                image_sha256=plan.image_sha256,
                image_name=plan.image_name,
                simulated=plan.simulated,
                attestation=authorization.attestation,
                authorization_nonce=authorization.nonce,
                identity=identity,
                unverified_checksum_override=plan.unverified_checksum_override,
                now=self._now,
            )
            run = _Run(journal, [JournalState.PENDING])
            receipt: BackupReceipt | None = None
            try:
                # stage 2: mandatory full backup
                port.enter_programming_session(authorization=authorization)
                port.unlock(authorization=authorization, provider=provider)
                backup_sink = self._stage_sink(run, FlashStage.BACKUP, progress, journal=False)
                reads = [
                    b"".join(
                        port.upload_region(
                            region, authorization=authorization, progress=backup_sink
                        )
                        for region in plan.regions
                    )
                    for _attempt in range(2)
                ]
                receipt = self._vault.store(
                    reads[0],
                    reads[1],
                    target=target,
                    identity=identity,
                    regions=plan.regions,
                    simulated=plan.simulated,
                )
                run.transition(JournalState.BACKUP_VERIFIED, backup=receipt_record(receipt))
                # stages 3 and 4
                after = self._program_and_verify(
                    run,
                    port,
                    authorization,
                    receipt,
                    plan.regions,
                    plan.region_data,
                    plan.image_sha256,
                    progress,
                    stage=FlashStage.PROGRAMMING,
                )
                run.transition(
                    JournalState.COMPLETED,
                    identity_after={f"{d:04X}": v for d, v in sorted(after.dids.items())},
                )
                archive = run.journal.archive()
                return FlashOutcome(
                    "flash",
                    JournalState.COMPLETED,
                    journal.transaction_id,
                    plan.simulated,
                    backup=receipt,
                    identity_after=after,
                    archive_path=archive,
                    states=tuple(run.states),
                )
            except BaseException as exc:
                outcome = self._fail_flash(run, port, exc, receipt, plan.simulated)
                if not isinstance(exc, Exception):
                    raise
                return outcome

    def _fail_flash(
        self,
        run: _Run,
        port: BenchProgrammerPort,
        exc: BaseException,
        receipt: BackupReceipt | None,
        simulated: bool,
    ) -> FlashOutcome:
        """Decision D3: untouched ECU -> default session + ``aborted``; else ``interrupted``."""
        reason = f"{type(exc).__name__}: {exc}"
        _LOG.warning("bench flash stopped in state %s: %s", run.journal.state, reason)
        archive = None
        if run.journal.state in (JournalState.PENDING, JournalState.BACKUP_VERIFIED):
            try:
                port.return_to_default_session()
            except Exception as leave:  # the ECU is untouched; leaving is a courtesy
                _LOG.warning("could not return to the default session: %s", leave)
            run.transition(JournalState.ABORTED, abort_reason=reason)
            archive = run.journal.archive()
            final = JournalState.ABORTED
        else:  # erase started: no reset, no session change - recovery decides
            run.transition(JournalState.INTERRUPTED, abort_reason=reason)
            final = JournalState.INTERRUPTED
        return FlashOutcome(
            "flash",
            final,
            run.journal.transaction_id,
            simulated,
            backup=receipt,
            archive_path=archive,
            error=reason,
            states=tuple(run.states),
        )

    # ------------------------------------------------------------------ recovery
    def recover(
        self,
        port: BenchProgrammerPort,
        authorization: FlashAuthorization,
        provider: SeedKeyProvider,
        *,
        progress: ProgressSink | None = None,
    ) -> FlashOutcome:
        """Restore the verified backup of a dirty journal; see the module docstring.

        Raises:
            FlashBlockedError / SafetyGuardError: Nothing to recover, a corrupt journal, a
                binding that does not match, or a foreign ECU - before anything is sent.
        """
        assessment = self.assess()
        if assessment.status is not AssessmentStatus.DIRTY or not assessment.can_recover:
            raise FlashBlockedError(
                f"nothing to recover: the flash journal is {assessment.status} "
                f"({assessment.detail})"
            )
        target = port.target
        if assessment.target_id != target.target_id:
            raise FlashBlockedError("the journal belongs to another bench target")
        with JournalLock(self._journal_dir):
            journal = FlashJournal.load(self._journal_dir, now=self._now)
            record = journal.backup_record
            assert record is not None
            receipt = self._vault.receipt_from_record(record)  # re-verifies the files
            backup = self._vault.read(receipt)
            authorization.check_for(
                target_id=target.target_id, image_sha256=receipt.sha256, simulated=port.simulated
            )
            if receipt.target_id != target.target_id or receipt.simulated != port.simulated:
                raise FlashBlockedError("the recorded backup belongs to another target or link")
            self._emit(progress, FlashProgress(FlashStage.PRE_FLIGHT, message="link check"))
            self._verify_link(port)
            identity = port.read_identity(identity_dids(target))
            in_bootloader = not identity.mismatches(target.bootloader_identity_patterns)
            same_application = (
                not identity.mismatches(target.identity_patterns)
                and identity.fingerprint() == receipt.identity_fingerprint
            )
            if not (in_bootloader or same_application):
                raise FlashBlockedError(
                    "the connected ECU is neither the target's bootloader nor the ECU the "
                    "backup was taken from; refusing to write someone else's backup to it"
                )
            run = _Run(journal, [journal.state])
            if journal.state in (
                JournalState.ERASING,
                JournalState.TRANSFERRING,
                JournalState.VERIFYING,
            ):  # the process died without recording the interruption
                run.transition(JournalState.INTERRUPTED, abort_reason="found dirty at start-up")
            if journal.state is JournalState.RECOVERING:  # a recovery died half-way
                run.transition(
                    JournalState.RECOVERY_FAILED, abort_reason="previous recovery did not finish"
                )
            attempts = list(journal.document.get("recovery") or [])
            attempts.append(
                {"authorization_nonce": authorization.nonce, "at": self._now().isoformat()}
            )
            run.transition(JournalState.RECOVERING, recovery=attempts)
            regions = receipt.regions

            def backup_of(region: MemoryRegion) -> bytes:
                start = region.image_offset
                return backup[start : start + region.size]

            try:
                port.enter_programming_session(authorization=authorization)
                port.unlock(authorization=authorization, provider=provider)
                after = self._program_and_verify(
                    run,
                    port,
                    authorization,
                    receipt,
                    regions,
                    backup_of,
                    receipt.sha256,
                    progress,
                    stage=FlashStage.RECOVERY,
                )
            except BaseException as exc:
                reason = f"{type(exc).__name__}: {exc}"
                _LOG.warning("bench recovery failed: %s", reason)
                run.transition(JournalState.RECOVERY_FAILED, abort_reason=reason)
                if not isinstance(exc, Exception):
                    raise
                return FlashOutcome(
                    "recovery",
                    JournalState.RECOVERY_FAILED,
                    journal.transaction_id,
                    receipt.simulated,
                    backup=receipt,
                    error=reason,
                    states=tuple(run.states),
                )
            run.transition(
                JournalState.RECOVERED,
                identity_after={f"{d:04X}": v for d, v in sorted(after.dids.items())},
            )
            archive = run.journal.archive()
            return FlashOutcome(
                "recovery",
                JournalState.RECOVERED,
                journal.transaction_id,
                receipt.simulated,
                backup=receipt,
                identity_after=after,
                archive_path=archive,
                states=tuple(run.states),
            )
