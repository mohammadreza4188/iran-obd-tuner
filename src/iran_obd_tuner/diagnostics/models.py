"""Result models of the diagnostic workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from iran_obd_tuner.core.dtc import DtcCodeStructure, DtcScope, DtcSystem, parse_dtc_code
from iran_obd_tuner.core.models import Measurement, VehicleInfo, utc_now
from iran_obd_tuner.database.repository import DtcDefinition
from iran_obd_tuner.hardware.base import AdapterInfo

__all__ = ["DiagnosticReport", "TroubleCode"]


@dataclass(frozen=True, slots=True)
class TroubleCode:
    """A stored trouble code together with what the knowledge database knows about it.

    The description is optional on purpose: an unknown code is a normal result (especially
    with manufacturer specific codes), and it must be reported as "unknown", never guessed.

    Attributes:
        structure: Decoded structure (system, scope) of the code.
        description: Definition from the knowledge database, or ``None``.
        description_source: Which catalogue the description came from.
    """

    structure: DtcCodeStructure
    description: str | None = None
    description_source: str | None = None

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
    def from_code(cls, code: str, definition: DtcDefinition | None = None) -> TroubleCode:
        """Build a trouble code from a raw code string and an optional definition.

        Raises:
            ValueError: If ``code`` is not a well-formed trouble code.
        """
        return cls(
            structure=parse_dtc_code(code),
            description=None if definition is None else definition.description,
            description_source=None if definition is None else definition.source,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "code": self.code,
            "system": str(self.system),
            "scope": str(self.scope),
            "description": self.description,
            "description_source": self.description_source,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Result of one read-only diagnostic run.

    Attributes:
        vehicle: Identification data of the vehicle (may be partially unknown).
        adapter: Adapter that produced the data, including whether it is a simulator.
        measurements: Decoded parameter values.
        supported_pids: Parameters the vehicle reports as supported.
        skipped_pids: Requested parameters that were not answered.
        trouble_codes: Stored trouble codes with knowledge database descriptions.
        generated_at: When the report was assembled (aware UTC).
        duration_s: Wall clock duration of the diagnostic session.
        read_only: Always ``True`` in this build; part of the report so a consumer never has
            to guess whether vehicle state could have been modified.
        knowledge_source: Where trouble code descriptions came from.
        knowledge_metadata: Provenance metadata of the knowledge catalogue.
    """

    vehicle: VehicleInfo
    adapter: AdapterInfo
    measurements: tuple[Measurement, ...] = ()
    supported_pids: tuple[str, ...] = ()
    skipped_pids: tuple[str, ...] = ()
    trouble_codes: tuple[TroubleCode, ...] = ()
    generated_at: datetime = field(default_factory=utc_now)
    duration_s: float = 0.0
    read_only: bool = True
    knowledge_source: str | None = None
    knowledge_metadata: Mapping[str, Any] | None = None

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
            },
            "measurements": [m.as_dict() for m in self.measurements],
            "supported_pids": list(self.supported_pids),
            "skipped_pids": list(self.skipped_pids),
            "trouble_codes": [t.as_dict() for t in self.trouble_codes],
            "generated_at": self.generated_at.isoformat(),
            "duration_s": self.duration_s,
            "read_only": self.read_only,
            "knowledge_source": self.knowledge_source,
            "knowledge_metadata": dict(self.knowledge_metadata or {}),
        }
