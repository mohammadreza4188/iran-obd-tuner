"""Flash Transaction Journal (Milestone 7d): crash-safe record of one bench flash.

The journal is written **before** each step it describes (so after a crash it never claims
less than what may have happened to the ECU), always atomically:

``flash-journal.json.tmp`` is written, flushed and ``fsync``-ed, then ``os.replace``-d over
``flash-journal.json`` (and the directory is ``fsync``-ed where the platform allows). A
crash between the two leaves the previous journal intact; the leftover ``.tmp`` is ignored
and overwritten by the next write.

* :class:`JournalLock` - one bench session per journal directory (``O_EXCL`` lock file with
  the PID). A lock left by a crashed process is **not** broken automatically; the operator
  removes it after making sure no other instance runs (fail closed).
* :class:`FlashJournal` - begin / load / transition (only the transitions of
  :data:`~iran_obd_tuner.core.bench.JOURNAL_TRANSITIONS`) / progress / archive to
  ``journal-archive/<transaction id>.json`` once terminal.
* :class:`JournalInspector` - read-only assessment at start-up: a dirty **or unreadable**
  journal blocks every new flash (:class:`RecoveryAssessment`); dirty journals with a backup
  record can be recovered.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.bench import (
    DIRTY_JOURNAL_STATES,
    TERMINAL_JOURNAL_STATES,
    BenchAttestation,
    EcuIdentity,
    JournalState,
    check_journal_transition,
)
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.models import utc_now

__all__ = [
    "ARCHIVE_DIR",
    "JOURNAL_KIND",
    "JOURNAL_NAME",
    "LOCK_NAME",
    "TEMP_NAME",
    "AssessmentStatus",
    "FlashJournal",
    "JournalError",
    "JournalInspector",
    "JournalLock",
    "JournalLockedError",
    "RecoveryAssessment",
]

JOURNAL_NAME = "flash-journal.json"
TEMP_NAME = JOURNAL_NAME + ".tmp"
LOCK_NAME = "flash-journal.lock"
ARCHIVE_DIR = "journal-archive"
JOURNAL_KIND = "bench_flash_journal"
_SCHEMA = 1
_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")
#: States after which a backup record must exist.
_NEEDS_BACKUP = frozenset(
    {JournalState.BACKUP_VERIFIED} | DIRTY_JOURNAL_STATES | {JournalState.RECOVERED}
)


class JournalError(SafetyGuardError):
    """The journal refuses an operation (unfinished journal, illegal state, corruption)."""


class JournalLockedError(JournalError):
    """Another bench session holds the journal directory."""


# --------------------------------------------------------------------------- #
# atomic writing and the lock
# --------------------------------------------------------------------------- #


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself where the platform allows (not on Windows)."""
    with contextlib.suppress(OSError, AttributeError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


#: Windows refuses to rename over (or delete) a file another process holds open without
#: delete sharing - typically an antivirus or indexer scanning the fresh journal for a few
#: milliseconds (``PermissionError``, WinError 5/32). Such a refusal is retried, bounded:
_SHARING_ATTEMPTS = 20
_SHARING_PAUSE_S = 0.05
_sleep = time.sleep


def _with_sharing_retry(operation: Callable[[], object]) -> None:
    """Run a rename/unlink; retry only a transient sharing refusal, at most ~1 s."""
    for attempt in range(_SHARING_ATTEMPTS):
        try:
            operation()
            return
        except PermissionError:
            if attempt == _SHARING_ATTEMPTS - 1:
                raise  # still refused: fail closed, the caller treats it as a failed write
            _sleep(_SHARING_PAUSE_S)


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    with temp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    # os.replace: atomic - the old journal or the new one, never a mix
    _with_sharing_retry(lambda: temp.replace(path))
    _fsync_directory(path.parent)


class JournalLock:
    """Exclusive lock of a journal directory (context manager)."""

    def __init__(self, directory: Path | str) -> None:
        self._path = Path(directory) / LOCK_NAME
        self._held = False

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            try:
                owner = self._path.read_text(encoding="ascii").strip() or "?"
            except OSError:
                owner = "?"
            raise JournalLockedError(
                f"another bench session (PID {owner}) holds {self._path}; if none is running, "
                "delete the lock file after checking - it is never broken automatically"
            ) from exc
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        self._held = True

    def release(self) -> None:
        if self._held:
            self._held = False
            self._path.unlink(missing_ok=True)

    def __enter__(self) -> JournalLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


# --------------------------------------------------------------------------- #
# the journal
# --------------------------------------------------------------------------- #


def _validate(document: Any) -> JournalState:
    """The state of a well-formed journal document; raises ``JournalError`` otherwise."""
    if not isinstance(document, dict):
        raise JournalError("the journal is not a JSON object")
    if document.get("kind") != JOURNAL_KIND or document.get("schema_version") != _SCHEMA:
        raise JournalError("the journal has an unknown kind or schema version")
    try:
        state = JournalState(document.get("state"))
    except ValueError as exc:
        raise JournalError(f"unknown journal state {document.get('state')!r}") from exc
    if not _ID.fullmatch(str(document.get("transaction_id", ""))):
        raise JournalError("the journal has no valid transaction id")
    if not _SHA.fullmatch(str(document.get("image_sha256", ""))):
        raise JournalError("the journal has no valid image SHA-256")
    history = document.get("history")
    if not isinstance(history, list) or not history or history[-1].get("state") != state:
        raise JournalError("the journal history does not end in its state")
    if not isinstance(document.get("simulated"), bool) or not document.get("target_id"):
        raise JournalError("the journal lacks its target or simulation flag")
    if state in _NEEDS_BACKUP and not isinstance(document.get("backup"), dict):
        raise JournalError(f"a journal in state {state} must record its backup")
    return state


class FlashJournal:
    """One flash transaction's journal file (create with :meth:`begin` or :meth:`load`)."""

    def __init__(
        self, directory: Path, document: dict[str, Any], now: Callable[[], datetime]
    ) -> None:
        self._directory = directory
        self._document = document
        self._state = _validate(document)
        self._now = now

    # ------------------------------------------------------------------ creation
    @classmethod
    def begin(
        cls,
        directory: Path | str,
        *,
        transaction_id: str,
        target_id: str,
        image_sha256: str,
        image_name: str,
        simulated: bool,
        attestation: BenchAttestation,
        authorization_nonce: str,
        identity: EcuIdentity,
        unverified_checksum_override: bool,
        now: Callable[[], datetime] = utc_now,
    ) -> FlashJournal:
        """Record a new transaction in state ``pending``.

        Raises:
            JournalError: A journal already exists (finished or not, it must be resolved or
                archived first).
        """
        root = Path(directory)
        if (root / JOURNAL_NAME).exists():
            raise JournalError(f"an unfinished flash journal exists in {root}; resolve it first")
        root.mkdir(parents=True, exist_ok=True)
        at = now().isoformat()
        document: dict[str, Any] = {
            "kind": JOURNAL_KIND,
            "schema_version": _SCHEMA,
            "transaction_id": transaction_id,
            "simulated": simulated,
            "target_id": target_id,
            "image_sha256": image_sha256,
            "image_name": image_name,
            "state": str(JournalState.PENDING),
            "history": [{"state": str(JournalState.PENDING), "at": at}],
            "attestation": {
                "operator": attestation.operator,
                "attested_at": attestation.attested_at.isoformat(),
                "statements": {name: getattr(attestation, name) for name in attestation.STATEMENTS},
                "simulated": attestation.simulated,
            },
            "authorization_nonce": authorization_nonce,
            "identity": {f"{d:04X}": v for d, v in sorted(identity.dids.items())},
            "identity_fingerprint": identity.fingerprint(),
            "unverified_checksum_override": unverified_checksum_override,
            "progress": None,
            "backup": None,
            "recovery": [],
            "abort_reason": None,
        }
        journal = cls(root, document, now)
        journal._write()
        return journal

    @classmethod
    def load(cls, directory: Path | str, *, now: Callable[[], datetime] = utc_now) -> FlashJournal:
        """Open the existing journal.

        Raises:
            JournalError: No journal, unreadable JSON or a malformed document.
        """
        path = Path(directory) / JOURNAL_NAME
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise JournalError(f"there is no flash journal in {path.parent}") from exc
        except (OSError, ValueError) as exc:
            raise JournalError(f"the flash journal {path} cannot be read: {exc}") from exc
        return cls(Path(directory), document, now)

    # ------------------------------------------------------------------ access
    @property
    def path(self) -> Path:
        return self._directory / JOURNAL_NAME

    @property
    def state(self) -> JournalState:
        return self._state

    @property
    def transaction_id(self) -> str:
        return str(self._document["transaction_id"])

    @property
    def document(self) -> dict[str, Any]:
        """A copy of the journal document."""
        return json.loads(json.dumps(self._document))

    @property
    def backup_record(self) -> dict[str, Any] | None:
        backup = self._document.get("backup")
        return None if backup is None else dict(backup)

    # ------------------------------------------------------------------ changes
    def _write(self) -> None:
        _atomic_write_json(self.path, self._document)

    def transition(self, new: JournalState, **fields: Any) -> None:
        """Move to ``new`` (an allowed transition only) and persist it with ``fields``.

        The in-memory state changes only after the write succeeded.
        """
        check_journal_transition(self._state, new)
        document = self.document
        document.update(fields)
        document["state"] = str(new)
        document["history"].append({"state": str(new), "at": self._now().isoformat()})
        _validate(document)
        _atomic_write_json(self.path, document)
        self._document, self._state = document, new

    def update(self, **fields: Any) -> None:
        """Persist ``fields`` (e.g. progress) without a state change."""
        document = self.document
        document.update(fields)
        _validate(document)
        _atomic_write_json(self.path, document)
        self._document = document

    def archive(self) -> Path:
        """Move a finished journal to ``journal-archive/<id>.json``.

        Raises:
            JournalError: The transaction is not in a terminal state.
        """
        if self._state not in TERMINAL_JOURNAL_STATES:
            raise JournalError(f"a journal in state {self._state} cannot be archived")
        target = self._directory / ARCHIVE_DIR / f"{self.transaction_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(target, self._document)
        _with_sharing_retry(self.path.unlink)
        return target


# --------------------------------------------------------------------------- #
# start-up inspection
# --------------------------------------------------------------------------- #


class AssessmentStatus(StrEnum):
    """What a journal directory says about the bench ECU."""

    CLEAN = "clean"  # no journal: a new flash may start
    STALE = "stale"  # unfinished but harmless (ECU untouched, or finished but not archived)
    DIRTY = "dirty"  # the ECU memory may be incomplete: recovery needed
    CORRUPT = "corrupt"  # unreadable journal: nothing is known, so everything is blocked


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Deterministic, read-only verdict on a journal directory.

    Attributes:
        status: See :class:`AssessmentStatus`.
        journal_path: The journal file (whether or not it exists).
        state: Journal state, when the journal could be read.
        transaction_id / target_id / image_sha256 / simulated: From the journal.
        backup: The journal's backup record (path, SHA-256, regions, ...), if any.
        detail: Human-readable explanation.
        leftover_temp_file: A ``.tmp`` from an interrupted write exists (harmless).
    """

    status: AssessmentStatus
    journal_path: Path
    state: JournalState | None = None
    transaction_id: str | None = None
    target_id: str | None = None
    image_sha256: str | None = None
    simulated: bool | None = None
    backup: Mapping[str, Any] | None = None
    detail: str = ""
    leftover_temp_file: bool = False

    @property
    def blocks_new_flash(self) -> bool:
        return self.status is not AssessmentStatus.CLEAN

    @property
    def can_recover(self) -> bool:
        return self.status is AssessmentStatus.DIRTY and self.backup is not None

    @property
    def backup_sha256(self) -> str | None:
        return None if self.backup is None else str(self.backup.get("sha256"))


class JournalInspector:
    """Read-only start-up check of a journal directory."""

    @staticmethod
    def inspect(directory: Path | str) -> RecoveryAssessment:
        root = Path(directory)
        path = root / JOURNAL_NAME
        temp = (root / TEMP_NAME).exists()
        if not path.exists():
            return RecoveryAssessment(
                AssessmentStatus.CLEAN, path, detail="no flash journal", leftover_temp_file=temp
            )
        try:
            journal = FlashJournal.load(root)
        except JournalError as exc:
            return RecoveryAssessment(
                AssessmentStatus.CORRUPT,
                path,
                detail=f"{exc}; nothing is known about the ECU - every bench flash is blocked "
                "until the journal is inspected by hand",
                leftover_temp_file=temp,
            )
        state = journal.state
        if state in DIRTY_JOURNAL_STATES:
            status = AssessmentStatus.DIRTY
            detail = f"interrupted in state {state}: the ECU memory may be incomplete"
        elif state in TERMINAL_JOURNAL_STATES:
            status = AssessmentStatus.STALE
            detail = f"finished ({state}) but not archived"
        else:
            status = AssessmentStatus.STALE
            detail = f"stopped in state {state} before any erase: the ECU memory is untouched"
        document = journal.document
        return RecoveryAssessment(
            status,
            path,
            state=state,
            transaction_id=journal.transaction_id,
            target_id=str(document["target_id"]),
            image_sha256=str(document["image_sha256"]),
            simulated=bool(document["simulated"]),
            backup=journal.backup_record,
            detail=detail,
            leftover_temp_file=temp,
        )

    @staticmethod
    def discard_stale(directory: Path | str, *, now: Callable[[], datetime] = utc_now) -> Path:
        """Archive a *stale* journal (untouched ECU, or finished) so a new flash may start.

        Raises:
            JournalError: The journal is dirty or corrupt (those are never discarded).
        """
        assessment = JournalInspector.inspect(directory)
        if assessment.status is not AssessmentStatus.STALE:
            raise JournalError(
                f"only a stale journal can be discarded; this one is {assessment.status}"
            )
        with JournalLock(directory):
            journal = FlashJournal.load(directory, now=now)
            if journal.state not in TERMINAL_JOURNAL_STATES:
                journal.transition(
                    JournalState.ABORTED,
                    abort_reason="discarded after the session stopped before any erase",
                )
            return journal.archive()
