"""ROM loader: size bounds, formats, fingerprints, immutability, strings, fill statistics."""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path

import pytest

from iran_obd_tuner.core.exceptions import RomLoadError
from iran_obd_tuner.tuning.rom_loader import (
    MAX_ROM_SIZE,
    MIN_ROM_SIZE,
    extract_ascii_strings,
    fill_statistics,
    load_rom,
    rom_from_bytes,
)


def _write(tmp_path: Path, size: int, name: str = "dump.bin", fill: int = 0xFF) -> Path:
    path = tmp_path / name
    path.write_bytes(bytes([fill]) * size)
    return path


@pytest.mark.parametrize("size", [MIN_ROM_SIZE, MAX_ROM_SIZE])
def test_sizes_at_the_bounds_are_accepted(tmp_path: Path, size: int) -> None:
    rom = load_rom(_write(tmp_path, size))
    assert rom.size == size


@pytest.mark.parametrize(
    ("size", "message"),
    [(0, "smaller"), (MIN_ROM_SIZE - 1, "smaller"), (MAX_ROM_SIZE + 1, "larger")],
)
def test_sizes_outside_the_bounds_are_refused(tmp_path: Path, size: int, message: str) -> None:
    with pytest.raises(RomLoadError, match=message):
        load_rom(_write(tmp_path, size))


def test_missing_paths_and_directories_are_refused(tmp_path: Path) -> None:
    with pytest.raises(RomLoadError, match="not found"):
        load_rom(tmp_path / "nope.bin")
    with pytest.raises(RomLoadError, match="not a regular file"):
        load_rom(tmp_path)


@pytest.mark.parametrize("suffix", [".hex", ".HEX", ".s19", ".srec", ".mot"])
def test_text_image_formats_are_refused(tmp_path: Path, suffix: str) -> None:
    path = _write(tmp_path, MIN_ROM_SIZE, name=f"dump{suffix}")
    with pytest.raises(RomLoadError, match="convert to a raw .bin"):
        load_rom(path)


def test_fingerprints_and_immutability(tmp_path: Path) -> None:
    data = bytes(range(256)) * (MIN_ROM_SIZE // 256)
    path = tmp_path / "x.bin"
    path.write_bytes(data)
    rom = load_rom(path)
    assert rom.sha256 == hashlib.sha256(data).hexdigest()
    assert rom.crc32 == zlib.crc32(data)
    assert isinstance(rom.data, bytes)
    assert rom.source_path == path.resolve()
    assert rom.simulated is False  # never guessed from the content
    with pytest.raises(TypeError):
        rom.data[0] = 1  # type: ignore[index]
    with pytest.raises(AttributeError):
        rom.data = b""  # type: ignore[misc]
    assert rom.verify_source_unchanged()
    path.write_bytes(b"\x07" + data[1:])  # byte 0 was 0x00
    assert not rom.verify_source_unchanged()
    path.unlink()
    assert not rom.verify_source_unchanged()


def test_generated_images_need_bytes_and_respect_the_bounds() -> None:
    rom = rom_from_bytes(bytearray(MIN_ROM_SIZE), name="gen.bin", simulated=True)
    assert isinstance(rom.data, bytes) and rom.simulated and rom.source_path is None
    assert rom.verify_source_unchanged()
    with pytest.raises(RomLoadError):
        rom_from_bytes(b"\x00" * 10, name="tiny")


def test_summary_reports_fill_statistics() -> None:
    data = bytearray(b"\xff" * MIN_ROM_SIZE)
    data[100:150] = bytes(50)
    data[200:210] = bytes(10)
    rom = rom_from_bytes(data, name="f.bin")
    summary = rom.summary()
    assert summary.zero_bytes == 60 and summary.longest_zero_run == 50
    assert summary.ff_bytes == MIN_ROM_SIZE - 60
    assert summary.as_dict()["crc32"] == f"{rom.crc32:08X}"
    assert fill_statistics(b"\x01\x02") == (0, 0, 0, 0)


def test_ascii_strings_are_observed_with_offsets() -> None:
    data = b"\x00" * 16 + b"ABC" + b"\x00" + b"SW-1234-XYZ" + b"\x01" + b"Z" * 200 + b"\xff\xfe"
    scan = extract_ascii_strings(data, min_length=6, max_length=40)
    assert [(s.offset, s.text) for s in scan.strings][:1] == [(20, "SW-1234-XYZ")]
    assert scan.strings[1].text.endswith("...") and len(scan.strings[1].text) == 40
    assert scan.total_found == 2 and not scan.truncated
    assert "nothing is identified" in scan.as_dict()["note"]


def test_string_scan_is_capped() -> None:
    data = b"\x00".join([b"ABCDEFG"] * 50)
    scan = extract_ascii_strings(data, max_count=10)
    assert len(scan.strings) == 10 and scan.total_found == 50 and scan.truncated
    with pytest.raises(ValueError):
        extract_ascii_strings(data, min_length=2)
