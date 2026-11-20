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


class FakeClock:
    """Manually advanced monotonic clock for time-dependent simulations."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    """A clock that only moves when the test says so."""
    return FakeClock()


# --------------------------------------------------------------------------- #
# ELM327: scripted serial transport (no hardware, no serial port)
# --------------------------------------------------------------------------- #

Reply = str | BaseException | list[str | BaseException]


class ScriptedTransport:
    """Byte transport that answers each command from a script and records what was sent.

    ``replies`` maps a command (as written, without the CR) to the adapter's reply text
    (without the ``>`` prompt), to an exception raised while waiting for the reply, or to a
    list of those used one after another. Unknown commands get ``?``, as on a real ELM327.
    """

    def __init__(self, replies: dict[str, Reply]) -> None:
        self.replies = dict(replies)
        self.written: list[str] = []
        self.closed = False
        self._pending: str | BaseException | None = None

    def write(self, data: bytes) -> None:
        assert not self.closed, "write after close"
        assert data.endswith(b"\r"), "ELM327 commands end with a carriage return"
        command = data[:-1].decode("ascii")
        self.written.append(command)
        reply = self.replies.get(command, "?")
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        self._pending = reply

    def read_until(self, terminator: bytes, timeout_s: float) -> bytes:
        assert terminator == b">"
        reply, self._pending = self._pending, None
        if isinstance(reply, BaseException):
            raise reply
        assert reply is not None, "read without a preceding command"
        return reply.replace("\n", "\r").encode("ascii") + b"\r\r"

    def discard_input(self) -> None:
        """Nothing is ever stale in a script."""

    def close(self) -> None:
        self.closed = True


#: A CAN vehicle (protocol 6) built from the data sheet examples. Supported service 01
#: PIDs: 0100 bitmask "BE 3E B8 11" (01, 03-07, 0B-0F, 11, 13-15, 1C, 20), then 0120
#: (2F, 40) and 0140 (42, 5C).
CAN_VEHICLE: dict[str, Reply] = {
    "ATZ": "\nELM327 v1.5",
    "ATE0": "ATE0\nOK",  # echo is still on when ATE0 itself arrives
    "ATL0": "OK",
    "ATH0": "OK",
    "ATTP0": "OK",
    "0100": "SEARCHING...\n41 00 BE 3E B8 11",
    "0120": "41 20 00 02 00 01",
    "0140": "41 40 40 00 00 10",
    "ATDPN": "A6",
    "010C": "41 0C 1A F8",  # data sheet: 6904 / 4 = 1726 rpm
    "0105": "41 05 7B",  # data sheet: 123 - 40 = 83 degC
    "0142": "41 42 37 2A",  # 14122 mV
    "012F": "41 2F 80",  # 128 * 100 / 255 = 50.2 %
    "0111": "41 11 33",  # 51 * 100 / 255 = 20 %
    "010F": "41 0F 44",  # 68 - 40 = 28 degC
    "0104": "41 04 40",  # 64 * 100 / 255 = 25.1 %
    "010D": "NO DATA",  # supported, but the ECU does not answer
    "0902": "014\n0: 49 02 01 31 44 34\n1: 47 50 30 30 52 35 35\n2: 42 31 32 33 34 35 36",
    "03": "43 02 01 33 01 71",
}


@pytest.fixture
def scripted_transport():
    """The :class:`ScriptedTransport` class (conftest classes cannot be imported)."""
    return ScriptedTransport


@pytest.fixture
def can_vehicle_script() -> dict[str, Reply]:
    """A fresh copy of :data:`CAN_VEHICLE` that a test may modify."""
    return {
        key: list(value) if isinstance(value, list) else value for key, value in CAN_VEHICLE.items()
    }
