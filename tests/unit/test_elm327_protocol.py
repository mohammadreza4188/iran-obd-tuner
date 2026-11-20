"""ELM327 reply parsing and the read-only command whitelist.

Most inputs are the examples printed in the ELM327 data sheet (ELM327DSJ), so these tests
check the parser against the vendor's own documentation, not against invented replies.
"""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedOperationError,
)
from iran_obd_tuner.core.pids import PID_CATALOG, decode_pid_value
from iran_obd_tuner.hardware import elm327_protocol as proto

# --------------------------------------------------------------------------- #
# whitelist
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command",
    [
        "ATZ",
        "AT Z",
        "ate0",
        "ATL0",
        "ATH0",
        "ATTP0",
        "ATDPN",
        "ATRV",
        "0100",
        "01 0C",
        "010c",
        "03",
        "0902",
    ],
)
def test_read_only_commands_are_allowed(command: str) -> None:
    assert proto.check_command_allowed(command) == command.replace(" ", "").upper()


@pytest.mark.parametrize(
    "command",
    [
        "04",  # clear trouble codes and stored values
        "0400",
        "08 01",  # control of on-board systems
        "ATSP0",  # writes the adapter's EEPROM
        "ATSP6",
        "ATMA",  # monitor all
        "ATPP 2C SV 01",  # programmable parameters
        "2E F1 90",  # UDS WriteDataByIdentifier
        "27 01",  # UDS SecurityAccess
        "31 01",  # UDS RoutineControl
        "10 02",  # UDS programming session
        "3E 00",
        "0100 0100",
        "",
    ],
)
def test_everything_else_is_refused_before_it_is_sent(command: str) -> None:
    with pytest.raises(SafetyGuardError, match="only read-only commands"):
        proto.check_command_allowed(command)


# --------------------------------------------------------------------------- #
# cleaning and status messages
# --------------------------------------------------------------------------- #


def test_clean_reply_drops_prompt_echo_progress_and_blank_lines() -> None:
    raw = b"0100\r\rSEARCHING...\r41 00 BE 1F B8 10\r\r>"

    assert proto.clean_reply(raw, "0100") == ["41 00 BE 1F B8 10"]


def test_clean_reply_keeps_data_after_a_bus_init_line() -> None:
    assert proto.clean_reply(b"BUS INIT: ...OK\r41 05 7B\r", "0105") == ["41 05 7B"]


def test_clean_reply_rejects_binary_garbage() -> None:
    with pytest.raises(ProtocolError, match="non-ASCII"):
        proto.clean_reply(b"\xff\xfe41 0C")


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("NO DATA", proto.NoDataError),
        ("UNABLE TO CONNECT", AdapterConnectionError),
        ("BUS INIT: ...ERROR", AdapterConnectionError),
        ("CAN ERROR", AdapterConnectionError),
        ("BUS ERROR", AdapterConnectionError),
        ("BUS BUSY", AdapterConnectionError),
        ("FB ERROR", AdapterConnectionError),
        ("LV RESET", AdapterConnectionError),
        ("ACT ALERT", AdapterConnectionError),
        ("!LP ALERT", AdapterConnectionError),
        ("ERR94", AdapterConnectionError),
        ("DATA ERROR", ProtocolError),
        ("<DATA ERROR", ProtocolError),
        ("<RX ERROR", ProtocolError),
        ("BUFFER FULL", ProtocolError),
        ("STOPPED", ProtocolError),
        ("?", ProtocolError),
    ],
)
def test_status_messages_become_typed_errors(line: str, error: type[Exception]) -> None:
    with pytest.raises(error) as caught:
        proto.raise_for_status([line], "010C")

    assert line in str(caught.value)
    assert "010C" in str(caught.value)


def test_no_data_is_a_timeout_kind_of_error() -> None:
    assert issubclass(proto.NoDataError, AdapterTimeoutError)


def test_data_lines_are_not_status_messages() -> None:
    proto.raise_for_status(["41 0C 1A F8", "OK", "ELM327 v1.5"])  # does not raise


# --------------------------------------------------------------------------- #
# messages
# --------------------------------------------------------------------------- #

DATASHEET_CAN_VIN = [
    "014",
    "0: 49 02 01 31 44 34",
    "1: 47 50 30 30 52 35 35",
    "2: 42 31 32 33 34 35 36",
]


def test_single_line_replies_with_and_without_spaces() -> None:
    assert proto.parse_messages(["41 0C 1A F8", "410C1AF8"]) == [b"\x41\x0c\x1a\xf8"] * 2


def test_multi_frame_can_reply_is_assembled_and_trimmed_to_the_byte_count() -> None:
    (message,) = proto.parse_messages(DATASHEET_CAN_VIN)

    assert len(message) == 0x14
    assert message[:3] == b"\x49\x02\x01"


def test_interleaved_multi_frame_replies_of_two_ecus_are_refused() -> None:
    """Data sheet, "Multiline Responses (continued)": two ECUs answering 09 04."""
    lines = [
        "013",
        "0: 49 04 01 35 36 30",
        "1: 32 38 39 34 39 41 43",
        "013",
        "0: 49 04 01 35 36 30",
        "2: 00 00 00 00 00 00 31",
        "1: 32 38 39 35 34 41 43",
        "2: 00 00 00 00 00 00 00",
    ]

    # Refused either as an incomplete first group or as out-of-order segments; never
    # silently stitched into one wrong message.
    with pytest.raises(ProtocolError, match="incomplete|out of order"):
        proto.parse_messages(lines)
    with pytest.raises(ProtocolError, match="out of order"):
        proto.parse_messages(["013", "0: 49 04 01 35 36 30", "2: 00 00 00 00 00 00 31"])


@pytest.mark.parametrize(
    "lines",
    [
        ["014", "0: 49 02 01 31 44 34"],  # incomplete
        ["0: 49 02 01"],  # segment without byte count
        ["41 0C 1A F"],  # odd number of digits
        ["41 0C ZZ 00"],  # not hex
    ],
)
def test_malformed_replies_are_refused(lines: list[str]) -> None:
    with pytest.raises(ProtocolError):
        proto.parse_messages(lines)


# --------------------------------------------------------------------------- #
# service 01
# --------------------------------------------------------------------------- #


def test_mode01_data_is_taken_from_the_matching_reply() -> None:
    messages = [b"\x41\x0d\x00", b"\x41\x0c\x1a\xf8"]

    assert proto.parse_mode01_data(messages, 0x0C, 2) == b"\x1a\xf8"


def test_mode01_short_or_missing_reply_is_an_error() -> None:
    with pytest.raises(ProtocolError, match="1 data byte"):
        proto.parse_mode01_data([b"\x41\x0c\x1a"], 0x0C, 2)
    with pytest.raises(ProtocolError, match="no reply"):
        proto.parse_mode01_data([b"\x41\x0d\x00"], 0x0C, 2)


def test_supported_pids_bitmask_from_the_data_sheet() -> None:
    """Data sheet: "41 00 BE 1F B8 10"."""
    supported = proto.parse_supported_pids([bytes.fromhex("4100BE1FB810")], 0x00)

    assert supported == {
        0x01,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x0C,
        0x0D,
        0x0E,
        0x0F,
        0x10,
        0x11,
        0x13,
        0x14,
        0x15,
        0x1C,
    }


def test_supported_pids_of_several_ecus_are_combined() -> None:
    """Data sheet: engine (BE 3E B8 11) and transmission (80 10 80 00) both answer."""
    messages = [bytes.fromhex("4100BE3EB811"), bytes.fromhex("410080108000")]

    supported = proto.parse_supported_pids(messages, 0x00)

    assert 0x0C in supported and 0x20 in supported  # engine
    assert 0x0C in supported and 0x11 in supported  # both


def test_supported_pids_without_a_bitmask_reply_is_an_error() -> None:
    with pytest.raises(ProtocolError):
        proto.parse_supported_pids([bytes.fromhex("410C1AF8")], 0x00)


@pytest.mark.parametrize(
    ("pid", "data", "value"),
    [
        ("0105", "7B", 83.0),  # data sheet
        ("010C", "1AF8", 1726.0),  # data sheet: 6904 / 4
        ("010D", "00", 0.0),
        ("0142", "372A", 14.122),
        ("010E", "00", -64.0),  # signed timing advance
        ("010F", "00", -40.0),  # lowest encodable temperature
        ("0111", "FF", 100.0),
        ("0110", "FFFF", 655.35),
    ],
)
def test_sae_j1979_scaling(pid: str, data: str, value: float) -> None:
    assert decode_pid_value(pid, bytes.fromhex(data)) == pytest.approx(value)


def test_every_catalogued_pid_can_be_decoded_and_checks_its_length() -> None:
    for spec in PID_CATALOG:
        assert spec.formula is not None and spec.data_bytes in (1, 2), spec.pid
        with pytest.raises(ValueError, match="data byte"):
            spec.decode(b"\x00" * (spec.data_bytes + 1))
    with pytest.raises(ValueError, match="not in the PID catalogue"):
        decode_pid_value("01FF", b"\x00")


# --------------------------------------------------------------------------- #
# service 03 and 09
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pair", "code"),
    [("0133", "P0133"), ("D016", "U1016"), ("1131", "P1131"), ("4123", "C0123"), ("8001", "B0001")],
)
def test_trouble_code_bytes_from_the_data_sheet(pair: str, code: str) -> None:
    raw = bytes.fromhex(pair)

    assert proto.decode_dtc_pair(raw[0], raw[1]) == code


def test_stored_codes_on_non_can_protocols_skip_padding() -> None:
    """Data sheet: "43 01 33 00 00 00 00" -> P0133."""
    messages = proto.parse_messages(["43 01 33 00 00 00 00"])

    assert proto.parse_stored_dtcs(messages, can=False) == ("P0133",)


def test_stored_codes_on_can_use_the_count_byte() -> None:
    messages = proto.parse_messages(["43 02 01 33 01 71", "43 00"])

    assert proto.parse_stored_dtcs(messages, can=True) == ("P0133", "P0171")


def test_stored_codes_count_mismatch_or_missing_reply_is_an_error() -> None:
    with pytest.raises(ProtocolError, match="announces 3"):
        proto.parse_stored_dtcs([bytes.fromhex("43030133")], can=True)
    with pytest.raises(ProtocolError, match="no service 03 reply"):
        proto.parse_stored_dtcs([bytes.fromhex("410C1AF8")], can=False)


def test_vin_from_the_data_sheet_on_can_and_j1850() -> None:
    j1850 = [
        "49 02 01 00 00 00 31",
        "49 02 02 44 34 47 50",
        "49 02 03 30 30 52 35",
        "49 02 04 35 42 31 32",
        "49 02 05 33 34 35 36",
    ]

    assert proto.parse_vin(proto.parse_messages(DATASHEET_CAN_VIN)) == "1D4GP00R55B123456"
    assert proto.parse_vin(proto.parse_messages(j1850)) == "1D4GP00R55B123456"


def test_implausible_vin_is_an_error() -> None:
    with pytest.raises(ProtocolError, match="17 alphanumeric"):
        proto.parse_vin([bytes.fromhex("490201") + b"SHORT"])
    with pytest.raises(ProtocolError, match="no reply to 0902"):
        proto.parse_vin([bytes.fromhex("410C1AF8")])


# --------------------------------------------------------------------------- #
# AT DPN
# --------------------------------------------------------------------------- #


def test_protocol_number_with_and_without_auto_prefix() -> None:
    automatic = proto.parse_dpn(["A6"])
    fixed = proto.parse_dpn(["3"])

    assert automatic.automatic is True and automatic.is_can is True
    assert automatic.name == "ISO 15765-4 CAN (11 bit ID, 500 kbaud)"
    assert fixed.automatic is False and fixed.is_can is False
    assert fixed.name == "ISO 9141-2 (5 baud init)"


@pytest.mark.parametrize("reply", ["0", "A0", "AZ", "OK", ""])
def test_unknown_or_inactive_protocol_is_an_error(reply: str) -> None:
    with pytest.raises(ProtocolError):
        proto.parse_dpn([reply])


# --------------------------------------------------------------------------- #
# M3: services 07 / 0A, negative responses, PID 0101, freeze frame 00
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["07", "0A", "0a", "020C00", "020200", "020000", "0101"])
def test_m3_read_only_requests_are_allowed(command: str) -> None:
    assert proto.check_command_allowed(command) == command.upper()


@pytest.mark.parametrize(
    "command",
    ["020C01", "020C", "02", "0700", "0A00", "05", "0600", "04", "0400", "08", "14 FF FF FF"],
)
def test_m3_other_frames_and_services_stay_refused(command: str) -> None:
    with pytest.raises(SafetyGuardError):
        proto.check_command_allowed(command)


@pytest.mark.parametrize(
    ("service", "lines", "can", "codes"),
    [
        (0x07, ["47 01 04 20"], True, ("P0420",)),
        (0x07, ["47 00"], True, ()),  # a real "no pending codes" answer
        (0x0A, ["4A 02 03 01 01 71"], True, ("P0301", "P0171")),
        (0x07, ["47 04 20 00 00 00 00"], False, ("P0420",)),
        (0x0A, ["4A 00 00 00 00 00 00"], False, ()),
        (
            0x07,
            ["00A", "0: 47 04 01 33 01 71", "1: 03 01 04 20 00 00 00"],
            True,
            ("P0133", "P0171", "P0301", "P0420"),
        ),
    ],
)
def test_pending_and_permanent_code_replies(service, lines, can, codes) -> None:
    messages = proto.parse_messages(lines)

    assert proto.parse_dtc_reply(messages, service, can=can) == codes


def test_a_reply_for_another_service_is_not_accepted() -> None:
    with pytest.raises(ProtocolError, match="no service 0A reply"):
        proto.parse_dtc_reply([bytes.fromhex("43020133")], 0x0A, can=True)


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("7F 0A 11", UnsupportedOperationError),
        ("7F 07 12", UnsupportedOperationError),
        ("7F 02 31", proto.NoDataError),
        ("7F 0A 78", ProtocolError),
        ("7F 0A 22", ProtocolError),
    ],
)
def test_negative_responses(line: str, error: type[Exception]) -> None:
    service = int(line.split()[1], 16)
    with pytest.raises(error):
        proto.check_negative_response(proto.parse_messages([line]), service)


def test_a_negative_answer_from_one_ecu_is_ignored_when_another_answers() -> None:
    messages = proto.parse_messages(["7F 07 11", "47 01 04 20"])

    assert proto.parse_dtc_reply(messages, 0x07, can=True) == ("P0420",)


def test_monitor_status_of_one_and_two_ecus() -> None:
    one = proto.parse_monitor_status(proto.parse_messages(["41 01 82 07 65 04"]))
    two = proto.parse_monitor_status(
        proto.parse_messages(["41 01 01 07 65 04", "41 01 81 00 00 00"])
    )

    assert (one.mil_on, one.confirmed_dtc_count, one.ecu_count) == (True, 2, 1)
    assert (two.mil_on, two.confirmed_dtc_count, two.ecu_count) == (True, 2, 2)
    with pytest.raises(ProtocolError, match="0101"):
        proto.parse_monitor_status(proto.parse_messages(["41 0C 1A F8"]))


def test_freeze_frame_bitmask_trigger_and_values() -> None:
    supported = proto.parse_freeze_frame_supported(
        proto.parse_messages(["42 00 00 5E 18 00 00"]), 0x00
    )
    trigger = proto.parse_freeze_frame_trigger(proto.parse_messages(["42 02 00 01 33"]))
    rpm = proto.parse_freeze_frame_data(proto.parse_messages(["42 0C 00 25 A8"]), 0x0C, 2)

    assert supported == {0x02, 0x04, 0x05, 0x06, 0x07, 0x0C, 0x0D}
    assert trigger == "P0133"
    assert rpm == b"\x25\xa8"


def test_no_stored_freeze_frame_is_none_not_a_code() -> None:
    assert proto.parse_freeze_frame_trigger(proto.parse_messages(["42 02 00 00 00"])) is None


@pytest.mark.parametrize(
    ("lines", "match"),
    [
        (["42 0C 01 25 A8"], "frame 01"),  # another frame
        (["42 0C 00 25"], "1 data byte"),  # too short
        (["41 0C 25 A8"], "no reply to 020C00"),  # a service 01 answer
        (["7F 02 12"], "does not support"),
    ],
)
def test_freeze_frame_edge_cases(lines: list[str], match: str) -> None:
    with pytest.raises((ProtocolError, UnsupportedOperationError), match=match):
        proto.parse_freeze_frame_data(proto.parse_messages(lines), 0x0C, 2)


def test_fuel_trim_scaling() -> None:
    assert decode_pid_value("0106", b"\x80") == 0.0
    assert decode_pid_value("0107", b"\x00") == -100.0
    assert decode_pid_value("0106", b"\xff") == pytest.approx(99.22, abs=0.01)
