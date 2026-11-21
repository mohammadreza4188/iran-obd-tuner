"""An editing session on one ROM: immutable original, working copy, undo / redo, checksums.

The :class:`~iran_obd_tuner.tuning.rom_loader.RomImage` is never modified; edits go into a
private ``bytearray`` copy through undoable commands. Every changed byte must lie inside a
table cell or a configured checksum field (:meth:`RomWorkspace.assert_edits_are_confined`),
which the exporter checks before anything is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from iran_obd_tuner.core.calibration import (
    AxisSnapshot,
    ChecksumResult,
    CompatibilityReport,
    EditOutcome,
    MapSummary,
    TableSnapshot,
)
from iran_obd_tuner.core.exceptions import MapDefinitionError, TableEditError
from iran_obd_tuner.tuning.checksum import encode_checksum, verify_all
from iran_obd_tuner.tuning.map_definitions import MapDefinition, MapDefinitions, check_compatibility
from iran_obd_tuner.tuning.rom_loader import RomImage
from iran_obd_tuner.tuning.tables import (
    CellChange,
    PatchBytesCommand,
    SetCellsCommand,
    axis_values,
    read_grid,
    to_physical,
    to_raw,
)

__all__ = ["HISTORY_LIMIT", "RomWorkspace"]

#: Undo steps kept; older steps are dropped (and counted).
HISTORY_LIMIT = 1000


class _Command(Protocol):
    label: str

    def apply(self, working: bytearray) -> None: ...
    def revert(self, working: bytearray) -> None: ...
    def touched(self) -> tuple[tuple[int, int], ...]: ...
    def as_dict(self) -> dict[str, object]: ...


class RomWorkspace:
    """Editing session. Not thread-safe: use it from one thread at a time.

    Args:
        rom: The loaded image (never modified).
        definitions: Map definitions; must pass :func:`check_compatibility`.

    Raises:
        MapDefinitionError: The definitions do not fit the ROM (the report lists why).
    """

    def __init__(self, rom: RomImage, definitions: MapDefinitions | None = None) -> None:
        self._rom = rom
        self._working = bytearray(rom.data)
        self._undo: list[_Command] = []
        self._redo: list[_Command] = []
        self._dropped = 0
        self._definitions: MapDefinitions | None = None
        self._compatibility = CompatibilityReport()
        if definitions is not None:
            self.attach_definitions(definitions)

    # ------------------------------------------------------------------ state
    @property
    def rom(self) -> RomImage:
        return self._rom

    @property
    def definitions(self) -> MapDefinitions | None:
        return self._definitions

    @property
    def compatibility(self) -> CompatibilityReport:
        return self._compatibility

    @property
    def is_modified(self) -> bool:
        return bytes(self._working) != self._rom.data

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def dropped_history(self) -> int:
        return self._dropped

    def working_bytes(self) -> bytes:
        return bytes(self._working)

    def history(self) -> tuple[dict[str, object], ...]:
        """The applied commands, oldest first (for the export manifest)."""
        return tuple(command.as_dict() for command in self._undo)

    def attach_definitions(self, definitions: MapDefinitions) -> CompatibilityReport:
        """Use ``definitions`` for this ROM. Refused (and nothing changes) on errors.

        Attaching new definitions is only allowed while the working copy is unmodified.
        """
        if self.is_modified:
            raise MapDefinitionError("undo all edits before loading other map definitions")
        report = check_compatibility(
            definitions,
            self._rom.data,
            rom_sha256=self._rom.sha256,
            rom_simulated=self._rom.simulated,
        )
        if not report.ok:
            raise MapDefinitionError(
                f"{definitions.name} does not fit {self._rom.name}: " + "; ".join(report.errors)
            )
        self._definitions = definitions
        self._compatibility = report
        self._undo.clear()
        self._redo.clear()
        return report

    def _map(self, map_id: str) -> MapDefinition:
        if self._definitions is None:
            raise TableEditError("no map definitions are loaded")
        try:
            return self._definitions.map(map_id)
        except MapDefinitionError as exc:
            raise TableEditError(str(exc)) from exc

    # ------------------------------------------------------------------ reading
    def maps(self) -> tuple[MapSummary, ...]:
        if self._definitions is None:
            return ()
        result = []
        for d in self._definitions.maps:
            table = self.table(d.id)
            values = [v for row in table.current for v in row]
            result.append(
                MapSummary(
                    id=d.id,
                    names=d.names,
                    category=d.category,
                    rows=d.rows,
                    cols=d.cols,
                    data_type=d.data_type,
                    byte_offset=d.byte_offset,
                    unit=d.unit,
                    min_physical=min(values),
                    max_physical=max(values),
                    changed_cells=table.changed_count,
                )
            )
        return tuple(result)

    def table(self, map_id: str) -> TableSnapshot:
        d = self._map(map_id)

        def physical(data: bytes | bytearray) -> tuple[tuple[float, ...], ...]:
            return tuple(
                tuple(to_physical(raw, d.scale, d.offset) for raw in row)
                for row in read_grid(data, d)
            )

        def axis(definition, data: bytes) -> AxisSnapshot | None:  # noqa: ANN001
            if definition is None:
                return None
            return AxisSnapshot(definition.names, definition.unit, axis_values(data, definition))

        return TableSnapshot(
            map_id=d.id,
            names=d.names,
            category=d.category,
            unit=d.unit,
            rows=d.rows,
            cols=d.cols,
            original=physical(self._rom.data),
            current=physical(self._working),
            x_axis=axis(d.x_axis, self._rom.data),
            y_axis=axis(d.y_axis, self._rom.data),
            edit_min=d.min,
            edit_max=d.max,
            step=abs(d.scale),
        )

    def checksums(self) -> tuple[ChecksumResult, ...]:
        blocks = () if self._definitions is None else self._definitions.checksums
        return verify_all(self._working, blocks)

    def original_checksums(self) -> tuple[ChecksumResult, ...]:
        blocks = () if self._definitions is None else self._definitions.checksums
        return verify_all(self._rom.data, blocks)

    # ------------------------------------------------------------------ editing
    def _execute(self, command: _Command) -> None:
        command.apply(self._working)
        self._undo.append(command)
        self._redo.clear()
        if len(self._undo) > HISTORY_LIMIT:
            del self._undo[0]
            self._dropped += 1

    def _set(
        self, map_id: str, targets: Sequence[tuple[int, int, float]], label: str
    ) -> EditOutcome:
        d = self._map(map_id)
        grid = read_grid(self._working, d)
        changes: list[CellChange] = []
        quantised: list[tuple[int, int, float, float]] = []
        seen: set[tuple[int, int]] = set()
        for row, col, physical in targets:
            if not (0 <= row < d.rows and 0 <= col < d.cols):
                raise TableEditError(f"cell ({row}, {col}) is outside {d.id} ({d.rows}x{d.cols})")
            if (row, col) in seen:
                raise TableEditError(f"cell ({row}, {col}) was given twice")
            seen.add((row, col))
            new_raw = to_raw(physical, d)  # refuses out-of-range before anything changes
            quantised.append((row, col, physical, to_physical(new_raw, d.scale, d.offset)))
            if new_raw != grid[row][col]:
                changes.append(CellChange(row, col, grid[row][col], new_raw))
        if changes:
            self._execute(SetCellsCommand(d, tuple(changes), label))
        return EditOutcome(changed_cells=len(changes), quantised=tuple(quantised))

    def set_cell(self, map_id: str, row: int, col: int, value: float) -> EditOutcome:
        return self._set(map_id, [(row, col, value)], f"{map_id}[{row},{col}] = {value:g}")

    def set_cells(self, map_id: str, cells: Sequence[tuple[int, int]], value: float) -> EditOutcome:
        return self._set(
            map_id, [(r, c, value) for r, c in cells], f"{map_id}: {len(cells)} cell(s) = {value:g}"
        )

    def scale_cells(
        self, map_id: str, cells: Sequence[tuple[int, int]], percent: float
    ) -> EditOutcome:
        """Add ``percent`` % to each cell (one undo step)."""
        table = self.table(map_id)
        targets = []
        for r, c in cells:
            if not (0 <= r < table.rows and 0 <= c < table.cols):
                raise TableEditError(f"cell ({r}, {c}) is outside {map_id}")
            targets.append((r, c, table.current[r][c] * (1 + percent / 100.0)))
        return self._set(map_id, targets, f"{map_id}: {len(cells)} cell(s) {percent:+g}%")

    def recalculate_checksums(self) -> int:
        """Write correct checksums into the working copy (one undo step). Returns the count."""
        if self._definitions is None:
            return 0
        patches = []
        scratch = bytearray(self._working)
        for block in self._definitions.checksums:
            new = encode_checksum(scratch, block)
            lo, hi = block.field_range
            old = bytes(scratch[lo:hi])
            if new != old:
                scratch[lo:hi] = new  # later blocks may cover earlier fields
                patches.append((lo, old, new))
        if patches:
            self._execute(PatchBytesCommand(tuple(patches), "recalculate checksums"))
        return len(patches)

    def undo(self) -> str | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.revert(self._working)
        self._redo.append(command)
        return command.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply(self._working)
        self._undo.append(command)
        return command.label

    # ------------------------------------------------------------------ diff
    def changed_ranges(self) -> tuple[tuple[int, int], ...]:
        """Changed byte ranges ``[start, end)`` of the working copy vs. the original."""
        ranges: list[tuple[int, int]] = []
        original = self._rom.data
        working = bytes(self._working)
        if working == original:
            return ()
        chunk = 4096
        differing: list[int] = []
        for base in range(0, len(original), chunk):  # compare chunks in C, bytes only where needed
            if original[base : base + chunk] != working[base : base + chunk]:
                differing.extend(
                    i
                    for i in range(base, min(base + chunk, len(original)))
                    if original[i] != working[i]
                )
        start = previous = differing[0]
        for i in differing[1:]:
            if i != previous + 1:
                ranges.append((start, previous + 1))
                start = i
            previous = i
        ranges.append((start, previous + 1))
        return tuple(ranges)

    def assert_edits_are_confined(self) -> None:
        """Every changed byte lies in a table cell or a checksum field.

        Raises:
            TableEditError: A byte outside those areas differs (would be a bug).
        """
        allowed: list[tuple[int, int]] = []
        if self._definitions is not None:
            allowed += [m.byte_range for m in self._definitions.maps]
            allowed += [b.field_range for b in self._definitions.checksums]
        for start, end in self.changed_ranges():
            for i in range(start, end):
                if not any(lo <= i < hi for lo, hi in allowed):
                    raise TableEditError(f"byte 0x{i:X} changed outside every table and checksum")
