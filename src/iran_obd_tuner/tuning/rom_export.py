"""Non-destructive export of a modified ROM working copy to a *file* (never to an ECU).

This is the only module in ``tuning`` that writes files (enforced by
``tests/unit/test_architecture.py``). An export is refused when:

* the destination is the loaded source file (any spelling, symlink or hardlink),
* the destination exists and ``overwrite`` was not given,
* the user did not acknowledge that the file is not validated for any ECU,
* nothing was changed,
* a configured checksum is invalid (recalculate first),
* a byte changed outside every table and checksum field,
* the source file changed on disk since it was loaded.

The image is written to a temporary file next to the destination and moved into place, then
read back and hashed. A ``<name>.manifest.json`` records the source and output fingerprints,
every edit, the checksum status and ``validated_for_vehicle: false``. This project has no way
to flash a file to an ECU; the manifest says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from iran_obd_tuner.core.calibration import ChecksumStatus, ExportResult
from iran_obd_tuner.core.exceptions import RomExportError, TableEditError
from iran_obd_tuner.core.models import utc_now
from iran_obd_tuner.tuning.workspace import RomWorkspace

__all__ = ["MANIFEST_KIND", "default_export_name", "export_modified_rom", "manifest_path_for"]

MANIFEST_KIND = "rom_export_manifest"
MANIFEST_SCHEMA_VERSION = 1


def default_export_name(workspace: RomWorkspace) -> str:
    """``<stem>_modified.bin`` (``_modified_simulated.bin`` for the simulated demo)."""
    stem = Path(workspace.rom.name).stem
    suffix = (
        "_modified_simulated"
        if workspace.rom.simulated and "simulated" not in stem
        else "_modified"
    )
    return f"{stem}{suffix}.bin"


def manifest_path_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".manifest.json")


def _same_file(a: Path, b: Path) -> bool:
    if os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve())):
        return True
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _atomic_write(target: Path, payload: bytes) -> None:
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed explicitly below
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def export_modified_rom(
    workspace: RomWorkspace,
    destination: Path | str,
    *,
    acknowledged: bool,
    tool_version: str,
    overwrite: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> ExportResult:
    """Write the working copy to ``destination`` (see the module docstring).

    Raises:
        RomExportError: Any refusal or I/O failure. The source file is never written.
    """
    target = Path(destination)
    rom = workspace.rom
    manifest_target = manifest_path_for(target)
    if rom.source_path is not None:
        for candidate in (target, manifest_target):
            if _same_file(candidate, rom.source_path):
                raise RomExportError(
                    "refusing to overwrite the loaded source ROM; choose another destination"
                )
    if not acknowledged:
        raise RomExportError(
            "export needs the acknowledgement that the file is not validated for any ECU"
        )
    if not workspace.is_modified:
        raise RomExportError("nothing to export: the working copy equals the original ROM")
    if target.exists() and not overwrite:
        raise RomExportError(f"{target} already exists")
    if target.exists() and not target.is_file():
        raise RomExportError(f"{target} is not a regular file")
    try:
        workspace.assert_edits_are_confined()  # a stray byte is a bug: report it first
    except TableEditError as exc:
        raise RomExportError(str(exc)) from exc
    checksums = workspace.checksums()
    invalid = [c.block_id for c in checksums if c.status is ChecksumStatus.INVALID]
    if invalid:
        raise RomExportError(
            f"checksum(s) {', '.join(str(i) for i in invalid)} are invalid; recalculate them first"
        )
    if not rom.verify_source_unchanged():
        raise RomExportError(f"{rom.source_path} changed on disk since it was loaded; reload it")

    payload = workspace.working_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    definitions = workspace.definitions
    manifest = {
        "kind": MANIFEST_KIND,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": clock().isoformat(),
        "tool_version": tool_version,
        "validated_for_vehicle": False,
        "flashing": "not supported by this project; this file was produced by offline analysis",
        "acknowledged": True,
        "simulated": rom.simulated or bool(definitions and definitions.simulated),
        "source": {
            "name": rom.name,
            "size": rom.size,
            "sha256": rom.sha256,
            "crc32": f"{rom.crc32:08X}",
        },
        "output": {"name": target.name, "size": len(payload), "sha256": sha, "crc32": f"{crc:08X}"},
        "definitions": None if definitions is None else definitions.summary().as_dict(),
        "checksums_before": [c.as_dict() for c in workspace.original_checksums()],
        "checksums_after": [c.as_dict() for c in checksums],
        "changed_ranges": [[f"0x{a:06X}", f"0x{b:06X}"] for a, b in workspace.changed_ranges()],
        "edits": list(workspace.history()),
        "dropped_history_steps": workspace.dropped_history,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, payload)
        written = target.read_bytes()
        if hashlib.sha256(written).hexdigest() != sha:
            raise RomExportError(f"{target}: read-back verification failed")
        _atomic_write(
            manifest_target,
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    except OSError as exc:
        raise RomExportError(f"could not write {target}: {exc}") from exc
    if not rom.verify_source_unchanged():  # pragma: no cover - would be a bug above
        raise RomExportError("the source ROM changed during export")
    return ExportResult(
        path=str(target),
        sha256=sha,
        crc32=crc,
        manifest_path=str(manifest_target),
        checksums=checksums,
        simulated=bool(manifest["simulated"]),
    )
