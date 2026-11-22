"""Backup vault (Milestone 7d): the **only** place a :class:`BackupReceipt` is issued.

:meth:`BackupVault.store` is the read-before-write lock of bench flashing:

1. the two independent read-outs of the ECU must be byte-identical (a flaky read is caught
   before it can become the only copy);
2. the image is written **once** (``open(..., "xb")``: an existing file is never
   overwritten), flushed and ``fsync``-ed, next to a JSON manifest (both ``SIMULATED-``
   prefixed for the virtual ECU) and made read-only;
3. both files are read back **from disk** and must carry the same SHA-256;
4. only then is a receipt issued.

:meth:`BackupVault.verify` repeats step 3 right before an erase (so a file changed after the
receipt was issued is caught - check-to-use), :meth:`BackupVault.read` returns verified bytes
for a recovery, and :meth:`BackupVault.receipt_from_record` re-issues the receipt of a backup
recorded in a flash journal after the same verification.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.bench import (
    _MINT,
    BackupReceipt,
    BenchTargetProfile,
    EcuIdentity,
    MemoryRegion,
)
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.models import utc_now

__all__ = ["MANIFEST_KIND", "BackupError", "BackupVault", "receipt_record"]

MANIFEST_KIND = "bench_flash_backup"
#: 2 since Milestone 7e.1 (``unit_fingerprint``); older backups no longer verify (fail closed).
_MANIFEST_SCHEMA = 2


class BackupError(SafetyGuardError):
    """A backup could not be taken or no longer verifies: nothing may be erased."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_once(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:  # never overwrites an existing file
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise BackupError(f"refusing to overwrite an existing backup file {path}") from exc
    with contextlib.suppress(OSError):  # best effort; the hashes are what is relied on
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _manifest_path(image_path: Path) -> Path:
    return image_path.with_name(image_path.stem + ".manifest.json")


def _regions_record(regions: Sequence[MemoryRegion]) -> list[list[int]]:
    return [[r.start_address, r.size, r.image_offset] for r in regions]


def receipt_record(receipt: BackupReceipt) -> dict[str, Any]:
    """The JSON form of a receipt, as the flash journal keeps it."""
    return {
        "path": receipt.path,
        "sha256": receipt.sha256,
        "size": receipt.size,
        "regions": _regions_record(receipt.regions),
        "target_id": receipt.target_id,
        "identity_fingerprint": receipt.identity_fingerprint,
        "unit_fingerprint": receipt.unit_fingerprint,
        "simulated": receipt.simulated,
        "created_at": receipt.created_at.isoformat(),
    }


class BackupVault:
    """Write-once store of verified ECU backups in one directory.

    Args:
        directory: Where backups go (created when missing).
        now: Clock for names and timestamps (aware UTC).
    """

    def __init__(self, directory: Path | str, *, now: Callable[[], datetime] = utc_now) -> None:
        self._directory = Path(directory)
        self._now = now

    @property
    def directory(self) -> Path:
        return self._directory

    # ------------------------------------------------------------------ store
    def store(
        self,
        first: bytes,
        second: bytes,
        *,
        target: BenchTargetProfile,
        identity: EcuIdentity,
        regions: Sequence[MemoryRegion],
        simulated: bool,
    ) -> BackupReceipt:
        """Persist a verified backup and issue its receipt (see the module docstring).

        Raises:
            BackupError: Read-outs differ, size mismatch, the file exists, or the read-back
                from disk does not match.
        """
        if first != second:
            raise BackupError(
                "the two independent read-outs of the ECU differ; refusing to keep an "
                "unreliable backup (nothing was written)"
            )
        expected = sum(region.size for region in regions)
        if len(first) != expected:
            raise BackupError(f"backup has {len(first)} bytes, the regions {expected}")
        unit = target.unit_fingerprint(identity)
        if unit is None:
            raise BackupError(
                "the ECU identity lacks a unit identity DID "
                f"({', '.join(f'{d:04X}' for d in target.unit_identity_dids)}): the backup could "
                "not be bound to this ECU (nothing was written)"
            )
        sha = _sha256(first)
        created = self._now()
        stamp = created.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        prefix = "SIMULATED-" if simulated else ""
        self._directory.mkdir(parents=True, exist_ok=True)
        image_path = self._directory / f"{prefix}backup-{target.target_id}-{stamp}-{sha[:12]}.bin"
        manifest = {
            "kind": MANIFEST_KIND,
            "schema_version": _MANIFEST_SCHEMA,
            "simulated": simulated,
            "target_id": target.target_id,
            "image_file": image_path.name,
            "sha256": sha,
            "size": len(first),
            "regions": _regions_record(regions),
            "identity": {f"{did:04X}": value for did, value in sorted(identity.dids.items())},
            "identity_fingerprint": identity.fingerprint(),
            "unit_fingerprint": unit,
            "unit_identity_dids": [f"{did:04X}" for did in target.unit_identity_dids],
            "created_at": created.isoformat(),
            "note": "SIMULATED backup of the virtual programmable ECU" if simulated else "",
        }
        _write_once(image_path, first)
        _write_once(
            _manifest_path(image_path),
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        receipt = BackupReceipt(
            path=str(image_path),
            sha256=sha,
            size=len(first),
            regions=tuple(regions),
            target_id=target.target_id,
            identity_fingerprint=identity.fingerprint(),
            unit_fingerprint=unit,
            simulated=simulated,
            created_at=created,
            _token=_MINT,
        )
        self.verify(receipt)  # read back from disk before anyone may rely on it
        return receipt

    # ------------------------------------------------------------------ verification
    def verify(self, receipt: BackupReceipt) -> None:
        """Re-read the backup and its manifest from disk; refuse any difference.

        Raises:
            BackupError: Missing or changed file, or a manifest that does not match.
        """
        self.read(receipt)

    def read(self, receipt: BackupReceipt) -> bytes:
        """The backup bytes, verified against the receipt and the manifest."""
        return self._verified_bytes(_record_from_receipt(receipt))

    def _verified_bytes(self, record: Mapping[str, Any]) -> bytes:
        path = Path(str(record["path"]))
        try:
            data = path.read_bytes()
            manifest = json.loads(_manifest_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BackupError(f"backup {path} cannot be read back: {exc}") from exc
        if len(data) != record["size"] or _sha256(data) != record["sha256"]:
            raise BackupError(f"backup {path} changed on disk: refusing to rely on it")
        expected = {
            "kind": MANIFEST_KIND,
            "schema_version": _MANIFEST_SCHEMA,
            "sha256": record["sha256"],
            "size": record["size"],
            "target_id": record["target_id"],
            "identity_fingerprint": record["identity_fingerprint"],
            "unit_fingerprint": record["unit_fingerprint"],
            "simulated": record["simulated"],
            "regions": record["regions"],
        }
        mismatched = [key for key, value in expected.items() if manifest.get(key) != value]
        if mismatched:
            raise BackupError(f"backup manifest of {path} does not match ({', '.join(mismatched)})")
        return data

    def receipt_from_record(self, record: Mapping[str, Any]) -> BackupReceipt:
        """Re-issue the receipt of a journal-recorded backup, after full verification.

        Raises:
            BackupError: The record is incomplete or the files no longer verify.
        """
        try:
            regions = tuple(MemoryRegion(int(s), int(n), int(o)) for s, n, o in record["regions"])
            created = datetime.fromisoformat(str(record["created_at"]))
            normalized = {
                "path": str(record["path"]),
                "sha256": str(record["sha256"]),
                "size": int(record["size"]),
                "regions": _regions_record(regions),
                "target_id": str(record["target_id"]),
                "identity_fingerprint": str(record["identity_fingerprint"]),
                "unit_fingerprint": str(record["unit_fingerprint"]),
                "simulated": bool(record["simulated"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError(f"incomplete backup record in the journal: {exc}") from exc
        self._verified_bytes(normalized)
        return BackupReceipt(
            path=normalized["path"],
            sha256=normalized["sha256"],
            size=normalized["size"],
            regions=regions,
            target_id=normalized["target_id"],
            identity_fingerprint=normalized["identity_fingerprint"],
            unit_fingerprint=normalized["unit_fingerprint"],
            simulated=normalized["simulated"],
            created_at=created,
            _token=_MINT,
        )


def _record_from_receipt(receipt: BackupReceipt) -> dict[str, Any]:
    record = receipt_record(receipt)
    record.pop("created_at")
    return record
