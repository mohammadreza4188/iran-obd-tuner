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
# CAN: synchronous fake bus with a fake clock (no python-can, no threads)
# --------------------------------------------------------------------------- #


class FakeCanBus:
    """In-memory :class:`CanBus`: records sent frames, replays scripted ones.

    ``responder(frame)`` is called for every sent frame and may return frames that become
    receivable, which is how a test plays the other side of an ISO-TP exchange without
    threads. Waiting for a frame that never comes advances the fake clock to the deadline.
    """

    def __init__(self, clock: FakeClock, responder=None, *, simulated: bool = True) -> None:
        self.clock = clock
        self.responder = responder
        self.sent: list = []
        self.incoming: list = []
        self.simulated = simulated
        self.closed = False

    def send(self, frame) -> None:
        self.sent.append(frame)
        if self.responder is not None:
            self.incoming.extend(self.responder(frame) or ())

    def recv(self, timeout_s: float):
        if self.incoming:
            return self.incoming.pop(0)
        self.clock.advance(timeout_s)
        return None

    def close(self) -> None:
        self.closed = True

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)


@pytest.fixture
def fake_can_bus(fake_clock):
    """Factory: ``fake_can_bus(responder=None)`` -> FakeCanBus sharing ``fake_clock``."""

    def build(responder=None, *, simulated: bool = True) -> FakeCanBus:
        return FakeCanBus(fake_clock, responder, simulated=simulated)

    return build


class SimUdsResponder:
    """Plays the ECU side of ISO-TP for :class:`FakeCanBus` (single-threaded).

    Requests reaching the responder are handed to a
    :class:`~iran_obd_tuner.hardware.uds_sim.SimulatedUdsEcu`; its answers are framed as
    Single Frames or as First Frame + Consecutive Frames released by the Flow Control.
    """

    def __init__(self, ecu, response_id: int = 0x7E8) -> None:
        self.ecu = ecu
        self.response_id = response_id
        self.pending_cfs: list = []

    def _pad(self, data: bytes):
        from iran_obd_tuner.hardware.can_bus import CanFrame

        return CanFrame(self.response_id, data + bytes([0xCC]) * (8 - len(data)))

    def _encode(self, response: bytes) -> list:
        if len(response) <= 7:
            return [self._pad(bytes([len(response)]) + response)]
        n = len(response)
        frames = [self._pad(bytes([0x10 | (n >> 8), n & 0xFF]) + response[:6])]
        rest, seq = response[6:], 1
        while rest:
            self.pending_cfs.append(self._pad(bytes([0x20 | seq]) + rest[:7]))
            rest, seq = rest[7:], (seq + 1) & 0x0F
        return frames

    def __call__(self, frame) -> list:
        pci = frame.data[0] >> 4
        if pci == 3:
            out, self.pending_cfs = self.pending_cfs, []
            return out
        if pci != 0:
            return []
        request = frame.data[1 : 1 + (frame.data[0] & 0x0F)]
        frames: list = []
        for response in self.ecu.handle(request):
            frames.extend(self._encode(response))
        return frames


@pytest.fixture
def make_uds_client(fake_can_bus):
    """Factory: ``make_uds_client(ecu=None, **client_kwargs)`` -> (client, ecu, bus).

    The client talks ISO-TP over a :class:`FakeCanBus` whose other side is the simulated
    ECU, so every exchange is deterministic and nothing touches a CAN interface.
    """
    from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig
    from iran_obd_tuner.hardware.uds import UdsClient
    from iran_obd_tuner.hardware.uds_sim import SimulatedUdsEcu

    def build(ecu=None, **kwargs):
        ecu = ecu or SimulatedUdsEcu()
        bus = fake_can_bus(SimUdsResponder(ecu))
        channel = IsoTpChannel(bus, IsoTpConfig(0x7E0, 0x7E8), clock=bus.clock, sleep=bus.sleep)
        client = UdsClient(channel, clock=bus.clock, sleep=bus.sleep, **kwargs)
        return client, ecu, bus

    return build


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
    # PID 0101: MIL on, 2 confirmed codes; spark; common monitors available + complete;
    # catalyst, evap, O2 sensor and heater available; evap (D bit 2) not complete.
    "0101": "41 01 82 07 65 04",
    "07": "47 01 04 20",  # one pending code: P0420
    "0A": "7F 0A 11",  # this ECU does not support permanent codes (serviceNotSupported)
    # Freeze frame 00: trigger P0133; stored PIDs 02, 04-07, 0C, 0D (bitmask 5E 18 00 00).
    "020200": "42 02 00 01 33",
    "020000": "42 00 00 5E 18 00 00",
    "020400": "42 04 00 73",  # 115 * 100 / 255 = 45.1 %
    "020500": "42 05 00 80",  # 128 - 40 = 88 degC
    "020600": "42 06 00 7C",  # 124 * 100 / 128 - 100 = -3.1 %
    "020700": "42 07 00 86",  # 134 * 100 / 128 - 100 = 4.7 %
    "020C00": "42 0C 00 25 A8",  # 9640 / 4 = 2410 rpm
    "020D00": "NO DATA",  # stored per the bitmask, but the ECU does not answer
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
