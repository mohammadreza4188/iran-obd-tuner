"""What the dashboard needs from the rest of the application.

The UI declares this small contract and the application layer
(:mod:`iran_obd_tuner.app.dashboard`) implements it, so the UI never chooses an adapter,
never imports ``hardware`` and never touches a serial port itself. Tests hand the window a
backend that builds sessions over scripted simulated adapters.

This module imports no Qt, so it is importable (and type-checkable) without the ``[gui]``
extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.diagnostics.dyno import VehicleParameters
from iran_obd_tuner.diagnostics.ecu_identification import UdsIdentificationSession
from iran_obd_tuner.diagnostics.live_data import LiveDataSession

__all__ = ["CanDefaults", "DashboardBackend"]


@dataclass(frozen=True, slots=True)
class CanDefaults:
    """What the UDS identification panel starts with (from the settings).

    Attributes:
        interfaces: Selectable interfaces; ``simulated_interface`` is the simulated ECU.
        interface / channel / bitrate: Initial selection.
    """

    interfaces: tuple[str, ...]
    simulated_interface: str
    interface: str
    channel: str | None
    bitrate: int


class DashboardBackend(Protocol):
    """Services the dashboard window uses."""

    @property
    def dyno_parameters(self) -> VehicleParameters:
        """Default vehicle parameters of the virtual dyno (from the settings)."""
        ...

    @property
    def traffic_log(self) -> TrafficLog:
        """Shared record of adapter TX/RX lines shown in the traffic panel."""
        ...

    @property
    def output_dir(self) -> Path:
        """Default directory for live data recordings."""
        ...

    def list_serial_ports(self) -> tuple[str, ...]:
        """Serial ports the operating system reports (nothing is opened)."""
        ...

    @property
    def can_defaults(self) -> CanDefaults:
        """Initial choices of the UDS identification panel."""
        ...

    def create_uds_session(
        self,
        *,
        interface: str,
        channel: str | None,
        bitrate: int,
        allow_extended_session: bool,
    ) -> UdsIdentificationSession:
        """Build a one-shot UDS identification session (nothing is opened yet).

        Raises:
            ConfigurationError: Unsupported interface or invalid parameters. A real
                interface never falls back to the simulated ECU.
        """
        ...

    def create_session(self, *, simulation: bool, port: str | None) -> LiveDataSession:
        """Build a not-yet-connected session.

        Args:
            simulation: ``True`` for the simulated vehicle, ``False`` for a real adapter.
            port: Serial port chosen by the user, ``None`` for automatic selection.

        Raises:
            IranObdTunerError: If the requested adapter cannot be created, e.g. because it
                is declared but not implemented in this build. A real-adapter request never
                falls back to the simulator.
        """
        ...
