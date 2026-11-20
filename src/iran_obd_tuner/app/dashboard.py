"""Application wiring for the desktop dashboard.

Implements :class:`iran_obd_tuner.ui.backend.DashboardBackend`: this is where the dashboard's
"simulation" and "real adapter" choices are mapped to concrete adapters from the registry.

* Simulation → the mock adapter with :meth:`MockScenario.drive_cycle` (time-varying values,
  ``AdapterInfo.simulated = True``).
* UDS identification → :class:`UdsIdentificationSession` over the CAN interface the user
  chose (``virtual-sim`` = the simulated ECU), with the identifiers and padding from the
  settings; the extended session only when the user ticked it for this run.
* Vehicle knowledge base → the bundled vehicle packs plus ``knowledge_dir/vehicles``, loaded
  once (read-only) for the Vehicle Knowledge tab.
* Real adapter → the read-only ``elm327`` adapter on the port the user selected, with the
  baud rate and command timeout from the settings. Without an explicit port the request
  fails with :class:`~iran_obd_tuner.core.exceptions.ConfigurationError` (no automatic
  probing of serial ports). It never falls back to the simulator.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from iran_obd_tuner.app.application import uds_link_config
from iran_obd_tuner.core.config import CAN_INTERFACE_CHOICES, AppSettings
from iran_obd_tuner.core.exceptions import ConfigurationError
from iran_obd_tuner.core.knowledge import LocalizedText, Manufacturer, VehicleProfile
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.database.repository import JsonKnowledgeRepository, KnowledgeRepository
from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase, VehicleKnowledgeBase
from iran_obd_tuner.diagnostics.dyno import VehicleParameters
from iran_obd_tuner.diagnostics.ecu_identification import UdsIdentificationSession
from iran_obd_tuner.diagnostics.live_data import LiveDataSession
from iran_obd_tuner.hardware.mock import MockScenario
from iran_obd_tuner.hardware.registry import (
    ELM327_ADAPTER_NAME,
    MOCK_ADAPTER_NAME,
    create_adapter,
)
from iran_obd_tuner.hardware.serial_ports import list_serial_ports
from iran_obd_tuner.hardware.uds_link import SIMULATED_INTERFACE
from iran_obd_tuner.ui.backend import CanDefaults

__all__ = ["AppDashboardBackend"]


class AppDashboardBackend:
    """Dashboard services built from the application settings.

    Args:
        settings: Resolved settings (timeouts, output directory, knowledge directory).
        knowledge: Knowledge repository; built from ``settings.knowledge_dir`` when omitted.
    """

    def __init__(
        self,
        settings: AppSettings,
        knowledge: KnowledgeRepository | None = None,
        vehicle_knowledge: VehicleKnowledgeBase | None = None,
    ) -> None:
        self._settings = settings
        self._knowledge = (
            knowledge if knowledge is not None else JsonKnowledgeRepository(settings.knowledge_dir)
        )
        self._vehicles = (
            vehicle_knowledge
            if vehicle_knowledge is not None
            else JsonVehicleKnowledgeBase(settings.knowledge_dir)
        )
        # One log for the whole dashboard: every session's adapter records into it.
        self._traffic_log = TrafficLog()
        try:
            self._dyno_parameters = self._build_dyno_parameters()
        except ValueError as exc:
            raise ConfigurationError(f"invalid dyno_* setting: {exc}") from exc

    @property
    def settings(self) -> AppSettings:
        """Settings this backend was built from."""
        return self._settings

    @property
    def dyno_parameters(self) -> VehicleParameters:
        return self._dyno_parameters

    def _build_dyno_parameters(self) -> VehicleParameters:
        s = self._settings
        return VehicleParameters(
            curb_mass_kg=s.dyno_curb_mass_kg,
            extra_mass_kg=s.dyno_extra_mass_kg,
            drag_coefficient=s.dyno_drag_coefficient,
            frontal_area_m2=s.dyno_frontal_area_m2,
            drivetrain_loss=s.dyno_drivetrain_loss,
            rolling_resistance=s.dyno_rolling_resistance,
            air_density_kg_m3=s.dyno_air_density_kg_m3,
        )

    @property
    def traffic_log(self) -> TrafficLog:
        return self._traffic_log

    @property
    def output_dir(self) -> Path:
        return self._settings.output_dir

    def list_serial_ports(self) -> tuple[str, ...]:
        return list_serial_ports()

    @property
    def can_defaults(self) -> CanDefaults:
        s = self._settings
        return CanDefaults(
            interfaces=CAN_INTERFACE_CHOICES,
            simulated_interface=SIMULATED_INTERFACE,
            interface=s.can_interface,
            channel=s.can_channel,
            bitrate=s.can_bitrate,
        )

    @property
    def vehicle_profiles(self) -> tuple[VehicleProfile, ...]:
        return self._vehicles.vehicles()

    @property
    def vehicle_manufacturers(self) -> tuple[Manufacturer, ...]:
        return self._vehicles.manufacturers()

    @property
    def vehicle_guidance(self) -> Mapping[str, LocalizedText]:
        return self._vehicles.guidance_catalog()

    @property
    def vehicle_knowledge_source(self) -> str:
        return self._vehicles.source

    def create_uds_session(
        self,
        *,
        interface: str,
        channel: str | None,
        bitrate: int,
        allow_extended_session: bool,
    ) -> UdsIdentificationSession:
        config = uds_link_config(
            self._settings, interface=interface, channel=channel or None, bitrate=bitrate
        )
        return UdsIdentificationSession(
            config,
            allow_extended_session=allow_extended_session,
            traffic_log=self._traffic_log,
        )

    def create_session(self, *, simulation: bool, port: str | None) -> LiveDataSession:
        if simulation:
            adapter = create_adapter(
                MOCK_ADAPTER_NAME,
                mock_scenario=MockScenario.drive_cycle(),
                traffic_log=self._traffic_log,
            )
        else:
            adapter = create_adapter(
                ELM327_ADAPTER_NAME,
                serial_port=port,
                serial_baudrate=self._settings.serial_baudrate,
                command_timeout_s=self._settings.command_timeout_s,
                traffic_log=self._traffic_log,
            )
        return LiveDataSession(
            adapter,
            self._knowledge,
            connect_timeout_s=self._settings.connect_timeout_s,
        )
