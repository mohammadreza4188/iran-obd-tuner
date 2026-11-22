"""Simulated bench flash wiring (Milestone 7e): the **only** place tuning meets hardware.

:class:`BenchFlashService` is the Qt-free facade the CLI (``bench flash-sim`` /
``bench journal``) and, from Milestone 7f, the dashboard use. It wires

* ``hardware.bench_link`` - :class:`BenchLink` on ``virtual-sim-programmable`` (the
  programmer plus the virtual programmable ECU on python-can's in-process ``virtual`` bus);
* ``tuning`` - :func:`build_flash_plan`, :func:`authorize_bench_flash` /
  :func:`authorize_bench_recovery`, :class:`BackupVault`, the flash journal and
  :class:`BenchFlashOrchestrator`;

and returns plain :mod:`iran_obd_tuner.core.bench_reports` objects (``ui`` may not import
``tuning``). An architecture test keeps this module the single importer of both sides.

**Simulated only.** :class:`BenchLinkConfig` is always built with its defaults (no interface,
no channel: an architecture test checks the call), the attestation must be simulated, and
the gate refuses anything that is not. There is no parameter through which a real interface,
port or ECU could be named.

The bench output directory holds everything of one simulated bench::

    DIR/flash-journal.json            journal of the running transaction (absent when clean)
    DIR/journal-archive/<id>.json     finished transactions
    DIR/backups/SIMULATED-backup-*    verified backups (+ manifests), write-once
    DIR/SIMULATED-bench-ecu-state.json  persistent state of the virtual ECU

The simulated ECU's persistent state (flash, validity record, failed key counter) is saved
after every run, so a later process - a recovery after an injected power loss, say - meets
the same half-written flash. A new process is a power cycle of the simulated bench ECU.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BenchAttestation,
    FlashProgress,
    FlashStage,
    ProgressSink,
    bench_flash_policy,
)
from iran_obd_tuner.core.bench_reports import (
    BenchFlashReport,
    BenchJournalReport,
    BenchJournalStatus,
    SimulatedFault,
    SimulatedFaultKind,
)
from iran_obd_tuner.core.exceptions import (
    BenchTargetNotQualifiedError,
    ConfigurationError,
    IranObdTunerError,
)
from iran_obd_tuner.core.models import utc_now
from iran_obd_tuner.hardware.bench_link import (
    BenchLink,
    BenchLinkConfig,
    BenchTiming,
    FaultPlan,
    SimulatedEcuState,
    simulated_bench_ecu,
    simulated_seed_key,
)
from iran_obd_tuner.tuning.backup_vault import BackupVault
from iran_obd_tuner.tuning.checksum import ChecksumBlock
from iran_obd_tuner.tuning.demo import load_demo
from iran_obd_tuner.tuning.flash_gate import authorize_bench_flash, authorize_bench_recovery
from iran_obd_tuner.tuning.flash_journal import (
    AssessmentStatus,
    FlashJournal,
    JournalError,
    JournalInspector,
    RecoveryAssessment,
)
from iran_obd_tuner.tuning.flash_orchestrator import (
    BenchFlashOrchestrator,
    FlashBlockedError,
    FlashOutcome,
)
from iran_obd_tuner.tuning.flash_plan import FlashPlanError, build_flash_plan
from iran_obd_tuner.tuning.map_definitions import load_definitions
from iran_obd_tuner.tuning.rom_loader import RomImage, load_rom

__all__ = [
    "BACKUP_DIR_NAME",
    "ECU_STATE_NAME",
    "SIMULATED_BLOCK_LENGTH",
    "SIMULATED_OPERATOR",
    "BenchFlashService",
    "FlashBlockedError",
    "FlashPlanError",
    "SimulatedBenchStateError",
    "SimulatedEcuStore",
    "journal_report",
    "simulated_attestation",
]

BACKUP_DIR_NAME = "backups"
ECU_STATE_NAME = "SIMULATED-bench-ecu-state.json"
_ECU_STATE_KIND = "simulated_bench_ecu_state"
_ECU_STATE_SCHEMA = 1
#: ``maxNumberOfBlockLength`` of the CLI's simulated ECU: 1024 data bytes per block, so the
#: 64 KiB image is 64 download blocks (``--fault ...-at-block=N`` takes N = 1..64).
SIMULATED_BLOCK_LENGTH = 1026
#: Who the journal names as operator of a CLI simulation.
SIMULATED_OPERATOR = "iran-obd-tuner CLI (simulated session)"
#: The virtual bus answers within milliseconds; a lost answer is detected after this.
_SIMULATED_P2_S = 0.3
#: Block whose stage-4 read-back the ``corrupt-readback`` fault corrupts.
_CORRUPT_READBACK_BLOCK = 1


class SimulatedBenchStateError(IranObdTunerError):
    """The saved state of the simulated bench ECU is unreadable (fail closed)."""


def simulated_attestation(
    *, operator: str = SIMULATED_OPERATOR, now: Callable[[], datetime] = utc_now
) -> BenchAttestation:
    """The attestation of a simulated session against the virtual programmable ECU.

    Its statements describe the *virtual* bench (the ECU is the in-process simulator, no
    vehicle and no supply are involved) and ``simulated=True``: the gate accepts it for the
    simulator only and refuses it for anything real. Decision D7 (owner approval of
    ``bench flash-sim``) is the ``owner_approval_recorded`` statement.
    """
    return BenchAttestation(
        ecu_on_bench_out_of_vehicle=True,
        supply_stable_and_current_limited=True,
        wiring_verified=True,
        owner_approval_recorded=True,
        operator=operator,
        confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
        attested_at=now(),
        simulated=True,
    )


# --------------------------------------------------------------------------- #
# the simulated bench ECU's persistent state
# --------------------------------------------------------------------------- #


def _replace_with_retry(source: Path, target: Path) -> None:
    """``os.replace`` with the bounded retry of ADR 68 (Windows sharing refusals)."""
    for attempt in range(20):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


class SimulatedEcuStore:
    """Saves and loads the virtual ECU's persistent state in a bench directory."""

    def __init__(self, directory: Path | str) -> None:
        self._path = Path(directory) / ECU_STATE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SimulatedEcuState | None:
        """The saved state, or ``None`` for a factory-fresh simulated ECU (no file).

        Raises:
            SimulatedBenchStateError: The file exists but cannot be trusted.
        """
        if not self._path.exists():
            return None
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            if (
                document.get("kind") != _ECU_STATE_KIND
                or document.get("schema_version") != _ECU_STATE_SCHEMA
                or document.get("simulated") is not True
                or document.get("target_id") != SIMULATED_BENCH_TARGET.target_id
            ):
                raise ValueError("unknown kind, schema, target or simulation flag")
            flash = base64.b64decode(document["flash_base64"], validate=True)
            if hashlib.sha256(flash).hexdigest() != document["flash_sha256"]:
                raise ValueError("the flash content does not match its SHA-256")
            valid_crc = document["valid_crc"]
            return SimulatedEcuState(
                flash=flash,
                valid_crc=None if valid_crc is None else int(valid_crc),
                failed_key_attempts=int(document["failed_key_attempts"]),
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError, binascii.Error) as exc:
            raise SimulatedBenchStateError(
                f"the simulated bench ECU state {self._path} cannot be used ({exc}); delete it "
                "to start again from a factory-fresh simulated ECU"
            ) from exc

    def save(self, state: SimulatedEcuState) -> None:
        """Write the state atomically (temporary file, fsync, replace)."""
        document = {
            "kind": _ECU_STATE_KIND,
            "schema_version": _ECU_STATE_SCHEMA,
            "simulated": True,
            "note": "SIMULATED state of the virtual programmable ECU - not a real ECU",
            "target_id": SIMULATED_BENCH_TARGET.target_id,
            "flash_sha256": hashlib.sha256(state.flash).hexdigest(),
            "flash_base64": base64.b64encode(state.flash).decode("ascii"),
            "valid_crc": state.valid_crc,
            "failed_key_attempts": state.failed_key_attempts,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        with temp.open("wb") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp, self._path)


# --------------------------------------------------------------------------- #
# translation into the shared reports
# --------------------------------------------------------------------------- #


def journal_report(directory: Path | str) -> BenchJournalReport:
    """Inspect a bench directory (read-only) and describe it as a :class:`BenchJournalReport`."""
    root = Path(directory)
    assessment: RecoveryAssessment = JournalInspector.inspect(root)
    document: dict[str, Any] = {}
    if assessment.status in (AssessmentStatus.STALE, AssessmentStatus.DIRTY):
        with contextlib.suppress(JournalError):  # read-only; the assessment stays the verdict
            document = FlashJournal.load(root).document
    backup = assessment.backup or {}
    history = tuple(
        (str(entry.get("state")), str(entry.get("at")))
        for entry in document.get("history") or []
        if isinstance(entry, dict)
    )
    progress = document.get("progress")
    return BenchJournalReport(
        status=BenchJournalStatus(str(assessment.status)),
        directory=str(root),
        journal_path=str(assessment.journal_path),
        detail=assessment.detail,
        state=assessment.state,
        transaction_id=assessment.transaction_id,
        target_id=assessment.target_id,
        image_sha256=assessment.image_sha256,
        image_name=document.get("image_name"),
        simulated=assessment.simulated,
        backup_path=None if not backup else str(backup.get("path")),
        backup_sha256=assessment.backup_sha256,
        progress=progress if isinstance(progress, dict) else None,
        abort_reason=document.get("abort_reason"),
        history=history,
        leftover_temp_file=assessment.leftover_temp_file,
    )


def _flash_report(
    outcome: FlashOutcome,
    directory: Path,
    *,
    image_name: str,
    image_sha256: str,
    image_simulated: bool,
    override: bool,
    fault: SimulatedFault | None,
    fault_fired: bool | None,
) -> BenchFlashReport:
    backup = outcome.backup
    identity = outcome.identity_after
    return BenchFlashReport(
        mode=outcome.mode,
        state=outcome.state,
        transaction_id=outcome.transaction_id,
        directory=str(directory),
        simulated=outcome.simulated,
        image_name=image_name,
        image_sha256=image_sha256,
        image_simulated=image_simulated,
        unverified_checksum_override=override,
        backup_path=None if backup is None else backup.path,
        backup_sha256=None if backup is None else backup.sha256,
        backup_size=None if backup is None else backup.size,
        archive_path=None if outcome.archive_path is None else str(outcome.archive_path),
        identity_after={}
        if identity is None
        else {f"{did:04X}": value for did, value in sorted(identity.dids.items())},
        error=outcome.error,
        states=outcome.states,
        fault=fault,
        fault_fired=fault_fired,
    )


# --------------------------------------------------------------------------- #
# the facade
# --------------------------------------------------------------------------- #


class BenchFlashService:
    """Simulated bench flashing for the CLI and the dashboard (see the module docstring).

    Args:
        block_length: ``maxNumberOfBlockLength`` of the simulated ECU.
        timing: Bench request timing (default: a short P2, the virtual bus is in-process).
        progress_every_blocks: Journal progress is saved every that many transfer blocks.
        now: Clock for attestations, journal and backup timestamps (aware UTC).
    """

    def __init__(
        self,
        *,
        block_length: int = SIMULATED_BLOCK_LENGTH,
        timing: BenchTiming | None = None,
        progress_every_blocks: int = 8,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._block_length = block_length
        self._timing = timing or BenchTiming(p2_s=_SIMULATED_P2_S)
        self._every = progress_every_blocks
        self._now = now

    # ------------------------------------------------------------------ helpers
    @property
    def blocks_per_transfer(self) -> int:
        """Download blocks of the largest region (the range of ``...-at-block=N``)."""
        chunk = self._block_length - 2
        return max(math.ceil(region.size / chunk) for region in SIMULATED_BENCH_TARGET.regions)

    def _orchestrator(self, directory: Path) -> BenchFlashOrchestrator:
        vault = BackupVault(directory / BACKUP_DIR_NAME, now=self._now)
        return BenchFlashOrchestrator(
            directory, vault, progress_every_blocks=self._every, now=self._now
        )

    def _attestation(self, attestation: BenchAttestation | None) -> BenchAttestation:
        chosen = attestation or simulated_attestation(now=self._now)
        if not chosen.simulated:
            raise BenchTargetNotQualifiedError(
                "only simulated bench sessions exist in this build (the attestation is not "
                "simulated)"
            )
        return chosen

    def _fault_plans(self, fault: SimulatedFault | None) -> tuple[FaultPlan, FaultPlan | None]:
        """The plan armed from the start, and one armed when stage 4 begins (or ``None``)."""
        if fault is None:
            return FaultPlan(), None
        if fault.block is not None and fault.block > self.blocks_per_transfer:
            raise ConfigurationError(
                f"fault {fault.describe()}: the simulated transfer has only "
                f"{self.blocks_per_transfer} blocks (N = 1..{self.blocks_per_transfer})"
            )
        kind = fault.kind
        if kind is SimulatedFaultKind.DROP_AT_BLOCK:
            return FaultPlan(drop_link_at_block=fault.block), None
        if kind is SimulatedFaultKind.NRC72_AT_BLOCK:
            assert fault.block is not None
            return FaultPlan(nrc_at_block=(fault.block, 0x72)), None
        if kind is SimulatedFaultKind.POWER_LOSS_AT_BLOCK:
            return FaultPlan(power_loss_at_block=fault.block), None
        if kind is SimulatedFaultKind.BAD_KEY:
            return FaultPlan(bad_key=True), None
        # corrupt-readback: armed only for the stage-4 read-back; armed from the start it
        # would hit the backup read-outs (which then differ and abort before any erase)
        return FaultPlan(), FaultPlan(corrupt_readback_at_block=_CORRUPT_READBACK_BLOCK)

    @staticmethod
    def _load_image(
        rom_path: Path | str | None, maps_path: Path | str | None
    ) -> tuple[RomImage, tuple[ChecksumBlock, ...]]:
        if rom_path is None:
            if maps_path is not None:
                raise ConfigurationError("--maps needs --rom (the demo ROM has its own maps)")
            rom, definitions = load_demo()
            return rom, tuple(definitions.checksums)
        rom = load_rom(rom_path)
        blocks = () if maps_path is None else tuple(load_definitions(maps_path).checksums)
        return rom, blocks

    # ------------------------------------------------------------------ queries
    def assess_journal(self, output_dir: Path | str) -> BenchJournalReport:
        """``clean`` / ``stale`` / ``dirty`` / ``corrupt`` verdict on a bench directory."""
        return journal_report(output_dir)

    # ------------------------------------------------------------------ flash
    def run_simulated_flash(
        self,
        output_dir: Path | str,
        *,
        rom_path: Path | str | None = None,
        maps_path: Path | str | None = None,
        fault: SimulatedFault | None = None,
        allow_unverified_checksum_in_simulation: bool = False,
        attestation: BenchAttestation | None = None,
        progress: ProgressSink | None = None,
    ) -> BenchFlashReport:
        """Run the four stages against the simulated bench ECU of ``output_dir``.

        Without ``rom_path`` the SIMULATED demo ROM and its map definitions are flashed.

        Raises:
            ConfigurationError / RomError: Unusable arguments or input files.
            FlashPlanError: The image does not fit, or a checksum is not ``valid`` (without
                the simulated-only override).
            FlashBlockedError / SafetyGuardError: The journal or the gate refuses - before
                anything is sent. Failures after the journal started are returned.
        """
        directory = Path(output_dir)
        rom, blocks = self._load_image(rom_path, maps_path)
        plan = build_flash_plan(
            rom,
            SIMULATED_BENCH_TARGET,
            checksum_blocks=blocks,
            allow_unverified_checksum_in_simulation=allow_unverified_checksum_in_simulation,
        )
        chosen = self._attestation(attestation)
        policy = bench_flash_policy(chosen)
        journal = self.assess_journal(directory)
        if journal.blocks_new_flash:
            raise FlashBlockedError(f"the flash journal is {journal.status}: {journal.detail}")
        initial, deferred = self._fault_plans(fault)
        store = SimulatedEcuStore(directory)
        ecu = simulated_bench_ecu(
            state=store.load(), faults=initial, max_block_length=self._block_length
        )
        link = BenchLink(BenchLinkConfig(), ecu=ecu, timing=self._timing)
        authorization = authorize_bench_flash(
            policy,
            chosen,
            plan,
            backup_dir=directory / BACKUP_DIR_NAME,
            link_simulated=link.simulated,
            now=self._now,
        )
        armed = False

        def sink(report: FlashProgress) -> None:
            nonlocal armed
            if deferred is not None and not armed and report.stage is FlashStage.VERIFY:
                ecu.faults = deferred  # the next upload is the stage-4 read-back
                armed = True
            if progress is not None:
                progress(report)

        opened = False
        try:
            with link:
                opened = True
                outcome = self._orchestrator(directory).flash(
                    link.programmer, plan, authorization, simulated_seed_key(), progress=sink
                )
        finally:
            if opened:  # also after Ctrl+C: the next run must meet the same flash
                store.save(ecu.persistent_state())
        fired = None if fault is None else bool((deferred or initial).fired)
        return _flash_report(
            outcome,
            directory,
            image_name=plan.image_name,
            image_sha256=plan.image_sha256,
            image_simulated=plan.image_simulated,
            override=plan.unverified_checksum_override,
            fault=fault,
            fault_fired=fired,
        )

    # ------------------------------------------------------------------ recovery
    def run_simulated_recovery(
        self,
        output_dir: Path | str,
        *,
        attestation: BenchAttestation | None = None,
        progress: ProgressSink | None = None,
    ) -> BenchFlashReport:
        """Restore the verified backup recorded in the dirty journal of ``output_dir``.

        Raises:
            FlashBlockedError / SafetyGuardError: Nothing to recover (clean, stale, corrupt),
                no backup record, or a backup that no longer verifies - before anything is
                sent. A failed recovery is returned (``recovery_failed``, retry allowed).
        """
        directory = Path(output_dir)
        journal = self.assess_journal(directory)
        if not journal.can_recover or journal.backup_sha256 is None:
            raise FlashBlockedError(
                f"nothing to recover: the flash journal is {journal.status} ({journal.detail})"
            )
        chosen = self._attestation(attestation)
        policy = bench_flash_policy(chosen)
        store = SimulatedEcuStore(directory)
        ecu = simulated_bench_ecu(state=store.load(), max_block_length=self._block_length)
        link = BenchLink(BenchLinkConfig(), ecu=ecu, timing=self._timing)
        authorization = authorize_bench_recovery(
            policy,
            chosen,
            link.target,
            journal.backup_sha256,
            link_simulated=link.simulated,
            now=self._now,
        )
        opened = False
        try:
            with link:
                opened = True
                outcome = self._orchestrator(directory).recover(
                    link.programmer, authorization, simulated_seed_key(), progress=progress
                )
        finally:
            if opened:
                store.save(ecu.persistent_state())
        return _flash_report(
            outcome,
            directory,
            image_name=Path(journal.backup_path or "backup").name,
            image_sha256=journal.backup_sha256,
            image_simulated=False,
            override=False,
            fault=None,
            fault_fired=None,
        )

    # ------------------------------------------------------------------ stale journals
    def discard_stale_journal(self, output_dir: Path | str) -> str:
        """Archive a *stale* journal (operator acknowledgement); returns the archive path.

        Raises:
            JournalError: The journal is clean, dirty or corrupt (never discarded).
        """
        return str(JournalInspector.discard_stale(output_dir, now=self._now))
