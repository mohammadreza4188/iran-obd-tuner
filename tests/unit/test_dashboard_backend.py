"""Dashboard wiring in the application layer (no Qt needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from iran_obd_tuner.app import dashboard
from iran_obd_tuner.app.dashboard import AppDashboardBackend
from iran_obd_tuner.core.config import AppSettings
from iran_obd_tuner.core.exceptions import AdapterNotImplementedError


def test_simulation_session_uses_the_simulated_drive_cycle() -> None:
    backend = AppDashboardBackend(AppSettings())

    session = backend.create_session(simulation=True, port=None)

    assert session.is_connected is False  # creating a session never connects
    assert session.simulated is True
    assert session.adapter_info.name == "mock"
    assert "drive cycle" in session.adapter_info.notes


def test_real_adapter_request_never_falls_back_to_the_simulator() -> None:
    backend = AppDashboardBackend(AppSettings())

    with pytest.raises(AdapterNotImplementedError, match="elm327"):
        backend.create_session(simulation=False, port="COM3")


def test_real_adapter_ignores_a_mock_adapter_setting() -> None:
    """Even with ``adapter = "mock"`` configured, "real adapter" means a real adapter."""
    backend = AppDashboardBackend(AppSettings(adapter="mock"))

    with pytest.raises(AdapterNotImplementedError):
        backend.create_session(simulation=False, port=None)


def test_output_dir_and_timeouts_come_from_the_settings(tmp_path: Path) -> None:
    settings = AppSettings(output_dir=tmp_path / "captures", connect_timeout_s=1.5)
    backend = AppDashboardBackend(settings)

    assert backend.output_dir == tmp_path / "captures"
    assert backend.create_session(simulation=True, port=None).connect_timeout_s == 1.5


def test_port_listing_is_delegated_to_the_hardware_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "list_serial_ports", lambda: ("COM9",))

    assert AppDashboardBackend(AppSettings()).list_serial_ports() == ("COM9",)
