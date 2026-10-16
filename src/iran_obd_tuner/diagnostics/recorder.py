"""CSV recorder for live OBD samples."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from iran_obd_tuner.diagnostics.obd_service import LiveSample


class CsvRecorder:
    """Write live OBD samples to a CSV file."""

    FIELDS = [
        "timestamp",
        "rpm",
        "speed",
        "coolant",
        "voltage",
        "fuel",
        "throttle",
        "iat",
        "load",
    ]

    def __init__(self, log_dir: Path = Path("logs")) -> None:
        self._log_dir = log_dir
        self._file = None
        self._writer = None
        self._path: Path | None = None
        self._count = 0

    @property
    def recording(self) -> bool:
        return self._file is not None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def count(self) -> int:
        return self._count

    def start(self) -> Path:
        """Open a new CSV file and write the header."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = self._log_dir / f"live_{stamp}.csv"
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDS)
        self._writer.writeheader()
        self._count = 0
        return self._path

    def write(self, sample: LiveSample) -> None:
        """Append one sample row."""
        if self._writer is None:
            return
        self._writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "rpm": round(sample.rpm, 1),
                "speed": round(sample.speed, 1),
                "coolant": round(sample.coolant, 1),
                "voltage": round(sample.voltage, 2),
                "fuel": round(sample.fuel, 1),
                "throttle": round(sample.throttle, 1),
                "iat": round(sample.iat, 1),
                "load": round(sample.load, 1),
            }
        )
        self._count += 1

    def stop(self) -> Path | None:
        """Close the file and return its path."""
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        path = self._path
        self._file = None
        self._writer = None
        self._path = None
        return path
