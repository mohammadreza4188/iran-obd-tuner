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

from iran_obd_tuner.core.dtc import DtcStatus
from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.core.pids import DEFAULT_LIVE_DATA_PIDS, normalize_pid
from iran_obd_tuner.database.repository import DtcDefinition, KnowledgeRepository
from iran_obd_tuner.diagnostics.models import (
    DiagnosticReport,
    DtcCategoryResult,
    FreezeFrameSnapshot,
    MonitorStatusResult,
    PidReading,
    PidStatus,
    ReadOutcome,
    TroubleCode,
)
from iran_obd_tuner.hardware.base import AdapterInfo, ObdAdapter

__all__ = [
    "FREEZE_FRAME_PIDS",
    "DiagnosticService",
    "interpret_trouble_code",
    "read_dtc_categories",
    "read_freeze_frame_snapshot",
    "read_monitor_status_result",
    "vehicle_info_for",
]

#: Freeze frame parameters read by default (service 02, frame 00), in service 01 form:
#: load, coolant, short/long term fuel trim bank 1, rpm, speed, intake air, throttle.
FREEZE_FRAME_PIDS: tuple[str, ...] = (
    "0104",
    "0105",
    "0106",
    "0107",
    "010C",
    "010D",
    "010F",
    "0111",
)


def interpret_trouble_code(
    code: str,
    knowledge: KnowledgeRepository | None,
    status: DtcStatus = DtcStatus.STORED,
) -> TroubleCode:
    """Interpret one raw trouble code string with an optional knowledge repository.

    Shared by the one-shot report workflow and the dashboard's live session so a code is
    always described the same way.

    Raises:
        ValueError: If ``code`` is not a well-formed trouble code.
    """
    definition: DtcDefinition | None = None
    if knowledge is not None:
        definition = knowledge.describe_dtc(code)
    return TroubleCode.from_code(code, definition, status)


def vehicle_info_for(adapter: ObdAdapter, vin: str | None) -> VehicleInfo:
    """Build the vehicle identification from what an adapter reported.

    ``protocol`` is the protocol negotiated on the connection
    (:attr:`ObdAdapter.active_protocol`), never a guess from the adapter's capabilities;
    ``simulated`` comes from the adapter's explicit declaration, never from its kind.
    """
    info: AdapterInfo = adapter.info
    return VehicleInfo(
        vin=vin,
        protocol=adapter.active_protocol,
        adapter_name=info.name,
        simulated=info.simulated,
    )


# --------------------------------------------------------------------------- #
# extended read-only sections: each one reports its own outcome
# --------------------------------------------------------------------------- #


def _outcome_of(error: Exception) -> ReadOutcome:
    """Map an adapter error to a section outcome; a lost connection is never mapped."""
    if isinstance(error, AdapterConnectionError):
        raise error
    if isinstance(error, (UnsupportedOperationError, UnsupportedPidError)):
        return ReadOutcome.UNSUPPORTED
    if isinstance(error, AdapterTimeoutError):
        return ReadOutcome.NO_RESPONSE
    return ReadOutcome.ERROR


_SECTION_ERRORS = (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    UnsupportedOperationError,
    UnsupportedPidError,
    ValueError,  # a malformed code string from the ECU
)


def read_dtc_categories(
    adapter: ObdAdapter, knowledge: KnowledgeRepository | None
) -> tuple[DtcCategoryResult, ...]:
    """Read services 03, 07 and 0A independently of each other.

    A category that is unsupported or unanswered is reported with that outcome; the other
    categories are still read. Only a lost connection aborts.
    """
    results: list[DtcCategoryResult] = []
    for status in DtcStatus:
        try:
            raw = adapter.read_trouble_codes(status)
            codes = tuple(interpret_trouble_code(code, knowledge, status) for code in raw)
        except _SECTION_ERRORS as exc:
            results.append(DtcCategoryResult(status, _outcome_of(exc), detail=str(exc)))
        else:
            results.append(DtcCategoryResult(status, ReadOutcome.OK, codes))
    return tuple(results)


def read_monitor_status_result(adapter: ObdAdapter) -> MonitorStatusResult:
    """Read PID 0101; unsupported or unanswered is an outcome, not an exception."""
    try:
        status = adapter.read_monitor_status()
    except _SECTION_ERRORS as exc:
        return MonitorStatusResult(_outcome_of(exc), detail=str(exc))
    return MonitorStatusResult(ReadOutcome.OK, status)


def _freeze_frame_reading(adapter: ObdAdapter, pid: str, supported: frozenset[str]) -> PidReading:
    if pid not in supported:
        return PidReading(pid, PidStatus.UNSUPPORTED, detail="not stored in the freeze frame")
    try:
        measurement = adapter.read_freeze_frame_value(pid)
    except AdapterConnectionError:
        raise
    except (UnsupportedOperationError, UnsupportedPidError) as exc:
        return PidReading(pid, PidStatus.UNSUPPORTED, detail=str(exc))
    except AdapterTimeoutError as exc:
        return PidReading(pid, PidStatus.NO_RESPONSE, detail=str(exc))
    except (ProtocolError, ValueError) as exc:
        return PidReading(pid, PidStatus.MALFORMED, detail=str(exc))
    return PidReading(pid, PidStatus.OK, measurement=measurement)


def read_freeze_frame_snapshot(
    adapter: ObdAdapter,
    knowledge: KnowledgeRepository | None,
    pids: Sequence[str] = FREEZE_FRAME_PIDS,
) -> FreezeFrameSnapshot:
    """Read freeze frame 00: the trigger first (``00 00`` means no frame is stored, and then
    nothing else is requested), then the supported-PID bitmask, then each parameter."""
    try:
        trigger = adapter.read_freeze_frame_trigger()
        if trigger is None:
            return FreezeFrameSnapshot(ReadOutcome.OK, present=False)
        supported = frozenset(adapter.read_freeze_frame_supported_pids())
        trigger_code = interpret_trouble_code(trigger, knowledge)
    except _SECTION_ERRORS as exc:
        return FreezeFrameSnapshot(_outcome_of(exc), detail=str(exc))
    readings = tuple(_freeze_frame_reading(adapter, normalize_pid(pid), supported) for pid in pids)
    return FreezeFrameSnapshot(
        ReadOutcome.OK, present=True, trigger=trigger_code, readings=readings
    )


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
        return interpret_trouble_code(code, self._knowledge)

    def collect_report(
        self,
        *,
        pids: Sequence[str] | None = None,
        read_vin: bool = True,
        read_freeze_frame: bool = True,
    ) -> DiagnosticReport:
        """Run the full read-only diagnostic workflow and return its report.

        Steps, all inside one connection: vehicle identification, supported parameters, the
        requested measurements, PID 0101, stored/pending/permanent trouble codes and freeze
        frame 00. The extended sections are read independently: one that is unsupported or
        unanswered is reported with its outcome while the others are still read. The
        connection is closed even when a step raises.

        Args:
            pids: Parameters to read; defaults to the configured live data list.
            read_vin: Whether to attempt reading the VIN.
            read_freeze_frame: Whether to read freeze frame 00.

        Returns:
            The assembled report.

        Raises:
            AdapterError: On connection failures, and on timeout or protocol failures of
                the identification and live data steps.
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
            monitor_status = read_monitor_status_result(self._adapter)
            categories = read_dtc_categories(self._adapter, self._knowledge)
            freeze_frame = (
                read_freeze_frame_snapshot(self._adapter, self._knowledge)
                if read_freeze_frame
                else None
            )

        stored = next(c for c in categories if c.status is DtcStatus.STORED)
        trouble_codes = stored.codes
        adapter_info = self._adapter.info
        report = DiagnosticReport(
            vehicle=vehicle_info_for(self._adapter, vin),
            adapter=adapter_info,
            measurements=batch.measurements,
            supported_pids=supported,
            skipped_pids=batch.skipped_pids,
            trouble_codes=trouble_codes,
            duration_s=round(time.monotonic() - started, 3),
            knowledge_source=None if self._knowledge is None else self._knowledge.source,
            knowledge_metadata=None if self._knowledge is None else self._knowledge.metadata(),
            dtc_categories=categories,
            monitor_status=monitor_status,
            freeze_frame=freeze_frame,
        )

        self._log.info(
            "diagnostic session finished: %d measurement(s), %d trouble code(s), %d skipped",
            len(report.measurements),
            len(report.trouble_codes),
            len(report.skipped_pids),
        )
        return report
