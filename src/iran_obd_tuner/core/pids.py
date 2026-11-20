"""OBD-II service 01 parameter catalogue (shared vocabulary) and value decoding.

Why this lives in ``core`` and not in ``diagnostics``: both the transport layer
(adapters decode raw frames into :class:`~iran_obd_tuner.core.models.Measurement`) and the
diagnostic layer (choosing which parameters to read, naming them in reports) need the same
identifiers and units. Putting the catalogue in ``core`` keeps ``hardware`` independent of
``diagnostics``.

Only a small, well-known subset of SAE J1979 service 01 parameters is listed. Parameter
identifiers are always written in request form: mode ``01`` followed by the two hex digits
of the PID, e.g. ``"010C"`` is engine speed.

Decoding: every catalogued parameter carries the number of data bytes it returns and the
SAE J1979 scaling formula (data bytes are named ``A``, ``B`` in the standard). For example
engine speed is ``(256*A + B) / 4`` rpm. :func:`decode_pid_value` refuses data of the wrong
length instead of guessing.

Not claimed here:

* manufacturer-specific parameters of the Iranian market vehicles (phase 3, knowledge
  database work),
* service 01 bitmask discovery (PID ``0100``/``0120``/``0140``), which is protocol work done
  by the adapters (see :mod:`iran_obd_tuner.hardware.elm327_protocol`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from iran_obd_tuner.core.models import Unit

__all__ = [
    "DEFAULT_LIVE_DATA_PIDS",
    "PID_CATALOG",
    "PidSpec",
    "decode_pid_value",
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
        data_bytes: Number of data bytes in the response (after the ``41 xx`` echo);
            ``0`` when no decoding formula is defined.
        formula: SAE J1979 scaling from the data bytes to ``unit``; ``None`` when not
            defined (such a parameter can be described but not decoded).
    """

    pid: str
    name: str
    unit: Unit
    description: str
    data_bytes: int = 0
    formula: Callable[[bytes], float] | None = None

    def __post_init__(self) -> None:
        if not is_valid_pid(self.pid):
            raise ValueError(f"invalid PID identifier: {self.pid!r} (expected e.g. '010C')")
        if (self.formula is None) != (self.data_bytes == 0):
            raise ValueError(f"{self.pid}: data_bytes and formula must be given together")

    def decode(self, data: bytes) -> float:
        """Convert the response data bytes into a value in :attr:`unit`.

        Raises:
            ValueError: If no formula is defined or ``data`` has the wrong length.
        """
        if self.formula is None:
            raise ValueError(f"{self.pid}: no decoding formula is defined")
        if len(data) != self.data_bytes:
            raise ValueError(
                f"{self.pid}: expected {self.data_bytes} data byte(s), got {len(data)}"
            )
        return float(self.formula(data))


def _word(data: bytes) -> int:
    return data[0] * 256 + data[1]


PID_CATALOG: tuple[PidSpec, ...] = (
    PidSpec(
        "0104",
        "Calculated engine load",
        Unit.PERCENT,
        "Load as a percentage of the maximum value available at the current engine speed.",
        1,
        lambda d: d[0] * 100 / 255,
    ),
    PidSpec(
        "0105",
        "Engine coolant temperature",
        Unit.CELSIUS,
        "Temperature of the engine coolant.",
        1,
        lambda d: d[0] - 40,
    ),
    PidSpec(
        "010B",
        "Intake manifold absolute pressure",
        Unit.KILOPASCAL,
        "Absolute pressure in the intake manifold.",
        1,
        lambda d: d[0],
    ),
    PidSpec(
        "010C",
        "Engine speed",
        Unit.RPM,
        "Crankshaft revolutions per minute.",
        2,
        lambda d: _word(d) / 4,
    ),
    PidSpec(
        "010D",
        "Vehicle speed",
        Unit.KILOMETRE_PER_HOUR,
        "Speed reported by the control module, not necessarily the wheel speed.",
        1,
        lambda d: d[0],
    ),
    PidSpec(
        "010E",
        "Timing advance",
        Unit.DEGREE,
        "Ignition timing advance of cylinder 1 relative to top dead centre (signed, offset 64).",
        1,
        lambda d: d[0] / 2 - 64,
    ),
    PidSpec(
        "010F",
        "Intake air temperature",
        Unit.CELSIUS,
        "Temperature of the air entering the intake manifold.",
        1,
        lambda d: d[0] - 40,
    ),
    PidSpec(
        "0110",
        "Mass air flow rate",
        Unit.GRAM_PER_SECOND,
        "Air mass flow measured by the MAF sensor.",
        2,
        lambda d: _word(d) / 100,
    ),
    PidSpec(
        "0111",
        "Throttle position",
        Unit.PERCENT,
        "Throttle valve position in percent of full travel.",
        1,
        lambda d: d[0] * 100 / 255,
    ),
    PidSpec(
        "011F",
        "Run time since engine start",
        Unit.SECOND,
        "Seconds since the control module powered up with the engine running.",
        2,
        _word,
    ),
    PidSpec(
        "012F",
        "Fuel tank level",
        Unit.PERCENT,
        "Fuel level as a percentage of the tank capacity.",
        1,
        lambda d: d[0] * 100 / 255,
    ),
    PidSpec(
        "0142",
        "Control module voltage",
        Unit.VOLT,
        "Supply voltage measured by the control module.",
        2,
        lambda d: _word(d) / 1000,
    ),
    PidSpec(
        "015C",
        "Engine oil temperature",
        Unit.CELSIUS,
        "Temperature of the engine oil.",
        1,
        lambda d: d[0] - 40,
    ),
)

_PIDS_BY_CODE: dict[str, PidSpec] = {spec.pid: spec for spec in PID_CATALOG}

#: Parameters read by the diagnostic workflow when no explicit list is given.
DEFAULT_LIVE_DATA_PIDS: tuple[str, ...] = ("010C", "010D", "0105", "0111", "010F", "0142")


def describe_pid(pid: str) -> PidSpec | None:
    """Return the catalogue entry for ``pid``, or ``None`` when it is not catalogued."""
    return _PIDS_BY_CODE.get(normalize_pid(pid))


def decode_pid_value(pid: str, data: bytes) -> float:
    """Decode the data bytes of a service 01 response for ``pid``.

    Raises:
        ValueError: If ``pid`` is not catalogued, has no formula, or ``data`` has the wrong
            length.
    """
    spec = describe_pid(pid)
    if spec is None:
        raise ValueError(f"PID {normalize_pid(pid)} is not in the PID catalogue")
    return spec.decode(data)
