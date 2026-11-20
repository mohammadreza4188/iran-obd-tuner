"""Decoding of service 01 PID 01: MIL, confirmed DTC count and readiness monitors.

PID ``0101`` ("monitor status since DTCs cleared") returns four bytes ``A B C D``:

* ``A`` bit 7: malfunction indicator lamp (MIL, "check engine") commanded on;
  ``A`` bits 0-6: number of confirmed emission-related DTCs;
* ``B`` bit 3: ignition type (0 = spark, 1 = compression);
  ``B`` bits 0-2: misfire / fuel system / components monitor *available*;
  ``B`` bits 4-6: the same monitors *not complete* (1 = not complete);
* ``C``: availability of the ignition-specific monitors; ``D``: the same, not complete.

Source and confidence: the bit tables follow the publicly documented SAE J1979 layout (as
summarised on the Wikipedia "OBD-II PIDs" page). They have **not** been checked against the
SAE J1979 text itself, and bit 4 of bytes C/D changed meaning between J1979 revisions
(A/C refrigerant in older ones, gasoline particulate filter in newer ones), so that monitor
is named neutrally instead of guessed. :data:`SOURCE_NOTE` travels with every decoded value.

Not decoded: PID ``0141`` (monitor status this drive cycle) and the reserved bits.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "SOURCE_NOTE",
    "IgnitionType",
    "MonitorReadiness",
    "MonitorStatus",
    "combine_monitor_status",
    "decode_monitor_status",
]

SOURCE_NOTE = (
    "PID 0101 bit layout from public SAE J1979 summaries; not verified against the J1979 text. "
    "Bit 4 of bytes C/D differs between J1979 revisions and is reported as 'bit4_ac_or_gpf'."
)


class IgnitionType(StrEnum):
    """Engine type reported in byte B bit 3."""

    SPARK = "spark"
    COMPRESSION = "compression"


#: Byte B, bits 0-2 (availability) and 4-6 (incomplete): monitors common to all engines.
_COMMON_MONITORS: tuple[tuple[int, str], ...] = (
    (0, "misfire"),
    (1, "fuel_system"),
    (2, "components"),
)

#: Bytes C (availability) and D (incomplete), spark ignition. Bit -> monitor.
_SPARK_MONITORS: tuple[tuple[int, str], ...] = (
    (0, "catalyst"),
    (1, "heated_catalyst"),
    (2, "evaporative_system"),
    (3, "secondary_air"),
    (4, "bit4_ac_or_gpf"),
    (5, "oxygen_sensor"),
    (6, "oxygen_sensor_heater"),
    (7, "egr_vvt"),
)

#: Bytes C and D, compression ignition (diesel). Bits 2 and 4 are reserved.
_COMPRESSION_MONITORS: tuple[tuple[int, str], ...] = (
    (0, "nmhc_catalyst"),
    (1, "nox_scr"),
    (3, "boost_pressure"),
    (5, "exhaust_gas_sensor"),
    (6, "pm_filter"),
    (7, "egr_vvt"),
)


@dataclass(frozen=True, slots=True)
class MonitorReadiness:
    """One readiness monitor.

    Attributes:
        name: Monitor identifier, e.g. ``"catalyst"``.
        available: Whether the ECU supports this monitor.
        complete: Whether its test has completed since the codes were last cleared;
            ``None`` when the monitor is not available (no claim is made).
    """

    name: str
    available: bool
    complete: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available, "complete": self.complete}


@dataclass(frozen=True, slots=True)
class MonitorStatus:
    """Decoded PID ``0101``.

    Attributes:
        mil_on: Whether the MIL ("check engine" light) is commanded on.
        confirmed_dtc_count: Number of confirmed emission-related trouble codes.
        ignition: Spark or compression ignition.
        monitors: Readiness of every defined monitor.
        ecu_count: How many ECUs answered; with more than one, ``mil_on`` is the OR and
            ``confirmed_dtc_count`` the sum over all of them, while ``monitors`` describe
            only the first answer (replies cannot be attributed with headers off).
        source_note: Provenance of the bit layout (see :data:`SOURCE_NOTE`).
    """

    mil_on: bool
    confirmed_dtc_count: int
    ignition: IgnitionType
    monitors: tuple[MonitorReadiness, ...]
    ecu_count: int = 1
    source_note: str = SOURCE_NOTE

    def __post_init__(self) -> None:
        if self.confirmed_dtc_count < 0:
            raise ValueError("confirmed_dtc_count must not be negative")
        if self.ecu_count < 1:
            raise ValueError("ecu_count must be at least 1")

    @property
    def incomplete_monitors(self) -> tuple[str, ...]:
        """Names of available monitors whose test has not completed."""
        return tuple(m.name for m in self.monitors if m.available and m.complete is False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mil_on": self.mil_on,
            "confirmed_dtc_count": self.confirmed_dtc_count,
            "ignition": str(self.ignition),
            "ecu_count": self.ecu_count,
            "monitors": [m.as_dict() for m in self.monitors],
            "source_note": self.source_note,
        }


def _readiness(
    table: tuple[tuple[int, str], ...], available_byte: int, incomplete_byte: int, shift: int
) -> list[MonitorReadiness]:
    monitors: list[MonitorReadiness] = []
    for bit, name in table:
        available = bool(available_byte & (1 << bit))
        incomplete = bool(incomplete_byte & (1 << (bit + shift)))
        monitors.append(
            MonitorReadiness(
                name=name, available=available, complete=not incomplete if available else None
            )
        )
    return monitors


def decode_monitor_status(data: bytes) -> MonitorStatus:
    """Decode the four data bytes of a ``41 01`` reply.

    Raises:
        ValueError: If ``data`` is not exactly four bytes.
    """
    if len(data) != 4:
        raise ValueError(f"PID 0101 carries 4 data bytes, got {len(data)}")
    a, b, c, d = data
    ignition = IgnitionType.COMPRESSION if b & 0x08 else IgnitionType.SPARK
    monitors = _readiness(_COMMON_MONITORS, b, b, shift=4)
    specific = _COMPRESSION_MONITORS if ignition is IgnitionType.COMPRESSION else _SPARK_MONITORS
    monitors += _readiness(specific, c, d, shift=0)
    return MonitorStatus(
        mil_on=bool(a & 0x80),
        confirmed_dtc_count=a & 0x7F,
        ignition=ignition,
        monitors=tuple(monitors),
    )


def combine_monitor_status(statuses: list[MonitorStatus]) -> MonitorStatus:
    """Combine the answers of several ECUs (MIL = any, count = sum, monitors = first).

    Raises:
        ValueError: If ``statuses`` is empty.
    """
    if not statuses:
        raise ValueError("at least one monitor status is required")
    first = statuses[0]
    return MonitorStatus(
        mil_on=any(s.mil_on for s in statuses),
        confirmed_dtc_count=sum(s.confirmed_dtc_count for s in statuses),
        ignition=first.ignition,
        monitors=first.monitors,
        ecu_count=len(statuses),
    )
