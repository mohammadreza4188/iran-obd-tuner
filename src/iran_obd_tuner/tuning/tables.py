"""Decoding and bounded encoding of calibration tables, and undoable edit commands.

Values are never clamped: a physical value outside the map's ``min``/``max`` (or outside what
its data type can store) is refused with :class:`~iran_obd_tuner.core.exceptions.
TableEditError`. Encoding rounds to the nearest raw step, and the caller learns the stored
value, so quantisation is visible.

Commands only touch the working copy (a ``bytearray``) they are given, and remember the old
bytes so that undo restores them exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from iran_obd_tuner.core.calibration import DataType
from iran_obd_tuner.core.exceptions import TableEditError
from iran_obd_tuner.tuning.map_definitions import AxisDefinition, MapDefinition

__all__ = [
    "CellChange",
    "PatchBytesCommand",
    "SetCellsCommand",
    "axis_values",
    "decode_raw",
    "encode_raw",
    "read_grid",
    "to_physical",
    "to_raw",
]


def decode_raw(data: bytes | bytearray, offset: int, dtype: DataType) -> int:
    chunk = bytes(data[offset : offset + dtype.size])
    if len(chunk) != dtype.size:
        raise TableEditError(f"0x{offset:X}: not enough bytes for {dtype}")
    return int.from_bytes(chunk, dtype.byteorder, signed=dtype.signed)  # type: ignore[arg-type]


def encode_raw(value: int, dtype: DataType) -> bytes:
    if not dtype.min_raw <= value <= dtype.max_raw:
        raise TableEditError(f"{value} does not fit {dtype}")
    return value.to_bytes(dtype.size, dtype.byteorder, signed=dtype.signed)  # type: ignore[arg-type]


def to_physical(raw: int, scale: float, offset: float) -> float:
    return raw * scale + offset


def to_raw(physical: float, definition: MapDefinition) -> int:
    """Nearest raw value for ``physical``; refuses values outside the map's bounds.

    Raises:
        TableEditError: Not a finite number, outside ``min``/``max``, or not storable.
    """
    if not isinstance(physical, int | float) or not math.isfinite(physical):
        raise TableEditError(f"{physical!r} is not a finite number")
    tolerance = abs(definition.scale) / 2
    if physical < definition.min - 1e-9 or physical > definition.max + 1e-9:
        raise TableEditError(
            f"{physical:g} {definition.unit} is outside {definition.id} "
            f"({definition.min:g}..{definition.max:g} {definition.unit})"
        )
    raw = round((physical - definition.offset) / definition.scale)
    dtype = definition.data_type
    if not dtype.min_raw <= raw <= dtype.max_raw:
        raise TableEditError(f"{physical:g} cannot be stored as {dtype} in {definition.id}")
    stored = to_physical(raw, definition.scale, definition.offset)
    if abs(stored - physical) > tolerance + 1e-9:  # pragma: no cover - guarded by rounding
        raise TableEditError(f"{physical:g} cannot be represented in {definition.id}")
    return raw


def read_grid(data: bytes | bytearray, definition: MapDefinition) -> tuple[tuple[int, ...], ...]:
    """Raw cell values, ``rows`` tuples of ``cols`` values."""
    return tuple(
        tuple(
            decode_raw(data, definition.cell_offset(r, c), definition.data_type)
            for c in range(definition.cols)
        )
        for r in range(definition.rows)
    )


def axis_values(data: bytes | bytearray, axis: AxisDefinition) -> tuple[float, ...]:
    if axis.kind == "fixed":
        return axis.values
    size = axis.data_type.size
    return tuple(
        to_physical(
            decode_raw(data, axis.byte_offset + i * size, axis.data_type), axis.scale, axis.offset
        )
        for i in range(axis.count)
    )


@dataclass(frozen=True, slots=True)
class CellChange:
    row: int
    col: int
    old_raw: int
    new_raw: int


@dataclass(frozen=True, slots=True)
class SetCellsCommand:
    """Change one or more cells of one table (one undo step)."""

    definition: MapDefinition
    changes: tuple[CellChange, ...]
    label: str

    def apply(self, working: bytearray) -> None:
        self._write(working, new=True)

    def revert(self, working: bytearray) -> None:
        self._write(working, new=False)

    def _write(self, working: bytearray, *, new: bool) -> None:
        dtype = self.definition.data_type
        for change in self.changes:
            offset = self.definition.cell_offset(change.row, change.col)
            value = change.new_raw if new else change.old_raw
            working[offset : offset + dtype.size] = encode_raw(value, dtype)

    def touched(self) -> tuple[tuple[int, int], ...]:
        size = self.definition.data_type.size
        return tuple(
            (o, o + size)
            for o in (self.definition.cell_offset(ch.row, ch.col) for ch in self.changes)
        )

    def as_dict(self) -> dict[str, object]:
        d = self.definition
        return {
            "kind": "set_cells",
            "label": self.label,
            "map_id": d.id,
            "cells": [
                {
                    "row": ch.row,
                    "col": ch.col,
                    "old_raw": ch.old_raw,
                    "new_raw": ch.new_raw,
                    "old_physical": to_physical(ch.old_raw, d.scale, d.offset),
                    "new_physical": to_physical(ch.new_raw, d.scale, d.offset),
                }
                for ch in self.changes
            ],
        }


@dataclass(frozen=True, slots=True)
class PatchBytesCommand:
    """Replace byte ranges (used for checksum recalculation; one undo step)."""

    patches: tuple[tuple[int, bytes, bytes], ...]  # (offset, old, new)
    label: str

    def apply(self, working: bytearray) -> None:
        for offset, _old, new in self.patches:
            working[offset : offset + len(new)] = new

    def revert(self, working: bytearray) -> None:
        for offset, old, _new in self.patches:
            working[offset : offset + len(old)] = old

    def touched(self) -> tuple[tuple[int, int], ...]:
        return tuple((offset, offset + len(new)) for offset, _old, new in self.patches)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": "patch_bytes",
            "label": self.label,
            "patches": [
                {"offset": f"0x{o:06X}", "old": old.hex().upper(), "new": new.hex().upper()}
                for o, old, new in self.patches
            ],
        }
