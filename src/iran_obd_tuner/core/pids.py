"""OBD-II service 01 parameter catalogue (shared vocabulary).

Why this lives in ``core`` and not in ``diagnostics``: both the transport layer
(adapters decode raw frames into :class:`~iran_obd_tuner.core.models.Measurement`) and the
diagnostic layer (choosing which parameters to read, naming them in reports) need the same
identifiers and units. Putting the catalogue in ``core`` keeps ``hardware`` independent of
``diagnostics``.

Only a small, well-known subset of SAE J1979 service 01 parameters is listed. Parameter
identifiers are always written in request form: mode ``01`` followed by the two hex digits
of the PID, e.g. ``"010C"`` is engine speed.

Not claimed here:

* manufacturer-specific parameters of the Iranian market vehicles (phase 3, knowledge
  database work),
* raw frame decoding/scaling (arrives with the first real adapter implementation, phase 1),
* service 01 bitmask discovery (PID ``0100``/``0120``/``0140``) which real adapters must
  implement to answer :meth:`~iran_obd_tuner.hardware.base.ObdAdapter.read_supported_pids`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from iran_obd_tuner.core.models import Unit

__all__ = [
    "DEFAULT_LIVE_DATA_PIDS",
    "PID_CATALOG",
    "PidSpec",
    "describe_pid",
    "is_valid_pid",
    "normalize_pid",
]

PID_PATTERN = re.compile(r"^01[0-9A-F]{2}$")


def normalize_pid(pid: str) -> str:
    """Return ``pid`` in canonical request form (uppercase, no surrounding whitespace)."""
    return pid.strip().upper()


def is_valid_pid(pid: str) -> bool:
    """Return ``True`` when ``pid`` is a well-formed service 01 request identifier."""
    return bool(PID_PATTERN.match(normalize_pid(pid)))


@dataclass(frozen=True, slots=True)
class PidSpec:
    """Definition of one OBD-II service 01 parameter.

    Attributes:
        pid: Request form identifier, e.g. ``"010C"``.
        name: Human readable name used in reports.
        unit: Engineering unit of the decoded value.
        description: What the parameter means, and its caveats.
    """

    pid: str
    name: str
    unit: Unit
    description: str

    def __post_init__(self) -> None:
        if not is_valid_pid(self.pid):
            raise ValueError(f"invalid PID identifier: {self.pid!r} (expected e.g. '010C')")


PID_CATALOG: tuple[PidSpec, ...] = (
    PidSpec(
        "0104",
        "Calculated engine load",
        Unit.PERCENT,
        "Load as a percentage of the maximum value available at the current engine speed.",
    ),
    PidSpec(
        "0105",
        "Engine coolant temperature",
        Unit.CELSIUS,
        "Temperature of the engine coolant.",
    ),
    PidSpec(
        "010B",
        "Intake manifold absolute pressure",
        Unit.KILOPASCAL,
        "Absolute pressure in the intake manifold.",
    ),
    PidSpec(
        "010C",
        "Engine speed",
        Unit.RPM,
        "Crankshaft revolutions per minute.",
    ),
    PidSpec(
        "010D",
        "Vehicle speed",
        Unit.KILOMETRE_PER_HOUR,
        "Speed reported by the control module, not necessarily the wheel speed.",
    ),
    PidSpec(
        "010E",
        "Timing advance",
        Unit.DEGREE,
        "Ignition timing advance of cylinder 1 relative to top dead centre (signed, offset 64).",
    ),
    PidSpec(
        "010F",
        "Intake air temperature",
        Unit.CELSIUS,
        "Temperature of the air entering the intake manifold.",
    ),
    PidSpec(
        "0110",
        "Mass air flow rate",
        Unit.GRAM_PER_SECOND,
        "Air mass flow measured by the MAF sensor.",
    ),
    PidSpec(
        "0111",
        "Throttle position",
        Unit.PERCENT,
        "Throttle valve position in percent of full travel.",
    ),
    PidSpec(
        "011F",
        "Run time since engine start",
        Unit.SECOND,
        "Seconds since the control module powered up with the engine running.",
    ),
    PidSpec(
        "012F",
        "Fuel tank level",
        Unit.PERCENT,
        "Fuel level as a percentage of the tank capacity.",
    ),
    PidSpec(
        "0142",
        "Control module voltage",
        Unit.VOLT,
        "Supply voltage measured by the control module.",
    ),
    PidSpec(
        "015C",
        "Engine oil temperature",
        Unit.CELSIUS,
        "Temperature of the engine oil.",
    ),
)

_PIDS_BY_CODE: dict[str, PidSpec] = {spec.pid: spec for spec in PID_CATALOG}

#: Parameters read by the diagnostic workflow when no explicit list is given.
DEFAULT_LIVE_DATA_PIDS: tuple[str, ...] = ("010C", "010D", "0105", "0111", "010F", "0142")


def describe_pid(pid: str) -> PidSpec | None:
    """Return the catalogue entry for ``pid``, or ``None`` when it is not catalogued."""
    return _PIDS_BY_CODE.get(normalize_pid(pid))
