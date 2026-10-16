"""Decoding of diagnostic trouble code strings."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.dtc import DtcScope, DtcSystem, parse_dtc_code


def test_generic_powertrain_code() -> None:
    structure = parse_dtc_code("p0301")

    assert structure.code == "P0301"
    assert structure.system is DtcSystem.POWERTRAIN
    assert structure.scope is DtcScope.GENERIC
    assert structure.is_generic is True


def test_manufacturer_specific_code_is_marked_as_such() -> None:
    structure = parse_dtc_code("P1301")

    assert structure.scope is DtcScope.MANUFACTURER_SPECIFIC
    assert structure.is_generic is False


@pytest.mark.parametrize(
    ("code", "system"),
    [
        ("P0171", DtcSystem.POWERTRAIN),
        ("C0035", DtcSystem.CHASSIS),
        ("B1234", DtcSystem.BODY),
        ("U0100", DtcSystem.NETWORK),
    ],
)
def test_system_is_decoded_from_first_character(code: str, system: DtcSystem) -> None:
    assert parse_dtc_code(code).system is system


def test_unclassified_scope_is_not_guessed() -> None:
    """Second character 2/3 marks further generic ranges; the project does not classify them."""
    assert parse_dtc_code("P2300").scope is DtcScope.UNKNOWN


@pytest.mark.parametrize("code", ["", "0301", "P30", "P03011", "P0-01", "X0301"])
def test_malformed_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValueError, match="invalid diagnostic trouble code"):
        parse_dtc_code(code)
