"""Read-only UDS client: allowlist, services, negative responses and the simulated ECU.

The client runs over ISO-TP on a synchronous fake bus whose responder is the simulated ECU,
so every exchange (including segmented answers and responsePending loops) is deterministic.
"""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import AdapterTimeoutError, SafetyGuardError
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig
from iran_obd_tuner.hardware.uds import (
    ALLOWED_REQUESTS,
    FORBIDDEN_SERVICES,
    UdsClient,
    UdsDtcStatus,
    UdsNegativeResponseError,
    UdsNotSupportedError,
    UdsTiming,
    check_request_allowed,
    decode_did_text,
    decode_uds_dtc,
)
from iran_obd_tuner.hardware.uds_sim import SimulatedEcuConfig, SimulatedUdsEcu

TX, RX = 0x7E0, 0x7E8


# --------------------------------------------------------------------------- allowlist


def test_allowlist_is_exactly_the_read_only_set() -> None:
    allowed: set[tuple[int, int]] = set()
    for sid in range(256):
        for sub in range(256):
            try:
                check_request_allowed(bytes([sid, sub, 0x00]))
            except SafetyGuardError:
                continue
            allowed.add((sid, sub))

    expected = {(0x10, s) for s in (0x01, 0x81, 0x03, 0x83)}
    expected |= {(0x3E, 0x00), (0x3E, 0x80), (0x19, 0x01), (0x19, 0x02)}
    expected |= {(0x22, sub) for sub in range(256)}  # any DID may be read
    assert allowed == expected


@pytest.mark.parametrize("sid", sorted(FORBIDDEN_SERVICES))
def test_write_security_and_programming_services_are_refused_by_name(sid: int) -> None:
    with pytest.raises(SafetyGuardError, match=FORBIDDEN_SERVICES[sid]):
        check_request_allowed(bytes([sid, 0x01, 0x00, 0x00]))


def test_required_forbidden_services_are_listed() -> None:
    required = {0x11, 0x14, 0x27, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37}
    assert required <= set(FORBIDDEN_SERVICES)
    assert required.isdisjoint(ALLOWED_REQUESTS)


@pytest.mark.parametrize("session", [0x02, 0x04, 0x40, 0x60, 0x7E])
def test_programming_and_vendor_sessions_are_refused(make_uds_client, session: int) -> None:
    client, ecu, bus = make_uds_client(allow_extended_session=True)
    with pytest.raises(SafetyGuardError):
        client.diagnostic_session_control(session)
    assert bus.sent == []  # nothing reached the bus


def test_malformed_requests_are_refused() -> None:
    for request in (b"", b"\x22\xf1", b"\x22\xf1\x90\xf1\x89", b"\x10", b"\x19"):
        with pytest.raises(SafetyGuardError):
            check_request_allowed(request)


# --------------------------------------------------------------------------- services


def test_read_vin_through_response_pending_and_segmentation(make_uds_client) -> None:
    client, ecu, _bus = make_uds_client()

    assert client.read_data_by_identifier(0xF190) == b"SIMULATEDVIN00004"
    assert ecu.requests == [b"\x22\xf1\x90"]


def test_negative_responses_are_typed(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client(
        SimulatedUdsEcu(SimulatedEcuConfig(conditions_not_correct_dids=frozenset({0xF189})))
    )

    with pytest.raises(UdsNotSupportedError) as unknown:
        client.read_data_by_identifier(0x1234)
    with pytest.raises(UdsNotSupportedError) as protected:
        client.read_data_by_identifier(0xF18C)
    with pytest.raises(UdsNegativeResponseError) as conditions:
        client.read_data_by_identifier(0xF189)

    assert unknown.value.nrc == 0x31 and "requestOutOfRange" in str(unknown.value)
    assert protected.value.nrc == 0x33 and "securityAccessDenied" in str(protected.value)
    assert conditions.value.nrc == 0x22 and "conditionsNotCorrect" in str(conditions.value)


def test_response_pending_has_a_ceiling(make_uds_client) -> None:
    ecu = SimulatedUdsEcu(SimulatedEcuConfig(pending_dids={0xF190: 50}))
    client, _ecu, _bus = make_uds_client(ecu, timing=UdsTiming(max_pending=10))

    with pytest.raises(AdapterTimeoutError, match="responsePending"):
        client.read_data_by_identifier(0xF190)


def test_busy_repeat_request_is_retried_twice(make_uds_client) -> None:
    class BusyEcu(SimulatedUdsEcu):
        busy = 2

        def handle(self, request: bytes) -> list[bytes]:
            if request[:1] == b"\x22" and self.busy:
                self.busy -= 1
                self.requests.append(request)
                return [b"\x7f\x22\x21"]
            return super().handle(request)

    client, ecu, _bus = make_uds_client(BusyEcu())

    assert client.read_data_by_identifier(0xF189) == b"SIM-SW-1.0"
    assert ecu.requests.count(b"\x22\xf1\x89") == 3


def test_no_answer_is_a_timeout(fake_can_bus) -> None:
    bus = fake_can_bus()
    channel = IsoTpChannel(bus, IsoTpConfig(TX, RX), clock=bus.clock, sleep=bus.sleep)
    client = UdsClient(channel, clock=bus.clock, sleep=bus.sleep)

    with pytest.raises(AdapterTimeoutError, match="no answer"):
        client.read_data_by_identifier(0xF190)


def test_extended_session_is_opt_in_and_always_left(make_uds_client) -> None:
    locked, _ecu, bus = make_uds_client()
    with pytest.raises(SafetyGuardError, match="not enabled"), locked.extended_session():
        pass
    assert bus.sent == []

    client, ecu, _bus = make_uds_client(allow_extended_session=True)
    with pytest.raises(UdsNotSupportedError):
        client.read_data_by_identifier(0xF197)  # extended-only DID
    with pytest.raises(RuntimeError), client.extended_session():
        assert client.read_data_by_identifier(0xF197) == b"SIMULATED ECU"
        raise RuntimeError("even on errors the default session is restored")

    assert ecu.session == 0x01 and client.in_extended_session is False
    assert ecu.requests[-1] == b"\x10\x01"


def test_tester_present_keeps_the_extended_session_alive(make_uds_client, fake_clock) -> None:
    client, ecu, _bus = make_uds_client(allow_extended_session=True)
    with client.extended_session():
        fake_clock.advance(3.0)  # longer than keepalive_s
        client.read_data_by_identifier(0xF197)

    assert b"\x3e\x80" in ecu.requests


def test_tester_present_with_and_without_suppression(make_uds_client) -> None:
    client, ecu, _bus = make_uds_client()
    client.tester_present(suppress_response=True)
    client.tester_present(suppress_response=False)

    assert ecu.requests == [b"\x3e\x80", b"\x3e\x00"]


def test_dtc_count_format_and_records(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()

    availability, dtc_format, count = client.read_dtc_count(0xFF)
    mask, records = client.read_dtcs_by_status_mask(0xFF)

    assert (availability, dtc_format, count) == (0xFF, 0x00, 2)
    assert mask == 0xFF
    codes = [decode_uds_dtc(raw, dtc_format) for raw, _status in records]
    assert codes == ["P0301-00", "P0420-00"]
    first, second = records[0][1], records[1][1]
    assert first.confirmed and first.test_failed and not first.warning_indicator
    assert second.pending and not second.confirmed


def test_status_mask_filters_dtcs(make_uds_client) -> None:
    client, _ecu, _bus = make_uds_client()
    _mask, records = client.read_dtcs_by_status_mask(0x08)  # confirmed only

    assert [raw.hex() for raw, _ in records] == ["030100"]


def test_unknown_dtc_formats_are_not_guessed() -> None:
    assert decode_uds_dtc(bytes.fromhex("030100"), 0x01) is None
    assert decode_uds_dtc(bytes.fromhex("0301"), 0x00) is None
    assert UdsDtcStatus(0x80).warning_indicator


def test_did_text_decoding() -> None:
    assert decode_did_text(b"SIM-SW-1.0\x00\x00") == "SIM-SW-1.0"
    assert decode_did_text(b"ABC   \xff") == "ABC"
    assert decode_did_text(b"\x01\x02") is None
    assert decode_did_text(b"\x00\x00") is None


def test_simulator_refuses_every_service_outside_the_read_set() -> None:
    ecu = SimulatedUdsEcu()
    for sid in range(256):
        if sid in (0x10, 0x19, 0x22, 0x3E):
            continue
        assert ecu.handle(bytes([sid, 0x01, 0x02])) == [bytes([0x7F, sid, 0x11])]
