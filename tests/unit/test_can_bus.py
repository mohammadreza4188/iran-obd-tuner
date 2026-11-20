"""python-can bus wrapper and ISO-TP over the in-process ``virtual`` bus (no hardware).

Every bus here is opened with ``interface="virtual"`` explicitly; nothing can reach a real
CAN interface. Skipped when python-can (the ``[can]`` extra) is not installed.
"""

from __future__ import annotations

import threading

import pytest

can = pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.core.exceptions import (  # noqa: E402
    AdapterConnectionError,
    ConfigurationError,
)
from iran_obd_tuner.hardware.can_bus import CanFrame, PythonCanBus  # noqa: E402
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig  # noqa: E402


@pytest.fixture
def channel_name(request: pytest.FixtureRequest) -> str:
    return f"test-{request.node.name}"


def test_unsupported_interface_and_bitrate_are_refused() -> None:
    with pytest.raises(ConfigurationError, match="unsupported CAN interface"):
        PythonCanBus.open("vector", "0", 500_000)
    with pytest.raises(ConfigurationError, match="bitrate"):
        PythonCanBus.open("virtual", "x", 0)


def test_frames_travel_between_virtual_buses_and_filters_apply(channel_name: str) -> None:
    tester = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E8,))
    ecu = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E0,))
    try:
        assert tester.simulated is True
        ecu.send(CanFrame(0x123, b"\x01"))  # filtered out at the tester
        ecu.send(CanFrame(0x7E8, b"\x02\x7e\x00"))
        received = tester.recv(1.0)
        assert received is not None and received.arbitration_id == 0x7E8
        assert received.data == b"\x02\x7e\x00" and received.timestamp is not None
        assert tester.recv(0.05) is None
    finally:
        tester.close()
        ecu.close()
        tester.close()  # idempotent


def test_an_error_frame_stops_the_session(channel_name: str) -> None:
    tester = PythonCanBus.open("virtual", channel_name, 500_000)
    raw = can.Bus(interface="virtual", channel=channel_name)
    try:
        raw.send(can.Message(arbitration_id=0, is_error_frame=True))
        with pytest.raises(AdapterConnectionError, match="error frame"):
            tester.recv(1.0)
    finally:
        tester.close()
        raw.shutdown()


def test_isotp_multi_frame_exchange_over_the_virtual_bus(channel_name: str) -> None:
    tester_bus = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E8,))
    ecu_bus = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E0,))
    tester = IsoTpChannel(tester_bus, IsoTpConfig(0x7E0, 0x7E8))
    ecu = IsoTpChannel(ecu_bus, IsoTpConfig(0x7E8, 0x7E0, block_size=4, st_min=1))
    answer = bytes(range(200))
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            request = ecu.recv(2.0)
            assert request == bytes(range(100))
            ecu.send(answer)
        except BaseException as exc:  # reported to the test thread
            errors.append(exc)

    server = threading.Thread(target=serve)
    server.start()
    try:
        tester.send(bytes(range(100)))
        assert tester.recv(3.0) == answer
    finally:
        server.join(5)
        tester_bus.close()
        ecu_bus.close()
    assert errors == []
