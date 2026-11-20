"""Application wiring: settings, logging, adapter, knowledge repository, services.

This is the one place where concrete implementations are chosen and handed to the layers
that only know interfaces. Keeping it small is deliberate: the CLI, a future GUI and a
future test harness all build an :class:`Application` instead of assembling pieces
themselves.
"""

from __future__ import annotations

from collections.abc import Sequence

from iran_obd_tuner.core.config import AppSettings
from iran_obd_tuner.core.logging import configure_logging, get_logger
from iran_obd_tuner.database.repository import JsonKnowledgeRepository, KnowledgeRepository
from iran_obd_tuner.diagnostics.models import DiagnosticReport
from iran_obd_tuner.diagnostics.service import DiagnosticService
from iran_obd_tuner.hardware.base import ObdAdapter
from iran_obd_tuner.hardware.registry import create_adapter
from iran_obd_tuner.hardware.uds_link import UdsLinkConfig

__all__ = ["Application", "uds_link_config"]


class Application:
    """Wired application: adapter + knowledge + diagnostic service.

    Args:
        settings: Resolved settings (see :func:`iran_obd_tuner.core.config.load_settings`).
        adapter: Adapter to use. When omitted, one is created from
            ``settings.adapter`` through the registry (default: the simulator).
        knowledge: Knowledge repository to use. When omitted, the JSON repository is built
            with ``settings.knowledge_dir`` as optional override directory.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        adapter: ObdAdapter | None = None,
        knowledge: KnowledgeRepository | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger("app")
        self._adapter = (
            adapter
            if adapter is not None
            else create_adapter(
                settings.adapter,
                serial_port=settings.serial_port,
                serial_baudrate=settings.serial_baudrate,
                command_timeout_s=settings.command_timeout_s,
            )
        )
        self._knowledge = (
            knowledge if knowledge is not None else JsonKnowledgeRepository(settings.knowledge_dir)
        )
        self._diagnostics = DiagnosticService(
            self._adapter,
            self._knowledge,
            live_data_pids=settings.live_data_pids,
            connect_timeout_s=settings.connect_timeout_s,
        )

    @classmethod
    def from_settings(cls, settings: AppSettings) -> Application:
        """Configure logging from ``settings`` and build the application.

        Only entry points (CLI, tests, future UI) should call this: it changes global
        logging state.
        """
        configure_logging(settings.log_level, settings.log_file, force=True)
        return cls(settings)

    @property
    def settings(self) -> AppSettings:
        """Settings this application was built from."""
        return self._settings

    @property
    def adapter(self) -> ObdAdapter:
        """Adapter used for diagnostic sessions."""
        return self._adapter

    @property
    def knowledge(self) -> KnowledgeRepository:
        """Knowledge repository used to interpret trouble codes."""
        return self._knowledge

    @property
    def diagnostics(self) -> DiagnosticService:
        """The diagnostic service."""
        return self._diagnostics

    def diagnose(
        self,
        *,
        pids: Sequence[str] | None = None,
        read_vin: bool = True,
    ) -> DiagnosticReport:
        """Run one read-only diagnostic session and return its report.

        Convenience wrapper around
        :meth:`iran_obd_tuner.diagnostics.service.DiagnosticService.collect_report`.
        """
        self._log.debug("application: starting diagnostic run")
        return self._diagnostics.collect_report(pids=pids, read_vin=read_vin)


def uds_link_config(
    settings: AppSettings,
    *,
    interface: str | None = None,
    channel: str | None = None,
    bitrate: int | None = None,
    request_id: int | None = None,
    response_id: int | None = None,
) -> UdsLinkConfig:
    """UDS link configuration from the settings, with per-run overrides.

    Raises:
        ConfigurationError: For an unsupported interface or invalid identifiers.
    """
    return UdsLinkConfig(
        interface=interface or settings.can_interface,
        channel=channel if channel is not None else settings.can_channel,
        bitrate=bitrate or settings.can_bitrate,
        request_id=settings.uds_request_id if request_id is None else request_id,
        response_id=settings.uds_response_id if response_id is None else response_id,
        padding=settings.isotp_padding,
    )
