"""Plain reports of simulated bench flash sessions (Milestone 7e), shared by the CLI and the UI.

``ui`` may not import ``tuning`` (ADR 13 / 40), so the application facade
(``app/bench_flash.py``) translates the tuning objects - the journal's
``RecoveryAssessment`` and the orchestrator's ``FlashOutcome`` - into these frozen, Qt-free
value objects. They carry no capability, mint nothing and cause nothing: they only describe
what happened.

:class:`SimulatedFault` is the operator-facing description of one deterministic fault of the
virtual programmable ECU (``drop-at-block=N``, ``nrc72-at-block=N``,
``power-loss-at-block=N``, ``bad-key``, ``corrupt-readback``). It exists only for the
simulator; there is no real-ECU equivalent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from iran_obd_tuner.core.bench import JournalState

__all__ = [
    "BENCH_REPORT_SCHEMA_VERSION",
    "BenchCheck",
    "BenchCheckKey",
    "BenchFlashReport",
    "BenchPreflightReport",
    "BenchJournalReport",
    "BenchJournalStatus",
    "SimulatedFault",
    "SimulatedFaultKind",
]

#: Version of the JSON documents built from these reports.
BENCH_REPORT_SCHEMA_VERSION = 1


class BenchJournalStatus(StrEnum):
    """What a journal directory says about the simulated bench ECU (mirrors tuning)."""

    CLEAN = "clean"  # no journal: a new flash may start
    STALE = "stale"  # unfinished but harmless: discard it explicitly
    DIRTY = "dirty"  # the ECU memory may be incomplete: recovery needed
    CORRUPT = "corrupt"  # unreadable journal: inspect by hand, everything is blocked


class SimulatedFaultKind(StrEnum):
    """Faults the virtual programmable ECU can inject (each fires once)."""

    DROP_AT_BLOCK = "drop-at-block"  # download block N is lost and the bus goes silent
    NRC72_AT_BLOCK = "nrc72-at-block"  # download block N is refused with 0x72
    POWER_LOSS_AT_BLOCK = "power-loss-at-block"  # block N is torn, then the power is lost
    BAD_KEY = "bad-key"  # the key is refused (before the backup: the ECU stays untouched)
    CORRUPT_READBACK = "corrupt-readback"  # one byte of the stage-4 read-back is flipped

    @property
    def needs_block(self) -> bool:
        return self.value.endswith("-at-block")


_FAULT_TEXT = re.compile(r"(?P<kind>[a-z0-9-]+?)(?:=(?P<block>[0-9]+))?")


@dataclass(frozen=True, slots=True)
class SimulatedFault:
    """One deterministic fault of the virtual programmable ECU.

    Attributes:
        kind: Which fault.
        block: Download block number (from 1) for the ``*-at-block`` faults, else ``None``.
    """

    kind: SimulatedFaultKind
    block: int | None = None

    def __post_init__(self) -> None:
        if self.kind.needs_block:
            if self.block is None or self.block < 1:
                raise ValueError(f"fault {self.kind} needs a block number >= 1 ({self.kind}=N)")
        elif self.block is not None:
            raise ValueError(f"fault {self.kind} takes no block number")

    @classmethod
    def parse(cls, text: str) -> SimulatedFault:
        """Parse ``drop-at-block=N``, ``nrc72-at-block=N``, ``power-loss-at-block=N``,
        ``bad-key`` or ``corrupt-readback``.

        Raises:
            ValueError: Unknown fault, a missing or superfluous block number.
        """
        match = _FAULT_TEXT.fullmatch(text.strip().lower())
        kinds = ", ".join(
            f"{kind}=N" if kind.needs_block else str(kind) for kind in SimulatedFaultKind
        )
        if match is None:
            raise ValueError(f"unknown fault {text!r} (choose one of: {kinds})")
        try:
            kind = SimulatedFaultKind(match["kind"])
        except ValueError as exc:
            raise ValueError(f"unknown fault {text!r} (choose one of: {kinds})") from exc
        block = None if match["block"] is None else int(match["block"])
        return cls(kind, block)

    def describe(self) -> str:
        return str(self.kind) if self.block is None else f"{self.kind}={self.block}"


@dataclass(frozen=True, slots=True)
class BenchJournalReport:
    """Read-only view of a bench journal directory.

    Attributes:
        status: See :class:`BenchJournalStatus`.
        directory / journal_path: Where the journal lives.
        detail: Human-readable explanation (English).
        state: Journal state, when the journal could be read.
        transaction_id / target_id / image_sha256 / image_name / simulated: From the journal.
        backup_path / backup_sha256: The verified backup the journal records, if any.
        progress: Last saved transfer progress (``stage``, ``done_bytes``, ``total_bytes``).
        abort_reason: Why the transaction stopped, if it did.
        history: ``(state, ISO timestamp)`` pairs, oldest first.
        leftover_temp_file: A ``.tmp`` of an interrupted journal write exists (harmless).
    """

    status: BenchJournalStatus
    directory: str
    journal_path: str
    detail: str
    state: JournalState | None = None
    transaction_id: str | None = None
    target_id: str | None = None
    image_sha256: str | None = None
    image_name: str | None = None
    simulated: bool | None = None
    backup_path: str | None = None
    backup_sha256: str | None = None
    progress: Mapping[str, Any] | None = None
    abort_reason: str | None = None
    history: tuple[tuple[str, str], ...] = ()
    leftover_temp_file: bool = False

    @property
    def blocks_new_flash(self) -> bool:
        return self.status is not BenchJournalStatus.CLEAN

    @property
    def can_recover(self) -> bool:
        return self.status is BenchJournalStatus.DIRTY and self.backup_sha256 is not None

    @property
    def can_discard(self) -> bool:
        return self.status is BenchJournalStatus.STALE

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "directory": self.directory,
            "journal_path": self.journal_path,
            "detail": self.detail,
            "state": None if self.state is None else str(self.state),
            "transaction_id": self.transaction_id,
            "target_id": self.target_id,
            "image_sha256": self.image_sha256,
            "image_name": self.image_name,
            "simulated": self.simulated,
            "backup": None
            if self.backup_path is None
            else {"path": self.backup_path, "sha256": self.backup_sha256},
            "progress": None if self.progress is None else dict(self.progress),
            "abort_reason": self.abort_reason,
            "history": [{"state": state, "at": at} for state, at in self.history],
            "leftover_temp_file": self.leftover_temp_file,
            "blocks_new_flash": self.blocks_new_flash,
            "can_recover": self.can_recover,
            "can_discard": self.can_discard,
        }


@dataclass(frozen=True, slots=True)
class BenchFlashReport:
    """How one simulated bench flash or recovery ended.

    Attributes:
        mode: ``flash`` or ``recovery``.
        state: Final journal state.
        transaction_id: The journal's transaction.
        directory: The bench output directory (journal, backups, simulated ECU state).
        simulated: Always ``True`` in this build.
        image_name / image_sha256: What was written (the backup image for a recovery).
        image_simulated: Whether the image is the SIMULATED demo ROM.
        unverified_checksum_override: Accepted despite an unverified checksum (simulation).
        backup_path / backup_sha256 / backup_size: The verified backup, once there is one.
        archive_path: Where the journal was archived (terminal states only).
        identity_after: Identification DIDs read after the reset (success only).
        error: Why it stopped (non-success states).
        states: Journal states this run went through, in order.
        fault: The injected fault, if any; ``fault_fired`` says whether it actually fired.
    """

    mode: str
    state: JournalState
    transaction_id: str
    directory: str
    simulated: bool
    image_name: str
    image_sha256: str
    image_simulated: bool = False
    unverified_checksum_override: bool = False
    backup_path: str | None = None
    backup_sha256: str | None = None
    backup_size: int | None = None
    archive_path: str | None = None
    identity_after: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None
    states: tuple[JournalState, ...] = ()
    fault: SimulatedFault | None = None
    fault_fired: bool | None = None

    @property
    def ok(self) -> bool:
        return self.state in (JournalState.COMPLETED, JournalState.RECOVERED)

    @property
    def ecu_untouched(self) -> bool:
        """Stopped before any erase: the (simulated) ECU memory is unchanged."""
        return self.state is JournalState.ABORTED

    @property
    def recovery_required(self) -> bool:
        """The run left a dirty journal (``interrupted`` or ``recovery_failed``)."""
        return self.state in (JournalState.INTERRUPTED, JournalState.RECOVERY_FAILED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "state": str(self.state),
            "ok": self.ok,
            "ecu_untouched": self.ecu_untouched,
            "recovery_required": self.recovery_required,
            "transaction_id": self.transaction_id,
            "directory": self.directory,
            "simulated": self.simulated,
            "image": {
                "name": self.image_name,
                "sha256": self.image_sha256,
                "simulated": self.image_simulated,
                "unverified_checksum_override": self.unverified_checksum_override,
            },
            "backup": None
            if self.backup_path is None
            else {"path": self.backup_path, "sha256": self.backup_sha256, "size": self.backup_size},
            "archive_path": self.archive_path,
            "identity_after": dict(self.identity_after),
            "error": self.error,
            "states": [str(state) for state in self.states],
            "fault": None if self.fault is None else self.fault.describe(),
            "fault_fired": self.fault_fired,
        }


class BenchCheckKey(StrEnum):
    """Machine checks shown before a simulated flash or recovery (Milestone 7f).

    These are what the software can verify itself; the human statements are the
    :class:`~iran_obd_tuner.core.bench.BenchAttestation`, asked separately.
    """

    JOURNAL_CLEAN = "journal_clean"  # flash: no journal blocks a new flash
    JOURNAL_RECOVERABLE = "journal_recoverable"  # recovery: dirty journal with a backup
    BACKUP_DIR_WRITABLE = "backup_dir_writable"
    FREE_SPACE = "free_space"
    IMAGE_VALID = "image_valid"  # the flash plan accepts the image (size, integrity)
    CHECKSUMS_VALID = "checksums_valid"
    BACKUP_VERIFIED = "backup_verified"  # recovery: the recorded backup verifies on disk
    TARGET_SIMULATED = "target_simulated"


@dataclass(frozen=True, slots=True)
class BenchCheck:
    """One machine check.

    Attributes:
        key: Which check (the UI translates by key).
        ok: Whether it passed.
        detail: Technical detail (English), e.g. the free bytes or why the plan refused.
        override: Passed only because of the simulation-only unverified-checksum override.
    """

    key: BenchCheckKey
    ok: bool
    detail: str = ""
    override: bool = False


@dataclass(frozen=True, slots=True)
class BenchPreflightReport:
    """The machine checks before a simulated flash or recovery (nothing was sent).

    Attributes:
        mode: ``flash`` or ``recovery``.
        directory: The bench directory.
        checks: In display order.
        image_name / image_sha256: What would be written (the backup for a recovery).
        image_simulated: The image is the SIMULATED demo ROM.
    """

    mode: str
    directory: str
    checks: tuple[BenchCheck, ...]
    image_name: str | None = None
    image_sha256: str | None = None
    image_simulated: bool = False

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def check(self, key: BenchCheckKey) -> BenchCheck | None:
        return next((c for c in self.checks if c.key is key), None)
