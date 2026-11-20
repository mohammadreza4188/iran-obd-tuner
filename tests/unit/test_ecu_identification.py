"""UDS ECU identification: per-DID outcomes, the opt-in extended fallback, DTCs, JSON.

Runs the real UDS client over ISO-TP on the synchronous fake bus (``make_uds_client``),
with the simulated ECU on the other side; no CAN interface is opened.
"""

from __future__ import annotations

import json

import pytest

from iran_obd_tuner.core.exceptions import AdapterConnectionError, ConfigurationError
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.diagnostics.ecu_identification import (
    IDENTIFICATION_DIDS,
    EcuIdentification,
    read_ecu_identification,
)
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.hardware.uds_link import LINK_INTERFACES, UdsLink, UdsLinkConfig
from iran_obd_tuner.hardware.uds_sim import SimulatedEcuConfig, SimulatedUdsEcu


def identify(client, *, simulated: bool = True) -> EcuIdentification:
    return read_ecu_identification(
        client, interface="virtual-sim", simulated=simulated, request_id=0x7E0, response_id=0x7E8
    )


def session_requests(ecu: SimulatedUdsEcu) -> list[bytes]:
    return [request for request in ecu.requests if request[:1] == b"\x10"]


def test_default_session_reads_every_did_independently(make_uds_client) -> None:
    client, ecu, _bus = make_uds_client()

    result = identify(client)

    outcomes = {reading.did: reading.outcome for reading in result.dids}
    assert [reading.did for reading in result.dids] == list(IDENTIFICATION_DIDS)
    assert outcomes[0xF190] is ReadOutcome.OK
    assert outcomes[0xF189] is ReadOutcome.OK
    system_name = result.did(0xF197)
    assert system_name is not None
    assert system_name.outcome is ReadOutcome.UNSUPPORTED  # extended-only in the simulator
    assert system_name.nrc == 0x31 and system_name.nrc_name == "requestOutOfRange"
    assert session_requests(ecu) == []  # no session change without the opt-in
    assert result.extended_session_used is False
    assert result.read_only is True


def test_vin_and_vehicle_info_come_from_did_f190(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()

    result = identify(client)
    info = result.vehicle_info()

    assert result.vin == "SIMULATEDVIN00004"
    assert info.vin == "SIMULATEDVIN00004"
    assert info.vin_source == "uds_f190"
    assert info.simulated is True
    assert info.adapter_name == "can:virtual-sim"


def test_simulated_flag_is_taken_from_the_caller_not_the_values(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()

    assert identify(client, simulated=False).vehicle_info().simulated is False


def test_extended_fallback_is_opt_in_and_returns_to_default(make_uds_client) -> None:
    client, ecu, _bus = make_uds_client(allow_extended_session=True)

    result = identify(client)

    system_name = result.did(0xF197)
    assert system_name is not None
    assert system_name.outcome is ReadOutcome.OK
    assert system_name.text == "SIMULATED ECU"
    assert system_name.session == "extended"
    assert result.did(0xF190).session == "default"  # type: ignore[union-attr]
    assert session_requests(ecu) == [b"\x10\x03", b"\x10\x01"]
    assert ecu.session == 0x01
    assert result.extended_session_used is True


def test_extended_session_refused_by_the_ecu_keeps_the_default_results(make_uds_client) -> None:
    class NoExtendedEcu(SimulatedUdsEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request == b"\x10\x03":
                self.requests.append(request)
                return [b"\x7f\x10\x12"]  # subFunctionNotSupported
            return super().handle(request)

    client, _ecu, _bus = make_uds_client(NoExtendedEcu(), allow_extended_session=True)

    result = identify(client)

    assert result.extended_session_used is False
    assert result.extended_session_detail is not None
    assert "subFunctionNotSupported" in result.extended_session_detail
    assert result.did(0xF197).outcome is ReadOutcome.UNSUPPORTED  # type: ignore[union-attr]
    assert result.vin == "SIMULATEDVIN00004"


def test_unanswered_and_negative_dids_do_not_hide_the_others(make_uds_client) -> None:
    class SilentOnSoftware(SimulatedUdsEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request == b"\x22\xf1\x89":
                self.requests.append(request)
                return []  # never answers
            return super().handle(request)

    ecu = SilentOnSoftware(SimulatedEcuConfig(conditions_not_correct_dids=frozenset({0xF191})))
    client, _ecu, _bus = make_uds_client(ecu)

    result = identify(client)

    software, hardware = result.did(0xF189), result.did(0xF191)
    assert software is not None and software.outcome is ReadOutcome.NO_RESPONSE
    assert hardware is not None and hardware.outcome is ReadOutcome.ERROR
    assert hardware.nrc_name == "conditionsNotCorrect"
    assert result.did(0xF187).outcome is ReadOutcome.OK  # type: ignore[union-attr]
    assert result.dtc_outcome is ReadOutcome.OK


def test_dtcs_are_decoded_with_their_status(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()

    result = identify(client)

    assert result.dtc_outcome is ReadOutcome.OK
    assert result.dtc_format == 0x00
    assert [dtc.code for dtc in result.dtcs] == ["P0301-00", "P0420-00"]
    assert result.dtcs[0].status.confirmed
    assert result.dtcs[1].status.pending and not result.dtcs[1].status.confirmed


def test_unknown_dtc_format_keeps_the_raw_bytes(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client(SimulatedUdsEcu(SimulatedEcuConfig(dtc_format=0x01)))

    result = identify(client)

    assert [dtc.code for dtc in result.dtcs] == [None, None]
    assert result.as_dict()["dtcs"][0]["raw_hex"] == "030100"


def test_unsupported_dtc_service_is_reported_as_such(make_uds_client) -> None:
    class NoDtcEcu(SimulatedUdsEcu):
        def handle(self, request: bytes) -> list[bytes]:
            if request[:1] == b"\x19":
                self.requests.append(request)
                return [b"\x7f\x19\x11"]
            return super().handle(request)

    client, _ecu, _bus = make_uds_client(NoDtcEcu())

    result = identify(client)

    assert result.dtc_outcome is ReadOutcome.UNSUPPORTED
    assert result.dtcs == ()
    assert result.vin == "SIMULATEDVIN00004"


def test_a_malformed_vin_is_not_reported_as_a_vin(make_uds_client) -> None:
    dids = dict(SimulatedEcuConfig().dids)
    dids[0xF190] = b"SHORTVIN"
    client, _ecu, _bus = make_uds_client(SimulatedUdsEcu(SimulatedEcuConfig(dids=dids)))

    result = identify(client)

    assert result.did(0xF190).text == "SHORTVIN"  # type: ignore[union-attr]
    assert result.vin is None
    assert result.vehicle_info().vin is None
    assert result.vehicle_info().vin_source is None


def test_a_lost_interface_aborts_instead_of_returning_partial_data(make_uds_client) -> None:
    client, _ecu, bus = make_uds_client()

    def unplugged(frame):
        raise AdapterConnectionError("CAN interface lost")

    bus.responder = unplugged
    with pytest.raises(AdapterConnectionError):
        identify(client)


def test_json_document_is_separate_and_labelled(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()

    payload = json.loads(json.dumps(identify(client).as_dict()))

    assert payload["kind"] == "uds_ecu_identification"
    assert payload["schema_version"] == 1
    assert payload["simulated"] is True and payload["read_only"] is True
    assert payload["request_id"] == "7E0" and payload["response_id"] == "7E8"
    assert payload["vehicle"]["vin_source"] == "uds_f190"
    assert payload["dids"][0] == {
        "did": "F190",
        "name": "vin",
        "outcome": "ok",
        "raw_hex": "53 49 4D 55 4C 41 54 45 44 56 49 4E 30 30 30 30 34",
        "text": "SIMULATEDVIN00004",
        "session": "default",
        "nrc": None,
        "nrc_name": None,
        "detail": None,
    }


# --------------------------------------------------------------------------- VehicleInfo


def test_vin_source_is_validated() -> None:
    assert VehicleInfo(vin="SIMULATEDVIN00004", vin_source="obd_09").vin_source == "obd_09"
    with pytest.raises(ValueError):
        VehicleInfo(vin="SIMULATEDVIN00004", vin_source="guess")
    with pytest.raises(ValueError):
        VehicleInfo(vin=None, vin_source="uds_f190")


def test_obd_identification_marks_its_vin_source(adapter) -> None:
    from iran_obd_tuner.diagnostics.service import vehicle_info_for

    adapter.connect()
    try:
        info = vehicle_info_for(adapter, adapter.read_vin())
    finally:
        adapter.disconnect()

    assert info.vin_source == "obd_09"
    assert vehicle_info_for(adapter, None).vin_source is None


# --------------------------------------------------------------------------- link config


def test_link_config_validation() -> None:
    assert UdsLinkConfig().simulated is True
    assert "virtual" not in LINK_INTERFACES  # nobody would answer on a bare virtual bus
    for bad in (
        {"interface": "virtual"},
        {"interface": "socketcand"},
        {"bitrate": 0},
        {"request_id": 0x800},
        {"request_id": 0x7E8},
        {"padding": 0x100},
    ):
        with pytest.raises(ConfigurationError):
            UdsLinkConfig(**bad)


def test_a_real_interface_needs_a_channel_and_never_starts_the_simulator() -> None:
    link = UdsLink(UdsLinkConfig(interface="slcan"))

    with pytest.raises(ConfigurationError, match="channel"):
        link.open()
    assert link.simulated is False
    assert link._server is None  # noqa: SLF001 - the simulator was never started


def test_missing_python_can_is_a_clear_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from iran_obd_tuner.diagnostics.ecu_identification import UdsIdentificationSession

    monkeypatch.setitem(sys.modules, "can", None)  # import can -> ImportError
    with pytest.raises(AdapterConnectionError, match=r"\[can\] extra"):
        UdsIdentificationSession(UdsLinkConfig()).identify()
