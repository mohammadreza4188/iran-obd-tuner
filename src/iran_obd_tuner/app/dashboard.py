"""Application wiring for the desktop dashboard.

Implements :class:`iran_obd_tuner.ui.backend.DashboardBackend`: this is where the dashboard's
"simulation" and "real adapter" choices are mapped to concrete adapters from the registry.

* Simulation → the mock adapter with :meth:`MockScenario.drive_cycle` (time-varying values,
  ``AdapterInfo.simulated = True``).
* Real adapter → the read-only ``elm327`` adapter on the port the user selected, with the
  baud rate and command timeout from the settings. Without an explicit port the request
  fails with :class:`~iran_obd_tuner.core.exceptions.ConfigurationError` (no automatic
  probing of serial ports). It never falls back to the simulator.
"""

from __future__ import annotations

from pathlib import Path

from iran_obd_tuner.core.config import AppSettings
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.database.repository import JsonKnowledgeRepository, KnowledgeRepository
from iran_obd_tuner.diagnostics.live_data import LiveDataSession
from iran_obd_tuner.hardware.mock import MockScenario
from iran_obd_tuner.hardware.registry import (
    ELM327_ADAPTER_NAME,
    MOCK_ADAPTER_NAME,
    create_adapter,
)
from iran_obd_tuner.hardware.serial_ports import list_serial_ports

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
    ) -> None:
        self._settings = settings
        self._knowledge = (
            knowledge if knowledge is not None else JsonKnowledgeRepository(settings.knowledge_dir)
        )
        # One log for the whole dashboard: every session's adapter records into it.
        self._traffic_log = TrafficLog()

    @property
    def settings(self) -> AppSettings:
        """Settings this backend was built from."""
        return self._settings

    @property
    def traffic_log(self) -> TrafficLog:
        return self._traffic_log

    @property
    def output_dir(self) -> Path:
        return self._settings.output_dir

    def list_serial_ports(self) -> tuple[str, ...]:
        return list_serial_ports()

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
