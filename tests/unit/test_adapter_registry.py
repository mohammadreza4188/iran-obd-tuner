"""Adapter registry: which adapters exist, and honest handling of the missing ones."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import AdapterNotFoundError, AdapterNotImplementedError
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy
from iran_obd_tuner.hardware.mock import MockObdAdapter, MockScenario
from iran_obd_tuner.hardware.registry import (
    MOCK_ADAPTER_NAME,
    create_adapter,
    declared_adapters,
    implemented_adapters,
)


def test_only_the_simulator_is_implemented() -> None:
    implemented = {info.name for info in implemented_adapters()}

    assert implemented == {MOCK_ADAPTER_NAME}


def test_declared_adapters_are_explicit_about_missing_support() -> None:
    declared = {info.name: info for info in declared_adapters()}

    assert {"mock", "elm327", "can-bench"} <= set(declared)
    for name in ("elm327", "can-bench"):
        info = declared[name]
        assert info.implemented is False
        assert info.hardware_required is True
        assert "Not implemented" in info.notes


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
        create_adapter("elm327")

    message = str(caught.value)
    assert "not implemented" in message
    assert "obd" in message  # names the missing extra


def test_unknown_adapter_lists_the_known_ones() -> None:
    with pytest.raises(AdapterNotFoundError) as caught:
        create_adapter("obdlink")

    message = str(caught.value)
    assert "unknown adapter" in message
    assert "mock" in message
