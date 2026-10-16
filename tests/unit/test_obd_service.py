"""The dashboard's OBD service: optional python-OBD, simulation mode and the write guard.

These tests never touch hardware: the real adapter path is either not reachable (python-OBD
missing) or explicitly disabled with ``monkeypatch``, and the simulation path is pure Python.
"""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy
from iran_obd_tuner.diagnostics import obd_service
from iran_obd_tuner.diagnostics.obd_service import DtcItem, LiveSample, ObdService


def _allowing_policy() -> SafetyPolicy:
    """A deliberately permissive policy, used only to prove the guard is the only barrier."""
    return SafetyPolicy(allowed=frozenset(OperationClass))


def test_module_imports_without_the_optional_dependency() -> None:
    """Importing must not require python-OBD, a port scan or an open adapter."""
    assert obd_service.obd is None or hasattr(obd_service.obd, "OBD")


def test_service_starts_disconnected_and_does_not_scan_on_construction() -> None:
    service = ObdService()

    assert service.connected is False
    assert service.simulation is False
    assert service.policy.allows(OperationClass.READ_ONLY)
    assert not service.policy.allows(OperationClass.STATE_CHANGING)
    assert service.list_ports()  # at least "Auto"; no exception without python-OBD


def test_reads_without_a_connection_return_empty_data() -> None:
    service = ObdService()

    sample = service.read_live()

    assert isinstance(sample, LiveSample)
    assert sample.rpm == 0.0
    assert service.read_dtc() == []


def test_simulation_mode_provides_live_data_and_codes() -> None:
    service = ObdService()
    messages: list[str] = []
    service.on_log = messages.append

    assert service.connect(simulation=True) is True
    assert service.connected is True
    assert service.simulation is True
    assert messages == ["Simulation mode started"]

    sample = service.read_live()
    assert sample.rpm > 0
    assert sample.coolant > 0

    codes = service.read_dtc()
    assert [code.code for code in codes] == ["P0300", "P0171"]
    assert all(isinstance(code, DtcItem) for code in codes)

    service.disconnect()
    assert service.connected is False
    assert service.simulation is False


def test_connect_is_idempotent_through_a_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(obd_service, "obd", None)
    service = ObdService()

    assert service.connect(simulation=True) is True
    assert service.connect(simulation=True) is True  # re-connect replaces the old session
    assert service.connected is True


def test_real_connection_without_python_obd_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real path is disabled by removing the optional dependency, never by opening a port."""
    monkeypatch.setattr(obd_service, "obd", None)
    service = ObdService()
    messages: list[str] = []
    service.on_log = messages.append

    assert service.connect(port="COM1", simulation=False) is False
    assert service.connected is False
    assert any("not installed" in message for message in messages)


def test_clearing_codes_is_refused_by_the_shipped_policy() -> None:
    service = ObdService()
    service.connect(simulation=True)

    with pytest.raises(SafetyGuardError) as caught:
        service.clear_dtc()

    assert "read-only" in str(caught.value)
    assert OperationClass.STATE_CHANGING.value in str(caught.value)
    # the refusal happened before anything was cleared
    assert [code.code for code in service.read_dtc()] == ["P0300", "P0171"]


def test_clearing_codes_needs_an_explicit_code_level_policy() -> None:
    """The guard is the only barrier: an explicitly permissive policy reaches the sim path."""
    service = ObdService(policy=_allowing_policy())
    service.connect(simulation=True)

    assert service.clear_dtc() is True
    assert service.read_dtc() == []


def test_permissive_policy_cannot_invent_a_real_write_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(obd_service, "obd", None)
    service = ObdService(policy=_allowing_policy())

    assert service.clear_dtc() is False  # nothing connected, nothing written


def test_disconnect_on_a_closed_service_is_harmless() -> None:
    service = ObdService()

    service.disconnect()

    assert service.connected is False
