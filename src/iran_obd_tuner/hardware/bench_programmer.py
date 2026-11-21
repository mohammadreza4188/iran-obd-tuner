"""Bench ECU programmer (Milestone 7c): the only module that composes programming requests.

**In this build it can only talk to the virtual programmable ECU.** The constructor refuses
a non-simulated bus or target, :meth:`BenchProgrammer.unlock` refuses a non-simulated
seed-key provider, and the only bench target that can exist is simulated
(:data:`~iran_obd_tuner.core.bench.SIMULATED_BENCH_TARGET`). It is created only by
:mod:`iran_obd_tuner.hardware.bench_link` (architecture test).

Safety design:

* **One choke point** - every request passes :func:`check_bench_request` (default-deny
  bench allowlist, :data:`BENCH_ALLOWED_REQUESTS`) inside ``_transmit``, the only place that
  sends. ``0x14`` ClearDiagnosticInformation, ``0x2E`` WriteDataByIdentifier, ``0x2F``
  InputOutputControlByIdentifier, ``0x3D`` WriteMemoryByAddress, ``0x28``
  CommunicationControl, ``0x85`` ControlDTCSetting and ``0x87`` LinkControl are refused by
  name (:data:`BENCH_FORBIDDEN_SERVICES`); so is everything not listed. RoutineControl is
  limited to the target's erase and check routines.
* **Capabilities first** - every method that changes the ECU checks its
  :class:`~iran_obd_tuner.core.bench.FlashAuthorization` (and, for erase and download, the
  :class:`~iran_obd_tuner.core.bench.BackupReceipt`) as its very first statement, before any
  frame (architecture test). The first authorisation used on a link is pinned; another one
  is refused. Downloaded data must hash to the authorised image. Erasing or writing needs an
  identity read on this link: an application identity must be the one the backup was taken
  from; a bootloader identity (an interrupted flash) is accepted, and binding the backup to
  that ECU is then the orchestrator's job (it holds the journal).
* **Bounded everything** - P2* is capped at :data:`BENCH_P2_STAR_CEILING_S`; no wait outlives
  the operation's ceiling (``max_erase_s`` for an erase); a request that goes unanswered on a
  link that never delivered a frame raises
  :class:`~iran_obd_tuner.hardware.uds.UdsNoTrafficError`. Only TransferData blocks are
  retried - at most :attr:`BenchTiming.max_block_retries` per block and
  :attr:`BenchTiming.max_transfer_retries` per transfer, only after a timeout or ``0x21``
  busyRepeatRequest - and ``0x70`` - ``0x73`` abort at once. SecurityAccess is tried once.
* **Keepalive** - outside the default session, ``3E 80`` precedes a request when the link was
  idle longer than :attr:`BenchTiming.keepalive_s` (no background thread).
* **Honest, small traffic log** - lines are recorded per request, not per frame: TransferData
  as a header only (``36 07 [256 data bytes]``), keys redacted.

``hardware/uds.py`` (the read-only client) is not used and not changed: this module has its
own request loop (decision D5 of Milestone 7).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from iran_obd_tuner.core.bench import (
    BackupReceipt,
    BenchTargetProfile,
    EcuIdentity,
    FlashAuthorization,
    FlashProgress,
    FlashStage,
    MemoryRegion,
    ProgressSink,
    SeedKeyProvider,
)
from iran_obd_tuner.core.exceptions import (
    AdapterTimeoutError,
    BenchTargetNotQualifiedError,
    ProtocolError,
    SafetyGuardError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.can_bus import CanBus
from iran_obd_tuner.hardware.isotp import MAX_PAYLOAD, IsoTpChannel, IsoTpConfig
from iran_obd_tuner.hardware.uds import UdsNoTrafficError

__all__ = [
    "BENCH_ALLOWED_REQUESTS",
    "BENCH_FORBIDDEN_SERVICES",
    "BENCH_P2_STAR_CEILING_S",
    "BenchNegativeResponseError",
    "BenchProgrammer",
    "BenchTiming",
    "BenchTransferError",
    "check_bench_request",
]

_LOG = get_logger("hardware.bench_programmer")

# --------------------------------------------------------------------------- #
# the bench allowlist (single choke point, default deny)
# --------------------------------------------------------------------------- #

SID_SESSION_CONTROL: Final = 0x10
SID_ECU_RESET: Final = 0x11
SID_READ_DATA_BY_IDENTIFIER: Final = 0x22
SID_SECURITY_ACCESS: Final = 0x27
SID_ROUTINE_CONTROL: Final = 0x31
SID_REQUEST_DOWNLOAD: Final = 0x34
SID_REQUEST_UPLOAD: Final = 0x35
SID_TRANSFER_DATA: Final = 0x36
SID_REQUEST_TRANSFER_EXIT: Final = 0x37
SID_TESTER_PRESENT: Final = 0x3E

_SESSION_DEFAULT, _SESSION_PROGRAMMING, _SESSION_EXTENDED = 0x01, 0x02, 0x03
_SUPPRESS = 0x80
_START_ROUTINE = 0x01
_ALFID = 0x44

#: Service -> allowed first sub-function/parameter byte (``None`` = checked by shape below).
#: SecurityAccess levels and RoutineControl identifiers are checked against the target.
BENCH_ALLOWED_REQUESTS: Final[Mapping[int, frozenset[int] | None]] = {
    SID_SESSION_CONTROL: frozenset({_SESSION_DEFAULT, _SESSION_PROGRAMMING, _SESSION_EXTENDED}),
    SID_ECU_RESET: frozenset({0x01}),  # hardReset only
    SID_READ_DATA_BY_IDENTIFIER: None,
    SID_SECURITY_ACCESS: None,
    SID_ROUTINE_CONTROL: frozenset({_START_ROUTINE}),
    SID_REQUEST_DOWNLOAD: None,
    SID_REQUEST_UPLOAD: None,
    SID_TRANSFER_DATA: None,
    SID_REQUEST_TRANSFER_EXIT: None,
    SID_TESTER_PRESENT: frozenset({0x00, _SUPPRESS}),
}

#: Refused by name even on the bench (the allowlist refuses everything else, too): none of
#: them is needed to program an ECU on a bench, and each can change ECU or network state.
BENCH_FORBIDDEN_SERVICES: Final[Mapping[int, str]] = {
    0x14: "ClearDiagnosticInformation",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x2F: "InputOutputControlByIdentifier",
    0x3D: "WriteMemoryByAddress",
    0x85: "ControlDTCSetting",
    0x87: "LinkControl",
}


def _refuse(request: bytes, reason: str) -> SafetyGuardError:
    return SafetyGuardError(f"refusing bench request {request[:4].hex(' ')}...: {reason}")


def check_bench_request(request: bytes, target: BenchTargetProfile) -> None:
    """Refuse ``request`` unless it is on the bench allowlist for ``target``.

    Raises:
        SafetyGuardError: For any other service, sub-function, routine or shape.
    """
    if not request:
        raise SafetyGuardError("refusing an empty bench request")
    sid = request[0]
    if sid in BENCH_FORBIDDEN_SERVICES:
        raise _refuse(request, f"{BENCH_FORBIDDEN_SERVICES[sid]} is never sent on the bench")
    if sid not in BENCH_ALLOWED_REQUESTS:
        raise _refuse(request, f"service {sid:#04x} is not on the bench allowlist")
    allowed = BENCH_ALLOWED_REQUESTS[sid]
    if allowed is not None and (len(request) < 2 or request[1] not in allowed):
        raise _refuse(request, f"sub-function not allowed for service {sid:#04x}")
    length = len(request)
    if sid in (SID_SESSION_CONTROL, SID_ECU_RESET, SID_TESTER_PRESENT) and length != 2:
        raise _refuse(request, "wrong length")
    if sid == SID_READ_DATA_BY_IDENTIFIER and length != 3:
        raise _refuse(request, "exactly one DID per request")
    if sid == SID_SECURITY_ACCESS:
        level = target.security_level
        if length < 2 or request[1] not in (level, level + 1):
            raise _refuse(request, f"only security level {level:#04x} of the target")
        if (request[1] == level and length != 2) or (request[1] == level + 1 and length < 3):
            raise _refuse(request, "wrong length")
    if sid == SID_ROUTINE_CONTROL:
        if length < 4:
            raise _refuse(request, "wrong length")
        routine = int.from_bytes(request[2:4], "big")
        expected = {target.erase_routine_id: 13, target.check_routine_id: 17}
        if routine not in expected:
            raise _refuse(request, f"routine {routine:#06x} is not the target's erase/check")
        if length != expected[routine] or request[4] != _ALFID:
            raise _refuse(request, "wrong routine parameters")
    if sid in (SID_REQUEST_DOWNLOAD, SID_REQUEST_UPLOAD) and (
        length != 11 or request[1] != 0x00 or request[2] != _ALFID
    ):
        raise _refuse(request, "only uncompressed, unencrypted 4-byte address/size transfers")
    if sid == SID_TRANSFER_DATA and length < 2:
        raise _refuse(request, "wrong length")
    if sid == SID_REQUEST_TRANSFER_EXIT and length != 1:
        raise _refuse(request, "no transfer request parameters are sent")


# --------------------------------------------------------------------------- #
# errors, timing
# --------------------------------------------------------------------------- #

#: Hard ceiling for one wait after a responsePending, whatever P2* the ECU announces.
BENCH_P2_STAR_CEILING_S: Final = 10.0
_NRC_BUSY = 0x21
_NRC_PENDING = 0x78
#: Transfer NRCs that end a transfer at once (no retry).
_ABORT_TRANSFER_NRCS = frozenset({0x70, 0x71, 0x72, 0x73})


class BenchNegativeResponseError(ProtocolError):
    """The ECU refused a bench request (``7F <sid> <nrc>``)."""

    def __init__(self, sid: int, nrc: int) -> None:
        super().__init__(f"bench request {sid:#04x}: negative response {nrc:#04x}")
        self.sid, self.nrc = sid, nrc


class BenchTransferError(ProtocolError):
    """A transfer was aborted (retry budget exhausted or an inconsistent answer)."""


@dataclass(frozen=True, slots=True)
class BenchTiming:
    """Bench request timing and retry budget.

    Attributes:
        p2_s: Wait for a first answer.
        p2_star_s: Wait after a responsePending (raised to the ECU's P2*, never above
            :data:`BENCH_P2_STAR_CEILING_S`).
        pending_ceiling_s: Total time one request may stay pending (an erase uses the
            target's ``max_erase_s`` instead).
        keepalive_s: Outside the default session, send ``3E 80`` first when idle longer.
        max_block_retries / max_transfer_retries: TransferData resend budget.
    """

    p2_s: float = 1.0
    p2_star_s: float = 5.0
    pending_ceiling_s: float = 30.0
    keepalive_s: float = 2.0
    max_block_retries: int = 2
    max_transfer_retries: int = 8

    def __post_init__(self) -> None:
        if min(self.p2_s, self.p2_star_s, self.pending_ceiling_s, self.keepalive_s) <= 0:
            raise ValueError("bench timing values must be positive")
        if self.p2_star_s > BENCH_P2_STAR_CEILING_S:
            raise ValueError(f"p2_star_s may not exceed {BENCH_P2_STAR_CEILING_S:.0f}s")
        if not 0 <= self.max_block_retries <= 2 or not 0 <= self.max_transfer_retries <= 8:
            raise ValueError("retry budget is at most 2 per block and 8 per transfer")


def _describe(payload: bytes, *, outgoing: bool) -> str:
    """Traffic log line: TransferData as a header only, keys redacted."""
    sid = payload[0]
    if sid in (SID_TRANSFER_DATA, SID_TRANSFER_DATA + 0x40) and len(payload) > 2:
        return f"{payload[:2].hex(' ').upper()} [{len(payload) - 2} data bytes]"
    if outgoing and sid == SID_SECURITY_ACCESS and len(payload) > 2:
        return f"{payload[:2].hex(' ').upper()} [key redacted]"
    if not outgoing and sid == SID_SECURITY_ACCESS + 0x40 and len(payload) > 2:
        return f"{payload[:2].hex(' ').upper()} [seed {len(payload) - 2} bytes]"
    return payload.hex(" ").upper()


# --------------------------------------------------------------------------- #
# the programmer
# --------------------------------------------------------------------------- #


class BenchProgrammer:
    """Implements :class:`~iran_obd_tuner.core.bench.BenchProgrammerPort` (simulated only).

    Args:
        bus: The CAN bus endpoint; must be simulated in this build.
        target: The bench target; must be simulated (single-region in this build).
        request_id / response_id: Physical addressing.
        padding: ISO-TP padding byte.
        timing: Waits and retry budget.
        traffic_log: Request-level, header-only record for the traffic panel.
        clock: Monotonic clock (injectable for deterministic tests).
    """

    def __init__(
        self,
        bus: CanBus,
        target: BenchTargetProfile,
        *,
        request_id: int = 0x7E0,
        response_id: int = 0x7E8,
        padding: int | None = 0xCC,
        timing: BenchTiming | None = None,
        traffic_log: TrafficLog | None = None,
        adapter_name: str = "bench:virtual-sim-programmable",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not bus.simulated or not target.simulated:
            raise BenchTargetNotQualifiedError(
                "bench programming is only possible against the simulated ECU in this build"
            )
        if len(target.regions) != 1:
            raise BenchTargetNotQualifiedError(
                "multi-region bench targets are not supported in this build"
            )
        self._bus = bus
        self._target = target
        self._timing = timing or BenchTiming()
        self._traffic = traffic_log
        self._adapter_name = adapter_name
        self._clock = clock
        # No traffic log on the channel: frames would carry whole payloads (see _describe).
        self._channel = IsoTpChannel(
            bus, IsoTpConfig(request_id, response_id, padding=padding), clock=clock
        )
        self._p2_star_s = self._timing.p2_star_s
        self._session = _SESSION_DEFAULT
        self._last_request = clock()
        self._pinned_nonce: str | None = None
        self._identity: EcuIdentity | None = None
        self._identity_kind: str | None = None  # "application" | "bootloader" | None

    # ------------------------------------------------------------------ identity
    @property
    def target(self) -> BenchTargetProfile:
        return self._target

    @property
    def simulated(self) -> bool:
        return bool(self._bus.simulated)

    @property
    def session(self) -> int:
        return self._session

    @property
    def p2_star_s(self) -> float:
        return self._p2_star_s

    # ------------------------------------------------------------------ capabilities
    def _check_authorization(self, authorization: FlashAuthorization) -> None:
        if not isinstance(authorization, FlashAuthorization):
            raise SafetyGuardError("a FlashAuthorization is required for this request")
        authorization.check_for(
            target_id=self._target.target_id,
            image_sha256=authorization.image_sha256,
            simulated=self.simulated,
        )
        if self._pinned_nonce is None:
            self._pinned_nonce = authorization.nonce
        elif authorization.nonce != self._pinned_nonce:
            raise SafetyGuardError("another authorisation is already in use on this bench link")

    def _check_capabilities(
        self,
        authorization: FlashAuthorization,
        backup: BackupReceipt,
        region: MemoryRegion,
    ) -> None:
        self._check_authorization(authorization)
        if not isinstance(backup, BackupReceipt):
            raise SafetyGuardError("a BackupReceipt is required before memory is changed")
        if backup.target_id != self._target.target_id or backup.simulated != self.simulated:
            raise SafetyGuardError("the backup belongs to another target or link")
        backup.check_covers([region])
        self._check_target_region(region)
        if self._identity_kind is None:
            raise SafetyGuardError("read the ECU identity on this link before changing memory")
        if self._identity_kind == "application":
            assert self._identity is not None
            backup.check_for(
                target_id=self._target.target_id,
                identity_fingerprint=self._identity.fingerprint(),
                simulated=self.simulated,
            )

    def _check_target_region(self, region: MemoryRegion) -> None:
        if not any(own.contains(region) for own in self._target.regions):
            raise SafetyGuardError(f"region {region.describe()} is outside the target's flash")

    # ------------------------------------------------------------------ transport
    def _record(self, direction: Direction, payload: bytes, *, outgoing: bool) -> None:
        if self._traffic is not None:
            self._traffic.append(
                direction,
                _describe(payload, outgoing=outgoing),
                adapter=self._adapter_name,
                simulated=self.simulated,
            )

    def _transmit(self, payload: bytes) -> None:
        """The only place that sends: the bench allowlist is checked first."""
        check_bench_request(payload, self._target)
        self._record(Direction.TX, payload, outgoing=True)
        self._channel.send(payload)
        self._last_request = self._clock()

    def _keepalive(self) -> None:
        idle = self._clock() - self._last_request
        if self._session != _SESSION_DEFAULT and idle > self._timing.keepalive_s:
            self._transmit(bytes([SID_TESTER_PRESENT, _SUPPRESS]))

    def _request(self, payload: bytes, *, ceiling_s: float | None = None) -> bytes:
        """Send ``payload`` and return the positive answer (responsePending waited for).

        Raises:
            BenchNegativeResponseError: For any final negative answer (``0x21`` included;
                only TransferData is retried, by its caller).
            UdsNoTrafficError: No answer and the link never delivered a frame.
            AdapterTimeoutError: No answer in time.
        """
        if payload[0] != SID_TESTER_PRESENT:
            self._keepalive()
        self._transmit(payload)
        sid = payload[0]
        ceiling = ceiling_s if ceiling_s is not None else self._timing.pending_ceiling_s
        started = self._clock()
        deadline = started + self._timing.p2_s
        while True:
            remaining = deadline - self._clock()
            answer = self._channel.recv(remaining) if remaining > 0 else None
            if answer is None:
                if self._channel.frames_received == 0:
                    raise UdsNoTrafficError(
                        f"bench request {sid:#04x}: no answer and not a single frame received "
                        "on this link; the session is aborted and nothing is retried"
                    )
                raise AdapterTimeoutError(f"bench request {sid:#04x}: no answer in time")
            self._record(Direction.RX, answer, outgoing=False)
            if answer[0] == sid + 0x40:
                return answer
            if answer[0] != 0x7F or len(answer) < 3 or answer[1] != sid:
                continue  # an answer to something else
            if answer[2] == _NRC_PENDING:
                deadline = min(self._clock() + self._p2_star_s, started + ceiling)
                continue
            raise BenchNegativeResponseError(sid, answer[2])

    # ------------------------------------------------------------------ read-only primitives
    def check_link(self, round_trips: int) -> float:
        """``3E 00`` round trips; returns the slowest one in seconds."""
        if round_trips < 1:
            raise ValueError("at least one round trip")
        slowest = 0.0
        for _ in range(round_trips):
            started = self._clock()
            self._request(bytes([SID_TESTER_PRESENT, 0x00]))
            slowest = max(slowest, self._clock() - started)
        return slowest

    def read_identity(self, dids: Iterable[int]) -> EcuIdentity:
        """Read identification DIDs; DIDs the ECU does not have (``0x31``) are left out."""
        values: dict[int, str] = {}
        for did in dids:
            try:
                answer = self._request(bytes([SID_READ_DATA_BY_IDENTIFIER, did >> 8, did & 0xFF]))
            except BenchNegativeResponseError as exc:
                if exc.nrc == 0x31:
                    continue
                raise
            if int.from_bytes(answer[1:3], "big") != did:
                raise ProtocolError(f"answer is for another DID: {answer[:3].hex(' ')}")
            raw = answer[3:]
            printable = raw and all(0x20 <= b < 0x7F for b in raw)
            values[did] = raw.decode("ascii") if printable else raw.hex().upper()
        identity = EcuIdentity(values)
        if not identity.mismatches(self._target.identity_patterns):
            self._identity, self._identity_kind = identity, "application"
        elif not identity.mismatches(self._target.bootloader_identity_patterns):
            # The same ECU switched to its bootloader (e.g. the programming session): an
            # application identity already read on this link is the stronger binding and
            # is kept; only a reset clears it. A bootloader-only binding is for recovery.
            if self._identity_kind != "application":
                self._identity, self._identity_kind = identity, "bootloader"
        else:
            self._identity, self._identity_kind = None, None  # unknown ECU: fail closed
        return identity

    def return_to_default_session(self) -> None:
        """``10 01`` (needs no capability: it only leaves the programming session)."""
        self._request(bytes([SID_SESSION_CONTROL, _SESSION_DEFAULT]))
        self._session = _SESSION_DEFAULT

    def close(self) -> None:
        """Release the bus (the link owns it; closing twice is harmless)."""
        self._bus.close()

    # ------------------------------------------------------------------ state changes
    def enter_programming_session(self, *, authorization: FlashAuthorization) -> None:
        self._check_authorization(authorization)
        answer = self._request(bytes([SID_SESSION_CONTROL, _SESSION_PROGRAMMING]))
        if len(answer) < 2 or answer[1] != _SESSION_PROGRAMMING:
            raise ProtocolError(f"unexpected session answer {answer.hex(' ')}")
        if len(answer) >= 6:
            announced = int.from_bytes(answer[4:6], "big") * 0.01
            self._p2_star_s = min(max(self._timing.p2_star_s, announced), BENCH_P2_STAR_CEILING_S)
        self._session = _SESSION_PROGRAMMING

    def unlock(self, *, authorization: FlashAuthorization, provider: SeedKeyProvider) -> None:
        """SecurityAccess at the target's level - **one attempt**: a refused key aborts."""
        self._check_authorization(authorization)
        if not provider.simulated:
            raise BenchTargetNotQualifiedError("only the simulated seed-key exists in this build")
        if provider.provider_id != self._target.seed_key_provider_id:
            raise SafetyGuardError(
                f"seed-key provider {provider.provider_id!r} is not the target's "
                f"({self._target.seed_key_provider_id!r})"
            )
        level = self._target.security_level
        seed = self._request(bytes([SID_SECURITY_ACCESS, level]))[2:]
        if not seed:
            raise ProtocolError("empty seed")
        if not any(seed):
            return  # all-zero seed: already unlocked
        key = provider.compute_key(level, seed)
        self._request(bytes([SID_SECURITY_ACCESS, level + 1]) + key)

    def _transfer_request(self, sid: int, region: MemoryRegion) -> int:
        """0x34/0x35; returns the data bytes per block."""
        answer = self._request(
            bytes([sid, 0x00, _ALFID])
            + region.start_address.to_bytes(4, "big")
            + region.size.to_bytes(4, "big")
        )
        width = answer[1] >> 4 if len(answer) >= 2 else 0
        if not 1 <= width <= 4 or len(answer) < 2 + width:
            raise ProtocolError(f"malformed transfer answer {answer.hex(' ')}")
        max_block = int.from_bytes(answer[2 : 2 + width], "big")
        chunk = min(max_block - 2, MAX_PAYLOAD - 2)
        if chunk < 1:
            raise ProtocolError(f"the ECU announced an unusable block length {max_block}")
        return chunk

    def _transfer_block(self, payload: bytes, budget: list[int]) -> bytes:
        """One TransferData exchange with the bounded retry rules; returns the answer."""
        sequence = payload[1]
        retries = 0
        while True:
            try:
                answer = self._request(payload)
            except UdsNoTrafficError:
                raise
            except AdapterTimeoutError:
                reason = "timeout"
            except BenchNegativeResponseError as exc:
                if exc.nrc != _NRC_BUSY:
                    raise  # 0x70..0x73 and every other refusal: abort at once
                reason = "busyRepeatRequest"
            else:
                if len(answer) < 2 or answer[1] != sequence:
                    raise BenchTransferError(
                        f"TransferData answer for block {answer[1:2].hex()} instead of "
                        f"{sequence:02x}"
                    )
                return answer
            if retries >= self._timing.max_block_retries or budget[0] <= 0:
                raise BenchTransferError(
                    f"TransferData block {sequence:#04x}: {reason} and the retry budget is "
                    "used up; the transfer is aborted"
                )
            retries += 1
            budget[0] -= 1
            _LOG.warning("TransferData block %#04x: %s, retry %d", sequence, reason, retries)

    def upload_region(
        self,
        region: MemoryRegion,
        *,
        authorization: FlashAuthorization,
        progress: ProgressSink | None = None,
    ) -> bytes:
        """Read ``region`` out of the ECU (0x35 / 0x36 / 0x37)."""
        self._check_authorization(authorization)
        self._check_target_region(region)
        chunk = self._transfer_request(SID_REQUEST_UPLOAD, region)
        data = bytearray()
        budget = [self._timing.max_transfer_retries]
        sequence = 0
        while len(data) < region.size:
            sequence = (sequence + 1) & 0xFF
            answer = self._transfer_block(bytes([SID_TRANSFER_DATA, sequence]), budget)
            block = answer[2:]
            if not block or len(block) > chunk or len(data) + len(block) > region.size:
                raise BenchTransferError(f"upload block {sequence:#04x} has a wrong length")
            data += block
            if progress is not None:
                progress(
                    FlashProgress(FlashStage.BACKUP, len(data), region.size, "upload", sequence)
                )
        self._request(bytes([SID_REQUEST_TRANSFER_EXIT]))
        return bytes(data)

    def erase_region(
        self,
        region: MemoryRegion,
        *,
        authorization: FlashAuthorization,
        backup: BackupReceipt,
    ) -> None:
        self._check_capabilities(authorization, backup, region)
        answer = self._request(
            bytes([SID_ROUTINE_CONTROL, _START_ROUTINE])
            + self._target.erase_routine_id.to_bytes(2, "big")
            + bytes([_ALFID])
            + region.start_address.to_bytes(4, "big")
            + region.size.to_bytes(4, "big"),
            ceiling_s=self._target.max_erase_s,
        )
        if len(answer) < 5 or answer[4] != 0x00:
            raise ProtocolError(f"erase routine did not complete: {answer.hex(' ')}")

    def download_region(
        self,
        region: MemoryRegion,
        data: bytes,
        *,
        authorization: FlashAuthorization,
        backup: BackupReceipt,
        progress: ProgressSink | None = None,
    ) -> None:
        self._check_capabilities(authorization, backup, region)
        if len(data) != region.size:
            raise SafetyGuardError("the data does not have the region's size")
        if hashlib.sha256(data).hexdigest() != authorization.image_sha256:
            raise SafetyGuardError("refusing to download data that is not the authorised image")
        chunk = self._transfer_request(SID_REQUEST_DOWNLOAD, region)
        budget = [self._timing.max_transfer_retries]
        for index, offset in enumerate(range(0, len(data), chunk)):
            sequence = (index + 1) & 0xFF  # 0x01..0xFF, then 0x00, 0x01, ...
            block = data[offset : offset + chunk]
            self._transfer_block(bytes([SID_TRANSFER_DATA, sequence]) + block, budget)
            if progress is not None:
                done = offset + len(block)
                progress(
                    FlashProgress(FlashStage.PROGRAMMING, done, len(data), "download", sequence)
                )
        self._request(bytes([SID_REQUEST_TRANSFER_EXIT]))

    def check_memory(
        self, region: MemoryRegion, crc32: int, *, authorization: FlashAuthorization
    ) -> bool:
        """The target's check routine over ``region``; ``True`` when the ECU reports a pass."""
        self._check_authorization(authorization)
        self._check_target_region(region)
        answer = self._request(
            bytes([SID_ROUTINE_CONTROL, _START_ROUTINE])
            + self._target.check_routine_id.to_bytes(2, "big")
            + bytes([_ALFID])
            + region.start_address.to_bytes(4, "big")
            + region.size.to_bytes(4, "big")
            + (crc32 & 0xFFFF_FFFF).to_bytes(4, "big")
        )
        if len(answer) < 5:
            raise ProtocolError(f"malformed check routine answer {answer.hex(' ')}")
        return answer[4] == 0x00

    def hard_reset(self, *, authorization: FlashAuthorization) -> None:
        """``11 01``; the identity must be read again before memory may change."""
        self._check_authorization(authorization)
        self._request(bytes([SID_ECU_RESET, 0x01]))
        self._session = _SESSION_DEFAULT
        self._identity, self._identity_kind = None, None
