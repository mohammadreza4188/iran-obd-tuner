"""ELM327 adapter over a scripted serial transport: set-up, reads, failures and safety.

No serial port is opened: the adapter is given a transport factory that returns a
:class:`ScriptedTransport` (see ``tests/conftest.py``) built from data sheet examples.
"""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.models import Unit
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy
from iran_obd_tuner.database.repository import JsonKnowledgeRepository
from iran_obd_tuner.diagnostics.live_data import LiveDataSession, PidStatus
from iran_obd_tuner.diagnostics.service import DiagnosticService
from iran_obd_tuner.hardware.elm327 import Elm327Adapter

INIT_SEQUENCE = ["ATZ", "ATE0", "ATL0", "ATH0", "ATTP0", "0100", "0120", "0140", "ATDPN"]


@pytest.fixture
def make_adapter(scripted_transport, can_vehicle_script):
    """Build an adapter whose port opens a scripted transport; returns (adapter, transport)."""

    def build(script=None, **kwargs):
        transport = scripted_transport(can_vehicle_script if script is None else script)
        opened: list[tuple[str, int]] = []

        def factory(port: str, baudrate: int):
            opened.append((port, baudrate))
            return transport

        adapter = Elm327Adapter("COM3", transport_factory=factory, **kwargs)
        adapter.opened = opened  # type: ignore[attr-defined]
        return adapter, transport

    return build


# --------------------------------------------------------------------------- #
# set-up
# --------------------------------------------------------------------------- #


def test_construction_opens_nothing(make_adapter) -> None:
    adapter, transport = make_adapter()

    assert adapter.is_connected is False
    assert adapter.opened == []  # type: ignore[attr-defined]
    assert transport.written == []
    assert adapter.info.simulated is False


def test_connect_runs_the_documented_init_sequence(make_adapter) -> None:
    adapter, transport = make_adapter(baudrate=9600)

    adapter.connect()

    assert adapter.opened == [("COM3", 9600)]  # type: ignore[attr-defined]
    assert transport.written == INIT_SEQUENCE
    assert adapter.version == "ELM327 v1.5"
    assert adapter.active_protocol == "ISO 15765-4 CAN (11 bit ID, 500 kbaud)"
    supported = adapter.read_supported_pids()
    assert {"010C", "0105", "012F", "0142", "015C"} <= set(supported)
    assert "0110" not in supported


def test_the_eeprom_writing_set_protocol_command_is_never_used(make_adapter) -> None:
    adapter, transport = make_adapter()
    adapter.connect()

    assert not any(command.startswith("ATSP") for command in transport.written)


def test_a_device_that_is_not_an_elm327_gets_nothing_but_the_reset(make_adapter) -> None:
    script = {"ATZ": "MODEM READY"}
    adapter, transport = make_adapter(script)

    with pytest.raises(AdapterConnectionError, match="did not identify as an ELM327"):
        adapter.connect()

    assert transport.written == ["ATZ"]
    assert transport.closed is True
    assert adapter.is_connected is False


def test_port_open_failure_propagates(scripted_transport) -> None:
    def broken_factory(port: str, baudrate: int):
        raise AdapterConnectionError(f"cannot open serial port {port}: access denied")

    adapter = Elm327Adapter("COM9", transport_factory=broken_factory)

    with pytest.raises(AdapterConnectionError, match="COM9"):
        adapter.connect()
    assert adapter.is_connected is False


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("SEARCHING...\nUNABLE TO CONNECT", "UNABLE TO CONNECT"),
        ("SEARCHING...\nNO DATA", "ignition"),
        ("BUS INIT: ...ERROR", "BUS INIT"),
        ("CAN ERROR", "CAN"),
    ],
)
def test_no_vehicle_answer_fails_the_connection_and_closes_the_port(
    make_adapter, can_vehicle_script, reply: str, message: str
) -> None:
    can_vehicle_script["0100"] = reply
    adapter, transport = make_adapter()

    with pytest.raises(AdapterConnectionError, match=message):
        adapter.connect()

    assert transport.closed is True
    assert adapter.is_connected is False


def test_unacknowledged_setup_command_fails_the_connection(
    make_adapter, can_vehicle_script
) -> None:
    can_vehicle_script["ATH0"] = "?"
    adapter, transport = make_adapter()

    with pytest.raises(ProtocolError):
        adapter.connect()
    assert transport.closed is True


def test_adapter_silence_is_a_timeout(make_adapter, can_vehicle_script) -> None:
    can_vehicle_script["ATZ"] = AdapterTimeoutError("COM3: no complete reply within 5.0s")
    adapter, transport = make_adapter()

    with pytest.raises(AdapterTimeoutError):
        adapter.connect()
    assert transport.closed is True


def test_disconnect_closes_the_port(make_adapter) -> None:
    adapter, transport = make_adapter()
    adapter.connect()

    adapter.disconnect()

    assert transport.closed is True
    assert adapter.is_connected is False


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def test_measurements_are_decoded_with_sae_scaling(make_adapter) -> None:
    adapter, _transport = make_adapter()
    adapter.connect()

    rpm = adapter.read_measurement("010c")
    coolant = adapter.read_measurement("0105")
    voltage = adapter.read_measurement("0142")

    assert (rpm.pid, rpm.value, rpm.unit) == ("010C", 1726.0, Unit.RPM)
    assert coolant.value == 83.0
    assert voltage.value == pytest.approx(14.122)


def test_unsupported_parameter_is_refused_without_asking_the_vehicle(make_adapter) -> None:
    adapter, transport = make_adapter()
    adapter.connect()
    sent_before = len(transport.written)

    with pytest.raises(UnsupportedPidError, match="not supported by the vehicle"):
        adapter.read_measurement("0110")

    assert len(transport.written) == sent_before


def test_uncatalogued_parameter_is_unsupported(make_adapter) -> None:
    adapter, _transport = make_adapter()
    adapter.connect()

    with pytest.raises(UnsupportedPidError, match="no decoding definition"):
        adapter.read_measurement("0101")


def test_no_data_for_a_supported_parameter_is_a_timeout(make_adapter) -> None:
    adapter, _transport = make_adapter()
    adapter.connect()

    with pytest.raises(AdapterTimeoutError, match="NO DATA"):
        adapter.read_measurement("010D")


@pytest.mark.parametrize("reply", ["41 0C 1A", "41 0C ZZ 00", "41 0D 00", "BUFFER FULL"])
def test_malformed_replies_are_protocol_errors(
    make_adapter, can_vehicle_script, reply: str
) -> None:
    can_vehicle_script["010C"] = reply
    adapter, _transport = make_adapter()
    adapter.connect()

    with pytest.raises(ProtocolError):
        adapter.read_measurement("010C")


def test_reads_require_a_connection(make_adapter) -> None:
    adapter, transport = make_adapter()

    with pytest.raises(AdapterConnectionError, match="not connected"):
        adapter.read_measurement("010C")
    assert transport.written == []


def test_vin_is_assembled_from_the_multi_frame_reply(make_adapter) -> None:
    adapter, _transport = make_adapter()
    adapter.connect()

    assert adapter.read_vin() == "1D4GP00R55B123456"


def test_vin_not_offered_by_the_vehicle_is_none(make_adapter, can_vehicle_script) -> None:
    can_vehicle_script["0902"] = "NO DATA"
    adapter, _transport = make_adapter()
    adapter.connect()

    assert adapter.read_vin() is None


def test_stored_trouble_codes_on_can(make_adapter) -> None:
    adapter, _transport = make_adapter()
    adapter.connect()

    assert adapter.read_diagnostic_trouble_codes() == ("P0133", "P0171")


def test_stored_trouble_codes_on_iso_9141(make_adapter, can_vehicle_script) -> None:
    can_vehicle_script["ATDPN"] = "A3"
    can_vehicle_script["03"] = "43 01 33 00 00 00 00"
    adapter, _transport = make_adapter()
    adapter.connect()

    assert adapter.active_protocol == "ISO 9141-2 (5 baud init)"
    assert adapter.read_diagnostic_trouble_codes() == ("P0133",)


def test_a_failed_trouble_code_read_is_never_reported_as_no_codes(
    make_adapter, can_vehicle_script
) -> None:
    can_vehicle_script["03"] = "NO DATA"
    adapter, _transport = make_adapter()
    adapter.connect()

    with pytest.raises(AdapterTimeoutError):
        adapter.read_diagnostic_trouble_codes()


# --------------------------------------------------------------------------- #
# safety
# --------------------------------------------------------------------------- #


def test_clearing_codes_is_refused_and_nothing_is_sent(make_adapter) -> None:
    adapter, transport = make_adapter()
    adapter.connect()
    sent_before = list(transport.written)

    with pytest.raises(SafetyGuardError):
        adapter.clear_diagnostic_trouble_codes()

    assert transport.written == sent_before


def test_even_a_permissive_policy_finds_no_clear_implementation(make_adapter) -> None:
    adapter, transport = make_adapter(policy=SafetyPolicy(allowed=frozenset(OperationClass)))
    adapter.connect()
    sent_before = list(transport.written)

    with pytest.raises(UnsupportedOperationError):
        adapter.clear_diagnostic_trouble_codes()

    assert transport.written == sent_before


@pytest.mark.parametrize("command", ["04", "ATSP0", "08 01", "2E F1 90"])
def test_the_command_gate_refuses_writes_before_they_reach_the_port(
    make_adapter, command: str
) -> None:
    adapter, transport = make_adapter()
    adapter.connect()
    sent_before = list(transport.written)

    with pytest.raises(SafetyGuardError):
        adapter._command(command)

    assert transport.written == sent_before


def test_every_command_a_full_session_sends_is_read_only(make_adapter) -> None:
    adapter, transport = make_adapter()
    session = LiveDataSession(adapter, JsonKnowledgeRepository())
    session.connect()
    session.poll(["010C", "010D", "0105", "0142", "012F", "0111", "010F", "0104", "0110"])
    session.read_trouble_codes()
    session.read_vehicle_info()
    session.disconnect()

    allowed_prefixes = ("AT", "01", "03", "09")
    assert all(command.startswith(allowed_prefixes) for command in transport.written)
    assert not any(command.startswith("04") for command in transport.written)


# --------------------------------------------------------------------------- #
# through the diagnostic layer
# --------------------------------------------------------------------------- #


def test_live_session_reports_per_parameter_status(make_adapter) -> None:
    adapter, _transport = make_adapter()
    session = LiveDataSession(adapter, JsonKnowledgeRepository())
    session.connect()

    snapshot = session.poll(["010C", "010D", "0110"])

    assert snapshot.simulated is False
    assert [r.status for r in snapshot.readings] == [
        PidStatus.OK,
        PidStatus.NO_RESPONSE,
        PidStatus.UNSUPPORTED,
    ]
    assert session.active_protocol == "ISO 15765-4 CAN (11 bit ID, 500 kbaud)"


def test_report_carries_the_negotiated_protocol_and_real_data_flag(make_adapter) -> None:
    adapter, transport = make_adapter()
    service = DiagnosticService(adapter, JsonKnowledgeRepository(), live_data_pids=["010C"])

    report = service.collect_report()

    assert report.vehicle.simulated is False
    assert report.vehicle.protocol == "ISO 15765-4 CAN (11 bit ID, 500 kbaud)"
    assert report.vehicle.vin == "1D4GP00R55B123456"
    assert [code.code for code in report.trouble_codes] == ["P0133", "P0171"]
    assert transport.closed is True  # the one-shot workflow closes the port
