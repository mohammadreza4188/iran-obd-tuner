"""Virtual programmable ECU for Milestone 7: a deterministic, simulated bootloader target.

**Everything here is simulated.** No real ECU, bootloader, memory map or security algorithm
is imitated: the layout is :data:`~iran_obd_tuner.core.bench.SIMULATED_BENCH_TARGET`, every
identification value starts with ``SIM-`` and the seed-key is a published XOR
(:class:`SimulatedXorSeedKey`). This module is the ECU *side* of bench programming; it
builds answers, never requests, so it gives the application no way to reprogram anything.

:class:`VirtualProgrammableEcu` is a pure request -> responses handler (no bus, no thread;
time only through an injectable clock):

* **Flash memory** behaves like NOR flash: erased bytes are ``0xFF``, programming can only
  clear bits (``1 -> 0``); a write that needs a ``0 -> 1`` transition is refused with
  ``0x72`` generalProgrammingFailure. Erase works on whole sectors.
* **Boot validity** - the application counts as valid only after a passing checkMemory
  routine over the whole flash (as a bootloader would record it), and only while the flash
  still has that CRC. Otherwise the ECU starts in its bootloader.
* **Persistent vs volatile state** - flash contents, the validity record and the failed
  key counter survive a reset or a power loss; session, security unlock, outstanding seed
  and transfer state do not.
* **Services** - ``0x10`` 01/02/03, ``0x3E``, ``0x22`` (identification, active session,
  ``0xFD00`` boot status), ``0x27`` 01/02 with attempt counter and delay (``0x35``,
  ``0x36``, ``0x37``; relocked on every session change and reset), ``0x31 01`` FF00 erase
  and FF01 checkMemory, ``0x34`` / ``0x35`` / ``0x36`` (sequence counter from 0x01, wrapping
  0xFF -> 0x00, repeat of the last block accepted, anything else ``0x73``) / ``0x37``,
  ``0x11 01``. Every other service is answered ``0x11`` serviceNotSupported.
* **Fault injection** - :class:`FaultPlan`; each fault fires once, so the recovery that
  follows can be tested deterministically.
* **Persistence (Milestone 7e)** - :meth:`VirtualProgrammableEcu.persistent_state` returns
  exactly the persistent part (:class:`SimulatedEcuState`), and a new simulator can boot
  from it. This lets the CLI keep one simulated bench ECU across processes (a new process
  is a power cycle), so a recovery in a later run meets the half-written flash.

:class:`VirtualProgrammableEcuServer` runs the handler on python-can's in-process
``virtual`` bus only; it has no parameter that could name another interface.
"""

from __future__ import annotations

import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import cycle
from typing import Final

from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET, BenchTargetProfile, MemoryRegion
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.hardware.can_bus import PythonCanBus
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig

__all__ = [
    "DID_BOOT_STATUS",
    "SIMULATED_SEED_KEY_MASK",
    "FaultPlan",
    "ProgrammableEcuConfig",
    "SimulatedEcuState",
    "SimulatedXorSeedKey",
    "VirtualProgrammableEcu",
    "VirtualProgrammableEcuServer",
    "default_simulated_image",
]

_LOG = get_logger("hardware.uds_sim_programmable")

# --------------------------------------------------------------------------- #
# constants (all simulator-specific; none describes a real ECU)
# --------------------------------------------------------------------------- #

#: Manufacturer-range DID reporting ``APPLICATION`` or ``BOOTLOADER`` (simulator only).
DID_BOOT_STATUS: Final = 0xFD00
_DID_ACTIVE_SESSION: Final = 0xF186
_DID_BOOT_SOFTWARE: Final = 0xF180

#: The published XOR mask of the simulated seed-key (key = seed XOR mask). Simulated only.
SIMULATED_SEED_KEY_MASK: Final = b"SIM!"

_SESSION_DEFAULT, _SESSION_PROGRAMMING, _SESSION_EXTENDED = 0x01, 0x02, 0x03
_SUPPRESS = 0x80
_ALFID = 0x44  # 4-byte address, 4-byte size

# negative response codes (ISO 14229-1)
_NRC_SERVICE_NOT_SUPPORTED = 0x11
_NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
_NRC_LENGTH = 0x13
_NRC_CONDITIONS = 0x22
_NRC_SEQUENCE = 0x24
_NRC_OUT_OF_RANGE = 0x31
_NRC_SECURITY_DENIED = 0x33
_NRC_INVALID_KEY = 0x35
_NRC_EXCEEDED_ATTEMPTS = 0x36
_NRC_DELAY_NOT_EXPIRED = 0x37
_NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
_NRC_TRANSFER_SUSPENDED = 0x71
_NRC_PROGRAMMING_FAILURE = 0x72
_NRC_WRONG_BLOCK_SEQUENCE = 0x73
_NRC_PENDING = 0x78
_NRC_NOT_IN_SESSION = 0x7F

_SERVICES_NEEDING_PROGRAMMING = frozenset({0x27, 0x31, 0x34, 0x35, 0x36, 0x37})
_SERVICES_NEEDING_UNLOCK = frozenset({0x31, 0x34, 0x35, 0x36, 0x37})


def _negative(sid: int, nrc: int) -> bytes:
    return bytes([0x7F, sid, nrc])


def default_simulated_image(target: BenchTargetProfile = SIMULATED_BENCH_TARGET) -> bytes:
    """The factory content of the simulated ECU: a labelled, deterministic pattern."""
    header = b"SIMULATED-APPLICATION-IMAGE-v1\x00"
    body = bytes((index * 7 + 3) & 0xFF for index in range(target.image_size - len(header)))
    return header + body


# --------------------------------------------------------------------------- #
# configuration, faults and the simulated seed-key
# --------------------------------------------------------------------------- #


@dataclass
class FaultPlan:
    """Deterministic faults; each one fires once and is then disarmed.

    Block numbers count the data blocks of one transfer, starting at 1 (a repeated block
    does not advance the count).

    Attributes:
        drop_link_at_block: Download block N is not answered and the link stays down until
            :meth:`VirtualProgrammableEcu.restore_link` (bus disconnect).
        nrc_at_block: ``(N, nrc)``: download block N is refused with ``nrc``, not written,
            and the transfer is aborted.
        power_loss_at_block: Download block N is torn (its first half is written), then the
            ECU loses power: flash stays as it is, RAM state is lost, nothing answers until
            :meth:`VirtualProgrammableEcu.power_on`.
        corrupt_readback_at_block: Upload block N comes back with one byte flipped.
        bad_key: Every key is refused with ``0x35`` (the ECU behaves as if the key were
            wrong; the attempt counter still counts).
        slow_erase_pending: ``0x78`` responsePending answers sent before an erase completes.
        refuse_upload: RequestUpload is refused with ``0x70`` uploadDownloadNotAccepted.
    """

    drop_link_at_block: int | None = None
    nrc_at_block: tuple[int, int] | None = None
    power_loss_at_block: int | None = None
    corrupt_readback_at_block: int | None = None
    bad_key: bool = False
    slow_erase_pending: int = 0
    refuse_upload: bool = False
    fired: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        for name in ("drop_link_at_block", "power_loss_at_block", "corrupt_readback_at_block"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} counts blocks from 1")
        if self.nrc_at_block is not None:
            block, nrc = self.nrc_at_block
            if block < 1 or not 0x10 <= nrc <= 0xFF or nrc == _NRC_PENDING:
                raise ValueError("nrc_at_block needs a block >= 1 and a final negative code")
        if self.slow_erase_pending < 0:
            raise ValueError("slow_erase_pending must not be negative")

    def take(self, name: str, *, block: int | None = None) -> bool:
        """Whether fault ``name`` fires now (consuming it)."""
        if name in self.fired:
            return False
        value = getattr(self, name)
        if name == "nrc_at_block":
            hit = value is not None and value[0] == block
        elif name in ("bad_key", "refuse_upload"):
            hit = bool(value)
        elif name == "slow_erase_pending":
            hit = value > 0
        else:
            hit = value is not None and value == block
        if hit:
            self.fired.add(name)
        return hit


@dataclass(frozen=True)
class ProgrammableEcuConfig:
    """Behaviour of the virtual programmable ECU.

    Attributes:
        target: The (simulated) bench target whose memory layout is simulated.
        initial_image: Factory flash content (valid application); default
            :func:`default_simulated_image`. ``None``-free: the ECU always starts flashed.
        max_block_length: ``maxNumberOfBlockLength`` announced for 0x34/0x35 (SID and
            counter included, so a block carries ``max_block_length - 2`` data bytes).
        security_max_attempts: Wrong keys before ``0x36`` and the delay.
        security_delay_s: Delay after too many wrong keys (also applied after a reset while
            the counter is exhausted).
        p2_ms / p2_star_10ms: Timing announced in the session control answer.
    """

    target: BenchTargetProfile = SIMULATED_BENCH_TARGET
    initial_image: bytes | None = None
    max_block_length: int = 258
    security_max_attempts: int = 3
    security_delay_s: float = 10.0
    p2_ms: int = 50
    p2_star_10ms: int = 500

    def __post_init__(self) -> None:
        if not self.target.simulated:  # unreachable today: real profiles cannot exist
            raise BenchTargetNotQualifiedError("the virtual ECU only simulates simulated targets")
        if self.initial_image is not None and len(self.initial_image) != self.target.image_size:
            raise ValueError("initial_image must have the target's image size")
        if not 3 <= self.max_block_length <= 4095:
            raise ValueError("max_block_length must be 3..4095 (ISO-TP payload limit)")
        if self.security_max_attempts < 1 or self.security_delay_s < 0:
            raise ValueError("invalid security attempt rules")


class SimulatedXorSeedKey:
    """The simulated ECU's seed-key: ``key = seed XOR "SIM!"``. **Simulated only.**

    Implements :class:`~iran_obd_tuner.core.bench.SeedKeyProvider`. It is deliberately
    trivial and published: it protects nothing and corresponds to no real ECU.
    """

    provider_id: Final = "simulated-xor"
    simulated: Final = True

    def __init__(self, level: int = SIMULATED_BENCH_TARGET.security_level) -> None:
        self._level = level

    def compute_key(self, level: int, seed: bytes) -> bytes:
        if level != self._level:
            raise ValueError(f"the simulated seed-key only knows level {self._level:#04x}")
        if not seed:
            raise ValueError("empty seed")
        return bytes(a ^ b for a, b in zip(seed, cycle(SIMULATED_SEED_KEY_MASK), strict=False))


# --------------------------------------------------------------------------- #
# the ECU
# --------------------------------------------------------------------------- #


@dataclass
class _Transfer:
    direction: str  # "download" or "upload"
    start: int
    size: int
    offset: int = 0
    last_sequence: int = 0x00  # the first block carries 0x01
    blocks: int = 0  # data blocks done (repeats not counted)
    last_chunk: int = 0  # data bytes of the last block (to answer a repeat)

    @property
    def expected_sequence(self) -> int:
        return (self.last_sequence + 1) & 0xFF

    @property
    def complete(self) -> bool:
        return self.offset >= self.size


@dataclass(frozen=True, slots=True)
class SimulatedEcuState:
    """The persistent part of a :class:`VirtualProgrammableEcu` (survives a power cycle).

    Attributes:
        flash: Flash content in image order.
        valid_crc: The bootloader's validity record (CRC32 of the flash recorded by the last
            passing checkMemory), ``None`` when no valid application is recorded.
        failed_key_attempts: Wrong keys counted so far.
    """

    flash: bytes
    valid_crc: int | None
    failed_key_attempts: int

    def __post_init__(self) -> None:
        if self.valid_crc is not None and not 0 <= self.valid_crc <= 0xFFFF_FFFF:
            raise ValueError("valid_crc must be a CRC32 or None")
        if self.failed_key_attempts < 0:
            raise ValueError("failed_key_attempts must not be negative")


class VirtualProgrammableEcu:
    """Pure request -> responses simulator of a bootloader-capable ECU (see module doc).

    Args:
        config: Behaviour and memory layout.
        faults: Fault injection plan (default: none).
        clock: Monotonic clock for the security delay.
        state: Boot from this persistent state instead of the factory image (it must not be
            combined with ``config.initial_image``).
    """

    def __init__(
        self,
        config: ProgrammableEcuConfig | None = None,
        faults: FaultPlan | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        state: SimulatedEcuState | None = None,
    ) -> None:
        self.config = config or ProgrammableEcuConfig()
        self.faults = faults or FaultPlan()
        self._clock = clock
        target = self.config.target
        # persistent state (survives reset and power loss)
        if state is None:
            image = self.config.initial_image or default_simulated_image(target)
            self._flash = bytearray(image)
            self._valid_crc: int | None = zlib.crc32(self._flash)  # factory flashed and checked
            self._failed_keys = 0
        else:
            if self.config.initial_image is not None:
                raise ValueError("pass either a persistent state or an initial image, not both")
            if len(state.flash) != target.image_size:
                raise ValueError("the persistent state does not have the target's image size")
            self._flash = bytearray(state.flash)
            self._valid_crc = state.valid_crc
            self._failed_keys = state.failed_key_attempts
        # bookkeeping visible to tests
        self.requests: list[bytes] = []
        self.powered = True
        self.link_up = True
        self.resets = 0
        self._seed_counter = 0
        self._boot()

    # ------------------------------------------------------------------ state
    def _boot(self) -> None:
        """(Re)start: volatile state is lost; the mode follows the validity record."""
        self.session = _SESSION_DEFAULT
        self.unlocked = False
        self._seed: bytes | None = None
        self._transfer: _Transfer | None = None
        exhausted = self._failed_keys >= self.config.security_max_attempts
        self._delay_until = self._clock() + self.config.security_delay_s if exhausted else 0.0

    def persistent_state(self) -> SimulatedEcuState:
        """What survives a power cycle (flash, validity record, failed key counter)."""
        return SimulatedEcuState(bytes(self._flash), self._valid_crc, self._failed_keys)

    @property
    def flash(self) -> bytes:
        """Current flash content (image order)."""
        return bytes(self._flash)

    @property
    def application_valid(self) -> bool:
        """Whether the bootloader would start the application."""
        return self._valid_crc is not None and self._valid_crc == zlib.crc32(self._flash)

    @property
    def in_bootloader(self) -> bool:
        return self.session == _SESSION_PROGRAMMING or not self.application_valid

    @property
    def failed_key_attempts(self) -> int:
        return self._failed_keys

    def power_on(self) -> None:
        """Restore power after a simulated power loss (boots from flash)."""
        self.powered = True
        self.link_up = True
        self._boot()

    def power_cycle(self) -> None:
        """Lose and restore power (flash and validity record are kept)."""
        self.powered = False
        self.power_on()

    def restore_link(self) -> None:
        """Reconnect after a simulated bus disconnect (the ECU itself kept running)."""
        self.link_up = True

    # ------------------------------------------------------------------ helpers
    def _region_of(self, address: int, size: int) -> int | None:
        """Image offset of ``address`` when ``[address, address+size)`` is inside one region."""
        if size <= 0:
            return None
        wanted = MemoryRegion(address, size) if address + size <= 0xFFFF_FFFF else None
        if wanted is None:
            return None
        for region in self.config.target.regions:
            if region.contains(wanted):
                return region.image_offset + (address - region.start_address)
        return None

    def _crc_of(self, offset: int, size: int) -> int:
        return zlib.crc32(self._flash[offset : offset + size])

    def _next_seed(self) -> bytes:
        self._seed_counter += 1
        value = (0x1234_ABCD + 0x9E37_79B9 * self._seed_counter) & 0xFFFF_FFFF
        return (value or 1).to_bytes(4, "big")

    # ------------------------------------------------------------------ dispatch
    def handle(self, request: bytes) -> list[bytes]:
        """Answer one request. ``[]`` means no answer (suppressed, powered off, link down)."""
        self.requests.append(request)
        if not self.powered or not self.link_up or not request:
            return []
        sid = request[0]
        handler = self._HANDLERS.get(sid)
        if handler is None:
            return [_negative(sid, _NRC_SERVICE_NOT_SUPPORTED)]
        if sid in _SERVICES_NEEDING_PROGRAMMING and self.session != _SESSION_PROGRAMMING:
            return [_negative(sid, _NRC_NOT_IN_SESSION)]
        if sid in _SERVICES_NEEDING_UNLOCK and not self.unlocked:
            return [_negative(sid, _NRC_SECURITY_DENIED)]
        return handler(self, request)

    # ------------------------------------------------------------------ 0x10 / 0x3E / 0x11
    def _session_control(self, request: bytes) -> list[bytes]:
        if len(request) != 2:
            return [_negative(0x10, _NRC_LENGTH)]
        sub, suppress = request[1] & 0x7F, request[1] & _SUPPRESS
        if sub not in (_SESSION_DEFAULT, _SESSION_PROGRAMMING, _SESSION_EXTENDED):
            return [_negative(0x10, _NRC_SUB_FUNCTION_NOT_SUPPORTED)]
        self.session = sub
        self.unlocked = False  # every session change relocks
        self._seed = None
        self._transfer = None
        if suppress:
            return []
        timing = self.config.p2_ms.to_bytes(2, "big") + self.config.p2_star_10ms.to_bytes(2, "big")
        return [bytes([0x50, sub]) + timing]

    def _tester_present(self, request: bytes) -> list[bytes]:
        if len(request) != 2:
            return [_negative(0x3E, _NRC_LENGTH)]
        if request[1] & 0x7F != 0x00:
            return [_negative(0x3E, _NRC_SUB_FUNCTION_NOT_SUPPORTED)]
        return [] if request[1] & _SUPPRESS else [b"\x7e\x00"]

    def _ecu_reset(self, request: bytes) -> list[bytes]:
        if len(request) != 2:
            return [_negative(0x11, _NRC_LENGTH)]
        if request[1] & 0x7F != 0x01:
            return [_negative(0x11, _NRC_SUB_FUNCTION_NOT_SUPPORTED)]
        answer = [] if request[1] & _SUPPRESS else [b"\x51\x01"]
        self.resets += 1
        self._boot()
        return answer

    # ------------------------------------------------------------------ 0x22
    def _did_values(self) -> dict[int, bytes]:
        values = {
            0xF191: b"SIM-HW-1.0",
            _DID_ACTIVE_SESSION: bytes([self.session]),
            DID_BOOT_STATUS: b"BOOTLOADER" if self.in_bootloader else b"APPLICATION",
        }
        if self.in_bootloader:
            values[_DID_BOOT_SOFTWARE] = b"SIM-BOOT-1.0"
        else:
            values[0xF187] = b"SIM-PART-0001"
            values[0xF189] = f"SIM-SW-{zlib.crc32(self._flash):08X}".encode("ascii")
            values[0xF18C] = b"SIM-SERIAL-42"
        return values

    def _read_did(self, request: bytes) -> list[bytes]:
        if len(request) != 3:
            return [_negative(0x22, _NRC_LENGTH)]
        did = int.from_bytes(request[1:3], "big")
        value = self._did_values().get(did)
        if value is None:
            return [_negative(0x22, _NRC_OUT_OF_RANGE)]
        return [b"\x62" + request[1:3] + value]

    # ------------------------------------------------------------------ 0x27
    def _security_access(self, request: bytes) -> list[bytes]:
        level = self.config.target.security_level
        if len(request) < 2:
            return [_negative(0x27, _NRC_LENGTH)]
        sub = request[1]
        if sub == level:  # requestSeed
            if len(request) != 2:
                return [_negative(0x27, _NRC_LENGTH)]
            if self._clock() < self._delay_until:
                return [_negative(0x27, _NRC_DELAY_NOT_EXPIRED)]
            if self.unlocked:
                return [bytes([0x67, sub, 0, 0, 0, 0])]  # all-zero seed: already unlocked
            self._seed = self._next_seed()
            return [bytes([0x67, sub]) + self._seed]
        if sub == level + 1:  # sendKey
            if self._seed is None:
                return [_negative(0x27, _NRC_SEQUENCE)]
            seed, self._seed = self._seed, None  # a seed is good for one key only
            expected = SimulatedXorSeedKey(level).compute_key(level, seed)
            if self.faults.take("bad_key") or request[2:] != expected:
                self._failed_keys += 1
                if self._failed_keys >= self.config.security_max_attempts:
                    self._delay_until = self._clock() + self.config.security_delay_s
                    return [_negative(0x27, _NRC_EXCEEDED_ATTEMPTS)]
                return [_negative(0x27, _NRC_INVALID_KEY)]
            self._failed_keys = 0
            self.unlocked = True
            return [bytes([0x67, sub])]
        return [_negative(0x27, _NRC_SUB_FUNCTION_NOT_SUPPORTED)]

    # ------------------------------------------------------------------ 0x31
    def _address_and_size(self, payload: bytes) -> tuple[int, int] | None:
        """Parse ``ALFID addr4 size4``; ``None`` when malformed."""
        if len(payload) != 9 or payload[0] != _ALFID:
            return None
        return int.from_bytes(payload[1:5], "big"), int.from_bytes(payload[5:9], "big")

    def _routine_control(self, request: bytes) -> list[bytes]:
        if len(request) < 4:
            return [_negative(0x31, _NRC_LENGTH)]
        sub, routine = request[1], int.from_bytes(request[2:4], "big")
        if sub != 0x01:
            return [_negative(0x31, _NRC_SUB_FUNCTION_NOT_SUPPORTED)]
        target = self.config.target
        if routine == target.erase_routine_id:
            return self._erase(request)
        if routine == target.check_routine_id:
            return self._check_memory(request)
        return [_negative(0x31, _NRC_OUT_OF_RANGE)]

    def _erase(self, request: bytes) -> list[bytes]:
        parsed = self._address_and_size(request[4:])
        if parsed is None:
            return [_negative(0x31, _NRC_LENGTH)]
        address, size = parsed
        sector = self.config.target.sector_size
        offset = self._region_of(address, size)
        if offset is None or address % sector or size % sector:
            return [_negative(0x31, _NRC_OUT_OF_RANGE)]
        if self._transfer is not None:
            return [_negative(0x31, _NRC_CONDITIONS)]
        pending = self.faults.slow_erase_pending if self.faults.take("slow_erase_pending") else 0
        self._flash[offset : offset + size] = b"\xff" * size
        self._valid_crc = None
        positive = b"\x71\x01" + request[2:4] + b"\x00"  # routine status 0x00: done
        return [_negative(0x31, _NRC_PENDING)] * pending + [positive]

    def _check_memory(self, request: bytes) -> list[bytes]:
        payload = request[4:]
        if len(payload) != 13:
            return [_negative(0x31, _NRC_LENGTH)]
        parsed = self._address_and_size(payload[:9])
        if parsed is None:
            return [_negative(0x31, _NRC_LENGTH)]
        address, size = parsed
        offset = self._region_of(address, size)
        if offset is None:
            return [_negative(0x31, _NRC_OUT_OF_RANGE)]
        expected = int.from_bytes(payload[9:13], "big")
        passed = self._crc_of(offset, size) == expected
        whole_flash = offset == 0 and size == len(self._flash)
        if passed and whole_flash:
            self._valid_crc = expected  # the bootloader records a valid application
        status = 0x00 if passed else 0x01
        return [b"\x71\x01" + request[2:4] + bytes([status])]

    # ------------------------------------------------------------------ 0x34 / 0x35
    def _request_transfer(self, request: bytes, *, direction: str) -> list[bytes]:
        sid = request[0]
        if len(request) != 11:
            return [_negative(sid, _NRC_LENGTH)]
        if request[1] != 0x00:  # no compression, no encryption
            return [_negative(sid, _NRC_OUT_OF_RANGE)]
        parsed = self._address_and_size(request[2:])
        if parsed is None:
            return [_negative(sid, _NRC_OUT_OF_RANGE)]
        if self._transfer is not None:
            return [_negative(sid, _NRC_CONDITIONS)]
        address, size = parsed
        offset = self._region_of(address, size)
        if offset is None:
            return [_negative(sid, _NRC_OUT_OF_RANGE)]
        if direction == "upload" and self.faults.take("refuse_upload"):
            return [_negative(sid, _NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED)]
        self._transfer = _Transfer(direction=direction, start=offset, size=size)
        if direction == "download":
            self._valid_crc = None  # the application is being replaced
        length = self.config.max_block_length.to_bytes(2, "big")
        return [bytes([sid + 0x40, 0x20]) + length]

    def _request_download(self, request: bytes) -> list[bytes]:
        return self._request_transfer(request, direction="download")

    def _request_upload(self, request: bytes) -> list[bytes]:
        return self._request_transfer(request, direction="upload")

    # ------------------------------------------------------------------ 0x36 / 0x37
    def _transfer_data(self, request: bytes) -> list[bytes]:
        transfer = self._transfer
        if transfer is None:
            return [_negative(0x36, _NRC_SEQUENCE)]
        if len(request) < 2:
            return [_negative(0x36, _NRC_LENGTH)]
        sequence = request[1]
        if transfer.blocks and sequence == transfer.last_sequence:
            return self._repeat_block(transfer, sequence)  # lost answer: confirm again
        if sequence != transfer.expected_sequence:
            return [_negative(0x36, _NRC_WRONG_BLOCK_SEQUENCE)]
        if transfer.direction == "upload":
            return self._upload_block(transfer, request)
        return self._download_block(transfer, request)

    def _repeat_block(self, transfer: _Transfer, sequence: int) -> list[bytes]:
        if transfer.direction == "download":
            return [bytes([0x76, sequence])]
        end = transfer.start + transfer.offset
        previous = self._flash[end - transfer.last_chunk : end]
        return [bytes([0x76, sequence]) + bytes(previous)]

    def _upload_block(self, transfer: _Transfer, request: bytes) -> list[bytes]:
        if len(request) != 2:
            return [_negative(0x36, _NRC_LENGTH)]
        if transfer.complete:
            return [_negative(0x36, _NRC_SEQUENCE)]
        chunk = min(self.config.max_block_length - 2, transfer.size - transfer.offset)
        begin = transfer.start + transfer.offset
        data = bytearray(self._flash[begin : begin + chunk])
        transfer.blocks += 1
        if self.faults.take("corrupt_readback_at_block", block=transfer.blocks):
            data[0] ^= 0x01
        transfer.offset += chunk
        transfer.last_chunk = chunk
        transfer.last_sequence = request[1]
        return [bytes([0x76, request[1]]) + bytes(data)]

    def _download_block(self, transfer: _Transfer, request: bytes) -> list[bytes]:
        data = request[2:]
        if not data or len(data) > self.config.max_block_length - 2:
            return [_negative(0x36, _NRC_LENGTH)]
        if transfer.offset + len(data) > transfer.size:
            self._transfer = None
            return [_negative(0x36, _NRC_TRANSFER_SUSPENDED)]
        block = transfer.blocks + 1
        if self.faults.take("drop_link_at_block", block=block):
            self.link_up = False  # this block is lost and nothing answers any more
            return []
        if self.faults.take("nrc_at_block", block=block):
            assert self.faults.nrc_at_block is not None
            self._transfer = None
            return [_negative(0x36, self.faults.nrc_at_block[1])]
        begin = transfer.start + transfer.offset
        if self.faults.take("power_loss_at_block", block=block):
            torn = data[: len(data) // 2]
            self._program(begin, torn)  # torn write; erased bytes accept it
            self.powered = False
            self._boot()  # RAM is gone; flash stays torn
            return []
        current = self._flash[begin : begin + len(data)]
        if any((old & new) != new for old, new in zip(current, data, strict=True)):
            self._transfer = None  # NOR flash: a bit cannot go from 0 back to 1
            return [_negative(0x36, _NRC_PROGRAMMING_FAILURE)]
        self._program(begin, data)
        transfer.offset += len(data)
        transfer.last_chunk = len(data)
        transfer.blocks = block
        transfer.last_sequence = request[1]
        return [bytes([0x76, request[1]])]

    def _program(self, begin: int, data: bytes) -> None:
        for index, value in enumerate(data):
            self._flash[begin + index] &= value
        self._valid_crc = None

    def _transfer_exit(self, request: bytes) -> list[bytes]:
        if len(request) != 1:
            return [_negative(0x37, _NRC_LENGTH)]
        transfer = self._transfer
        if transfer is None:
            return [_negative(0x37, _NRC_SEQUENCE)]
        if not transfer.complete:
            return [_negative(0x37, _NRC_SEQUENCE)]
        self._transfer = None
        return [b"\x77"]

    _HANDLERS: Final[dict[int, Callable[[VirtualProgrammableEcu, bytes], list[bytes]]]] = {
        0x10: _session_control,
        0x11: _ecu_reset,
        0x22: _read_did,
        0x27: _security_access,
        0x31: _routine_control,
        0x34: _request_download,
        0x35: _request_upload,
        0x36: _transfer_data,
        0x37: _transfer_exit,
        0x3E: _tester_present,
    }


# --------------------------------------------------------------------------- #
# server on the virtual bus
# --------------------------------------------------------------------------- #


class VirtualProgrammableEcuServer:
    """Serve a :class:`VirtualProgrammableEcu` on python-can's ``virtual`` bus (only).

    There is no interface parameter: the bus is always opened with ``"virtual"`` (an
    architecture test checks the literal), so this server can never answer on a real bus.

    Args:
        channel: Name of the virtual bus channel shared with the client.
        ecu: The simulator to answer with.
        request_id / response_id: Physical addressing (default 0x7E0 / 0x7E8).
        pending_delay_s: Pause between consecutive answers (e.g. responsePending).
    """

    def __init__(
        self,
        channel: str,
        ecu: VirtualProgrammableEcu | None = None,
        *,
        request_id: int = 0x7E0,
        response_id: int = 0x7E8,
        bitrate: int = 500_000,
        pending_delay_s: float = 0.02,
    ) -> None:
        self.ecu = ecu or VirtualProgrammableEcu()
        self._request_id = request_id
        self._pending_delay_s = pending_delay_s
        self._bus = PythonCanBus.open("virtual", channel, bitrate, receive_ids=(request_id,))
        self._iso = IsoTpChannel(self._bus, IsoTpConfig(response_id, request_id))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="uds-sim-prog", daemon=True)
        self.errors: list[BaseException] = []

    @property
    def simulated(self) -> bool:
        return True

    def __enter__(self) -> VirtualProgrammableEcuServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(5)
        self._bus.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                request = self._iso.recv(0.05)
                if request is None:
                    continue
                for index, response in enumerate(self.ecu.handle(request)):
                    if index:
                        self._stop.wait(self._pending_delay_s)
                    self._iso.send(response)
            except Exception as exc:  # kept for the tests that run the server
                _LOG.warning("simulated programmable ECU error: %s", exc)
                self.errors.append(exc)
