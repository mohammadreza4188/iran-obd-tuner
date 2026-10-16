"""The diagnostic workflow: connect, identify, read, interpret, report.

:class:`DiagnosticService` is the single place where a diagnostic run is defined. It is
strictly read-only, and it is deliberately independent of the concrete adapter (it talks to
:class:`~iran_obd_tuner.hardware.base.ObdAdapter`) and of the knowledge storage (it talks to
:class:`~iran_obd_tuner.database.repository.KnowledgeRepository`), so it can be exercised
against the simulated vehicle and against a stub knowledge repository in tests.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.core.pids import DEFAULT_LIVE_DATA_PIDS, normalize_pid
from iran_obd_tuner.database.repository import DtcDefinition, KnowledgeRepository
from iran_obd_tuner.diagnostics.models import DiagnosticReport, TroubleCode
from iran_obd_tuner.hardware.base import AdapterKind, ObdAdapter

__all__ = ["DiagnosticService"]


class DiagnosticService:
    """Runs one read-only diagnostic workflow over an adapter.

    Args:
        adapter: Transport to use. Never connects at construction time.
        knowledge: Knowledge repository for trouble code descriptions. Without one, codes
            are reported without descriptions (which is honest, not a failure).
        live_data_pids: Parameters read when a caller does not pass an explicit list.
        connect_timeout_s: Seconds to wait for the connection to be established.
    """

    def __init__(
        self,
        adapter: ObdAdapter,
        knowledge: KnowledgeRepository | None = None,
        *,
        live_data_pids: Sequence[str] = DEFAULT_LIVE_DATA_PIDS,
        connect_timeout_s: float = 5.0,
    ) -> None:
        self._adapter = adapter
        self._knowledge = knowledge
        self._live_data_pids = tuple(normalize_pid(pid) for pid in live_data_pids)
        self._connect_timeout_s = connect_timeout_s
        self._log = get_logger("diagnostics.service")

    @property
    def adapter(self) -> ObdAdapter:
        """The adapter used by this service."""
        return self._adapter

    @property
    def knowledge(self) -> KnowledgeRepository | None:
        """The knowledge repository used to interpret trouble codes."""
        return self._knowledge

    @property
    def live_data_pids(self) -> tuple[str, ...]:
        """Parameters read when no explicit list is given."""
        return self._live_data_pids

    def describe_trouble_code(self, code: str) -> TroubleCode:
        """Interpret one raw trouble code string.

        Args:
            code: Code as returned by the adapter, e.g. ``"P0301"``.

        Returns:
            The code with its knowledge database description when one exists.

        Raises:
            ValueError: If ``code`` is not a well-formed trouble code.
        """
        definition: DtcDefinition | None = None
        if self._knowledge is not None:
            definition = self._knowledge.describe_dtc(code)
        return TroubleCode.from_code(code, definition)

    def collect_report(
        self,
        *,
        pids: Sequence[str] | None = None,
        read_vin: bool = True,
    ) -> DiagnosticReport:
        """Run the full read-only diagnostic workflow and return its report.

        Steps, all inside one connection: vehicle identification, supported parameters, the
        requested measurements and the stored trouble codes. The connection is closed even
        when a step raises.

        Args:
            pids: Parameters to read; defaults to the configured live data list.
            read_vin: Whether to attempt reading the VIN.

        Returns:
            The assembled report.

        Raises:
            AdapterError: On connection, timeout or protocol failures.
        """
        requested = (
            tuple(normalize_pid(pid) for pid in pids) if pids is not None else self._live_data_pids
        )
        started = time.monotonic()
        self._log.info(
            "starting diagnostic session on %s adapter (%d parameter(s) requested)",
            self._adapter.info.name,
            len(requested),
        )

        with self._adapter.session(timeout_s=self._connect_timeout_s):
            vin = self._adapter.read_vin() if read_vin else None
            supported = self._adapter.read_supported_pids()
            batch = self._adapter.read_measurements(requested)
            raw_codes = self._adapter.read_diagnostic_trouble_codes()

        trouble_codes = tuple(self.describe_trouble_code(code) for code in raw_codes)
        adapter_info = self._adapter.info
        report = DiagnosticReport(
            vehicle=VehicleInfo(
                vin=vin,
                protocol=adapter_info.protocols[0] if adapter_info.protocols else None,
                adapter_name=adapter_info.name,
                simulated=adapter_info.kind is AdapterKind.MOCK,
            ),
            adapter=adapter_info,
            measurements=batch.measurements,
            supported_pids=supported,
            skipped_pids=batch.skipped_pids,
            trouble_codes=trouble_codes,
            duration_s=round(time.monotonic() - started, 3),
            knowledge_source=None if self._knowledge is None else self._knowledge.source,
            knowledge_metadata=None if self._knowledge is None else self._knowledge.metadata(),
        )

        self._log.info(
            "diagnostic session finished: %d measurement(s), %d trouble code(s), %d skipped",
            len(report.measurements),
            len(report.trouble_codes),
            len(report.skipped_pids),
        )
        return report
