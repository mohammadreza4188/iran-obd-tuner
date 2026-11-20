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
)

__all__ = [
    "PROMPT",
    "NoDataError",
    "ProtocolInfo",
    "check_command_allowed",
    "clean_reply",
    "decode_dtc_pair",
    "parse_dpn",
    "parse_messages",
    "parse_mode01_data",
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

#: OBD requests this driver may send: service 01 (current data), 03 (stored trouble codes)
#: and 09 (vehicle information). Everything else - in particular service 04 (clear trouble
#: codes), 08 (control of on-board systems) and any manufacturer service - is refused.
_ALLOWED_OBD = re.compile(r"^(01[0-9A-F]{2}|03|09[0-9A-F]{2})$")


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
        "(AT setup, OBD services 01, 03 and 09) are allowed in this build"
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


def parse_stored_dtcs(messages: Sequence[bytes], *, can: bool) -> tuple[str, ...]:
    """Decode a service 03 reply into trouble code strings (duplicates removed).

    On CAN the byte after ``43`` is the number of codes that follow; on the other protocols
    each message carries three byte pairs padded with ``00 00``.

    Raises:
        ProtocolError: If no ``43`` reply is present or a CAN count does not match.
    """
    codes: list[str] = []
    found = False
    for message in messages:
        if not message or message[0] != 0x43:
            continue
        found = True
        if can:
            if len(message) < 2:
                raise ProtocolError(f"truncated service 03 reply: {message.hex(' ')}")
            count = message[1]
            payload = message[2:]
            if len(payload) < count * 2:
                raise ProtocolError(
                    f"service 03 reply announces {count} code(s) but carries "
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
        raise ProtocolError("no service 03 reply (43 ...) in the adapter output")
    return tuple(codes)


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
