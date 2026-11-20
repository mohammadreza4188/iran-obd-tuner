"""Traffic log: bounded ring buffer, thread safety, and what the adapters record."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.elm327 import Elm327Adapter
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario

FIXED_WALL = datetime(2026, 11, 20, 12, 0, 0, 123000, tzinfo=UTC)


def _log(capacity: int = 5) -> TrafficLog:
    return TrafficLog(capacity, clock=lambda: 1.0, wall_clock=lambda: FIXED_WALL)


def test_entries_get_increasing_sequence_numbers() -> None:
    log = _log()
    first = log.append(Direction.TX, "010C", adapter="elm327", simulated=False)
    second = log.append(Direction.RX, "41 0C 1A F8", adapter="elm327", simulated=False)

    assert (first.seq, second.seq) == (1, 2)
    assert log.entries_since(1) == (second,)
    assert log.snapshot() == (first, second)
    assert log.last_seq == 2


def test_capacity_is_bounded_and_drops_are_counted() -> None:
    log = _log(capacity=3)
    for n in range(5):
        log.append(Direction.TX, f"01{n:02X}", adapter="mock", simulated=True)

    assert len(log) == 3
    assert log.dropped_count == 2
    assert [entry.text for entry in log.snapshot()] == ["0102", "0103", "0104"]


def test_clear_keeps_sequence_numbers_increasing() -> None:
    log = _log()
    log.append(Direction.TX, "03", adapter="mock", simulated=True)
    log.clear()
    entry = log.append(Direction.TX, "07", adapter="mock", simulated=True)

    assert len(log) == 1 and entry.seq == 2 and log.dropped_count == 0


def test_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        TrafficLog(0)


def test_concurrent_writers_never_lose_or_duplicate_entries() -> None:
    log = TrafficLog(10_000)

    def writer(name: str) -> None:
        for n in range(1000):
            log.append(Direction.TX, f"{name}{n}", adapter="mock", simulated=True)

    threads = [threading.Thread(target=writer, args=(name,)) for name in "ABCD"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    seqs = [entry.seq for entry in log.snapshot()]
    assert len(seqs) == 4000 and len(set(seqs)) == 4000 and seqs == sorted(seqs)


def test_format_line_marks_simulated_entries() -> None:
    log = _log()
    real = log.append(Direction.RX, "41 0C 1A F8", adapter="elm327", simulated=False)
    sim = log.append(Direction.TX, "010C", adapter="mock", simulated=True)

    assert real.format_line().endswith("RX   41 0C 1A F8")
    assert sim.format_line("[SIM]").endswith("TX   010C  [SIM]")


def test_elm327_records_allowed_commands_and_raw_replies(scripted_transport, can_vehicle_script):
    log = TrafficLog()
    transport = scripted_transport(can_vehicle_script)
    adapter = Elm327Adapter("COM3", transport_factory=lambda _p, _b: transport, traffic_log=log)
    adapter.connect()
    adapter.read_measurement("010C")

    entries = log.snapshot()
    pairs = [(e.direction, e.text) for e in entries]
    assert (Direction.TX, "010C") in pairs
    assert (Direction.RX, "41 0C 1A F8⏎⏎") in pairs
    assert (Direction.RX, "SEARCHING...⏎41 00 BE 3E B8 11⏎⏎") in pairs
    assert all(entry.simulated is False and entry.adapter == "elm327" for entry in entries)


def test_refused_commands_never_reach_the_log(scripted_transport, can_vehicle_script):
    from iran_obd_tuner.core.exceptions import SafetyGuardError

    log = TrafficLog()
    transport = scripted_transport(can_vehicle_script)
    adapter = Elm327Adapter("COM3", transport_factory=lambda _p, _b: transport, traffic_log=log)
    adapter.connect()

    with pytest.raises(SafetyGuardError):
        adapter._command("04")

    assert not any(entry.text.startswith("04") for entry in log.snapshot())


def test_elm327_timeouts_are_noted(scripted_transport, can_vehicle_script):
    from iran_obd_tuner.core.exceptions import AdapterTimeoutError

    log = TrafficLog()
    can_vehicle_script["010C"] = AdapterTimeoutError("no complete reply within 2.0s")
    transport = scripted_transport(can_vehicle_script)
    adapter = Elm327Adapter("COM3", transport_factory=lambda _p, _b: transport, traffic_log=log)
    adapter.connect()

    with pytest.raises(AdapterTimeoutError):
        adapter.read_measurement("010C")

    last = log.snapshot()[-1]
    assert last.direction is Direction.INFO and "no complete reply" in last.text


def test_mock_records_requests_and_decoded_answers_but_no_hex_frames() -> None:
    log = TrafficLog()
    adapter = MockObdAdapter(traffic_log=log)
    adapter.connect()
    adapter.read_measurement("010C")
    adapter.read_diagnostic_trouble_codes()

    pairs = [(e.direction, e.text) for e in log.snapshot()]
    assert pairs == [
        (Direction.TX, "010C"),
        (Direction.RX, "842 rpm"),
        (Direction.TX, "03"),
        (Direction.RX, "P0301 P0135 P0171"),
    ]
    assert all(entry.simulated for entry in log.snapshot())
    assert not any(e.text.startswith("41") for e in log.snapshot())  # no invented frames


def test_mock_failures_are_noted() -> None:
    log = TrafficLog()
    adapter = MockObdAdapter(
        MockScenario.default().with_failure(FailureMode.READ_TIMEOUT), traffic_log=log
    )
    adapter.connect()

    with pytest.raises(Exception, match="simulated failure"):
        adapter.read_vin()

    assert log.snapshot()[-1].direction is Direction.INFO


def test_adapters_without_a_log_record_nothing() -> None:
    adapter = MockObdAdapter()
    adapter.connect()
    adapter.read_measurement("010C")

    assert adapter.traffic_log is None
