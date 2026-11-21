"""Virtual programmable ECU served on python-can's virtual bus, with real ISO-TP both ways.

The tester side here is a tiny raw-request helper (the real bench programmer arrives in
Milestone 7c). Segmented requests (TransferData) and segmented answers (upload blocks) go
through :class:`IsoTpChannel` with flow control, exactly as on a CAN bus - but only on the
in-process ``virtual`` interface. Skipped without the ``[can]`` extra.
"""

from __future__ import annotations

import inspect
import zlib
from collections.abc import Iterator

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET  # noqa: E402
from iran_obd_tuner.hardware.can_bus import PythonCanBus  # noqa: E402
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig  # noqa: E402
from iran_obd_tuner.hardware.uds_sim_programmable import (  # noqa: E402
    DID_BOOT_STATUS,
    FaultPlan,
    ProgrammableEcuConfig,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    VirtualProgrammableEcuServer,
    default_simulated_image,
)

START = SIMULATED_BENCH_TARGET.regions[0].start_address
SIZE = SIMULATED_BENCH_TARGET.image_size
#: Larger blocks keep the bus test quick: 64 KiB in 64 blocks of 1024 data bytes.
MAX_BLOCK = 1026
CHUNK = MAX_BLOCK - 2


class Tester:
    """Raw-request tester over ISO-TP (test helper; waits through responsePending)."""

    __test__ = False  # a helper, not a pytest test class

    def __init__(self, bus: PythonCanBus) -> None:
        self.channel = IsoTpChannel(bus, IsoTpConfig(0x7E0, 0x7E8))

    def request(self, payload: bytes, timeout_s: float = 2.0) -> bytes | None:
        self.channel.send(payload)
        while True:
            answer = self.channel.recv(timeout_s)
            if answer is None or answer[:1] != b"\x7f" or answer[2:3] != b"\x78":
                return answer

    def unlock(self) -> None:
        assert self.request(b"\x10\x02")[:2] == b"\x50\x02"
        seed = self.request(b"\x27\x01")[2:]
        key = SimulatedXorSeedKey().compute_key(0x01, seed)
        assert self.request(b"\x27\x02" + key) == b"\x67\x02"

    @staticmethod
    def alfid(address: int, size: int) -> bytes:
        return b"\x44" + address.to_bytes(4, "big") + size.to_bytes(4, "big")


@pytest.fixture
def channel_name(request: pytest.FixtureRequest) -> str:
    return f"prog-{request.node.name}"


@pytest.fixture
def served(channel_name: str):
    """Factory: ``served(ecu)`` -> (server, tester); everything is closed afterwards."""
    opened: list[tuple[VirtualProgrammableEcuServer, PythonCanBus]] = []

    def start(ecu: VirtualProgrammableEcu) -> tuple[VirtualProgrammableEcuServer, Tester]:
        server = VirtualProgrammableEcuServer(channel_name, ecu, pending_delay_s=0.01)
        server.start()
        bus = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E8,))
        opened.append((server, bus))
        return server, Tester(bus)

    yield start
    for server, bus in opened:
        bus.close()
        server.stop()


def _ecu(**faults: object) -> VirtualProgrammableEcu:
    return VirtualProgrammableEcu(
        ProgrammableEcuConfig(max_block_length=MAX_BLOCK),
        FaultPlan(**faults),  # type: ignore[arg-type]
    )


def _image() -> bytes:
    return bytes((index * 31 + 5) & 0xFF for index in range(SIZE))


def _blocks(data: bytes) -> Iterator[tuple[int, bytes]]:
    for index, offset in enumerate(range(0, len(data), CHUNK)):
        yield (index + 1) & 0xFF, data[offset : offset + CHUNK]


def test_the_server_only_ever_opens_the_virtual_interface(monkeypatch) -> None:
    opened: list[str] = []
    real_open = PythonCanBus.open

    def spy(interface, channel, bitrate, **kwargs):
        opened.append(interface)
        return real_open(interface, channel, bitrate, **kwargs)

    monkeypatch.setattr(PythonCanBus, "open", staticmethod(spy))
    server = VirtualProgrammableEcuServer("prog-spy")
    server.stop()

    assert opened == ["virtual"]
    assert "interface" not in inspect.signature(VirtualProgrammableEcuServer).parameters
    assert server.simulated is True


def test_full_cycle_over_the_virtual_bus(served) -> None:
    server, tester = served(_ecu(slow_erase_pending=2))
    image = _image()

    tester.unlock()
    erase = tester.request(b"\x31\x01\xff\x00" + tester.alfid(START, SIZE), timeout_s=3.0)
    assert erase == b"\x71\x01\xff\x00\x00"  # after two responsePending answers
    assert tester.request(b"\x34\x00" + tester.alfid(START, SIZE)) == b"\x74\x20\x04\x02"
    for sequence, chunk in _blocks(image):  # segmented TransferData requests
        assert tester.request(bytes([0x36, sequence]) + chunk) == bytes([0x76, sequence])
    assert tester.request(b"\x37") == b"\x77"
    check = b"\x31\x01\xff\x01" + tester.alfid(START, SIZE) + zlib.crc32(image).to_bytes(4, "big")
    assert tester.request(check) == b"\x71\x01\xff\x01\x00"
    assert tester.request(b"\x11\x01") == b"\x51\x01"

    software = tester.request(b"\x22\xf1\x89")
    assert software == b"\x62\xf1\x89" + f"SIM-SW-{zlib.crc32(image):08X}".encode()
    tester.unlock()
    assert tester.request(b"\x35\x00" + tester.alfid(START, SIZE)) == b"\x75\x20\x04\x02"
    read_back = b""
    for sequence, _chunk in _blocks(image):  # segmented answers, flow control by the tester
        answer = tester.request(bytes([0x36, sequence]))
        assert answer[:2] == bytes([0x76, sequence])
        read_back += answer[2:]
    assert tester.request(b"\x37") == b"\x77"
    assert read_back == image
    assert server.errors == []


def test_a_dropped_link_goes_silent_and_resumes(served) -> None:
    server, tester = served(_ecu(drop_link_at_block=2))
    image = _image()
    tester.unlock()
    tester.request(b"\x31\x01\xff\x00" + tester.alfid(START, SIZE))
    tester.request(b"\x34\x00" + tester.alfid(START, SIZE))
    blocks = list(_blocks(image))
    assert tester.request(bytes([0x36, 1]) + blocks[0][1]) == b"\x76\x01"

    assert tester.request(bytes([0x36, 2]) + blocks[1][1], timeout_s=0.3) is None
    assert tester.request(b"\x3e\x00", timeout_s=0.3) is None  # the bus stays silent

    server.ecu.restore_link()
    assert tester.request(bytes([0x36, 2]) + blocks[1][1]) == b"\x76\x02"
    assert server.errors == []


def test_power_loss_mid_transfer_then_the_bootloader_answers(served) -> None:
    server, tester = served(_ecu(power_loss_at_block=3))
    image = _image()
    tester.unlock()
    tester.request(b"\x31\x01\xff\x00" + tester.alfid(START, SIZE))
    tester.request(b"\x34\x00" + tester.alfid(START, SIZE))
    blocks = list(_blocks(image))
    for sequence, chunk in blocks[:2]:
        assert tester.request(bytes([0x36, sequence]) + chunk) == bytes([0x76, sequence])

    assert tester.request(bytes([0x36, 3]) + blocks[2][1], timeout_s=0.3) is None
    assert tester.request(b"\x3e\x00", timeout_s=0.3) is None  # powered off

    server.ecu.power_on()
    status = tester.request(b"\x22" + DID_BOOT_STATUS.to_bytes(2, "big"))
    assert status == b"\x62\xfd\x00BOOTLOADER"  # the incomplete application does not start
    assert server.ecu.flash[: 2 * CHUNK] == image[: 2 * CHUNK]
    assert server.errors == []


def test_an_untouched_ecu_answers_identification_like_an_application(served) -> None:
    server, tester = served(_ecu())

    assert tester.request(b"\x22\xfd\x00") == b"\x62\xfd\x00APPLICATION"
    assert tester.request(b"\x22\xf1\x87") == b"\x62\xf1\x87SIM-PART-0001"
    assert tester.request(b"\x2e\xf1\x90\x00") == b"\x7f\x2e\x11"  # not a service it offers
    assert server.ecu.flash == default_simulated_image()
