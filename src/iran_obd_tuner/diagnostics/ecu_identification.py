"""Read-only UDS ECU identification: standard DIDs and DTCs, each with its own outcome.

:func:`read_ecu_identification` reads each identification DID (``0x22``) independently - an
unsupported or unanswered DID never hides the others - and then the DTCs (``0x19 0x01`` and
``0x19 0x02``). Only a lost connection aborts.

Extended session fallback: when the client was built with the code-level opt-in
(``allow_extended_session=True``), DIDs refused with a session-related NRC (``0x31``,
``0x7E``, ``0x7F``) are read once more inside ``0x10 0x03``, and the client always returns
to ``0x10 0x01`` afterwards. Without the opt-in no session change is ever requested.

The result says whether it came from the simulated ECU (:attr:`EcuIdentification.simulated`,
taken from the link, never guessed from the values). Nothing here claims that a DID, an
identifier pair or a DTC format applies to a particular vehicle.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from iran_obd_tuner.core.exceptions import AdapterTimeoutError, ProtocolError
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import VIN_LENGTH, VehicleInfo, utc_now
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.hardware.uds import (
    NRC_NAMES,
    SESSION_RELATED_NRCS,
    STANDARD_DIDS,
    UdsClient,
    UdsDtcStatus,
    UdsNegativeResponseError,
    UdsNotSupportedError,
    decode_did_text,
    decode_uds_dtc,
)
from iran_obd_tuner.hardware.uds_link import UdsLink, UdsLinkConfig

__all__ = [
    "IDENTIFICATION_DIDS",
    "IDENTIFICATION_SCHEMA_VERSION",
    "DidReading",
    "EcuIdentification",
    "UdsDtc",
    "UdsIdentificationSession",
    "identify_over_link",
    "read_ecu_identification",
]

_LOG = get_logger("diagnostics.ecu_identification")

#: Version of the JSON produced by :meth:`EcuIdentification.as_dict` (separate from the
#: diagnostic report's schema).
IDENTIFICATION_SCHEMA_VERSION = 1

#: DIDs read by default, in display order (ISO 14229-1 Annex C identification DIDs).
IDENTIFICATION_DIDS: tuple[int, ...] = (0xF190, 0xF187, 0xF189, 0xF191, 0xF197)

SESSION_DEFAULT_NAME = "default"
SESSION_EXTENDED_NAME = "extended"

_DID_ERRORS = (UdsNotSupportedError, UdsNegativeResponseError, AdapterTimeoutError, ProtocolError)


def _outcome_of(error: Exception) -> ReadOutcome:
    if isinstance(error, UdsNotSupportedError):
        return ReadOutcome.UNSUPPORTED
    if isinstance(error, AdapterTimeoutError):
        return ReadOutcome.NO_RESPONSE
    return ReadOutcome.ERROR


@dataclass(frozen=True, slots=True)
class DidReading:
    """One ``0x22`` read.

    Attributes:
        did: Data identifier.
        name: Standard name (``vin``, ``ecu_software_version``, ...) or ``did_XXXX``.
        outcome: How the read went.
        raw: Value bytes when ``outcome`` is OK.
        text: Printable ASCII of ``raw`` (padding removed), ``None`` when not text.
        session: Session the value was read in (``default`` / ``extended``).
        nrc: Negative response code, when the ECU answered negatively.
        detail: Error text when the read did not succeed.
    """

    did: int
    name: str
    outcome: ReadOutcome
    raw: bytes | None = None
    text: str | None = None
    session: str | None = None
    nrc: int | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if (self.raw is not None) != (self.outcome is ReadOutcome.OK):
            raise ValueError("raw is present exactly when the outcome is OK")

    @property
    def nrc_name(self) -> str | None:
        return None if self.nrc is None else NRC_NAMES.get(self.nrc, "unknown")

    def as_dict(self) -> dict[str, Any]:
        return {
            "did": f"{self.did:04X}",
            "name": self.name,
            "outcome": str(self.outcome),
            "raw_hex": None if self.raw is None else self.raw.hex(" ").upper(),
            "text": self.text,
            "session": self.session,
            "nrc": None if self.nrc is None else f"{self.nrc:02X}",
            "nrc_name": self.nrc_name,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class UdsDtc:
    """One DTC record of ``0x19 0x02``.

    Attributes:
        raw: The three DTC bytes.
        code: ``P0301-1C`` style text for SAE J2012-DA formats, ``None`` otherwise (the raw
            bytes are then the only honest representation).
        status: The status byte.
    """

    raw: bytes
    code: str | None
    status: UdsDtcStatus

    def as_dict(self) -> dict[str, Any]:
        s = self.status
        return {
            "raw_hex": self.raw.hex().upper(),
            "code": self.code,
            "status": f"{s.raw:02X}",
            "test_failed": s.test_failed,
            "pending": s.pending,
            "confirmed": s.confirmed,
            "warning_indicator": s.warning_indicator,
        }


@dataclass(frozen=True, slots=True)
class EcuIdentification:
    """Everything one read-only identification run produced.

    Attributes:
        interface / channel / request_id / response_id: Where it was read.
        simulated: ``True`` when the simulated ECU answered.
        dids: One reading per requested DID, in request order.
        dtc_outcome: How reading the DTCs went.
        dtcs: DTC records (only when ``dtc_outcome`` is OK).
        dtc_format: DTCFormatIdentifier reported by ``0x19 0x01``.
        dtc_detail: Error text when the DTCs could not be read.
        extended_session_used: Whether ``0x10 0x03`` was entered (and left) during the run.
        extended_session_detail: Why the extended session could not be entered, if so.
        timestamp: When the run finished (aware UTC).
        duration_s: How long it took.
    """

    interface: str
    channel: str | None
    request_id: int
    response_id: int
    simulated: bool
    dids: tuple[DidReading, ...]
    dtc_outcome: ReadOutcome
    dtcs: tuple[UdsDtc, ...] = ()
    dtc_format: int | None = None
    dtc_detail: str | None = None
    extended_session_used: bool = False
    extended_session_detail: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    duration_s: float = 0.0
    read_only: bool = True

    def did(self, did: int) -> DidReading | None:
        return next((r for r in self.dids if r.did == did), None)

    @property
    def vin(self) -> str | None:
        """The VIN from DID F190, when it was read and is a well-formed 17 character VIN."""
        reading = self.did(0xF190)
        if reading is None or reading.text is None:
            return None
        text = reading.text.strip().upper()
        return text if len(text) == VIN_LENGTH and text.isalnum() else None

    def vehicle_info(self) -> VehicleInfo:
        """Vehicle identification with ``vin_source="uds_f190"`` when a VIN was read."""
        vin = self.vin
        return VehicleInfo(
            vin=vin,
            protocol="ISO 15765-2 / UDS (ISO 14229-1)",
            adapter_name=f"can:{self.interface}",
            simulated=self.simulated,
            vin_source="uds_f190" if vin is not None else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "uds_ecu_identification",
            "schema_version": IDENTIFICATION_SCHEMA_VERSION,
            "simulated": self.simulated,
            "read_only": self.read_only,
            "interface": self.interface,
            "channel": self.channel,
            "request_id": f"{self.request_id:03X}",
            "response_id": f"{self.response_id:03X}",
            "timestamp": self.timestamp.isoformat(),
            "duration_s": round(self.duration_s, 3),
            "extended_session_used": self.extended_session_used,
            "extended_session_detail": self.extended_session_detail,
            "vehicle": self.vehicle_info().as_dict(),
            "dids": [reading.as_dict() for reading in self.dids],
            "dtc_outcome": str(self.dtc_outcome),
            "dtc_format": None if self.dtc_format is None else f"{self.dtc_format:02X}",
            "dtc_detail": self.dtc_detail,
            "dtcs": [dtc.as_dict() for dtc in self.dtcs],
        }


def _did_name(did: int) -> str:
    return STANDARD_DIDS.get(did, f"did_{did:04X}")


def _read_did(client: UdsClient, did: int, session: str) -> DidReading:
    try:
        raw = client.read_data_by_identifier(did)
    except _DID_ERRORS as exc:
        nrc = getattr(exc, "nrc", None)
        return DidReading(did, _did_name(did), _outcome_of(exc), nrc=nrc, detail=str(exc))
    return DidReading(
        did, _did_name(did), ReadOutcome.OK, raw=raw, text=decode_did_text(raw), session=session
    )


def read_ecu_identification(
    client: UdsClient,
    *,
    interface: str,
    simulated: bool,
    request_id: int,
    response_id: int,
    channel: str | None = None,
    dids: Sequence[int] = IDENTIFICATION_DIDS,
    dtc_status_mask: int = 0xFF,
) -> EcuIdentification:
    """Read the identification DIDs and the DTCs; see the module docstring.

    Raises:
        AdapterConnectionError: The CAN interface was lost (nothing partial is returned).
    """
    started = time.monotonic()
    readings = {did: _read_did(client, did, SESSION_DEFAULT_NAME) for did in dids}

    retry = [
        did
        for did, reading in readings.items()
        if reading.nrc in SESSION_RELATED_NRCS and client.extended_session_allowed
    ]
    extended_used = False
    extended_detail: str | None = None
    if retry:
        try:
            with client.extended_session():
                extended_used = True
                for did in retry:
                    second = _read_did(client, did, SESSION_EXTENDED_NAME)
                    if second.outcome is ReadOutcome.OK:
                        readings[did] = second
                    else:
                        first = readings[did]
                        readings[did] = replace(
                            first, detail=f"{first.detail}; extended session: {second.detail}"
                        )
        except _DID_ERRORS as exc:  # the ECU refused 0x10 0x03: keep the default results
            extended_detail = str(exc)
            _LOG.info("extended session not available: %s", exc)

    dtc_outcome = ReadOutcome.OK
    dtc_format: int | None = None
    dtc_detail: str | None = None
    dtcs: tuple[UdsDtc, ...] = ()
    try:
        _availability, dtc_format, _count = client.read_dtc_count(dtc_status_mask)
        _mask, records = client.read_dtcs_by_status_mask(dtc_status_mask)
        dtcs = tuple(
            UdsDtc(raw, decode_uds_dtc(raw, dtc_format), status) for raw, status in records
        )
    except _DID_ERRORS as exc:
        dtc_outcome, dtc_detail, dtcs = _outcome_of(exc), str(exc), ()

    return EcuIdentification(
        interface=interface,
        channel=channel,
        request_id=request_id,
        response_id=response_id,
        simulated=simulated,
        dids=tuple(readings[did] for did in dids),
        dtc_outcome=dtc_outcome,
        dtcs=dtcs,
        dtc_format=dtc_format,
        dtc_detail=dtc_detail,
        extended_session_used=extended_used,
        extended_session_detail=extended_detail,
        duration_s=time.monotonic() - started,
    )


def identify_over_link(
    link: UdsLink, *, dids: Sequence[int] = IDENTIFICATION_DIDS
) -> EcuIdentification:
    """Run :func:`read_ecu_identification` on an open :class:`UdsLink`."""
    cfg = link.config
    return read_ecu_identification(
        link.client,
        interface=cfg.interface,
        simulated=link.simulated,
        request_id=cfg.request_id,
        response_id=cfg.response_id,
        channel=link.channel,
        dids=dids,
    )


class UdsIdentificationSession:
    """One-shot UDS work for the dashboard: each call opens the link, works and closes it.

    Nothing stays open between calls, so no CAN interface (or simulated ECU thread) is left
    behind when the window closes. Calls block; the dashboard runs them on its worker.
    """

    def __init__(
        self,
        config: UdsLinkConfig,
        *,
        allow_extended_session: bool = False,
        traffic_log: TrafficLog | None = None,
    ) -> None:
        self._config = config
        self._allow_extended = allow_extended_session
        self._traffic = traffic_log

    @property
    def simulated(self) -> bool:
        return self._config.simulated

    @property
    def interface(self) -> str:
        return self._config.interface

    @property
    def extended_session_allowed(self) -> bool:
        return self._allow_extended

    def _link(self) -> UdsLink:
        return UdsLink(
            self._config, allow_extended_session=self._allow_extended, traffic_log=self._traffic
        )

    def discover(self, window_s: float = 0.3) -> tuple[int, ...]:
        """Response identifiers answering a functional TesterPresent (0x7DF, ``3E 00``)."""
        with self._link() as link:
            return link.discover(window_s)

    def identify(self) -> EcuIdentification:
        with self._link() as link:
            return identify_over_link(link)
