"""Flash gate (Milestone 7d): the **only** place a :class:`FlashAuthorization` is issued.

:func:`authorize_bench_flash` checks, in this order, and refuses on the first failure:

1. the session policy allows :attr:`~iran_obd_tuner.core.safety.OperationClass.BENCH_FLASH`
   (only :func:`~iran_obd_tuner.core.bench.bench_flash_policy` can make such a policy);
2. the human :class:`~iran_obd_tuner.core.bench.BenchAttestation` is complete;
3. attestation, plan target and link agree that everything is simulated - a real target or
   link raises :class:`~iran_obd_tuner.core.exceptions.BenchTargetNotQualifiedError`;
4. the machine checks (:class:`~iran_obd_tuner.tuning.interfaces.BenchMachineChecks`): the
   backup directory is writable and has room for the backup (twice the image plus a
   margin).

:func:`authorize_bench_recovery` issues the authorisation a recovery needs: bound to the
SHA-256 of the **verified backup** recorded in the journal, never to a new image.

Nothing here writes a file (the directory check uses ``os.access`` and ``disk_usage``).
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from iran_obd_tuner.core.bench import (
    _MINT,
    BenchAttestation,
    BenchTargetProfile,
    FlashAuthorization,
)
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, SafetyGuardError
from iran_obd_tuner.core.models import utc_now
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy, require
from iran_obd_tuner.tuning.flash_plan import FlashPlan
from iran_obd_tuner.tuning.interfaces import BenchMachineChecks

__all__ = [
    "BACKUP_SPACE_MARGIN",
    "authorize_bench_flash",
    "authorize_bench_recovery",
    "machine_checks",
]

#: Extra free space required next to two copies of the image (manifest, journal, slack).
BACKUP_SPACE_MARGIN = 1024 * 1024


def _nearest_existing(directory: Path) -> Path | None:
    for candidate in (directory, *directory.parents):
        if candidate.exists():
            return candidate
    return None


def machine_checks(
    plan: FlashPlan,
    backup_dir: Path | str,
    *,
    disk_usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage,
) -> BenchMachineChecks:
    """What the software can verify before stage 2 (nothing is written)."""
    directory = Path(backup_dir)
    existing = _nearest_existing(directory)
    writable = (
        existing is not None
        and existing.is_dir()
        and os.access(existing, os.W_OK | os.X_OK)
        and (existing == directory or not directory.exists())
    )
    free = disk_usage(existing)[2] if existing is not None and existing.is_dir() else 0
    return BenchMachineChecks(
        backup_dir_writable=writable,
        free_bytes=free,
        required_bytes=2 * len(plan.image) + BACKUP_SPACE_MARGIN,
        image_fits_target=len(plan.image) == plan.target.image_size,
        checksums_valid=plan.checksums_valid,
        target_simulated=plan.target.simulated,
    )


def _mint(
    policy: SafetyPolicy,
    attestation: BenchAttestation,
    target: BenchTargetProfile,
    image_sha256: str,
    *,
    link_simulated: bool,
    now: Callable[[], datetime],
) -> FlashAuthorization:
    require(OperationClass.BENCH_FLASH, policy=policy, detail="bench flash")
    unmet = attestation.unmet()
    if unmet:
        raise SafetyGuardError("the bench attestation is incomplete: " + ", ".join(unmet))
    if not (attestation.simulated and target.simulated and link_simulated):
        raise BenchTargetNotQualifiedError(
            "bench flashing is only possible against the simulated ECU in this build "
            f"(attestation simulated={attestation.simulated}, target simulated="
            f"{target.simulated}, link simulated={link_simulated})"
        )
    return FlashAuthorization(
        policy=policy,
        attestation=attestation,
        target_id=target.target_id,
        image_sha256=image_sha256,
        simulated=True,
        nonce=uuid.uuid4().hex,
        issued_at=now(),
        _token=_MINT,
    )


def authorize_bench_flash(
    policy: SafetyPolicy,
    attestation: BenchAttestation,
    plan: FlashPlan,
    *,
    backup_dir: Path | str,
    link_simulated: bool,
    disk_usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage,
    now: Callable[[], datetime] = utc_now,
) -> FlashAuthorization:
    """Authorise flashing ``plan`` (see the module docstring for the checks).

    Raises:
        SafetyGuardError: Policy, attestation or a machine check refuses.
        BenchTargetNotQualifiedError: Anything about the session is not simulated.
    """
    authorization = _mint(
        policy,
        attestation,
        plan.target,
        plan.image_sha256,
        link_simulated=link_simulated,
        now=now,
    )
    problems = machine_checks(plan, backup_dir, disk_usage=disk_usage).problems()
    if problems:
        raise SafetyGuardError("bench flash machine checks failed: " + "; ".join(problems))
    return authorization


def authorize_bench_recovery(
    policy: SafetyPolicy,
    attestation: BenchAttestation,
    target: BenchTargetProfile,
    backup_sha256: str,
    *,
    link_simulated: bool,
    now: Callable[[], datetime] = utc_now,
) -> FlashAuthorization:
    """Authorise restoring the verified backup ``backup_sha256`` (from the journal).

    A recovery writes no new backup, so no backup directory is checked; the orchestrator
    re-verifies the backup file itself before anything is sent.
    """
    return _mint(policy, attestation, target, backup_sha256, link_simulated=link_simulated, now=now)
