"""PID 0101 decoding: MIL, confirmed DTC count, ignition type and readiness monitors."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.monitors import (
    SOURCE_NOTE,
    IgnitionType,
    combine_monitor_status,
    decode_monitor_status,
)


def _monitor(status, name):
    return next(m for m in status.monitors if m.name == name)


def test_mil_and_confirmed_count_come_from_byte_a() -> None:
    on = decode_monitor_status(bytes.fromhex("83070000"))
    off = decode_monitor_status(bytes.fromhex("05070000"))

    assert (on.mil_on, on.confirmed_dtc_count) == (True, 3)
    assert (off.mil_on, off.confirmed_dtc_count) == (False, 5)
    assert decode_monitor_status(bytes.fromhex("FF000000")).confirmed_dtc_count == 127


def test_spark_ignition_readiness() -> None:
    # B=07: misfire/fuel/components available, all complete.
    # C=65 (0110 0101): catalyst, evap, O2 sensor, O2 heater available.
    # D=04: evaporative system not complete.
    status = decode_monitor_status(bytes.fromhex("82076504"))

    assert status.ignition is IgnitionType.SPARK
    assert _monitor(status, "misfire").available and _monitor(status, "misfire").complete
    assert _monitor(status, "catalyst").complete is True
    assert _monitor(status, "evaporative_system").complete is False
    assert status.incomplete_monitors == ("evaporative_system",)


def test_unavailable_monitors_make_no_completion_claim() -> None:
    status = decode_monitor_status(bytes.fromhex("000000FF"))  # incomplete bits, no monitors

    assert all(m.complete is None for m in status.monitors if not m.available)
    assert status.incomplete_monitors == ()


def test_common_monitor_incomplete_bits_are_in_the_high_nibble_of_byte_b() -> None:
    status = decode_monitor_status(bytes.fromhex("00170000"))  # B=0001 0111

    assert _monitor(status, "misfire").complete is False
    assert _monitor(status, "fuel_system").complete is True


def test_compression_ignition_uses_the_diesel_table() -> None:
    status = decode_monitor_status(bytes.fromhex("000F4140"))  # B bit 3 set

    assert status.ignition is IgnitionType.COMPRESSION
    names = {m.name for m in status.monitors}
    assert {"nmhc_catalyst", "pm_filter", "boost_pressure"} <= names
    assert "catalyst" not in names
    assert _monitor(status, "pm_filter").complete is False


def test_ambiguous_bit_is_named_neutrally_and_provenance_is_carried() -> None:
    status = decode_monitor_status(bytes.fromhex("00071010"))

    assert _monitor(status, "bit4_ac_or_gpf").available is True
    assert status.source_note == SOURCE_NOTE
    assert "not verified" in status.as_dict()["source_note"]


def test_wrong_length_is_refused() -> None:
    for data in (b"", b"\x00\x00\x00", b"\x00" * 5):
        with pytest.raises(ValueError, match="4 data bytes"):
            decode_monitor_status(data)


def test_several_ecus_combine_mil_any_and_count_sum() -> None:
    engine = decode_monitor_status(bytes.fromhex("01076504"))
    gearbox = decode_monitor_status(bytes.fromhex("82000000"))

    combined = combine_monitor_status([engine, gearbox])

    assert combined.mil_on is True
    assert combined.confirmed_dtc_count == 3
    assert combined.ecu_count == 2
    assert combined.monitors == engine.monitors
    with pytest.raises(ValueError):
        combine_monitor_status([])
