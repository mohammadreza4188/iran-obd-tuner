"""Shared domain models.

Everything here is standard library only, so any layer (hardware, diagnostics, tuning,
database, UI) may import it without pulling in third-party packages or creating cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = ["Measurement", "Unit", "VehicleInfo", "utc_now"]

# VIN is a 17 character vehicle identifier (ISO 3779). Only length and character set are
# validated here; the check digit is not verified because some markets and some adapters
# return non-conforming values that must still be displayed.
VIN_LENGTH = 17


def utc_now() -> datetime:
    """Return the current time as an aware UTC timestamp."""
    return datetime.now(UTC)


class Unit(StrEnum):
    """Engineering unit of a measurement.

    The string value is the symbol written to logs, CSV exports and JSON reports.
    """

    RPM = "rpm"
    KILOMETRE_PER_HOUR = "km/h"
    CELSIUS = "degC"
    PERCENT = "%"
    VOLT = "V"
    KILOPASCAL = "kPa"
    GRAM_PER_SECOND = "g/s"
    DEGREE = "deg"
    SECOND = "s"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class Measurement:
    """A single decoded vehicle measurement.

    Attributes:
        pid: OBD-II service 01 parameter identifier in request form, e.g. ``"010C"``.
        name: Human readable parameter name, e.g. ``"Engine speed"``.
        value: Decoded value expressed in ``unit``.
        unit: Engineering unit of ``value``.
        timestamp: When the value was obtained (aware UTC).
    """

    pid: str
    name: str
    value: float
    unit: Unit
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.pid.strip():
            raise ValueError("pid must be a non-empty string")
        if not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.unit, Unit):
            raise ValueError(f"unit must be a Unit member, got {self.unit!r}")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone aware")

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this measurement."""
        return {
            "pid": self.pid,
            "name": self.name,
            "value": self.value,
            "unit": str(self.unit),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class VehicleInfo:
    """Identification data of the vehicle behind the adapter.

    All fields are optional because a partially working connection must still be able to
    produce a report (a missing VIN is a fact to be reported, not an error).

    Attributes:
        vin: Vehicle identification number, uppercase, ``None`` when unreadable.
        protocol: Protocol reported by the adapter, e.g. ``"ISO 15765-4 CAN"``.
        adapter_name: Registry name of the adapter that produced the data.
        simulated: ``True`` when the data comes from a simulator rather than a vehicle.
    """

    vin: str | None = None
    protocol: str | None = None
    adapter_name: str | None = None
    simulated: bool = False

    def __post_init__(self) -> None:
        if self.vin is None:
            return
        vin = self.vin.strip().upper()
        if len(vin) != VIN_LENGTH or not vin.isalnum():
            raise ValueError(f"vin must be {VIN_LENGTH} alphanumeric characters, got {self.vin!r}")
        object.__setattr__(self, "vin", vin)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this vehicle identification."""
        return {
            "vin": self.vin,
            "protocol": self.protocol,
            "adapter_name": self.adapter_name,
            "simulated": self.simulated,
        }
