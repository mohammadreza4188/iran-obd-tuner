"""Safe, read-only loading of ECU binary dump files (offline; nothing talks to an ECU).

* Size bounds are fixed in code (:data:`MIN_ROM_SIZE` .. :data:`MAX_ROM_SIZE`); the size is
  checked with ``stat`` before reading and again after, so an oversized file is never read.
* Only raw binary images are accepted; Intel HEX / Motorola S-record files are refused.
* The loaded image is ``bytes`` inside a frozen :class:`RomImage`: it cannot be modified.
  Editing happens on a separate working copy (:mod:`iran_obd_tuner.tuning.workspace`).
* ASCII strings are reported as observed evidence with their offsets. Nothing here guesses an
  ECU, a vehicle or a software version from them.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from iran_obd_tuner.core.calibration import AsciiString, RomSummary, StringScan
from iran_obd_tuner.core.exceptions import RomLoadError
from iran_obd_tuner.core.models import utc_now

__all__ = [
    "MAX_ROM_SIZE",
    "MIN_ROM_SIZE",
    "RomImage",
    "extract_ascii_strings",
    "fill_statistics",
    "load_rom",
    "rom_from_bytes",
]

MIN_ROM_SIZE = 16 * 1024
MAX_ROM_SIZE = 8 * 1024 * 1024

#: Text image formats that need a converter; refused instead of being read as raw bytes.
_TEXT_FORMATS = {".hex", ".ihex", ".ihx", ".s19", ".s28", ".s37", ".srec", ".mot"}
_PRINTABLE_CACHE: dict[int, re.Pattern[bytes]] = {}


def _check_size(size: int, label: str) -> None:
    if size < MIN_ROM_SIZE:
        raise RomLoadError(
            f"{label}: {size} bytes is smaller than the minimum ROM size ({MIN_ROM_SIZE} bytes)"
        )
    if size > MAX_ROM_SIZE:
        raise RomLoadError(
            f"{label}: {size} bytes is larger than the maximum ROM size ({MAX_ROM_SIZE} bytes)"
        )


def _fingerprint(data: bytes) -> tuple[str, int]:
    return hashlib.sha256(data).hexdigest(), zlib.crc32(data) & 0xFFFFFFFF


def _longest_run(data: bytes, byte: int) -> int:
    pattern = re.compile(re.escape(bytes([byte])) + b"+")
    return max((m.end() - m.start() for m in pattern.finditer(data)), default=0)


def fill_statistics(data: bytes) -> tuple[int, int, int, int]:
    """``(zero_bytes, ff_bytes, longest_zero_run, longest_ff_run)``."""
    return data.count(0), data.count(0xFF), _longest_run(data, 0), _longest_run(data, 0xFF)


@dataclass(frozen=True, slots=True)
class RomImage:
    """An immutable ROM image and its fingerprints.

    Attributes:
        data: The bytes exactly as read (immutable).
        name: File name (or a label for generated images).
        source_path: Resolved path of the loaded file, ``None`` for generated images.
        sha256 / crc32: Fingerprints of ``data``.
        source_mtime_ns: Modification time of the file when it was read.
        simulated: ``True`` only for the generated demo image (set by its builder, never
            guessed from the content).
    """

    data: bytes
    name: str
    source_path: Path | None
    sha256: str
    crc32: int
    source_mtime_ns: int | None = None
    simulated: bool = False
    loaded_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("RomImage.data must be immutable bytes")

    @property
    def size(self) -> int:
        return len(self.data)

    def summary(self) -> RomSummary:
        zeros, ffs, zero_run, ff_run = fill_statistics(self.data)
        return RomSummary(
            name=self.name,
            path=None if self.source_path is None else str(self.source_path),
            size=self.size,
            sha256=self.sha256,
            crc32=self.crc32,
            simulated=self.simulated,
            loaded_at=self.loaded_at,
            zero_bytes=zeros,
            ff_bytes=ffs,
            longest_zero_run=zero_run,
            longest_ff_run=ff_run,
        )

    def verify_source_unchanged(self) -> bool:
        """Whether the file on disk still has exactly the loaded content.

        Generated images (no source file) are trivially unchanged.
        """
        if self.source_path is None:
            return True
        try:
            current = self.source_path.read_bytes()
        except OSError:
            return False
        return hashlib.sha256(current).hexdigest() == self.sha256


def rom_from_bytes(data: bytes, *, name: str, simulated: bool = False) -> RomImage:
    """Wrap bytes that did not come from a file (the demo builder, tests)."""
    blob = bytes(data)
    _check_size(len(blob), name)
    sha, crc = _fingerprint(blob)
    return RomImage(
        data=blob, name=name, source_path=None, sha256=sha, crc32=crc, simulated=simulated
    )


def load_rom(path: Path | str) -> RomImage:
    """Read a raw binary ROM image.

    Raises:
        RomLoadError: Missing path, not a regular file, a text image format, outside the
            size bounds, or unreadable.
    """
    source = Path(path)
    label = str(source)
    if source.suffix.lower() in _TEXT_FORMATS:
        raise RomLoadError(
            f"{label}: Intel HEX / S-record files are not supported; convert to a raw .bin first"
        )
    try:
        status = source.stat()
    except FileNotFoundError as exc:
        raise RomLoadError(f"{label}: file not found") from exc
    except OSError as exc:
        raise RomLoadError(f"{label}: cannot be read: {exc}") from exc
    if not source.is_file():
        raise RomLoadError(f"{label}: not a regular file")
    _check_size(status.st_size, label)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise RomLoadError(f"{label}: cannot be read: {exc}") from exc
    _check_size(len(data), label)  # the file may have changed between stat and read
    sha, crc = _fingerprint(data)
    return RomImage(
        data=data,
        name=source.name,
        source_path=source.resolve(),
        sha256=sha,
        crc32=crc,
        source_mtime_ns=status.st_mtime_ns,
    )


def extract_ascii_strings(
    data: bytes, *, min_length: int = 6, max_count: int = 5000, max_length: int = 120
) -> StringScan:
    """Printable ASCII runs (0x20..0x7E) of at least ``min_length`` bytes, with offsets.

    At most ``max_count`` runs are returned (``truncated`` says so); a run longer than
    ``max_length`` is cut and ends with ``...``.
    """
    if min_length < 3:
        raise ValueError("min_length must be at least 3")
    pattern = _PRINTABLE_CACHE.get(min_length)
    if pattern is None:
        pattern = re.compile(rb"[\x20-\x7e]{%d,}" % min_length)
        _PRINTABLE_CACHE[min_length] = pattern
    found: list[AsciiString] = []
    total = 0
    for match in pattern.finditer(data):
        total += 1
        if len(found) < max_count:
            text = match.group().decode("ascii")
            if len(text) > max_length:
                text = text[: max_length - 3] + "..."
            found.append(AsciiString(offset=match.start(), text=text))
    return StringScan(
        strings=tuple(found), total_found=total, truncated=total > len(found), min_length=min_length
    )
