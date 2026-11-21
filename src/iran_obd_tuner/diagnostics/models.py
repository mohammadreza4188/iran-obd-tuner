"""Result models of the diagnostic workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from iran_obd_tuner.core.dtc import (
    DtcCodeStructure,
    DtcScope,
    DtcStatus,
    DtcSystem,
    parse_dtc_code,
)
from iran_obd_tuner.core.models import Measurement, VehicleInfo, utc_now
from iran_obd_tuner.core.monitors import MonitorStatus
from iran_obd_tuner.database.repository import DtcDefinition
from iran_obd_tuner.hardware.base import AdapterInfo

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "DiagnosticReport",
    "DtcCategoryResult",
    "FreezeFrameSnapshot",
    "MonitorStatusResult",
    "PidReading",
    "PidStatus",
    "ReadOutcome",
    "TroubleCode",
]

#: Version of the JSON produced by :meth:`DiagnosticReport.as_dict`. 2 added
#: ``dtc_categories``, ``monitor_status``, ``freeze_frame`` and per-code ``status``;
#: 3 added ``unanswered_pids`` (``skipped_pids`` now means "unsupported" only).
REPORT_SCHEMA_VERSION = 3


class PidStatus(StrEnum):
    """Outcome of reading one parameter."""

    #: A value was read and decoded.
    OK = "ok"
    #: The adapter or vehicle does not support the parameter.
    UNSUPPORTED = "unsupported"
    #: The vehicle did not answer in time.
    NO_RESPONSE = "no_response"
    #: An answer arrived but could not be decoded.
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class PidReading:
    """Result of reading one parameter in a poll.

    Attributes:
        pid: Requested parameter identifier in request form.
        status: Outcome of the read.
        measurement: The decoded measurement; only present when ``status`` is ``OK``.
        detail: Error message of a failed read, for logs and tooltips.
        sampled_at_s: Monotonic time (seconds) when this parameter's read finished; used by
            the physics analytics, which need per-parameter timing rather than the time of
            the whole poll. ``None`` when unknown (e.g. freeze frame values).
    """

    pid: str
    status: PidStatus
    measurement: Measurement | None = None
    detail: str | None = None
    sampled_at_s: float | None = None

    def __post_init__(self) -> None:
        if (self.status is PidStatus.OK) != (self.measurement is not None):
            raise ValueError("a measurement is present exactly when the status is OK")

    def as_dict(self) -> dict[str, Any]:
        """JSON view: the value and unit when read, otherwise only the status."""
        m = self.measurement
        return {
            "pid": self.pid,
            "status": str(self.status),
            "name": None if m is None else m.name,
            "value": None if m is None else m.value,
            "unit": None if m is None else str(m.unit),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TroubleCode:
    """A stored trouble code together with what the knowledge database knows about it.

    The description is optional on purpose: an unknown code is a normal result (especially
    with manufacturer specific codes), and it must be reported as "unknown", never guessed.

    Attributes:
        structure: Decoded structure (system, scope) of the code.
        description: Definition from the knowledge database, or ``None``.
        description_source: Which catalogue the description came from.
        status: Which service reported the code (stored 03, pending 07, permanent 0A).
    """

    structure: DtcCodeStructure
    description: str | None = None
    description_source: str | None = None
    status: DtcStatus = DtcStatus.STORED

    @property
    def code(self) -> str:
        """The code string, e.g. ``"P0301"``."""
        return self.structure.code

    @property
    def system(self) -> DtcSystem:
        """On-board system the code belongs to."""
        return self.structure.system

    @property
    def scope(self) -> DtcScope:
        """Whether the code is generic or manufacturer defined."""
        return self.structure.scope

    @property
    def is_known(self) -> bool:
        """``True`` when the knowledge database has a description for this code."""
        return self.description is not None

    @property
    def display_text(self) -> str:
        """Single line rendering, e.g. ``"P0301 — Cylinder 1 misfire detected"``."""
        if self.description is None:
            return f"{self.code} — description unknown (not in the knowledge database)"
        return f"{self.code} — {self.description}"

    @classmethod
    def from_code(
        cls,
        code: str,
        definition: DtcDefinition | None = None,
        status: DtcStatus = DtcStatus.STORED,
    ) -> TroubleCode:
        """Build a trouble code from a raw code string and an optional definition.

        Raises:
            ValueError: If ``code`` is not a well-formed trouble code.
        """
        return cls(
            structure=parse_dtc_code(code),
            description=None if definition is None else definition.description,
            description_source=None if definition is None else definition.source,
            status=status,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "code": self.code,
            "system": str(self.system),
            "scope": str(self.scope),
            "description": self.description,
            "description_source": self.description_source,
            "status": str(self.status),
        }


class ReadOutcome(StrEnum):
    """Outcome of one read-only request group (a DTC category, PID 0101, a freeze frame)."""

    #: Answered and decoded. An empty result (no codes) is a real answer.
    OK = "ok"
    #: The adapter or the vehicle does not support the request.
    UNSUPPORTED = "unsupported"
    #: The vehicle did not answer. Never to be read as "nothing to report".
    NO_RESPONSE = "no_response"
    #: An answer arrived but could not be decoded.
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DtcCategoryResult:
    """Trouble codes of one category (stored / pending / permanent) and how the read went.

    Attributes:
        status: The category (service 03, 07 or 0A).
        outcome: Whether the category could be read.
        codes: The codes; only meaningful when ``outcome`` is ``OK``.
        detail: Error message when the read did not succeed.
    """

    status: DtcStatus
    outcome: ReadOutcome
    codes: tuple[TroubleCode, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.codes and self.outcome is not ReadOutcome.OK:
            raise ValueError("codes can only be present when the outcome is OK")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "service": self.status.service,
            "outcome": str(self.outcome),
            "codes": [code.as_dict() for code in self.codes],
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MonitorStatusResult:
    """PID 0101 (MIL, confirmed DTC count, readiness) and how the read went."""

    outcome: ReadOutcome
    status: MonitorStatus | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if (self.outcome is ReadOutcome.OK) != (self.status is not None):
            raise ValueError("a monitor status is present exactly when the outcome is OK")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"outcome": str(self.outcome), "detail": self.detail}
        if self.status is not None:
            payload.update(self.status.as_dict())
        return payload


@dataclass(frozen=True, slots=True)
class FreezeFrameSnapshot:
    """Freeze frame 00 (service 02): the trigger DTC and the frozen parameter values.

    Attributes:
        outcome: Whether the freeze frame could be read at all.
        present: ``False`` when the ECU reports that no freeze frame is stored (trigger
            ``00 00``); then ``readings`` is empty because the other values are meaningless.
        trigger: The trouble code that stored the frame.
        readings: One reading per requested parameter (``"01xx"`` form), each with its own
            status - a parameter the ECU did not store is ``UNSUPPORTED``, never a number.
        detail: Error message when ``outcome`` is not ``OK``.
    """

    outcome: ReadOutcome
    present: bool = False
    trigger: TroubleCode | None = None
    readings: tuple[PidReading, ...] = ()
    detail: str | None = None

    def reading_for(self, pid: str) -> PidReading | None:
        wanted = pid.strip().upper()
        return next((r for r in self.readings if r.pid == wanted), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": str(self.outcome),
            "present": self.present,
            "trigger": None if self.trigger is None else self.trigger.as_dict(),
            "readings": [reading.as_dict() for reading in self.readings],
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Result of one read-only diagnostic run.

    Attributes:
        vehicle: Identification data of the vehicle (may be partially unknown).
        adapter: Adapter that produced the data, including whether it is a simulator.
        measurements: Decoded parameter values.
        supported_pids: Parameters the vehicle reports as supported.
        skipped_pids: Requested parameters the vehicle does not support (nothing was sent).
        unanswered_pids: Requested, supported parameters that timed out or came back
            malformed; the rest of the report was still read.
        trouble_codes: Stored trouble codes with knowledge database descriptions.
        generated_at: When the report was assembled (aware UTC).
        duration_s: Wall clock duration of the diagnostic session.
        read_only: Always ``True`` in this build; part of the report so a consumer never has
            to guess whether vehicle state could have been modified.
        knowledge_source: Where trouble code descriptions came from.
        knowledge_metadata: Provenance metadata of the knowledge catalogue.
        dtc_categories: Stored, pending and permanent codes, each with its read outcome.
        monitor_status: PID 0101 (MIL, confirmed DTC count, readiness) with its outcome.
        freeze_frame: Freeze frame 00, or ``None`` when it was not requested.
    """

    vehicle: VehicleInfo
    adapter: AdapterInfo
    measurements: tuple[Measurement, ...] = ()
    supported_pids: tuple[str, ...] = ()
    skipped_pids: tuple[str, ...] = ()
    unanswered_pids: tuple[str, ...] = ()
    trouble_codes: tuple[TroubleCode, ...] = ()
    generated_at: datetime = field(default_factory=utc_now)
    duration_s: float = 0.0
    read_only: bool = True
    knowledge_source: str | None = None
    knowledge_metadata: Mapping[str, Any] | None = None
    dtc_categories: tuple[DtcCategoryResult, ...] = ()
    monitor_status: MonitorStatusResult | None = None
    freeze_frame: FreezeFrameSnapshot | None = None

    @property
    def has_trouble_codes(self) -> bool:
        """``True`` when at least one trouble code is stored."""
        return bool(self.trouble_codes)

    def measurement_for(self, pid: str) -> Measurement | None:
        """Return the measurement for ``pid``, or ``None`` when it was not read."""
        wanted = pid.strip().upper()
        for measurement in self.measurements:
            if measurement.pid == wanted:
                return measurement
        return None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the whole report."""
        return {
            "vehicle": self.vehicle.as_dict(),
            "adapter": {
                "name": self.adapter.name,
                "kind": str(self.adapter.kind),
                "implemented": self.adapter.implemented,
                "hardware_required": self.adapter.hardware_required,
                "protocols": list(self.adapter.protocols),
                "notes": self.adapter.notes,
                "simulated": self.adapter.simulated,
            },
            "measurements": [m.as_dict() for m in self.measurements],
            "supported_pids": list(self.supported_pids),
            "skipped_pids": list(self.skipped_pids),
            "unanswered_pids": list(self.unanswered_pids),
            "trouble_codes": [t.as_dict() for t in self.trouble_codes],
            "generated_at": self.generated_at.isoformat(),
            "duration_s": self.duration_s,
            "read_only": self.read_only,
            "knowledge_source": self.knowledge_source,
            "knowledge_metadata": dict(self.knowledge_metadata or {}),
            "schema_version": REPORT_SCHEMA_VERSION,
            "monitor_status": None
            if self.monitor_status is None
            else self.monitor_status.as_dict(),
            "dtc_categories": [category.as_dict() for category in self.dtc_categories],
            "freeze_frame": None if self.freeze_frame is None else self.freeze_frame.as_dict(),
        }

    def category(self, status: DtcStatus) -> DtcCategoryResult | None:
        """Return the result for one DTC category, if it was read."""
        return next((c for c in self.dtc_categories if c.status is status), None)
