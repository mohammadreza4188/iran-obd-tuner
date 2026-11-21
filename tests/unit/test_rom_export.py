"""Non-destructive export: refusals, atomic write, source untouched, manifest."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iran_obd_tuner.core.exceptions import RomExportError
from iran_obd_tuner.tuning.demo import build_demo_rom, demo_definitions, load_demo
from iran_obd_tuner.tuning.rom_export import (
    MANIFEST_KIND,
    default_export_name,
    export_modified_rom,
    manifest_path_for,
)
from iran_obd_tuner.tuning.rom_loader import load_rom
from iran_obd_tuner.tuning.workspace import RomWorkspace

FUEL = "demo_fuel_base"
FIXED = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "stock.bin"
    path.parent.mkdir()
    path.write_bytes(build_demo_rom())
    return path


@pytest.fixture
def ws(source: Path) -> RomWorkspace:
    workspace = RomWorkspace(load_rom(source), demo_definitions())
    workspace.set_cell(FUEL, 0, 0, 3.0)
    workspace.recalculate_checksums()
    return workspace


def _export(ws: RomWorkspace, target: Path, **kwargs):
    kwargs.setdefault("acknowledged", True)
    return export_modified_rom(ws, target, tool_version="test", clock=lambda: FIXED, **kwargs)


def test_export_writes_the_copy_and_a_manifest(
    ws: RomWorkspace, source: Path, tmp_path: Path
) -> None:
    before = source.read_bytes()
    mtime = source.stat().st_mtime_ns
    result = _export(ws, tmp_path / "out" / "mod.bin")
    out = Path(result.path)
    assert out.read_bytes() == ws.working_bytes()
    assert result.sha256 == hashlib.sha256(out.read_bytes()).hexdigest()
    assert source.read_bytes() == before and source.stat().st_mtime_ns == mtime
    assert not list(out.parent.glob("*.tmp"))
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["kind"] == MANIFEST_KIND
    assert manifest["validated_for_vehicle"] is False
    assert "not supported" in manifest["flashing"]
    assert manifest["source"]["sha256"] == ws.rom.sha256
    assert manifest["output"]["sha256"] == result.sha256
    assert manifest["simulated"] is True  # demo definitions
    assert [e["kind"] for e in manifest["edits"]] == ["set_cells", "patch_bytes"]
    assert manifest["edits"][0]["cells"][0]["new_physical"] == 3.0
    assert {c["status"] for c in manifest["checksums_after"]} == {"valid"}
    assert manifest["created_at"] == FIXED.isoformat()


@pytest.mark.parametrize("variant", ["same", "dotdot", "case", "hardlink", "symlink"])
def test_the_source_file_can_never_be_the_destination(
    ws: RomWorkspace, source: Path, variant: str
) -> None:
    if variant == "same":
        target = source
    elif variant == "dotdot":
        target = source.parent / ".." / source.parent.name / source.name
    elif variant == "case":
        if os.path.normcase("A") != "a":
            pytest.skip("case-insensitive paths only on Windows")
        target = source.with_name(source.name.upper())
    else:
        target = source.parent / f"link_{variant}.bin"
        try:
            if variant == "hardlink":
                os.link(source, target)
            else:
                target.symlink_to(source)
        except (OSError, NotImplementedError):
            pytest.skip(f"{variant}s are not available here")
    before = source.read_bytes()
    with pytest.raises(RomExportError, match="source ROM"):
        _export(ws, target, overwrite=True)
    assert source.read_bytes() == before


def test_the_manifest_can_never_overwrite_the_source(tmp_path: Path) -> None:
    source = tmp_path / "evil.bin.manifest.json"  # a dump that happens to have this name
    source.write_bytes(build_demo_rom())
    ws = RomWorkspace(load_rom(source), demo_definitions())
    ws.set_cell(FUEL, 0, 0, 3.0)
    ws.recalculate_checksums()
    with pytest.raises(RomExportError, match="source ROM"):
        _export(ws, tmp_path / "evil.bin")
    assert source.read_bytes() == build_demo_rom()


def test_refusals(ws: RomWorkspace, source: Path, tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    with pytest.raises(RomExportError, match="acknowledgement"):
        _export(ws, target, acknowledged=False)
    target.write_bytes(b"x")
    with pytest.raises(RomExportError, match="already exists"):
        _export(ws, target)
    _export(ws, target, overwrite=True)  # explicit overwrite of another file is allowed
    with pytest.raises(RomExportError, match="not a regular file"):
        _export(ws, tmp_path, overwrite=True)


def test_invalid_checksum_blocks_export(source: Path, tmp_path: Path) -> None:
    ws = RomWorkspace(load_rom(source), demo_definitions())
    ws.set_cell(FUEL, 0, 0, 3.0)
    with pytest.raises(RomExportError, match="demo_low_sum.*recalculate"):
        _export(ws, tmp_path / "o.bin")
    assert not (tmp_path / "o.bin").exists()


def test_nothing_to_export(source: Path, tmp_path: Path) -> None:
    ws = RomWorkspace(load_rom(source), demo_definitions())
    with pytest.raises(RomExportError, match="nothing to export"):
        _export(ws, tmp_path / "o.bin")


def test_changed_source_blocks_export(ws: RomWorkspace, source: Path, tmp_path: Path) -> None:
    data = bytearray(source.read_bytes())
    data[0x100] ^= 1
    source.write_bytes(bytes(data))
    with pytest.raises(RomExportError, match="changed on disk"):
        _export(ws, tmp_path / "o.bin")


def test_bytes_outside_tables_block_export(ws: RomWorkspace, tmp_path: Path) -> None:
    ws._working[0x5000] ^= 0xFF  # a bug writing outside every table
    with pytest.raises(RomExportError, match="outside every table"):
        _export(ws, tmp_path / "o.bin")


def test_default_names() -> None:
    rom, defs = load_demo()
    assert default_export_name(RomWorkspace(rom, defs)) == "simulated_demo_rom_modified.bin"
    assert manifest_path_for(Path("a/b.bin")) == Path("a/b.bin.manifest.json")
