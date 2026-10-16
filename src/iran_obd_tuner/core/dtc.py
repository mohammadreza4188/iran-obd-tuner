"""Structure of a diagnostic trouble code (DTC) string.

This module only knows the *shape* of a diagnostic trouble code (SAE J2012 style: one
system letter, a scope digit and three more hex-ish characters, e.g. ``P0301``). It does
not know what a code means — descriptions come from the knowledge database
(:mod:`iran_obd_tuner.database.repository`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["DtcCodeStructure", "DtcScope", "DtcSystem", "parse_dtc_code"]

DTC_PATTERN = re.compile(r"^[PCBU][0-3][0-9A-F]{3}$")


class DtcSystem(StrEnum):
    """Which on-board system a trouble code belongs to (first character of the code)."""

    POWERTRAIN = "P"
    CHASSIS = "C"
    BODY = "B"
    NETWORK = "U"


class DtcScope(StrEnum):
    """Whether a code is defined by the standard or by the manufacturer.

    Only the two values that are unambiguous are decoded: the second character ``0`` marks
    an SAE/ISO defined (generic) code, ``1`` a manufacturer defined code. The standard
    reserves ``2`` and ``3`` for further generic code ranges; those are reported as
    :attr:`DtcScope.UNKNOWN` rather than guessed.
    """

    GENERIC = "generic"
    MANUFACTURER_SPECIFIC = "manufacturer_specific"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DtcCodeStructure:
    """Decoded structure of a trouble code string.

    Attributes:
        code: The normalised code, e.g. ``"P0301"``.
        system: On-board system the code belongs to.
        scope: Generic or manufacturer defined.
    """

    code: str
    system: DtcSystem
    scope: DtcScope

    @property
    def is_generic(self) -> bool:
        """``True`` when the code is defined by the standard, not by a manufacturer."""
        return self.scope is DtcScope.GENERIC


def parse_dtc_code(code: str) -> DtcCodeStructure:
    """Decode ``code`` into its structural parts.

    Args:
        code: Trouble code string, e.g. ``"p0301"`` (case insensitive).

    Returns:
        The decoded structure.

    Raises:
        ValueError: If ``code`` is not a well-formed trouble code.
    """
    normalized = code.strip().upper()
    if not DTC_PATTERN.match(normalized):
        raise ValueError(
            f"invalid diagnostic trouble code: {code!r} "
            "(expected one system letter, a scope digit and three characters, e.g. 'P0301')"
        )
    system = DtcSystem(normalized[0])
    scope_char = normalized[1]
    if scope_char == "0":
        scope = DtcScope.GENERIC
    elif scope_char == "1":
        scope = DtcScope.MANUFACTURER_SPECIFIC
    else:
        scope = DtcScope.UNKNOWN
    return DtcCodeStructure(code=normalized, system=system, scope=scope)
