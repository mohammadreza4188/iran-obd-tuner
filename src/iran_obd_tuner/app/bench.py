"""Application wiring of the offline Bench workspace (Phase 4).

Implements :class:`iran_obd_tuner.ui.backend.BenchWorkspace` on top of ``tuning`` so that the
dashboard never imports ``tuning`` (layer rule) and only sees ``core.calibration`` snapshots.
The CLI uses the same class for ``bench inspect`` / ``bench table``.

Nothing here talks to an ECU: it opens files, edits an in-memory working copy, and exports a
modified copy to a *different* file through :mod:`iran_obd_tuner.tuning.rom_export`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from iran_obd_tuner import __version__
from iran_obd_tuner.core.calibration import (
    ChecksumResult,
    CompatibilityReport,
    DefinitionsSummary,
    EditOutcome,
    ExportResult,
    MapSummary,
    RomSummary,
    StringScan,
    TableSnapshot,
)
from iran_obd_tuner.core.exceptions import RomLoadError
from iran_obd_tuner.tuning.demo import load_demo
from iran_obd_tuner.tuning.map_definitions import load_definitions
from iran_obd_tuner.tuning.rom_export import default_export_name, export_modified_rom
from iran_obd_tuner.tuning.rom_loader import extract_ascii_strings, load_rom
from iran_obd_tuner.tuning.workspace import RomWorkspace

__all__ = ["AppBenchWorkspace"]


class AppBenchWorkspace:
    """One Bench session: at most one ROM (and its definitions) at a time.

    Args:
        output_dir: Default folder for exported files.
        tool_version: Recorded in export manifests.
    """

    def __init__(self, output_dir: Path, *, tool_version: str = __version__) -> None:
        self._output_dir = Path(output_dir)
        self._tool_version = tool_version
        self._workspace: RomWorkspace | None = None
        self._strings: StringScan | None = None

    def _require(self) -> RomWorkspace:
        if self._workspace is None:
            raise RomLoadError("open a ROM file first")
        return self._workspace

    # ------------------------------------------------------------------ opening
    def open_rom(self, path: Path | str, *, min_string_length: int = 6) -> RomSummary:
        rom = load_rom(path)
        self._workspace = RomWorkspace(rom)
        self._strings = extract_ascii_strings(rom.data, min_length=min_string_length)
        return rom.summary()

    def open_demo(self) -> RomSummary:
        rom, definitions = load_demo()
        self._workspace = RomWorkspace(rom, definitions)
        self._strings = extract_ascii_strings(rom.data)
        return rom.summary()

    def load_definitions(self, path: Path | str) -> CompatibilityReport:
        """Attach a definition file. Raises ``MapDefinitionError`` when it does not fit."""
        workspace = self._require()
        return workspace.attach_definitions(load_definitions(path))

    # ------------------------------------------------------------------ state
    @property
    def loaded(self) -> bool:
        return self._workspace is not None

    def summary(self) -> RomSummary | None:
        return None if self._workspace is None else self._workspace.rom.summary()

    def strings(self) -> StringScan | None:
        return self._strings

    def definitions(self) -> DefinitionsSummary | None:
        if self._workspace is None or self._workspace.definitions is None:
            return None
        return self._workspace.definitions.summary()

    def compatibility(self) -> CompatibilityReport | None:
        if self._workspace is None or self._workspace.definitions is None:
            return None
        return self._workspace.compatibility

    def maps(self) -> tuple[MapSummary, ...]:
        return () if self._workspace is None else self._workspace.maps()

    def table(self, map_id: str) -> TableSnapshot:
        return self._require().table(map_id)

    def checksums(self) -> tuple[ChecksumResult, ...]:
        return () if self._workspace is None else self._workspace.checksums()

    @property
    def is_modified(self) -> bool:
        return self._workspace is not None and self._workspace.is_modified

    @property
    def can_undo(self) -> bool:
        return self._workspace is not None and self._workspace.can_undo

    @property
    def can_redo(self) -> bool:
        return self._workspace is not None and self._workspace.can_redo

    # ------------------------------------------------------------------ editing
    def set_cell(self, map_id: str, row: int, col: int, value: float) -> EditOutcome:
        return self._require().set_cell(map_id, row, col, value)

    def set_cells(self, map_id: str, cells: Sequence[tuple[int, int]], value: float) -> EditOutcome:
        return self._require().set_cells(map_id, cells, value)

    def scale_cells(
        self, map_id: str, cells: Sequence[tuple[int, int]], percent: float
    ) -> EditOutcome:
        return self._require().scale_cells(map_id, cells, percent)

    def undo(self) -> str | None:
        return self._require().undo()

    def redo(self) -> str | None:
        return self._require().redo()

    def recalculate_checksums(self) -> int:
        return self._require().recalculate_checksums()

    # ------------------------------------------------------------------ export
    def default_export_path(self) -> Path:
        return self._output_dir / default_export_name(self._require())

    def export(
        self, path: Path | str, *, acknowledged: bool, overwrite: bool = False
    ) -> ExportResult:
        return export_modified_rom(
            self._require(),
            path,
            acknowledged=acknowledged,
            overwrite=overwrite,
            tool_version=self._tool_version,
        )
