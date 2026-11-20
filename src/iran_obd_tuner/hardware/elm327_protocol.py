"""Pure parsing of ELM327 replies (no I/O), and the read-only command whitelist.

Sources: the ELM327 data sheet (Elm Electronics, ELM327DSJ): command set, protocol numbers
(Figure 2), "Error Messages and Alerts", "Multiline Responses" and the mode 03 example; the
SAE J1979 conventions it quotes for service 01/03/09 replies.

Every function here works on text the adapter already returned, so all of it is unit
tested with the data sheet's own examples. Assumptions this module relies on, set by
:class:`~iran_obd_tuner.hardware.elm327.Elm327Adapter` during initialisation:

* echo off (``AT E0``), linefeeds off (``AT L0``), headers off (``AT H0``);
* CAN auto formatting left at its default (on), so multi-frame CAN replies show a byte count
  line followed by ``0:``, ``1:`` ... segments.

Replies with or without spaces between bytes are both accepted (``AT S0`` only exists from
v1.3 and is not sent).

Known limitation: with headers off, replies from several ECUs cannot be told apart. Single
values take the first matching reply; supported-PID bitmasks are combined (a PID counts as
supported if any ECU reports it); mixed multi-frame replies are refused as ambiguous.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedOperationError,
)
from iran_obd_tuner.core.monitors import (
    MonitorStatus,
    combine_monitor_status,
    decode_monitor_status,
)

__all__ = [
    "PROMPT",
    "NoDataError",
    "ProtocolInfo",
    "check_command_allowed",
    "check_negative_response",
    "clean_reply",
    "decode_dtc_pair",
    "parse_dpn",
    "parse_dtc_reply",
    "parse_freeze_frame_data",
    "parse_freeze_frame_supported",
    "parse_freeze_frame_trigger",
    "parse_messages",
    "parse_mode01_data",
    "parse_monitor_status",
    "parse_stored_dtcs",
    "parse_supported_pids",
    "parse_vin",
    "raise_for_status",
]

#: The ELM327 prints this character when it is ready for the next command.
PROMPT = b">"

# --------------------------------------------------------------------------- #
# read-only command whitelist (defence in depth: checked before anything is sent)
# --------------------------------------------------------------------------- #

#: AT commands this driver may send. All of them configure the adapter for the current
#: session or read information; none of them writes to the vehicle. ``AT SP`` is
#: deliberately absent (it writes the adapter's EEPROM); ``AT TP 0`` is used instead.
ALLOWED_AT_COMMANDS: frozenset[str] = frozenset(
    {"ATZ", "ATE0", "ATL0", "ATH0", "ATTP0", "ATDPN", "ATDP", "ATI", "ATRV"}
)

#: OBD requests this driver may send, all of them reads:
#:
#: * ``01xx`` current data, ``09xx`` vehicle information;
#: * ``02xx00`` freeze frame data - **frame 00 only**;
#: * ``03`` stored, ``07`` pending and ``0A`` permanent trouble codes.
#:
#: Everything else is refused - in particular service 04 (clear trouble codes and freeze
#: frames), 05/06 (test results, not needed), 08 (control of on-board systems) and every
#: UDS or manufacturer service.
_ALLOWED_OBD = re.compile(r"^(01[0-9A-F]{2}|02[0-9A-F]{2}00|03|07|0A|09[0-9A-F]{2})$")


def check_command_allowed(command: str) -> str:
    """Return the normalised command, or refuse it before it can be sent.

    Raises:
        SafetyGuardError: If ``command`` is not on the read-only whitelist.
    """
    normalized = command.replace(" ", "").upper()
    if normalized in ALLOWED_AT_COMMANDS or _ALLOWED_OBD.match(normalized):
        return normalized
    raise SafetyGuardError(
        f"refusing to send {command!r} to the adapter: only read-only commands "
        "(AT setup, OBD services 01, 02 frame 00, 03, 07, 0A and 09) are allowed in this build"
    )


# --------------------------------------------------------------------------- #
# reply cleaning and status messages
# --------------------------------------------------------------------------- #


class NoDataError(AdapterTimeoutError):
    """The adapter waited and the vehicle did not answer (``NO DATA``)."""


#: Progress prefixes that carry no data and no error (data sheet: printed during a search
#: or a slow ISO 9141/14230 bus initiation). Whatever follows them is kept.
_PROGRESS = re.compile(r"^(SEARCHING\.*|BUS INIT:\s*[.\s]*OK)\s*")

#: Exact status messages and the error each one becomes.
_STATUS_ERRORS: tuple[tuple[re.Pattern[str], type[Exception], str], ...] = (
    (re.compile(r"^NO DATA$"), NoDataError, "the vehicle did not answer (NO DATA)"),
    (
        re.compile(r"^UNABLE TO CONNECT$"),
        AdapterConnectionError,
        "no OBD protocol answered (UNABLE TO CONNECT) - is the ignition on?",
    ),
    (
        re.compile(r"^BUS INIT:.*ERROR$"),
        AdapterConnectionError,
        "bus initialisation failed (BUS INIT ... ERROR)",
    ),
    (re.compile(r"^CAN ERROR$"), AdapterConnectionError, "CAN communication failed"),
    (re.compile(r"^BUS ERROR$"), AdapterConnectionError, "invalid signal on the bus"),
    (re.compile(r"^BUS BUSY$"), AdapterConnectionError, "the bus is too busy to send"),
    (re.compile(r"^FB ERROR$"), AdapterConnectionError, "OBD output feedback error"),
    (re.compile(r"^LV RESET$"), AdapterConnectionError, "the adapter reset (low voltage)"),
    (
        re.compile(r"^!?(ACT|LP) ALERT$"),
        AdapterConnectionError,
        "the adapter is switching to low power",
    ),
    (re.compile(r"^ERR[0-9A-F]{2}$"), AdapterConnectionError, "internal adapter error"),
    (re.compile(r"^<?(DATA|RX) ERROR$"), ProtocolError, "corrupted reply"),
    (re.compile(r"^BUFFER FULL$"), ProtocolError, "the adapter's buffer overflowed"),
    (re.compile(r"^STOPPED$"), ProtocolError, "the request was interrupted (STOPPED)"),
    (re.compile(r"^\?$"), ProtocolError, "the adapter did not understand the command"),
)


def clean_reply(raw: bytes, command: str = "") -> list[str]:
    """Split a raw reply into meaningful lines.

    Removes the prompt, empty lines, an echoed command (if echo could not be disabled)
    and progress messages such as ``SEARCHING...``.

    Raises:
        ProtocolError: If the reply contains non-ASCII bytes.
    """
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"non-ASCII bytes in adapter reply: {raw!r}") from exc
    echo = command.replace(" ", "").upper()
    lines: list[str] = []
    for part in re.split(r"[\r\n]+", text.replace(">", "")):
        line = part.strip()
        progress = _PROGRESS.match(line.upper())
        if progress:
            line = line[progress.end() :].strip()
        if not line:
            continue
        if echo and line.replace(" ", "").upper() == echo:
            continue
        lines.append(line)
    return lines


def raise_for_status(lines: Sequence[str], command: str = "") -> None:
    """Raise the matching error if the reply is a status message instead of data."""
    for line in lines:
        upper = line.upper()
        for pattern, error, meaning in _STATUS_ERRORS:
            if pattern.match(upper):
                suffix = f" for {command}" if command else ""
                raise error(f"ELM327: {meaning}{suffix} [{line}]")


# --------------------------------------------------------------------------- #
# hex messages (single line, multi-line, multi-frame CAN)
# --------------------------------------------------------------------------- #

_COUNT_LINE = re.compile(r"^[0-9A-F]{3}$")
_SEGMENT_LINE = re.compile(r"^([0-9A-F]):\s*(.*)$")


def _hex_bytes(text: str) -> bytes:
    compact = text.replace(" ", "")
    if not compact or len(compact) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", compact):
        raise ProtocolError(f"malformed hex data in adapter reply: {text!r}")
    return bytes.fromhex(compact)


def parse_messages(lines: Iterable[str]) -> list[bytes]:
    """Turn reply lines into complete messages.

    A plain hex line is one message. A CAN multi-frame reply (a 3-digit byte count line,
    then ``0:``, ``1:`` ... segments) becomes one message trimmed to the byte count.

    Raises:
        ProtocolError: On malformed hex, missing or out-of-order segments, a segment number
            that repeats (two ECUs interleaved), or fewer bytes than announced.
    """
    messages: list[bytes] = []
    expected: int | None = None
    segments: list[bytes] = []

    def finish() -> None:
        nonlocal expected, segments
        if expected is None:
            return
        data = b"".join(segments)
        if len(data) < expected:
            raise ProtocolError(f"incomplete multi-frame reply: {len(data)} of {expected} byte(s)")
        messages.append(data[:expected])
        expected, segments = None, []

    for raw_line in lines:
        line = raw_line.strip().upper()
        if _COUNT_LINE.match(line):
            finish()
            expected = int(line, 16)
            continue
        segment = _SEGMENT_LINE.match(line)
        if segment:
            if expected is None:
                raise ProtocolError(f"multi-frame segment without a byte count: {line!r}")
            number = int(segment.group(1), 16)
            if number != len(segments) % 16:
                raise ProtocolError(
                    f"multi-frame segment {number} out of order (expected "
                    f"{len(segments) % 16}); several ECUs may be answering at once"
                )
            segments.append(_hex_bytes(segment.group(2)))
            continue
        finish()
        messages.append(_hex_bytes(line))
    finish()
    return messages


# --------------------------------------------------------------------------- #
# service 01, 03 and 09 replies
# --------------------------------------------------------------------------- #


def parse_mode01_data(messages: Sequence[bytes], pid: int, data_bytes: int) -> bytes:
    """Return the data bytes of the first ``41 <pid>`` reply.

    Raises:
        ProtocolError: If no reply matches, or the matching reply is too short.
    """
    for message in messages:
        if len(message) >= 2 and message[0] == 0x41 and message[1] == pid:
            data = message[2:]
            if len(data) < data_bytes:
                raise ProtocolError(
                    f"reply to 01{pid:02X} has {len(data)} data byte(s), "
                    f"{data_bytes} expected: {message.hex(' ').upper()}"
                )
            return data[:data_bytes]
    raise ProtocolError(f"no reply to 01{pid:02X} among {len(messages)} message(s)")


def parse_supported_pids(messages: Sequence[bytes], base: int) -> set[int]:
    """Decode a supported-PIDs bitmask reply (``41 00``, ``41 20``, ``41 40`` ...).

    Bit 7 of the first byte is PID ``base + 1``; the last bit is ``base + 0x20``, which
    announces whether the next bitmask may be requested. Replies of all ECUs are combined.

    Raises:
        ProtocolError: If no ECU sent a well-formed bitmask.
    """
    supported: set[int] = set()
    found = False
    for message in messages:
        if len(message) >= 6 and message[0] == 0x41 and message[1] == base:
            found = True
            mask = int.from_bytes(message[2:6], "big")
            for bit in range(32):
                if mask & (1 << (31 - bit)):
                    supported.add(base + bit + 1)
    if not found:
        raise ProtocolError(f"no supported-PIDs reply for 01{base:02X}")
    return supported


_DTC_SYSTEMS = "PCBU"


def decode_dtc_pair(high: int, low: int) -> str:
    """Decode two DTC bytes into a code, e.g. ``0x01 0x33`` -> ``"P0133"``.

    The top two bits select the system (P, C, B, U), the next two the first digit; the
    remaining twelve bits are three hex digits (data sheet, "Reading Trouble Codes").
    """
    system = _DTC_SYSTEMS[(high >> 6) & 0x03]
    return f"{system}{(high >> 4) & 0x03}{high & 0x0F:X}{low:02X}"


#: Negative response codes (ISO 14229 / ISO 15765 diagnostics) that mean "not supported".
_NRC_NOT_SUPPORTED = frozenset({0x11, 0x12})
#: requestOutOfRange: the ECU has no data for this request.
_NRC_OUT_OF_RANGE = 0x31
#: responsePending: the ECU needs more time; newer ELM327 versions wait for it themselves.
_NRC_RESPONSE_PENDING = 0x78


def check_negative_response(messages: Sequence[bytes], service: int) -> None:
    """Raise if the only answer to ``service`` is a negative response ``7F <svc> <NRC>``.

    A negative response from one ECU is ignored when another ECU answered positively.

    Raises:
        UnsupportedOperationError: NRC ``11``/``12`` (service or sub-function not supported).
        NoDataError: NRC ``31`` (request out of range: no such data).
        ProtocolError: NRC ``78`` that reached us (the adapter did not wait) or any other.
    """
    positive = service + 0x40
    if any(message and message[0] == positive for message in messages):
        return
    for message in messages:
        if len(message) >= 3 and message[0] == 0x7F and message[1] == service:
            nrc = message[2]
            where = f"service {service:02X}"
            if nrc in _NRC_NOT_SUPPORTED:
                raise UnsupportedOperationError(
                    f"the vehicle does not support {where} (negative response {nrc:02X})"
                )
            if nrc == _NRC_OUT_OF_RANGE:
                raise NoDataError(f"the vehicle has no data for {where} (request out of range)")
            if nrc == _NRC_RESPONSE_PENDING:
                raise ProtocolError(
                    f"{where}: the ECU asked for more time (response pending) and the adapter "
                    "did not wait for the answer"
                )
            raise ProtocolError(f"{where}: negative response {nrc:02X}")


def parse_dtc_reply(messages: Sequence[bytes], service: int, *, can: bool) -> tuple[str, ...]:
    """Decode a service 03, 07 or 0A reply into trouble code strings (duplicates removed).

    The positive reply byte is ``service + 0x40`` (``43``, ``47``, ``4A``). On CAN the next
    byte is the number of codes that follow (``47 00`` is a valid "no codes" answer); on the
    other protocols each message carries three byte pairs padded with ``00 00``.

    Raises:
        UnsupportedOperationError / NoDataError / ProtocolError: See
            :func:`check_negative_response`.
        ProtocolError: If no positive reply is present or a CAN count does not match.
    """
    check_negative_response(messages, service)
    positive = service + 0x40
    label = f"service {service:02X}"
    codes: list[str] = []
    found = False
    for message in messages:
        if not message or message[0] != positive:
            continue
        found = True
        if can:
            if len(message) < 2:
                raise ProtocolError(f"truncated {label} reply: {message.hex(' ')}")
            count = message[1]
            payload = message[2:]
            if len(payload) < count * 2:
                raise ProtocolError(
                    f"{label} reply announces {count} code(s) but carries "
                    f"{len(payload) // 2}: {message.hex(' ').upper()}"
                )
            pairs = [payload[i : i + 2] for i in range(0, count * 2, 2)]
        else:
            payload = message[1:]
            pairs = [payload[i : i + 2] for i in range(0, len(payload) - 1, 2)]
            pairs = [pair for pair in pairs if pair != b"\x00\x00"]
        for pair in pairs:
            code = decode_dtc_pair(pair[0], pair[1])
            if code not in codes:
                codes.append(code)
    if not found:
        raise ProtocolError(f"no {label} reply ({positive:02X} ...) in the adapter output")
    return tuple(codes)


def parse_stored_dtcs(messages: Sequence[bytes], *, can: bool) -> tuple[str, ...]:
    """Decode a service 03 reply (kept for callers of the M2 API)."""
    return parse_dtc_reply(messages, 0x03, can=can)


# --------------------------------------------------------------------------- #
# PID 0101 and service 02 (freeze frame 00)
# --------------------------------------------------------------------------- #


def parse_monitor_status(messages: Sequence[bytes]) -> MonitorStatus:
    """Decode ``41 01 A B C D`` replies; several ECUs are combined (MIL any, count sum).

    Raises:
        ProtocolError: If no well-formed ``41 01`` reply is present.
    """
    statuses = [
        decode_monitor_status(message[2:6])
        for message in messages
        if len(message) >= 6 and message[0] == 0x41 and message[1] == 0x01
    ]
    if not statuses:
        raise ProtocolError("no reply to 0101 (monitor status) in the adapter output")
    return combine_monitor_status(statuses)


def _freeze_frame_replies(messages: Sequence[bytes], pid: int) -> list[bytes]:
    check_negative_response(messages, 0x02)
    replies = [m for m in messages if len(m) >= 3 and m[0] == 0x42 and m[1] == pid]
    if not replies:
        raise ProtocolError(f"no reply to 02{pid:02X}00 among {len(messages)} message(s)")
    for reply in replies:
        if reply[2] != 0x00:
            raise ProtocolError(
                f"reply to 02{pid:02X}00 is for frame {reply[2]:02X}, not frame 00: "
                f"{reply.hex(' ').upper()}"
            )
    return replies


def parse_freeze_frame_supported(messages: Sequence[bytes], base: int) -> set[int]:
    """Decode a freeze-frame supported-PIDs bitmask (``42 00 00 b1 b2 b3 b4``)."""
    supported: set[int] = set()
    for reply in _freeze_frame_replies(messages, base):
        if len(reply) < 7:
            raise ProtocolError(f"truncated freeze frame bitmask: {reply.hex(' ').upper()}")
        mask = int.from_bytes(reply[3:7], "big")
        for bit in range(32):
            if mask & (1 << (31 - bit)):
                supported.add(base + bit + 1)
    return supported


def parse_freeze_frame_trigger(messages: Sequence[bytes]) -> str | None:
    """Decode ``42 02 00 HH LL``: the DTC that stored freeze frame 00.

    Returns:
        The trouble code, or ``None`` for ``00 00`` - no freeze frame is stored, and every
        other service 02 value is then meaningless (SAE J1979).
    """
    reply = _freeze_frame_replies(messages, 0x02)[0]
    if len(reply) < 5:
        raise ProtocolError(f"truncated freeze frame trigger reply: {reply.hex(' ').upper()}")
    if reply[3] == 0 and reply[4] == 0:
        return None
    return decode_dtc_pair(reply[3], reply[4])


def parse_freeze_frame_data(messages: Sequence[bytes], pid: int, data_bytes: int) -> bytes:
    """Return the data bytes of the first ``42 <pid> 00 <data>`` reply.

    Raises:
        ProtocolError: If no reply matches, the frame is not 00, or the data is too short.
    """
    reply = _freeze_frame_replies(messages, pid)[0]
    data = reply[3:]
    if len(data) < data_bytes:
        raise ProtocolError(
            f"reply to 02{pid:02X}00 has {len(data)} data byte(s), {data_bytes} expected: "
            f"{reply.hex(' ').upper()}"
        )
    return data[:data_bytes]


def parse_vin(messages: Sequence[bytes]) -> str:
    """Assemble the VIN from a ``09 02`` reply.

    CAN: one multi-frame message ``49 02 01`` + 17 ASCII bytes. Other protocols: several
    ``49 02 <n>`` messages with four bytes each, leading ``00`` filler bytes ignored.

    Raises:
        ProtocolError: If no reply is present or the result is not 17 printable characters.
    """
    replies = [m for m in messages if len(m) >= 3 and m[0] == 0x49 and m[1] == 0x02]
    if not replies:
        raise ProtocolError("no reply to 0902 (VIN) in the adapter output")
    if len(replies) == 1:
        data = replies[0][3:]
    else:
        ordered = sorted(replies, key=lambda m: m[2])
        data = b"".join(m[3:] for m in ordered)
    vin_bytes = data.lstrip(b"\x00")
    try:
        vin = vin_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"VIN reply is not ASCII: {data.hex(' ')}") from exc
    if len(vin) != 17 or not vin.isalnum():
        raise ProtocolError(f"VIN reply does not contain 17 alphanumeric characters: {vin!r}")
    return vin


# --------------------------------------------------------------------------- #
# protocol description (AT DPN)
# --------------------------------------------------------------------------- #

#: Data sheet, Figure 2 "ELM327 Protocol Numbers".
PROTOCOL_NAMES: dict[str, str] = {
    "1": "SAE J1850 PWM (41.6 kbaud)",
    "2": "SAE J1850 VPW (10.4 kbaud)",
    "3": "ISO 9141-2 (5 baud init)",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
    "6": "ISO 15765-4 CAN (11 bit ID, 500 kbaud)",
    "7": "ISO 15765-4 CAN (29 bit ID, 500 kbaud)",
    "8": "ISO 15765-4 CAN (11 bit ID, 250 kbaud)",
    "9": "ISO 15765-4 CAN (29 bit ID, 250 kbaud)",
    "A": "SAE J1939 CAN (29 bit ID, 250 kbaud)",
    "B": "User1 CAN (11 bit ID, 125 kbaud)",
    "C": "User2 CAN (11 bit ID, 50 kbaud)",
}

_CAN_PROTOCOLS = frozenset("6789ABC")


@dataclass(frozen=True, slots=True)
class ProtocolInfo:
    """The protocol the adapter is using.

    Attributes:
        number: Protocol number from Figure 2 (``"1"`` ... ``"C"``).
        name: Human readable name.
        automatic: ``True`` when it was found by the automatic search (``A`` prefix).
    """

    number: str
    name: str
    automatic: bool

    @property
    def is_can(self) -> bool:
        """Whether replies use the CAN (ISO 15765-4) formatting."""
        return self.number in _CAN_PROTOCOLS


def parse_dpn(lines: Sequence[str]) -> ProtocolInfo:
    """Decode the reply to ``AT DPN``, e.g. ``"A6"`` or ``"3"``.

    Raises:
        ProtocolError: If the reply is not a known protocol number. ``0`` (automatic, no
            protocol found yet) is refused too: it means no vehicle protocol is active.
    """
    text = "".join(lines).strip().upper()
    automatic = text.startswith("A") and len(text) == 2
    number = text[1:] if automatic else text
    if number not in PROTOCOL_NAMES:
        raise ProtocolError(f"unexpected reply to AT DPN: {text!r}")
    return ProtocolInfo(number=number, name=PROTOCOL_NAMES[number], automatic=automatic)
