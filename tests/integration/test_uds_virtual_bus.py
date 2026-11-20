"""End to end: python-can virtual bus -> ISO-TP -> UDS client <-> simulated ECU server.

Real threads and real (short) timing, but only python-can's in-process ``virtual``
interface: nothing can reach a physical CAN bus. Skipped without the ``[can]`` extra.
"""

from __future__ import annotations

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.hardware.can_bus import PythonCanBus  # noqa: E402
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig, functional_request  # noqa: E402
from iran_obd_tuner.hardware.uds import UdsClient, UdsNotSupportedError, decode_uds_dtc  # noqa: E402
from iran_obd_tuner.hardware.uds_sim import VirtualBusEcuServer  # noqa: E402


@pytest.fixture
def channel_name(request: pytest.FixtureRequest) -> str:
    return f"uds-{request.node.name}"


def test_identification_and_dtcs_over_the_virtual_bus(channel_name: str) -> None:
    with VirtualBusEcuServer(channel_name) as server:
        bus = PythonCanBus.open("virtual", channel_name, 500_000, receive_ids=(0x7E8,))
        try:
            client = UdsClient(IsoTpChannel(bus, IsoTpConfig(0x7E0, 0x7E8)))

            vin = client.read_data_by_identifier(0xF190)  # 2x responsePending, multi-frame
            software = client.read_data_by_identifier(0xF189)  # single frame
            with pytest.raises(UdsNotSupportedError):
                client.read_data_by_identifier(0xF18C)  # protected: never unlocked
            _avail, dtc_format, count = client.read_dtc_count()
            _mask, records = client.read_dtcs_by_status_mask()
        finally:
            bus.close()
    assert server.errors == []
    assert vin == b"SIMULATEDVIN00004"
    assert software == b"SIM-SW-1.0"
    assert count == 2
    assert [decode_uds_dtc(raw, dtc_format) for raw, _ in records] == ["P0301-00", "P0420-00"]


def test_functional_tester_present_discovers_the_simulated_ecu(channel_name: str) -> None:
    with VirtualBusEcuServer(channel_name):
        bus = PythonCanBus.open(
            "virtual", channel_name, 500_000, receive_ids=tuple(range(0x7E8, 0x7F0))
        )
        try:
            answers = functional_request(bus, 0x7DF, b"\x3e\x00", range(0x7E8, 0x7F0), 0.5)
        finally:
            bus.close()
    assert answers == {0x7E8: b"\x7e\x00"}


def test_the_server_only_opens_the_virtual_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    real_open = PythonCanBus.open

    def spy(interface, channel, bitrate, **kwargs):
        opened.append(interface)
        return real_open(interface, channel, bitrate, **kwargs)

    monkeypatch.setattr(PythonCanBus, "open", staticmethod(spy))
    server = VirtualBusEcuServer("uds-spy")
    server.stop()

    assert opened == ["virtual"]


def test_evidence_bundle_redacts_the_vin_from_real_iso_tp_frames(channel_name: str) -> None:
    """The simulated VIN (F190, multi-frame with responsePending) must not survive export."""
    from iran_obd_tuner.core.traffic import TrafficLog
    from iran_obd_tuner.diagnostics.ecu_identification import identify_over_link
    from iran_obd_tuner.diagnostics.evidence_export import (
        REDACTED_VIN_RESPONSE,
        build_evidence_bundle,
    )
    from iran_obd_tuner.hardware.uds_link import UdsLink, UdsLinkConfig

    log = TrafficLog()
    config = UdsLinkConfig(interface="virtual-sim", channel=channel_name)
    with UdsLink(config, traffic_log=log) as link:
        result = identify_over_link(link)
    assert result.vin == "SIMULATEDVIN00004"
    raw = "".join(e.text.replace(" ", "") for e in log.snapshot())
    assert "4E3030303034" in raw  # the serial ("N00004") really is in the captured frames

    bundle = build_evidence_bundle(
        tool_version="test", identification=result, traffic=log.snapshot()
    )
    text = bundle.to_json()
    assert "SIMULATEDVIN00004" not in text
    assert "4E3030303034" not in text.replace(" ", "")
    assert REDACTED_VIN_RESPONSE in text
    assert bundle.document["simulated"] is True
    assert bundle.document["usable_for_verification"] is False
