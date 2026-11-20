"""ISO-TP (ISO 15765-2) segmentation and reassembly over classic CAN.

Frame types (first nibble of the first data byte, "PCI"):

* ``0`` Single Frame - ``0L`` + up to 7 bytes;
* ``1`` First Frame - ``1L LL`` (12-bit length, 8..4095) + 6 bytes;
* ``2`` Consecutive Frame - ``2N`` (sequence 1..F, then 0..F) + up to 7 bytes;
* ``3`` Flow Control - ``3S BS STmin`` with S = 0 continue, 1 wait, 2 overflow.

STmin: ``00``-``7F`` milliseconds, ``F1``-``F9`` 100-900 microseconds; reserved values are
treated as 127 ms, as the standard requires. All frames are padded to 8 bytes (ISO 15765-4
requires this on the OBD connector).

The channel is synchronous and single-threaded; clock and sleep are injectable, so every
timing rule is tested deterministically. Frames with other identifiers are ignored, which
is also how a session coexists with the rest of the bus traffic.

Not implemented: CAN FD (SF escape ``00 LL``), lengths above 4095 (FF escape ``10 00``),
extended and mixed addressing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from iran_obd_tuner.core.exceptions import AdapterTimeoutError, ProtocolError
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.can_bus import CanBus, CanFrame

__all__ = [
    "MAX_PAYLOAD",
    "IsoTpChannel",
    "IsoTpConfig",
    "decode_st_min",
    "functional_request",
]

MAX_PAYLOAD = 4095
_SF, _FF, _CF, _FC = 0x0, 0x1, 0x2, 0x3
_FC_CTS, _FC_WAIT, _FC_OVERFLOW = 0x0, 0x1, 0x2


def decode_st_min(raw: int) -> float:
    """Separation time in seconds from the STmin byte of a flow control frame."""
    if 0x00 <= raw <= 0x7F:
        return raw / 1000.0
    if 0xF1 <= raw <= 0xF9:
        return (raw - 0xF0) / 10_000.0
    return 0.127  # reserved values: use the longest defined time


@dataclass(frozen=True, slots=True)
class IsoTpConfig:
    """Addressing and timing of one ISO-TP channel.

    Attributes:
        tx_id / rx_id: CAN identifiers this side sends on / listens to (e.g. 0x7E0 / 0x7E8).
        is_extended: 29-bit identifiers.
        padding: Byte used to pad frames to 8 bytes (``None`` = no padding).
        block_size / st_min: What this side asks for when it receives (FC parameters).
        n_bs_s: Time to wait for a flow control after a First Frame or a block.
        n_cr_s: Time to wait for the next Consecutive Frame.
        max_wait_frames: Flow control "wait" frames accepted in a row (N_WFTmax).
    """

    tx_id: int
    rx_id: int
    is_extended: bool = False
    padding: int | None = 0xCC
    block_size: int = 0
    st_min: int = 0
    n_bs_s: float = 1.0
    n_cr_s: float = 1.0
    max_wait_frames: int = 10

    def __post_init__(self) -> None:
        if self.padding is not None and not 0 <= self.padding <= 0xFF:
            raise ValueError("padding must be a byte")
        if not 0 <= self.block_size <= 0xFF or not 0 <= self.st_min <= 0xFF:
            raise ValueError("block_size and st_min must be bytes")


class IsoTpChannel:
    """One ISO-TP connection (physical addressing) over a :class:`CanBus`."""

    def __init__(
        self,
        bus: CanBus,
        config: IsoTpConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        traffic_log: TrafficLog | None = None,
        adapter_name: str = "can",
    ) -> None:
        self._bus = bus
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._traffic = traffic_log
        self._adapter_name = adapter_name

    @property
    def config(self) -> IsoTpConfig:
        return self._config

    # ------------------------------------------------------------------ framing helpers
    def _frame(self, arbitration_id: int, data: bytes) -> CanFrame:
        if self._config.padding is not None and len(data) < 8:
            data = data + bytes([self._config.padding]) * (8 - len(data))
        return CanFrame(arbitration_id, data, self._config.is_extended)

    def _send_frame(self, data: bytes) -> None:
        frame = self._frame(self._config.tx_id, data)
        self._record(Direction.TX, frame)
        self._bus.send(frame)

    def _record(self, direction: Direction, frame: CanFrame) -> None:
        if self._traffic is not None:
            self._traffic.append(
                direction, frame.format(), adapter=self._adapter_name, simulated=self._bus.simulated
            )

    def _next_frame(self, deadline: float) -> CanFrame | None:
        """Next frame from ``rx_id`` before ``deadline`` (other identifiers are skipped)."""
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return None
            frame = self._bus.recv(remaining)
            if frame is None:
                return None
            if frame.arbitration_id != self._config.rx_id or (
                frame.is_extended != self._config.is_extended
            ):
                continue
            if not frame.data:
                continue
            self._record(Direction.RX, frame)
            return frame

    # ------------------------------------------------------------------ sending
    def send(self, payload: bytes) -> None:
        """Send one message, segmenting it when it does not fit a Single Frame.

        Raises:
            ValueError: If ``payload`` is empty or longer than :data:`MAX_PAYLOAD`.
            AdapterTimeoutError: If the receiver's flow control does not arrive in time.
            ProtocolError: On receiver overflow, too many wait frames or a malformed FC.
        """
        if not 1 <= len(payload) <= MAX_PAYLOAD:
            raise ValueError(f"ISO-TP payload must be 1..{MAX_PAYLOAD} bytes, got {len(payload)}")
        if len(payload) <= 7:
            self._send_frame(bytes([(_SF << 4) | len(payload)]) + payload)
            return
        length = len(payload)
        self._send_frame(bytes([(_FF << 4) | (length >> 8), length & 0xFF]) + payload[:6])
        offset, sequence = 6, 1
        while offset < length:
            block_size, st_min_s = self._await_flow_control()
            sent_in_block = 0
            while offset < length and (block_size == 0 or sent_in_block < block_size):
                if sent_in_block:  # STmin separates consecutive frames, not FC -> CF
                    self._sleep(st_min_s)
                chunk = payload[offset : offset + 7]
                self._send_frame(bytes([(_CF << 4) | sequence]) + chunk)
                offset += len(chunk)
                sequence = (sequence + 1) & 0x0F
                sent_in_block += 1

    def _await_flow_control(self) -> tuple[int, float]:
        waits = 0
        deadline = self._clock() + self._config.n_bs_s
        while True:
            frame = self._next_frame(deadline)
            if frame is None:
                raise AdapterTimeoutError(
                    f"ISO-TP: no flow control from {self._config.rx_id:#x} within "
                    f"{self._config.n_bs_s:.1f}s (N_Bs)"
                )
            if frame.data[0] >> 4 != _FC:
                continue  # not a flow control: ignore
            if len(frame.data) < 3:
                raise ProtocolError(f"ISO-TP: truncated flow control {frame.format()}")
            status = frame.data[0] & 0x0F
            if status == _FC_CTS:
                return frame.data[1], decode_st_min(frame.data[2])
            if status == _FC_WAIT:
                waits += 1
                if waits > self._config.max_wait_frames:
                    raise ProtocolError("ISO-TP: receiver kept sending 'wait' (N_WFTmax)")
                deadline = self._clock() + self._config.n_bs_s
                continue
            if status == _FC_OVERFLOW:
                raise ProtocolError("ISO-TP: receiver reported overflow (message too long)")
            raise ProtocolError(f"ISO-TP: invalid flow status {status} in {frame.format()}")

    # ------------------------------------------------------------------ receiving
    def recv(self, timeout_s: float) -> bytes | None:
        """Receive one complete message, sending flow control for segmented ones.

        Returns ``None`` when no message starts within ``timeout_s``.

        Raises:
            AdapterTimeoutError: If a segmented message stops arriving (N_Cr).
            ProtocolError: On a wrong sequence number or a malformed frame.
        """
        deadline = self._clock() + timeout_s
        frame = self._next_frame(deadline)
        while frame is not None:
            kind = frame.data[0] >> 4
            if kind == _SF:
                length = frame.data[0] & 0x0F
                if not 1 <= length <= 7 or length > len(frame.data) - 1:
                    raise ProtocolError(f"ISO-TP: invalid single frame {frame.format()}")
                return frame.data[1 : 1 + length]
            if kind == _FF:
                result = self.receive_segmented(frame)
                if isinstance(result, bytes):
                    return result
                frame = result  # the sender started over: process its new SF/FF
                continue
            frame = self._next_frame(deadline)  # stray CF/FC frames are ignored
        return None

    def receive_segmented(self, first: CanFrame) -> bytes | CanFrame:
        """Reassemble the message started by ``first`` (a First Frame already received).

        Sends the flow control frames. Returns the payload, or the new Single/First Frame if
        the sender started over (the caller then processes that frame instead).
        """
        if len(first.data) < 8:
            raise ProtocolError(f"ISO-TP: truncated first frame {first.format()}")
        length = ((first.data[0] & 0x0F) << 8) | first.data[1]
        if length < 8:
            raise ProtocolError(f"ISO-TP: first frame announces only {length} bytes")
        buffer = bytearray(first.data[2:8])
        expected = 1
        block_size = self._config.block_size
        self._send_flow_control()
        received_in_block = 0
        while len(buffer) < length:
            frame = self._next_frame(self._clock() + self._config.n_cr_s)
            if frame is None:
                raise AdapterTimeoutError(
                    f"ISO-TP: consecutive frame {expected} not received within "
                    f"{self._config.n_cr_s:.1f}s (N_Cr); got {len(buffer)} of {length} bytes"
                )
            kind = frame.data[0] >> 4
            if kind in (_SF, _FF):
                return frame  # the sender started over: abandon this message (ISO 15765-2)
            if kind != _CF:
                continue
            sequence = frame.data[0] & 0x0F
            if sequence != expected:
                raise ProtocolError(
                    f"ISO-TP: consecutive frame {sequence} received, {expected} expected"
                )
            buffer.extend(frame.data[1 : 1 + min(7, length - len(buffer))])
            expected = (expected + 1) & 0x0F
            received_in_block += 1
            if block_size and received_in_block == block_size and len(buffer) < length:
                self._send_flow_control()
                received_in_block = 0
        return bytes(buffer)

    def _send_flow_control(self) -> None:
        self._send_frame(
            bytes([(_FC << 4) | _FC_CTS, self._config.block_size, self._config.st_min])
        )


def functional_request(
    bus: CanBus,
    functional_id: int,
    payload: bytes,
    response_ids: range | tuple[int, ...],
    window_s: float,
    *,
    padding: int | None = 0xCC,
    clock: Callable[[], float] = time.monotonic,
    traffic_log: TrafficLog | None = None,
    adapter_name: str = "can",
) -> dict[int, bytes]:
    """Send one Single Frame to a functional address and collect Single Frame answers.

    Functional requests are Single Frame only (ISO 15765-4). Answers longer than one frame
    are not collected (their First Frame is ignored), which is enough for discovery
    requests such as TesterPresent.

    Returns:
        Response identifier -> payload of its (first) Single Frame answer.
    """
    if not 1 <= len(payload) <= 7:
        raise ValueError("a functional request must fit a single frame (1..7 bytes)")
    channel = IsoTpChannel(
        bus,
        IsoTpConfig(functional_id, functional_id, padding=padding),
        clock=clock,
        traffic_log=traffic_log,
        adapter_name=adapter_name,
    )
    channel.send(payload)
    answers: dict[int, bytes] = {}
    wanted = set(response_ids)
    deadline = clock() + window_s
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return answers
        frame = bus.recv(remaining)
        if frame is None:
            return answers
        if frame.arbitration_id not in wanted or not frame.data:
            continue
        if traffic_log is not None:
            traffic_log.append(
                Direction.RX, frame.format(), adapter=adapter_name, simulated=bus.simulated
            )
        length = frame.data[0] & 0x0F
        if frame.data[0] >> 4 == _SF and 1 <= length <= len(frame.data) - 1:
            answers.setdefault(frame.arbitration_id, frame.data[1 : 1 + length])
