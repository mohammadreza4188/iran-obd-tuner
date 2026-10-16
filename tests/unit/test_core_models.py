"""Shared domain models: validation and serialisation."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from iran_obd_tuner.core.models import Measurement, Unit, VehicleInfo


def test_measurement_serialises_with_unit_symbol() -> None:
    measurement = Measurement(pid="010C", name="Engine speed", value=842.0, unit=Unit.RPM)

    payload = measurement.as_dict()

    assert payload["pid"] == "010C"
    assert payload["unit"] == "rpm"
    assert isinstance(payload["timestamp"], str)
    json.dumps(payload)  # must be JSON serialisable
    assert measurement.timestamp.tzinfo is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pid": "  ", "name": "Engine speed", "value": 1.0, "unit": Unit.RPM},
        {"pid": "010C", "name": "", "value": 1.0, "unit": Unit.RPM},
        {"pid": "010C", "name": "Engine speed", "value": 1.0, "unit": "rpm"},
        {
            "pid": "010C",
            "name": "Engine speed",
            "value": 1.0,
            "unit": Unit.RPM,
            "timestamp": datetime(2026, 1, 1),
        },
    ],
)
def test_measurement_rejects_invalid_data(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Measurement(**kwargs)  # type: ignore[arg-type]


def test_vehicle_info_normalises_vin() -> None:
    vehicle = VehicleInfo(vin=" simulatedvin00001 ", adapter_name="mock")

    assert vehicle.vin == "SIMULATEDVIN00001"
    assert vehicle.as_dict()["simulated"] is False


def test_vehicle_info_accepts_unknown_vin() -> None:
    assert VehicleInfo().vin is None


def test_vehicle_info_rejects_implausible_vin() -> None:
    with pytest.raises(ValueError, match="17 alphanumeric"):
        VehicleInfo(vin="TOO-SHORT")
