"""Simulated adapter: lifecycle, readings, failure injection and refusals."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.models import Measurement, Unit
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy
from iran_obd_tuner.hardware.base import AdapterKind
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario, SimulatedReading


def test_info_declares_a_simulated_adapter(adapter: MockObdAdapter) -> None:
    info = adapter.info

    assert info.name == "mock"
    assert info.kind is AdapterKind.MOCK
    assert info.implemented is True
    assert info.hardware_required is False
    assert "simulated" in info.protocols[0]
    assert "simulated" in info.notes


def test_connection_lifecycle(adapter: MockObdAdapter) -> None:
    assert adapter.is_connected is False

    adapter.connect()
    assert adapter.is_connected is True

    adapter.disconnect()
    assert adapter.is_connected is False

    adapter.disconnect()  # idempotent
    assert adapter.is_connected is False


def test_double_connect_is_refused(adapter: MockObdAdapter) -> None:
    adapter.connect()
    try:
        with pytest.raises(AdapterConnectionError, match="already connected"):
            adapter.connect()
    finally:
        adapter.disconnect()


def test_session_closes_the_connection_even_on_failure(adapter: MockObdAdapter) -> None:
    with pytest.raises(RuntimeError, match="boom"), adapter.session():
        assert adapter.is_connected is True
        raise RuntimeError("boom")

    assert adapter.is_connected is False


def test_reads_require_a_connection(adapter: MockObdAdapter) -> None:
    with pytest.raises(AdapterConnectionError, match="not connected"):
        adapter.read_vin()
    with pytest.raises(AdapterConnectionError, match="not connected"):
        adapter.read_measurement("010C")


def test_measurements_carry_the_catalogue_name_and_unit(adapter: MockObdAdapter) -> None:
    with adapter.session():
        measurement = adapter.read_measurement("010c")  # case insensitive on purpose

    assert isinstance(measurement, Measurement)
    assert measurement.pid == "010C"
    assert measurement.name == "Engine speed"
    assert measurement.unit is Unit.RPM
    assert measurement.value == 842.0


def test_unsupported_parameter_is_reported(adapter: MockObdAdapter) -> None:
    with adapter.session(), pytest.raises(UnsupportedPidError, match="015C"):
        adapter.read_measurement("015C")


def test_batch_read_reports_skipped_parameters(adapter: MockObdAdapter) -> None:
    with adapter.session():
        batch = adapter.read_measurements(["010C", "015C", "010D"])

    assert [m.pid for m in batch.measurements] == ["010C", "010D"]
    assert batch.skipped_pids == ("015C",)
    assert len(batch) == 2


def test_batch_read_can_fail_fast_on_unsupported_parameters(adapter: MockObdAdapter) -> None:
    with adapter.session(), pytest.raises(UnsupportedPidError):
        adapter.read_measurements(["015C"], ignore_unsupported=False)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (FailureMode.CONNECT_TIMEOUT, AdapterTimeoutError),
        (FailureMode.CONNECT_REFUSED, AdapterConnectionError),
    ],
)
def test_connection_failures_are_explicit(
    fixture_scenario: MockScenario, failure: FailureMode, expected: type[Exception]
) -> None:
    adapter = MockObdAdapter(fixture_scenario.with_failure(failure))

    with pytest.raises(expected):
        adapter.connect()

    assert adapter.is_connected is False


def test_read_timeout_is_explicit(fixture_scenario: MockScenario) -> None:
    adapter = MockObdAdapter(fixture_scenario.with_failure(FailureMode.READ_TIMEOUT))

    with adapter.session(), pytest.raises(AdapterTimeoutError):
        adapter.read_vin()


@pytest.mark.parametrize(
    "call",
    [
        lambda adapter: adapter.read_vin(),
        lambda adapter: adapter.read_supported_pids(),
        lambda adapter: adapter.read_measurement("010C"),
        lambda adapter: adapter.read_diagnostic_trouble_codes(),
    ],
)
def test_malformed_responses_are_explicit(fixture_scenario: MockScenario, call) -> None:
    adapter = MockObdAdapter(fixture_scenario.with_failure(FailureMode.MALFORMED_RESPONSE))

    with adapter.session(), pytest.raises(ProtocolError, match="malformed"):
        call(adapter)


def test_fixture_scenario_is_loaded_from_json(fixture_scenario: MockScenario) -> None:
    adapter = MockObdAdapter(fixture_scenario)

    with adapter.session():
        assert adapter.read_vin() == "SIMULATEDVIN00002"
        assert adapter.read_measurement("010C").value == 1450.0
        assert adapter.read_diagnostic_trouble_codes() == ("P0300", "P0420", "P0500")
        assert "015C" not in adapter.read_supported_pids()


def test_clearing_trouble_codes_is_refused_by_policy(adapter: MockObdAdapter) -> None:
    with adapter.session(), pytest.raises(SafetyGuardError, match="read-only"):
        adapter.clear_diagnostic_trouble_codes()


def test_clearing_trouble_codes_is_also_not_implemented(adapter: MockObdAdapter) -> None:
    """Even an explicitly allowing policy cannot make a missing feature work."""
    permissive = MockObdAdapter(policy=SafetyPolicy(allowed=frozenset(OperationClass)))

    with permissive.session(), pytest.raises(UnsupportedOperationError, match="not implemented"):
        permissive.clear_diagnostic_trouble_codes()


def test_scenario_default_includes_stored_trouble_codes(adapter: MockObdAdapter) -> None:
    with adapter.session():
        assert "P0301" in adapter.read_diagnostic_trouble_codes()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MockScenario(readings=(SimulatedReading("01FF", 1.0),)),
        lambda: MockScenario(
            readings=(SimulatedReading("010C", 1.0), SimulatedReading("010C", 2.0))
        ),
        lambda: MockScenario(trouble_codes=("nonsense",)),
        lambda: MockScenario(latency_s=-1.0),
    ],
)
def test_invalid_scenarios_are_rejected(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_scenario_dict_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown scenario key"):
        MockScenario.from_dict({"surprise": 1})


def test_scenario_dict_rejects_unknown_reading_keys() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        MockScenario.from_dict({"readings": [{"pid": "010C", "value": 1.0, "extra": 1}]})


def test_scenario_dict_rejects_missing_reading_keys() -> None:
    with pytest.raises(ValueError, match="missing the key"):
        MockScenario.from_dict({"readings": [{"pid": "010C"}]})


def test_scenario_dict_rejects_unknown_failure_mode() -> None:
    with pytest.raises(ValueError):
        MockScenario.from_dict({"failure": "explode"})


def test_scenario_with_failure_returns_a_copy(adapter: MockObdAdapter) -> None:
    original = adapter.scenario
    broken = original.with_failure(FailureMode.READ_TIMEOUT)

    assert original.failure is FailureMode.NONE
    assert broken.failure is FailureMode.READ_TIMEOUT
    assert broken.name == original.name
