"""Strictly read-only UDS (ISO 14229-1) client over ISO-TP.

Services implemented - all of them reads:

* ``0x10`` DiagnosticSessionControl - **only** ``0x01`` default and ``0x03`` extended
  session (the latter only when the caller enabled it in code, see
  :meth:`UdsClient.extended_session`); programming (``0x02``) and every vendor session are
  refused;
* ``0x3E`` TesterPresent (``0x00``, or ``0x80`` = suppress positive response);
* ``0x22`` ReadDataByIdentifier, one DID per request;
* ``0x19`` ReadDTCInformation, sub-functions ``0x01`` (count + DTC format) and ``0x02``
  (reportDTCByStatusMask);
* :func:`discover_ecus`: one functional ``0x3E 0x00`` on ``0x7DF`` (ISO 15765-4), only
  when the user asks for it.

Safety: every request passes :func:`check_request_allowed` - the single choke point between
this client and the bus - before it is sent. The allowlist is default-deny: any service not
listed in :data:`ALLOWED_REQUESTS` is refused, and the write/security/programming services
in :data:`FORBIDDEN_SERVICES` are named so the refusal is explicit. There is no code path
that could send them.

Negative responses (``7F <SID> <NRC>``): ``0x78`` responsePending waits P2* again, up to
:attr:`UdsTiming.max_pending` answers and :attr:`UdsTiming.pending_ceiling_s` seconds;
``0x21`` busyRepeatRequest is retried twice; "not supported" codes raise
:class:`UdsNotSupportedError` (an ``UnsupportedOperationError``); everything else raises
:class:`UdsNegativeResponseError` (a ``ProtocolError``) carrying the NRC.

Sources: public descriptions of ISO 14229-1 / ISO 15031-6 (the standards are not freely
available); not validated against a physical ECU.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from iran_obd_tuner.core.exceptions import (
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedOperationError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.hardware.can_bus import CanBus
from iran_obd_tuner.hardware.elm327_protocol import decode_dtc_pair
from iran_obd_tuner.hardware.isotp import IsoTpChannel, functional_request

__all__ = [
    "ALLOWED_REQUESTS",
    "FORBIDDEN_SERVICES",
    "NRC_NAMES",
    "STANDARD_DIDS",
    "UdsClient",
    "UdsDtcStatus",
    "UdsNegativeResponseError",
    "UdsNotSupportedError",
    "UdsTiming",
    "check_request_allowed",
    "decode_did_text",
    "decode_uds_dtc",
    "discover_ecus",
    "parse_dtc_records",
]

_LOG = get_logger("hardware.uds")

# --------------------------------------------------------------------------- #
# the read-only allowlist (single choke point)
# --------------------------------------------------------------------------- #

SID_SESSION_CONTROL = 0x10
SID_READ_DTC_INFORMATION = 0x19
SID_READ_DATA_BY_IDENTIFIER = 0x22
SID_TESTER_PRESENT = 0x3E

SESSION_DEFAULT = 0x01
SESSION_EXTENDED = 0x03
_SUPPRESS = 0x80

#: Service -> allowed sub-functions (``None`` = the service has no sub-function byte).
ALLOWED_REQUESTS: Mapping[int, frozenset[int] | None] = {
    SID_SESSION_CONTROL: frozenset(
        {
            SESSION_DEFAULT,
            SESSION_DEFAULT | _SUPPRESS,
            SESSION_EXTENDED,
            SESSION_EXTENDED | _SUPPRESS,
        }
    ),
    SID_TESTER_PRESENT: frozenset({0x00, _SUPPRESS}),
    SID_READ_DATA_BY_IDENTIFIER: None,
    SID_READ_DTC_INFORMATION: frozenset({0x01, 0x02}),
}

#: Services that are refused by name (the allowlist refuses everything else too). These
#: write, erase, reset, unlock, reprogram or take control of the ECU.
FORBIDDEN_SERVICES: Mapping[int, str] = {
    0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x2F: "InputOutputControlByIdentifier",
    0x31: "RoutineControl",
    0x34: "RequestDownload",
    0x35: "RequestUpload",
    0x36: "TransferData",
    0x37: "RequestTransferExit",
    0x3D: "WriteMemoryByAddress",
    0x85: "ControlDTCSetting",
    0x87: "LinkControl",
}


def check_request_allowed(request: bytes) -> None:
    """Refuse ``request`` unless it is a read-only request on the allowlist.

    Raises:
        SafetyGuardError: For any other service or sub-function, before anything is sent.
    """
    if not request:
        raise SafetyGuardError("refusing to send an empty UDS request")
    sid = request[0]
    if sid in FORBIDDEN_SERVICES:
        raise SafetyGuardError(
            f"refusing UDS service {sid:#04x} ({FORBIDDEN_SERVICES[sid]}): it can change ECU "
            "state; only read-only services are implemented in this build"
        )
    if sid not in ALLOWED_REQUESTS:
        raise SafetyGuardError(f"refusing UDS service {sid:#04x}: not on the read-only allowlist")
    allowed_sub = ALLOWED_REQUESTS[sid]
    if allowed_sub is not None:
        if len(request) < 2 or request[1] not in allowed_sub:
            sub = f"{request[1]:#04x}" if len(request) > 1 else "missing"
            raise SafetyGuardError(
                f"refusing UDS service {sid:#04x} with sub-function {sub}: only "
                + ", ".join(f"{s:#04x}" for s in sorted(allowed_sub))
                + " are allowed (no programming or vendor sessions)"
            )
    elif sid == SID_READ_DATA_BY_IDENTIFIER and len(request) != 3:
        raise SafetyGuardError("ReadDataByIdentifier requests carry exactly one DID")


# --------------------------------------------------------------------------- #
# negative responses
# --------------------------------------------------------------------------- #

NRC_NAMES: Mapping[int, str] = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x78: "requestCorrectlyReceived-ResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}
_NOT_SUPPORTED = frozenset({0x11, 0x12, 0x31, 0x33, 0x7E, 0x7F})
#: NRCs after which the same read may succeed in the extended session.
SESSION_RELATED_NRCS = frozenset({0x31, 0x7E, 0x7F})
_RESPONSE_PENDING = 0x78
_BUSY = 0x21


def _describe(sid: int, nrc: int) -> str:
    return f"UDS service {sid:#04x}: negative response {nrc:#04x} ({NRC_NAMES.get(nrc, 'unknown')})"


class UdsNotSupportedError(UnsupportedOperationError):
    """The ECU does not support the request (or not in this session / without unlock)."""

    def __init__(self, sid: int, nrc: int) -> None:
        super().__init__(_describe(sid, nrc))
        self.sid, self.nrc = sid, nrc


class UdsNegativeResponseError(ProtocolError):
    """Any other negative response, e.g. ``0x22`` conditionsNotCorrect."""

    def __init__(self, sid: int, nrc: int) -> None:
        super().__init__(_describe(sid, nrc))
        self.sid, self.nrc = sid, nrc


# --------------------------------------------------------------------------- #
# identification DIDs and DTC records
# --------------------------------------------------------------------------- #

#: Standard identification DIDs (ISO 14229-1, annex C).
STANDARD_DIDS: Mapping[int, str] = {
    0xF190: "vin",
    0xF187: "spare_part_number",
    0xF189: "ecu_software_version",
    0xF191: "ecu_hardware_number",
    0xF197: "system_name",
}


def decode_did_text(data: bytes) -> str | None:
    """Printable ASCII text of a DID value (trailing 00/20/FF padding removed), else ``None``."""
    trimmed = data.rstrip(b"\x00\x20\xff")
    if not trimmed:
        return None
    if all(0x20 <= b < 0x7F for b in trimmed):
        return trimmed.decode("ascii")
    return None


@dataclass(frozen=True, slots=True)
class UdsDtcStatus:
    """The eight status bits of a UDS DTC (ISO 14229-1)."""

    raw: int

    test_failed = property(lambda self: bool(self.raw & 0x01))
    test_failed_this_cycle = property(lambda self: bool(self.raw & 0x02))
    pending = property(lambda self: bool(self.raw & 0x04))
    confirmed = property(lambda self: bool(self.raw & 0x08))
    not_completed_since_clear = property(lambda self: bool(self.raw & 0x10))
    failed_since_clear = property(lambda self: bool(self.raw & 0x20))
    not_completed_this_cycle = property(lambda self: bool(self.raw & 0x40))
    warning_indicator = property(lambda self: bool(self.raw & 0x80))


#: DTCFormatIdentifier values whose first two bytes follow SAE J2012 (P/C/B/U codes).
_J2012_FORMATS = frozenset({0x00, 0x04})


def decode_uds_dtc(raw: bytes, format_id: int | None) -> str | None:
    """``P0301-1C`` for SAE J2012-DA formats (code + failure type byte), else ``None``.

    Other formats (e.g. ISO 14229-1 or J1939) are manufacturer/vehicle specific and are not
    guessed; callers show the raw bytes instead.
    """
    if len(raw) != 3 or format_id not in _J2012_FORMATS:
        return None
    return f"{decode_dtc_pair(raw[0], raw[1])}-{raw[2]:02X}"


def parse_dtc_records(response: bytes) -> tuple[int, list[tuple[bytes, UdsDtcStatus]]]:
    """Parse ``59 02 availabilityMask (DTC3 status)*``.

    Raises:
        ProtocolError: If the length is not a whole number of records.
    """
    if len(response) < 3 or response[:2] != b"\x59\x02" or (len(response) - 3) % 4:
        raise ProtocolError(f"malformed ReadDTCInformation 0x02 response: {response.hex(' ')}")
    records = [
        (response[i : i + 3], UdsDtcStatus(response[i + 3])) for i in range(3, len(response), 4)
    ]
    return response[2], records


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UdsTiming:
    """Client timing.

    Attributes:
        p2_s: Time to wait for a first answer (generous; the ECU's P2 is usually 50 ms).
        p2_star_s: Time to wait after a responsePending answer (updated from the ECU).
        max_pending: responsePending answers accepted for one request.
        pending_ceiling_s: Total time one request may stay pending.
        keepalive_s: In the extended session, send TesterPresent before a request when the
            last request is older than this (the ECU's S3 timer is typically 5 s).
    """

    p2_s: float = 1.0
    p2_star_s: float = 5.0
    max_pending: int = 10
    pending_ceiling_s: float = 30.0
    keepalive_s: float = 2.0


class UdsClient:
    """Read-only UDS client over one physical ISO-TP channel.

    Args:
        channel: ISO-TP channel to the ECU (e.g. 0x7E0 / 0x7E8).
        timing: See :class:`UdsTiming`.
        allow_extended_session: Code-level opt-in for ``0x10 0x03``; never read from a
            configuration file or the environment.
    """

    def __init__(
        self,
        channel: IsoTpChannel,
        *,
        timing: UdsTiming = UdsTiming(),
        allow_extended_session: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._channel = channel
        self._timing = timing
        self._p2_star_s = timing.p2_star_s
        self._allow_extended = allow_extended_session
        self._clock = clock
        self._sleep = sleep
        self._session = SESSION_DEFAULT
        self._last_request = clock()

    @property
    def extended_session_allowed(self) -> bool:
        return self._allow_extended

    @property
    def in_extended_session(self) -> bool:
        return self._session == SESSION_EXTENDED

    # ------------------------------------------------------------------ transport
    def _request(self, payload: bytes, *, expect_response: bool = True) -> bytes | None:
        check_request_allowed(payload)
        if (
            self.in_extended_session
            and payload[0] != SID_TESTER_PRESENT
            and self._clock() - self._last_request > self._timing.keepalive_s
        ):
            self._request(bytes([SID_TESTER_PRESENT, _SUPPRESS]), expect_response=False)
        sid = payload[0]
        busy_retries = 0
        while True:
            self._channel.send(payload)
            self._last_request = self._clock()
            if not expect_response:
                return None
            started = self._clock()
            deadline = started + self._timing.p2_s
            pending = 0
            while True:
                remaining = deadline - self._clock()
                response = self._channel.recv(remaining) if remaining > 0 else None
                if response is None:
                    raise AdapterTimeoutError(
                        f"UDS service {sid:#04x}: no answer within "
                        f"{self._timing.p2_s if not pending else self._p2_star_s:.1f}s"
                    )
                if response[0] == sid + 0x40:
                    return response
                if response[0] != 0x7F or len(response) < 3 or response[1] != sid:
                    continue  # an answer to something else: ignore
                nrc = response[2]
                if nrc == _RESPONSE_PENDING:
                    pending += 1
                    if (
                        pending > self._timing.max_pending
                        or self._clock() - started > self._timing.pending_ceiling_s
                    ):
                        raise AdapterTimeoutError(
                            f"UDS service {sid:#04x}: still pending after {pending} "
                            "responsePending answers; giving up"
                        )
                    deadline = self._clock() + self._p2_star_s
                    continue
                if nrc == _BUSY and busy_retries < 2:
                    busy_retries += 1
                    self._sleep(0.2)
                    break  # resend
                if nrc in _NOT_SUPPORTED:
                    raise UdsNotSupportedError(sid, nrc)
                raise UdsNegativeResponseError(sid, nrc)

    # ------------------------------------------------------------------ services
    def diagnostic_session_control(self, session: int) -> None:
        """Switch to the default (``0x01``) or - when allowed - extended (``0x03``) session.

        Raises:
            SafetyGuardError: For any other session, or ``0x03`` without the code opt-in.
        """
        if session == SESSION_EXTENDED and not self._allow_extended:
            raise SafetyGuardError("the extended diagnostic session is not enabled for this client")
        response = self._request(bytes([SID_SESSION_CONTROL, session]))
        assert response is not None
        if len(response) < 2 or response[1] != session:
            raise ProtocolError(f"unexpected session control answer: {response.hex(' ')}")
        if len(response) >= 6:  # P2 (ms) and P2* (10 ms units) of the ECU
            server_p2_star = int.from_bytes(response[4:6], "big") * 0.01
            self._p2_star_s = max(self._timing.p2_star_s, server_p2_star)
        self._session = session

    def tester_present(self, *, suppress_response: bool = True) -> None:
        sub = _SUPPRESS if suppress_response else 0x00
        response = self._request(
            bytes([SID_TESTER_PRESENT, sub]), expect_response=not suppress_response
        )
        if response is not None and response[:2] != b"\x7e\x00":
            raise ProtocolError(f"unexpected TesterPresent answer: {response.hex(' ')}")

    def read_data_by_identifier(self, did: int) -> bytes:
        """Value of one DID.

        Raises:
            UdsNotSupportedError: Unknown, protected or session-restricted DID.
            UdsNegativeResponseError: e.g. conditionsNotCorrect.
            ProtocolError: If the answer echoes another DID.
        """
        if not 0 <= did <= 0xFFFF:
            raise ValueError("a DID is 16 bits")
        response = self._request(bytes([SID_READ_DATA_BY_IDENTIFIER, did >> 8, did & 0xFF]))
        assert response is not None
        if len(response) < 3 or int.from_bytes(response[1:3], "big") != did:
            raise ProtocolError(f"answer to DID {did:#06x} is for another DID: {response.hex(' ')}")
        return response[3:]

    def read_dtc_count(self, status_mask: int = 0xFF) -> tuple[int, int, int]:
        """``(availability_mask, dtc_format_id, count)`` from sub-function ``0x01``."""
        response = self._request(bytes([SID_READ_DTC_INFORMATION, 0x01, status_mask]))
        assert response is not None
        if len(response) < 6 or response[1] != 0x01:
            raise ProtocolError(f"malformed ReadDTCInformation 0x01 answer: {response.hex(' ')}")
        return response[2], response[3], int.from_bytes(response[4:6], "big")

    def read_dtcs_by_status_mask(
        self, status_mask: int = 0xFF
    ) -> tuple[int, list[tuple[bytes, UdsDtcStatus]]]:
        """``(availability_mask, [(dtc_3_bytes, status), ...])`` from sub-function ``0x02``."""
        response = self._request(bytes([SID_READ_DTC_INFORMATION, 0x02, status_mask]))
        assert response is not None
        return parse_dtc_records(response)

    @contextmanager
    def extended_session(self) -> Iterator[None]:
        """Run the block in the extended session and **always** return to the default one.

        Raises:
            SafetyGuardError: If the extended session was not enabled for this client.
        """
        self.diagnostic_session_control(SESSION_EXTENDED)
        try:
            yield
        finally:
            try:
                self.diagnostic_session_control(SESSION_DEFAULT)
            except Exception as exc:  # report; the ECU falls back by itself after S3
                _LOG.warning("could not return to the default session: %s", exc)
                self._session = SESSION_DEFAULT


#: ISO 15765-4 11-bit functional request identifier and physical response identifiers.
FUNCTIONAL_REQUEST_ID = 0x7DF
OBD_RESPONSE_IDS = range(0x7E8, 0x7F0)


def discover_ecus(
    bus: CanBus,
    *,
    functional_id: int = FUNCTIONAL_REQUEST_ID,
    response_ids: range | tuple[int, ...] = OBD_RESPONSE_IDS,
    window_s: float = 0.3,
    padding: int | None = 0xCC,
    clock: Callable[[], float] = time.monotonic,
    traffic_log: TrafficLog | None = None,
    adapter_name: str = "can",
) -> tuple[int, ...]:
    """Response identifiers that answered one functional TesterPresent (``3E 00``).

    The request passes :func:`check_request_allowed` like every other request. Only
    positive answers (``7E 00``) count; the result is sorted.
    """
    request = bytes([SID_TESTER_PRESENT, 0x00])
    check_request_allowed(request)
    answers = functional_request(
        bus,
        functional_id,
        request,
        response_ids,
        window_s,
        padding=padding,
        clock=clock,
        traffic_log=traffic_log,
        adapter_name=adapter_name,
    )
    positive = bytes([SID_TESTER_PRESENT + 0x40, 0x00])
    return tuple(sorted(rid for rid, payload in answers.items() if payload[:2] == positive))
