"""Live data session for the dashboard: one read-only connection, polled repeatedly.

:class:`DiagnosticService` runs a one-shot workflow (connect, read, disconnect). A dashboard
needs a connection that stays open while values are polled; :class:`LiveDataSession`
provides that, over the same :class:`~iran_obd_tuner.hardware.base.ObdAdapter` contract and
the same trouble code interpretation as the report workflow. There is no second data path.

Honesty rules implemented here:

* a parameter that could not be read is reported with an explicit :class:`PidStatus`
  (unsupported, no answer, malformed response) and **no value** — never a fallback number;
* a failed trouble code read raises instead of returning "no codes";
* whether data is simulated comes from ``AdapterInfo.simulated`` and travels with every
  :class:`LiveSnapshot`.

Not implemented here: freeze frames, pending/permanent trouble codes. The session is not
thread-safe; the dashboard serialises all calls on one worker thread.
State-changing requests are refused by the adapter's safety gate; no implementation exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VehicleInfo, utc_now
from iran_obd_tuner.core.pids import normalize_pid
from iran_obd_tuner.database.repository import KnowledgeRepository
from iran_obd_tuner.diagnostics.models import (
    DtcCategoryResult,
    FreezeFrameSnapshot,
    MonitorStatusResult,
    PidReading,
    PidStatus,
    TroubleCode,
)
from iran_obd_tuner.diagnostics.service import (
    FREEZE_FRAME_PIDS,
    interpret_trouble_code,
    read_dtc_categories,
    read_freeze_frame_snapshot,
    read_monitor_status_result,
    vehicle_info_for,
)
from iran_obd_tuner.hardware.base import AdapterInfo, ObdAdapter

__all__ = ["LiveDataSession", "LiveSnapshot", "PidReading", "PidStatus"]


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """All parameter readings of one poll.

    Attributes:
        readings: One reading per requested parameter, in request order.
        simulated: ``True`` when the values come from a simulator, not a vehicle.
        timestamp: When the poll started (aware UTC).
    """

    readings: tuple[PidReading, ...]
    simulated: bool
    timestamp: datetime = field(default_factory=utc_now)

    def reading_for(self, pid: str) -> PidReading | None:
        """Return the reading for ``pid``, or ``None`` when it was not requested."""
        wanted = normalize_pid(pid)
        for reading in self.readings:
            if reading.pid == wanted:
                return reading
        return None

    def value_for(self, pid: str) -> float | None:
        """Return the decoded value for ``pid``, or ``None`` when it is not available."""
        reading = self.reading_for(pid)
        if reading is None or reading.measurement is None:
            return None
        return reading.measurement.value

    @property
    def has_values(self) -> bool:
        """``True`` when at least one parameter was read successfully."""
        return any(reading.status is PidStatus.OK for reading in self.readings)


class LiveDataSession:
    """A persistent, read-only connection used by the dashboard.

    Args:
        adapter: Transport to use. Never connects at construction time.
        knowledge: Knowledge repository for trouble code descriptions (optional).
        connect_timeout_s: Seconds to wait for the connection to be established.
    """

    def __init__(
        self,
        adapter: ObdAdapter,
        knowledge: KnowledgeRepository | None = None,
        *,
        connect_timeout_s: float = 5.0,
    ) -> None:
        self._adapter = adapter
        self._knowledge = knowledge
        self._connect_timeout_s = connect_timeout_s
        self._log = get_logger("diagnostics.live_data")

    @property
    def adapter_info(self) -> AdapterInfo:
        """Static description of the adapter behind this session."""
        return self._adapter.info

    @property
    def simulated(self) -> bool:
        """``True`` when every value of this session is simulated."""
        return self._adapter.info.simulated

    @property
    def is_connected(self) -> bool:
        """Whether the connection is open."""
        return self._adapter.is_connected

    @property
    def active_protocol(self) -> str | None:
        """Protocol negotiated with the vehicle, ``None`` until known."""
        return self._adapter.active_protocol

    @property
    def connect_timeout_s(self) -> float:
        """Seconds :meth:`connect` waits for the adapter."""
        return self._connect_timeout_s

    def connect(self) -> None:
        """Open the connection.

        Raises:
            AdapterError: On timeout, refusal or any other connection failure. The session
                is left disconnected; there is no fallback to another adapter.
        """
        self._adapter.connect(timeout_s=self._connect_timeout_s)

    def disconnect(self) -> None:
        """Close the connection. Safe to call when not connected."""
        self._adapter.disconnect()

    def poll(self, pids: Sequence[str]) -> LiveSnapshot:
        """Read every parameter in ``pids`` once.

        Per-parameter problems (unsupported, no answer, malformed answer) are reported in
        the snapshot and do not abort the poll.

        Raises:
            AdapterConnectionError: If the connection is not open or was lost.
        """
        started = utc_now()
        readings = tuple(self._read_one(normalize_pid(pid)) for pid in pids)
        return LiveSnapshot(readings=readings, simulated=self.simulated, timestamp=started)

    def read_trouble_codes(self) -> tuple[TroubleCode, ...]:
        """Read and interpret the stored trouble codes (service 03).

        Raises:
            AdapterError: If the codes could not be read. A failed read is never reported as
                "no trouble codes".
        """
        raw_codes = self._adapter.read_diagnostic_trouble_codes()
        return tuple(interpret_trouble_code(code, self._knowledge) for code in raw_codes)

    def read_vehicle_info(self) -> VehicleInfo:
        """Read the vehicle identification (VIN when available).

        Raises:
            AdapterError: If the request fails.
        """
        return vehicle_info_for(self._adapter, self._adapter.read_vin())

    def read_trouble_code_categories(self) -> tuple[DtcCategoryResult, ...]:
        """Read stored, pending and permanent codes; each category reports its own outcome.

        Raises:
            AdapterConnectionError: Only when the connection is lost (a category that is
                unsupported or unanswered is reported, not raised).
        """
        return read_dtc_categories(self._adapter, self._knowledge)

    def read_monitor_status(self) -> MonitorStatusResult:
        """Read PID 0101 (MIL, confirmed DTC count, readiness monitors)."""
        return read_monitor_status_result(self._adapter)

    def read_freeze_frame(self) -> FreezeFrameSnapshot:
        """Read freeze frame 00: trigger DTC and the standard frozen parameters."""
        return read_freeze_frame_snapshot(self._adapter, self._knowledge, FREEZE_FRAME_PIDS)

    def request_clear_trouble_codes(self) -> None:
        """Forward a "clear trouble codes" request to the adapter's safety gate.

        This build has no implementation for clearing codes: the adapter raises
        :class:`~iran_obd_tuner.core.exceptions.SafetyGuardError` under the default policy
        (and :class:`~iran_obd_tuner.core.exceptions.UnsupportedOperationError` under any
        other). The method exists so the dashboard can show that refusal honestly.
        """
        self._adapter.clear_diagnostic_trouble_codes()

    def _read_one(self, pid: str) -> PidReading:
        try:
            measurement = self._adapter.read_measurement(pid)
        except AdapterConnectionError:
            raise
        except (UnsupportedPidError, UnsupportedOperationError) as exc:
            return PidReading(pid=pid, status=PidStatus.UNSUPPORTED, detail=str(exc))
        except AdapterTimeoutError as exc:
            self._log.debug("no answer for %s: %s", pid, exc)
            return PidReading(pid=pid, status=PidStatus.NO_RESPONSE, detail=str(exc))
        except ProtocolError as exc:
            self._log.warning("malformed answer for %s: %s", pid, exc)
            return PidReading(pid=pid, status=PidStatus.MALFORMED, detail=str(exc))
        return PidReading(pid=pid, status=PidStatus.OK, measurement=measurement)
