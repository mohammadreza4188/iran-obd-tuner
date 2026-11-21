"""``iran-obd-tuner bench inspect|table|demo`` (offline, in process, no hardware)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iran_obd_tuner.app.cli import main
from iran_obd_tuner.tuning.demo import DEMO_DEFINITIONS_NAME, DEMO_ROM_NAME, build_demo_rom

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def demo_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, Path]:
    assert main(["bench", "demo", "--output", str(tmp_path / "demo")]) == 0
    capsys.readouterr()
    return tmp_path / "demo" / DEMO_ROM_NAME, tmp_path / "demo" / DEMO_DEFINITIONS_NAME


def test_demo_writes_labelled_files_and_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "d"
    assert main(["bench", "demo", "--output", str(folder)]) == 0
    assert "SIMULATED demo ROM" in capsys.readouterr().out
    assert (folder / DEMO_ROM_NAME).read_bytes() == build_demo_rom()
    assert main(["bench", "demo", "--output", str(folder)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_inspect_without_definitions(
    demo_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    rom, _ = demo_files
    assert main(["bench", "inspect", str(rom), "--strings", "2"]) == 0
    out = capsys.readouterr().out
    assert "nothing is read from or written to any ECU" in out
    assert "sha256: 017c76f637fdaab12da54c6ab8ec4b8e" in out
    assert "observed only, no identification" in out
    assert "0x000040  SIMULATED-DEMO-ROM" in out
    assert "checksums: NOT_CONFIGURED" in out
    assert out.isascii()


def test_inspect_with_definitions(
    demo_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    rom, defs = demo_files
    assert main(["bench", "inspect", str(rom), "--maps", str(defs)]) == 0
    out = capsys.readouterr().out
    assert "confidence unverified" in out
    assert "demo_fuel_base" in out and "2..5.5 ms" in out
    assert "demo_low_sum" in out and "VALID" in out and "not proof any ECU accepts it" in out
    # a file on disk is not known to be simulated, so the mismatch is pointed out
    assert "warning: SIMULATED definitions on a non-simulated ROM" in out


def test_inspect_json(demo_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    rom, defs = demo_files
    assert (
        main(["bench", "inspect", str(rom), "--maps", str(defs), "--json", "--strings", "1"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "rom_inspection" and payload["read_only"] is True
    assert payload["rom"]["size"] == 65536
    assert len(payload["strings"]["strings"]) == 1 and payload["strings"]["total_found"] >= 2
    assert payload["compatibility"]["ok"] is True
    assert [c["status"] for c in payload["checksums"]] == ["valid", "valid"]
    assert payload["definitions"]["simulated"] is True


def test_incompatible_definitions_are_reported_with_exit_2(
    demo_files: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, defs = demo_files
    other = tmp_path / "other.bin"
    other.write_bytes(b"\xff" * 65536)
    assert main(["bench", "inspect", str(other), "--maps", str(defs)]) == 2
    out = capsys.readouterr().out
    assert "definitions REFUSED" in out and "SIMULATED-DEMO-ROM" in out
    assert "NOT_CONFIGURED" in out


def test_table(demo_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    rom, defs = demo_files
    assert (
        main(["bench", "table", str(rom), "--maps", str(defs), "--map", "demo_torque_limit"]) == 0
    )
    out = capsys.readouterr().out
    assert "demo_torque_limit" in out and "1000" in out and "155" in out
    assert (
        main(["bench", "table", str(rom), "--maps", str(defs), "--map", "demo_fuel_base", "--json"])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "rom_table" and payload["values"][0][0] == 2.0
    assert payload["y_axis"]["values"][0] == 20


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["bench", "inspect", "missing.bin"], "file not found"),
        (["bench", "inspect", "x.hex"], "convert to a raw .bin"),
        (["bench", "inspect", "{rom}", "--strings", "-1"], "--strings must not be negative"),
        (["bench", "table", "{rom}", "--maps", "{defs}", "--map", "nope"], "no map 'nope'"),
    ],
)
def test_usage_errors(
    demo_files: tuple[Path, Path],
    argv: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rom, defs = demo_files
    argv = [a.format(rom=rom, defs=defs) for a in argv]
    assert main(argv) == 2
    assert message in capsys.readouterr().err


def test_inspect_never_modifies_the_rom(
    demo_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    rom, defs = demo_files
    before = rom.read_bytes()
    mtime = rom.stat().st_mtime_ns
    main(["bench", "inspect", str(rom), "--maps", str(defs)])
    capsys.readouterr()
    assert rom.read_bytes() == before and rom.stat().st_mtime_ns == mtime
