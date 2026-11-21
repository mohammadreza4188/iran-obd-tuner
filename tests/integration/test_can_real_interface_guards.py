"""Guards of the *real* CAN interface path (audit H2), exercised without any hardware.

``can.Bus`` is replaced by a recorder that opens python-can's in-process ``virtual`` bus
instead, so the code path taken for ``slcan`` (bitrate allowlist, listen-only probe, no
transmission before a clean probe, abort on a bus that never answers) runs for real while
nothing can reach a physical interface. Skipped without python-can (``[can]`` extra).
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

can = pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.core.exceptions import (  # noqa: E402
    AdapterConnectionError,
    ConfigurationError,
    SafetyGuardError,
)
from iran_obd_tuner.core.traffic import Direction, TrafficLog  # noqa: E402
from iran_obd_tuner.diagnostics.ecu_identification import identify_over_link  # noqa: E402
from iran_obd_tuner.hardware import can_bus  # noqa: E402
from iran_obd_tuner.hardware.can_bus import REAL_BITRATES, PythonCanBus  # noqa: E402
from iran_obd_tuner.hardware.uds import UdsNoTrafficError  # noqa: E402
from iran_obd_tuner.hardware.uds_link import UdsLink, UdsLinkConfig  # noqa: E402
from iran_obd_tuner.hardware.uds_sim import VirtualBusEcuServer  # noqa: E402

REAL_BUS = can.Bus


@pytest.fixture
def channel_name(request: pytest.FixtureRequest) -> str:
    return f"guard-{request.node.name}"


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every ``can.Bus`` call for a non-virtual interface, served by a virtual bus."""
    calls: list[dict[str, Any]] = []

    def fake_bus(*, interface: str, channel: str, **kwargs: Any):
        if interface == "virtual":
            return REAL_BUS(interface="virtual", channel=channel, **kwargs)
        calls.append({"interface": interface, "channel": channel, **kwargs})
        return REAL_BUS(interface="virtual", channel=channel, can_filters=kwargs["can_filters"])

    monkeypatch.setattr(can, "Bus", fake_bus)
    return calls


def _later(delay_s: float, action) -> threading.Timer:
    timer = threading.Timer(delay_s, action)
    timer.start()
    return timer


# --------------------------------------------------------------------------- bitrate allowlist


@pytest.mark.parametrize("bitrate", [125_000, 50_000, 1_000_000, 500_001])
def test_real_interfaces_accept_only_the_obd_bitrates(bitrate: int) -> None:
    with pytest.raises(ConfigurationError, match="not allowed on a real interface"):
        PythonCanBus.open("slcan", "COM5", bitrate)
    with pytest.raises(ConfigurationError, match="not allowed on a real interface"):
        UdsLinkConfig(interface="pcan", channel="PCAN_USBBUS1", bitrate=bitrate)
    UdsLinkConfig(interface="virtual-sim", bitrate=bitrate)  # the simulator does not care


def test_interfaces_without_listen_only_support_are_refused() -> None:
    for interface in ("gs_usb", "socketcan"):
        with pytest.raises(ConfigurationError, match="unsupported CAN interface"):
            PythonCanBus.open(interface, "can0", 500_000)
        with pytest.raises(ConfigurationError):
            UdsLinkConfig(interface=interface, channel="can0")


def test_listen_only_arguments_per_driver() -> None:
    assert can_bus._listen_only_kwargs(can, "slcan") == {"listen_only": True}
    assert can_bus._listen_only_kwargs(can, "pcan") == {"state": can.BusState.PASSIVE}
    assert can_bus._listen_only_kwargs(can, "kvaser") == {"driver_mode": False}


# --------------------------------------------------------------------------- listen-only probe


def test_a_listen_only_bus_refuses_to_transmit(channel_name: str) -> None:
    bus = PythonCanBus.open("virtual", channel_name, 500_000, listen_only=True)
    try:
        with pytest.raises(SafetyGuardError, match="listen-only"):
            bus.send(can_bus.CanFrame(0x7E0, b"\x02\x3e\x00"))
    finally:
        bus.close()


def test_probe_counts_traffic_and_opens_listen_only(opened, channel_name: str) -> None:
    other = REAL_BUS(interface="virtual", channel=channel_name)
    timers = [
        _later(0.05 * i, lambda: other.send(can.Message(arbitration_id=0x0A0, data=b"\x01")))
        for i in range(1, 4)
    ]
    try:
        probe = PythonCanBus.probe("slcan", channel_name, 500_000, duration_s=0.5)
    finally:
        for timer in timers:
            timer.join()
        other.shutdown()

    assert opened[0]["listen_only"] is True
    assert probe.frames == 3 and probe.traffic_seen
    assert "bitrate confirmed" in probe.describe()


def test_an_error_frame_during_the_probe_aborts_before_any_transmission(
    opened, channel_name: str
) -> None:
    other = REAL_BUS(interface="virtual", channel=channel_name)
    timer = _later(0.05, lambda: other.send(can.Message(arbitration_id=0, is_error_frame=True)))
    link = UdsLink(UdsLinkConfig(interface="slcan", channel=channel_name), probe_s=0.5)
    try:
        with pytest.raises(AdapterConnectionError, match="error frame"):
            link.open()
    finally:
        timer.join()
        other.shutdown()
        link.close()

    assert len(opened) == 1 and opened[0]["listen_only"] is True  # never reopened active


def test_a_bad_controller_status_aborts_the_probe() -> None:
    class SickPcan:
        def recv(self, timeout: float):
            return None

        def status_is_ok(self) -> bool:
            return False

        def shutdown(self) -> None:
            raise can.CanOperationError("device gone")  # suppressed by close()

    bus = PythonCanBus(SickPcan(), "pcan", "PCAN_USBBUS1")
    with pytest.raises(AdapterConnectionError, match="error status"):
        bus.recv(0.01)
    bus.close()  # L2: a failing shutdown does not raise


def test_the_skipping_of_the_probe_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="cannot be skipped"):
        UdsLink(UdsLinkConfig(interface="slcan", channel="COM5"), probe_s=0)


# --------------------------------------------------------------------------- full real path


def test_real_path_probes_first_then_identifies(opened, channel_name: str) -> None:
    server = VirtualBusEcuServer(channel_name)
    server.start()
    log = TrafficLog()
    try:
        with UdsLink(
            UdsLinkConfig(interface="slcan", channel=channel_name), traffic_log=log, probe_s=0.2
        ) as link:
            result = identify_over_link(link)
            probe = link.probe
    finally:
        server.stop()

    assert [call.get("listen_only", False) for call in opened] == [True, False]
    assert probe is not None and probe.frames == 0  # the simulated ECU is silent until asked
    entries = log.snapshot()
    assert entries[0].direction is Direction.INFO and "NOT confirmed" in entries[0].text
    assert entries[0].simulated is False
    assert result.simulated is False  # labelled by the link, never by the data
    assert result.vin is not None


def test_a_bus_that_never_answers_aborts_after_one_request(opened, channel_name: str) -> None:
    log = TrafficLog()
    with (
        UdsLink(
            UdsLinkConfig(interface="slcan", channel=channel_name), traffic_log=log, probe_s=0.1
        ) as link,
        pytest.raises(UdsNoTrafficError),
    ):
        identify_over_link(link)

    sent = [entry for entry in log.snapshot() if entry.direction is Direction.TX]
    assert len(sent) == 1  # the first DID request only; nothing retried, no other DID


def test_bitrates_constant_matches_iso_15765_4() -> None:
    assert REAL_BITRATES == (250_000, 500_000)
