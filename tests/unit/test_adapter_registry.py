"""Adapter registry: which adapters exist, and honest handling of the missing ones."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import (
    AdapterNotFoundError,
    AdapterNotImplementedError,
    ConfigurationError,
)
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy
from iran_obd_tuner.hardware.elm327 import Elm327Adapter
from iran_obd_tuner.hardware.mock import MockObdAdapter, MockScenario
from iran_obd_tuner.hardware.registry import (
    MOCK_ADAPTER_NAME,
    create_adapter,
    declared_adapters,
    implemented_adapters,
)


def test_simulator_and_elm327_are_implemented() -> None:
    implemented = {info.name for info in implemented_adapters()}

    assert implemented == {MOCK_ADAPTER_NAME, "elm327"}


def test_declared_adapters_are_explicit_about_their_status() -> None:
    declared = {info.name: info for info in declared_adapters()}

    assert {"mock", "elm327", "can-bench"} <= set(declared)
    can_bench = declared["can-bench"]
    assert can_bench.implemented is False
    assert "Not implemented" in can_bench.notes
    elm327 = declared["elm327"]
    assert elm327.hardware_required is True
    assert elm327.simulated is False
    assert "NOT yet validated" in elm327.notes  # honest about missing hardware validation


def test_create_mock_adapter_is_case_insensitive() -> None:
    assert isinstance(create_adapter("MOCK"), MockObdAdapter)
    assert isinstance(create_adapter(" mock "), MockObdAdapter)


def test_create_adapter_carries_the_scenario_and_policy() -> None:
    scenario = MockScenario.default().with_failure(MockScenario.default().failure)
    policy = SafetyPolicy(allowed=frozenset(OperationClass))

    adapter = create_adapter("mock", mock_scenario=scenario, policy=policy)

    assert adapter.scenario is scenario
    assert adapter.policy is policy


def test_declared_but_unimplemented_adapter_is_refused_with_reasons() -> None:
    with pytest.raises(AdapterNotImplementedError) as caught:
        create_adapter("can-bench")

    message = str(caught.value)
    assert "not implemented" in message
    assert "python-can" in message  # names the missing dependency


def test_elm327_requires_an_explicit_port() -> None:
    """No automatic probing: that would send commands to unknown serial devices."""
    for port in (None, "", "   "):
        with pytest.raises(ConfigurationError, match="explicit serial port"):
            create_adapter("elm327", serial_port=port)


def test_elm327_is_created_with_the_serial_settings_but_not_opened() -> None:
    adapter = create_adapter(
        "ELM327", serial_port="COM7", serial_baudrate=9600, command_timeout_s=1.5
    )

    assert isinstance(adapter, Elm327Adapter)
    assert adapter.port == "COM7"
    assert adapter.is_connected is False  # creating an adapter never opens the port


def test_unknown_adapter_lists_the_known_ones() -> None:
    with pytest.raises(AdapterNotFoundError) as caught:
        create_adapter("obdlink")

    message = str(caught.value)
    assert "unknown adapter" in message
    assert "mock" in message
