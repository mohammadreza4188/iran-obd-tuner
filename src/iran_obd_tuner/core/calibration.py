"""Shared models of offline ROM analysis (Phase 4): data types, checksum ids, snapshots.

The engine lives in :mod:`iran_obd_tuner.tuning`; the CLI and the dashboard only receive the
frozen snapshots defined here (the UI may not import ``tuning``). Everything is standard
library only (``core`` rule).

Nothing here talks to an ECU. A checksum status is always relative to the *configured*
algorithm: ``valid`` never means that any ECU would accept an image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from iran_obd_tuner.core.knowledge import ConfidenceLevel, LocalizedText, Source

__all__ = [
    "AsciiString",
    "AxisSnapshot",
    "ChecksumAlgorithm",
    "ChecksumResult",
    "ChecksumStatus",
    "CompatibilityReport",
    "DataType",
    "DefinitionsSummary",
    "EditOutcome",
    "ExportResult",
    "MapCategory",
    "MapSummary",
    "RomSummary",
    "StringScan",
    "TableSnapshot",
    "TableViewMode",
]


class DataType(StrEnum):
    """Element type of a calibration table cell or axis value."""

    UINT8 = "uint8"
    INT8 = "int8"
    UINT16_LE = "uint16_le"
    UINT16_BE = "uint16_be"
    INT16_LE = "int16_le"
    INT16_BE = "int16_be"

    @property
    def size(self) -> int:
        return 1 if self in (DataType.UINT8, DataType.INT8) else 2

    @property
    def signed(self) -> bool:
        return self in (DataType.INT8, DataType.INT16_LE, DataType.INT16_BE)

    @property
    def byteorder(self) -> str:
        return "big" if self.value.endswith("_be") else "little"

    @property
    def min_raw(self) -> int:
        return -(1 << (8 * self.size - 1)) if self.signed else 0

    @property
    def max_raw(self) -> int:
        bits = 8 * self.size
        return (1 << (bits - 1)) - 1 if self.signed else (1 << bits) - 1


class MapCategory(StrEnum):
    FUEL = "fuel"
    IGNITION = "ignition"
    TORQUE = "torque"
    LIMITER = "limiter"
    OTHER = "other"


class ChecksumAlgorithm(StrEnum):
    """Checksum algorithms with exact definitions (see :mod:`iran_obd_tuner.tuning.checksum`)."""

    SUM8 = "sum8"
    SUM16_LE = "sum16_le"
    SUM16_BE = "sum16_be"
    CRC16_CCITT_FALSE = "crc16_ccitt_false"
    CRC32 = "crc32"
    TWOS_COMPLEMENT_8 = "twos_complement_8"
    TWOS_COMPLEMENT_16_LE = "twos_complement_16_le"


class ChecksumStatus(StrEnum):
    VALID = "valid"  # stored value equals the value computed with the configured algorithm
    INVALID = "invalid"
    NOT_CONFIGURED = "not_configured"  # the definitions declare no checksum block


class TableViewMode(StrEnum):
    ORIGINAL = "original"
    MODIFIED = "modified"
    DELTA = "delta"
    DELTA_PERCENT = "delta_percent"


@dataclass(frozen=True, slots=True)
class RomSummary:
    """Metadata of a loaded ROM image (the image itself never changes)."""

    name: str
    path: str | None
    size: int
    sha256: str
    crc32: int
    simulated: bool
    loaded_at: datetime
    zero_bytes: int
    ff_bytes: int
    longest_zero_run: int
    longest_ff_run: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "crc32": f"{self.crc32:08X}",
            "simulated": self.simulated,
            "loaded_at": self.loaded_at.isoformat(),
            "fill": {
                "zero_bytes": self.zero_bytes,
                "ff_bytes": self.ff_bytes,
                "longest_zero_run": self.longest_zero_run,
                "longest_ff_run": self.longest_ff_run,
            },
        }


@dataclass(frozen=True, slots=True)
class AsciiString:
    """A printable ASCII run observed in the image (evidence only, no identification)."""

    offset: int
    text: str


@dataclass(frozen=True, slots=True)
class StringScan:
    strings: tuple[AsciiString, ...]
    total_found: int
    truncated: bool
    min_length: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "note": "observed ASCII runs only; nothing is identified from them",
            "min_length": self.min_length,
            "total_found": self.total_found,
            "truncated": self.truncated,
            "strings": [{"offset": f"0x{s.offset:06X}", "text": s.text} for s in self.strings],
        }


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Whether a set of map definitions fits a ROM. Errors refuse, warnings only inform."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings)}


@dataclass(frozen=True, slots=True)
class DefinitionsSummary:
    name: str
    sha256: str
    simulated: bool
    confidence: ConfidenceLevel
    source: Source | None
    notes: str | None
    description: str
    map_count: int
    checksum_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "simulated": self.simulated,
            "confidence": str(self.confidence),
            "source": None if self.source is None else self.source.as_dict(),
            "notes": self.notes,
            "description": self.description,
            "map_count": self.map_count,
            "checksum_count": self.checksum_count,
        }


@dataclass(frozen=True, slots=True)
class MapSummary:
    id: str
    names: LocalizedText
    category: MapCategory
    rows: int
    cols: int
    data_type: DataType
    byte_offset: int
    unit: str
    min_physical: float
    max_physical: float
    changed_cells: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.names.en,
            "category": str(self.category),
            "shape": [self.rows, self.cols],
            "data_type": str(self.data_type),
            "byte_offset": f"0x{self.byte_offset:06X}",
            "unit": self.unit,
            "min": self.min_physical,
            "max": self.max_physical,
            "changed_cells": self.changed_cells,
        }


@dataclass(frozen=True, slots=True)
class AxisSnapshot:
    names: LocalizedText | None
    unit: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    """Original and working values of one table (physical units), row-major."""

    map_id: str
    names: LocalizedText
    category: MapCategory
    unit: str
    rows: int
    cols: int
    original: tuple[tuple[float, ...], ...]
    current: tuple[tuple[float, ...], ...]
    x_axis: AxisSnapshot | None
    y_axis: AxisSnapshot | None
    edit_min: float
    edit_max: float
    step: float

    def changed(self, row: int, col: int) -> bool:
        return self.original[row][col] != self.current[row][col]

    def delta(self, row: int, col: int) -> float:
        return self.current[row][col] - self.original[row][col]

    def delta_percent(self, row: int, col: int) -> float | None:
        """Change in percent of the original; ``None`` when the original is zero."""
        original = self.original[row][col]
        if original == 0:
            return None
        return 100.0 * self.delta(row, col) / abs(original)

    def value(self, row: int, col: int, mode: TableViewMode) -> float | None:
        if mode is TableViewMode.ORIGINAL:
            return self.original[row][col]
        if mode is TableViewMode.MODIFIED:
            return self.current[row][col]
        if mode is TableViewMode.DELTA:
            return self.delta(row, col)
        return self.delta_percent(row, col)

    @property
    def changed_count(self) -> int:
        return sum(1 for r in range(self.rows) for c in range(self.cols) if self.changed(r, c))

    @property
    def max_abs_delta(self) -> float:
        return max(
            (abs(self.delta(r, c)) for r in range(self.rows) for c in range(self.cols)),
            default=0.0,
        )

    def as_dict(self) -> dict[str, Any]:
        def axis(a: AxisSnapshot | None) -> dict[str, Any] | None:
            if a is None:
                return None
            return {
                "unit": a.unit,
                "values": list(a.values),
                "name": a.names.en if a.names else None,
            }

        return {
            "map_id": self.map_id,
            "name": self.names.en,
            "category": str(self.category),
            "unit": self.unit,
            "shape": [self.rows, self.cols],
            "x_axis": axis(self.x_axis),
            "y_axis": axis(self.y_axis),
            "values": [list(row) for row in self.current],
            "original": [list(row) for row in self.original],
            "changed_cells": self.changed_count,
        }


@dataclass(frozen=True, slots=True)
class ChecksumResult:
    """Outcome of one configured checksum block (or ``not_configured``)."""

    status: ChecksumStatus
    block_id: str | None = None
    algorithm: ChecksumAlgorithm | None = None
    start: int | None = None
    end: int | None = None
    checksum_offset: int | None = None
    width: int | None = None
    stored: int | None = None
    computed: int | None = None

    def as_dict(self) -> dict[str, Any]:
        def hexed(value: int | None) -> str | None:
            if value is None or self.width is None:
                return None
            return f"{value:0{2 * self.width}X}"

        return {
            "status": str(self.status),
            "block_id": self.block_id,
            "algorithm": None if self.algorithm is None else str(self.algorithm),
            "range": None if self.start is None else [f"0x{self.start:06X}", f"0x{self.end:06X}"],
            "checksum_offset": None
            if self.checksum_offset is None
            else f"0x{self.checksum_offset:06X}",
            "stored": hexed(self.stored),
            "computed": hexed(self.computed),
            "note": "valid means: matches the configured algorithm; not proof any ECU accepts it",
        }


@dataclass(frozen=True, slots=True)
class EditOutcome:
    """Result of one edit request on a table.

    Attributes:
        changed_cells: Cells whose stored value actually changed.
        quantised: ``(row, col, requested, stored)`` for every requested cell (physical).
    """

    changed_cells: int
    quantised: tuple[tuple[int, int, float, float], ...] = field(default=())

    @property
    def max_quantisation_error(self) -> float:
        return max((abs(req - got) for _r, _c, req, got in self.quantised), default=0.0)


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: str
    sha256: str
    crc32: int
    manifest_path: str
    checksums: tuple[ChecksumResult, ...]
    simulated: bool
