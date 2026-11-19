"""CSV recorder for live data snapshots.

One row per :class:`~iran_obd_tuner.diagnostics.live_data.LiveSnapshot`:

* ``timestamp_utc`` — ISO 8601 with milliseconds and an explicit ``+00:00`` offset;
* ``simulated`` — ``true`` or ``false``, so a simulated recording can never be mistaken for a
  vehicle capture once the file leaves the application;
* one column per parameter, named ``<PID> <name> [<unit>]`` from the PID catalogue.

A parameter that was not read successfully leaves its cell **empty**: no fallback value is
ever written. Not implemented: per-cell status columns, file rotation, compression.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from iran_obd_tuner.core.pids import describe_pid, normalize_pid
from iran_obd_tuner.diagnostics.live_data import LiveSnapshot

__all__ = ["CsvRecorder", "column_name"]


def column_name(pid: str) -> str:
    """Return the CSV column header for ``pid``, e.g. ``"010C Engine speed [rpm]"``."""
    normalized = normalize_pid(pid)
    spec = describe_pid(normalized)
    if spec is None:
        return normalized
    return f"{normalized} {spec.name} [{spec.unit}]"


class CsvRecorder:
    """Write live data snapshots to a CSV file."""

    def __init__(self) -> None:
        self._file: TextIO | None = None
        self._writer: Any = None  # csv writer objects have no public class
        self._path: Path | None = None
        self._pids: tuple[str, ...] = ()
        self._count = 0

    @property
    def recording(self) -> bool:
        """Whether a file is currently open."""
        return self._file is not None

    @property
    def path(self) -> Path | None:
        """File being written, or ``None`` when not recording."""
        return self._path

    @property
    def count(self) -> int:
        """Rows written since the last :meth:`start`."""
        return self._count

    def start(self, path: Path, pids: Sequence[str]) -> Path:
        """Create ``path`` (and its parent directories) and write the header row.

        Raises:
            RuntimeError: If a recording is already running.
            ValueError: If ``pids`` is empty.
            OSError: If the file cannot be created.
        """
        if self._file is not None:
            raise RuntimeError(f"already recording to {self._path}")
        if not pids:
            raise ValueError("at least one parameter is required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._pids = tuple(normalize_pid(pid) for pid in pids)
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(
            ["timestamp_utc", "simulated", *(column_name(pid) for pid in self._pids)]
        )
        self._path = path
        self._count = 0
        return path

    def write(self, snapshot: LiveSnapshot) -> None:
        """Append one row. Ignored when not recording."""
        if self._writer is None:
            return
        cells: list[str] = []
        for pid in self._pids:
            value = snapshot.value_for(pid)
            cells.append("" if value is None else f"{value:.3f}")
        self._writer.writerow(
            [
                snapshot.timestamp.isoformat(timespec="milliseconds"),
                "true" if snapshot.simulated else "false",
                *cells,
            ]
        )
        self._count += 1

    def stop(self) -> Path | None:
        """Close the file and return its path (``None`` when nothing was recording)."""
        path = self._path
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None
        self._path = None
        return path
