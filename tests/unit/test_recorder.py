"""CSV recorder: header with units, simulated column, empty cells instead of invented values."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iran_obd_tuner.core.models import Measurement, Unit
from iran_obd_tuner.diagnostics.live_data import LiveSnapshot, PidReading, PidStatus
from iran_obd_tuner.diagnostics.recorder import CsvRecorder, column_name

STAMP = datetime(2026, 11, 19, 12, 30, 45, 123000, tzinfo=UTC)


def _snapshot(*, simulated: bool, rpm: float | None) -> LiveSnapshot:
    rpm_reading = (
        PidReading(
            pid="010C",
            status=PidStatus.OK,
            measurement=Measurement(pid="010C", name="Engine speed", value=rpm, unit=Unit.RPM),
        )
        if rpm is not None
        else PidReading(pid="010C", status=PidStatus.NO_RESPONSE, detail="NO DATA")
    )
    fuel = PidReading(pid="012F", status=PidStatus.UNSUPPORTED, detail="unsupported")
    return LiveSnapshot(readings=(rpm_reading, fuel), simulated=simulated, timestamp=STAMP)


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_column_names_carry_pid_name_and_unit() -> None:
    assert column_name("010c") == "010C Engine speed [rpm]"
    assert column_name("0105") == "0105 Engine coolant temperature [degC]"
    assert column_name("01FF") == "01FF"  # uncatalogued: identifier only, nothing guessed


def test_recording_writes_header_rows_and_simulated_flag(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "capture.csv"
    recorder = CsvRecorder()

    assert recorder.start(target, ["010C", "012F"]) == target
    assert recorder.recording is True
    recorder.write(_snapshot(simulated=True, rpm=812.5))
    recorder.write(_snapshot(simulated=True, rpm=None))
    assert recorder.count == 2
    assert recorder.stop() == target
    assert recorder.recording is False

    rows = _rows(target)
    assert rows[0] == [
        "timestamp_utc",
        "simulated",
        "010C Engine speed [rpm]",
        "012F Fuel tank level [%]",
    ]
    assert rows[1] == ["2026-11-19T12:30:45.123+00:00", "true", "812.500", ""]
    # a parameter without a value leaves its cell empty: no fallback number is invented
    assert rows[2] == ["2026-11-19T12:30:45.123+00:00", "true", "", ""]


def test_vehicle_data_is_marked_as_not_simulated(tmp_path: Path) -> None:
    recorder = CsvRecorder()
    recorder.start(tmp_path / "real.csv", ["010C"])
    recorder.write(_snapshot(simulated=False, rpm=1000.0))
    recorder.stop()

    assert _rows(tmp_path / "real.csv")[1][1] == "false"


def test_write_without_recording_is_ignored(tmp_path: Path) -> None:
    recorder = CsvRecorder()

    recorder.write(_snapshot(simulated=True, rpm=1.0))

    assert recorder.count == 0
    assert recorder.stop() is None


def test_double_start_and_empty_parameter_list_are_rejected(tmp_path: Path) -> None:
    recorder = CsvRecorder()
    with pytest.raises(ValueError):
        recorder.start(tmp_path / "x.csv", [])

    recorder.start(tmp_path / "x.csv", ["010C"])
    try:
        with pytest.raises(RuntimeError, match="already recording"):
            recorder.start(tmp_path / "y.csv", ["010C"])
    finally:
        recorder.stop()


def test_unwritable_target_raises_os_error(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        CsvRecorder().start(blocker / "capture.csv", ["010C"])
