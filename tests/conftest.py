"""Shared fixtures for the automated tests.

Everything here runs without a vehicle, without an adapter and without third-party
packages: the simulated adapter and the JSON knowledge repository are part of the package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iran_obd_tuner.database.repository import JsonKnowledgeRepository
from iran_obd_tuner.hardware.mock import MockObdAdapter, MockScenario

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SIMULATED_VEHICLE_FIXTURE = FIXTURE_DIR / "simulated_vehicle.json"


@pytest.fixture
def fixture_dir() -> Path:
    """Directory holding the test fixtures."""
    return FIXTURE_DIR


@pytest.fixture
def simulated_vehicle_path() -> Path:
    """Path of the simulated vehicle fixture."""
    return SIMULATED_VEHICLE_FIXTURE


@pytest.fixture
def fixture_scenario(simulated_vehicle_path: Path) -> MockScenario:
    """Scenario loaded from the simulated vehicle fixture."""
    return MockScenario.from_json(simulated_vehicle_path)


@pytest.fixture
def repository() -> JsonKnowledgeRepository:
    """Knowledge repository with the bundled trouble code catalogue."""
    return JsonKnowledgeRepository()


@pytest.fixture
def adapter_factory():
    """Return a factory building simulated adapters with an optional scenario."""

    def build(scenario: MockScenario | None = None, **kwargs: object) -> MockObdAdapter:
        return MockObdAdapter(scenario, **kwargs)  # type: ignore[arg-type]

    return build


@pytest.fixture
def adapter(adapter_factory) -> MockObdAdapter:
    """Simulated adapter with the built-in default scenario."""
    return adapter_factory()
