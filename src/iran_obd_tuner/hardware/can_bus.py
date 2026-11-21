"""Classic CAN frames and the bus abstraction used by ISO-TP and UDS.

:class:`CanBus` is the only thing the ISO-TP layer talks to. :class:`PythonCanBus` implements
it over python-can (``[can]`` extra), imported lazily inside :meth:`PythonCanBus.open` so
this module imports without python-can.

Safety rules implemented here:

* the python-can interface is **always** passed explicitly (python-can would otherwise pick
  one from ``~/.canrc`` or the environment, which could silently be a real vehicle bus);
  only :data:`SUPPORTED_INTERFACES` are accepted;
* a real (non-``virtual``) interface may only use one of the two ISO 15765-4 OBD bitrates
  (:data:`REAL_BITRATES`): a mistyped bitrate on a vehicle bus makes the controller send
  error frames that disturb every other ECU;
* :meth:`PythonCanBus.probe` opens the interface in **listen-only** mode first (the
  controller neither acknowledges nor transmits, so a wrong bitrate cannot disturb the bus)
  and counts the frames it hears; an error frame or a bad controller status aborts before
  anything is transmitted. Only interfaces whose python-can driver can open listen-only are
  supported (``slcan``, ``pcan``, ``kvaser``); ``gs_usb`` and ``socketcan`` were removed
  because python-can cannot put them into listen-only mode;
* a received error frame, a send failure or a bad controller status stops the session with
  :class:`AdapterConnectionError`; nothing is retried automatically;
* a hardware receive filter is installed for the response identifiers, so the rest of the
  bus traffic never reaches the application.

What the error detection can and cannot see (python-can 4.x): error frames arrive only from
drivers that report them; the controller status is only known where the driver offers it
(``pcan``: ``status_is_ok()``; drivers that override ``BusABC.state``, whose default always
answers ``ACTIVE``). ``slcan`` reports neither, so on ``slcan`` a disturbed bus shows up only
as missing answers - which is why the UDS client aborts when a request goes unanswered on a
bus that never delivered a single frame (see :mod:`iran_obd_tuner.hardware.uds`).

Not implemented: CAN FD, remote frames (ignored on receipt), automatic bitrate detection.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    ConfigurationError,
    SafetyGuardError,
)

__all__ = [
    "LISTEN_PROBE_S",
    "REAL_BITRATES",
    "SUPPORTED_INTERFACES",
    "BusProbe",
    "CanBus",
    "CanFrame",
    "PythonCanBus",
    "check_bitrate",
]

#: python-can interfaces the application may open. ``virtual`` is in-process (tests and the
#: simulated ECU); the others need their vendor driver or extra package, and each of them
#: can be opened in listen-only mode for :meth:`PythonCanBus.probe`.
SUPPORTED_INTERFACES: tuple[str, ...] = (
    "virtual",
    "slcan",
    "pcan",
    "kvaser",
)

#: Bitrates a real interface may use: ISO 15765-4 allows exactly these two on the OBD port.
REAL_BITRATES: tuple[int, ...] = (250_000, 500_000)

#: Seconds the listen-only probe listens before the first transmission.
LISTEN_PROBE_S = 1.0

_MAX_STANDARD_ID = 0x7FF
_MAX_EXTENDED_ID = 0x1FFFFFFF


def _listen_only_kwargs(can: Any, interface: str) -> dict[str, Any]:
    """python-can constructor arguments that open ``interface`` without transmitting."""
    if interface == "slcan":
        return {"listen_only": True}  # slcan "L" command instead of "O"
    if interface == "pcan":
        return {"state": can.BusState.PASSIVE}  # PCAN_LISTEN_ONLY
    if interface == "kvaser":
        return {"driver_mode": False}  # python-can kvaser: DRIVER_MODE_SILENT
    return {}  # virtual (in-process): PythonCanBus itself refuses to send


def check_bitrate(interface: str, bitrate: int) -> None:
    """Refuse a non-positive bitrate, or a non-OBD bitrate on a real interface.

    Raises:
        ConfigurationError: See above.
    """
    if bitrate <= 0:
        raise ConfigurationError("the CAN bitrate must be positive")
    if interface != "virtual" and bitrate not in REAL_BITRATES:
        raise ConfigurationError(
            f"CAN bitrate {bitrate} is not allowed on a real interface; ISO 15765-4 uses "
            + " or ".join(str(rate) for rate in REAL_BITRATES)
            + " bit/s (a wrong bitrate disturbs every ECU on the bus)"
        )


@dataclass(frozen=True, slots=True)
class BusProbe:
    """Result of a listen-only probe.

    Attributes:
        interface / channel / bitrate: What was probed.
        duration_s: How long it listened.
        frames: Data frames heard. ``0`` means the bus was silent (e.g. behind a gateway)
            *or* the bitrate is wrong - silence does not confirm the bitrate.
    """

    interface: str
    channel: str
    bitrate: int
    duration_s: float
    frames: int

    @property
    def traffic_seen(self) -> bool:
        """``True`` when error-free frames were heard: the bitrate matches the bus."""
        return self.frames > 0

    def describe(self) -> str:
        """One line for the log and the traffic panel."""
        verdict = (
            "bitrate confirmed by error-free traffic"
            if self.traffic_seen
            else "bus silent - bitrate NOT confirmed"
        )
        return (
            f"listen-only probe {self.interface}:{self.channel} at {self.bitrate} bit/s: "
            f"{self.frames} frame(s) in {self.duration_s:.1f}s, no error ({verdict})"
        )


@dataclass(frozen=True, slots=True)
class CanFrame:
    """One classic CAN data frame.

    Attributes:
        arbitration_id: 11-bit identifier, or 29-bit when ``is_extended``.
        data: Payload, 0 to 8 bytes (the DLC is its length).
        is_extended: 29-bit identifier.
        timestamp: Monotonic receive time in seconds (``None`` for frames to send).
    """

    arbitration_id: int
    data: bytes
    is_extended: bool = False
    timestamp: float | None = None

    def __post_init__(self) -> None:
        limit = _MAX_EXTENDED_ID if self.is_extended else _MAX_STANDARD_ID
        if not 0 <= self.arbitration_id <= limit:
            raise ValueError(f"arbitration id {self.arbitration_id:#x} out of range")
        if len(self.data) > 8:
            raise ValueError(f"classic CAN carries at most 8 bytes, got {len(self.data)}")

    @property
    def dlc(self) -> int:
        return len(self.data)

    def format(self) -> str:
        """``7E0  02 10 01 CC CC CC CC CC`` (for logs and the traffic panel)."""
        width = 8 if self.is_extended else 3
        return f"{self.arbitration_id:0{width}X}  {self.data.hex(' ').upper()}"


class CanBus(Protocol):
    """A CAN bus endpoint."""

    @property
    def simulated(self) -> bool:
        """``True`` for the in-process virtual bus of the simulator."""
        ...

    def send(self, frame: CanFrame) -> None:
        """Transmit ``frame``.

        Raises:
            AdapterConnectionError: If transmission failed or the bus is in error.
        """
        ...

    def recv(self, timeout_s: float) -> CanFrame | None:
        """Next received data frame, or ``None`` when ``timeout_s`` elapsed.

        Raises:
            AdapterConnectionError: On an error frame or a broken bus.
        """
        ...

    def close(self) -> None:
        """Release the interface. Safe to call twice."""
        ...


class PythonCanBus:
    """:class:`CanBus` over a python-can bus."""

    def __init__(
        self, bus: Any, interface: str, channel: str, *, listen_only: bool = False
    ) -> None:
        self._bus = bus
        self._interface = interface
        self._channel = channel
        self._listen_only = listen_only
        self._closed = False

    @classmethod
    def open(
        cls,
        interface: str,
        channel: str,
        bitrate: int,
        *,
        receive_ids: tuple[int, ...] = (),
        is_extended: bool = False,
        listen_only: bool = False,
    ) -> PythonCanBus:
        """Open ``interface``/``channel`` at ``bitrate`` with a receive filter.

        Args:
            listen_only: Open the controller in listen-only (silent) mode; :meth:`send` is
                refused then.

        Raises:
            ConfigurationError: For an interface outside :data:`SUPPORTED_INTERFACES`, or a
                bitrate refused by :func:`check_bitrate`.
            AdapterConnectionError: If python-can is missing or the interface cannot open.
        """
        if interface not in SUPPORTED_INTERFACES:
            raise ConfigurationError(
                f"unsupported CAN interface {interface!r}; supported: "
                + ", ".join(SUPPORTED_INTERFACES)
            )
        check_bitrate(interface, bitrate)
        try:
            import can
        except ImportError as exc:
            raise AdapterConnectionError(
                "python-can is not installed; install the [can] extra to use a CAN interface"
            ) from exc
        mask = _MAX_EXTENDED_ID if is_extended else _MAX_STANDARD_ID
        filters = [
            {"can_id": can_id, "can_mask": mask, "extended": is_extended} for can_id in receive_ids
        ] or None
        extra = _listen_only_kwargs(can, interface) if listen_only else {}
        try:
            bus = can.Bus(
                interface=interface,
                channel=channel,
                bitrate=bitrate,
                can_filters=filters,
                **extra,
            )
        except (can.CanError, OSError, ValueError) as exc:
            raise AdapterConnectionError(
                f"cannot open CAN interface {interface}:{channel}: {exc}"
            ) from exc
        return cls(bus, interface, channel, listen_only=listen_only)

    @classmethod
    def probe(
        cls,
        interface: str,
        channel: str,
        bitrate: int,
        *,
        duration_s: float = LISTEN_PROBE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> BusProbe:
        """Listen without transmitting for ``duration_s`` and count the frames heard.

        The interface is closed again before this returns; the caller reopens it in normal
        mode only after a clean probe.

        Raises:
            ConfigurationError: See :meth:`open`.
            AdapterConnectionError: On an error frame or a bad controller status - most
                likely a wrong bitrate. Nothing was transmitted.
        """
        bus = cls.open(interface, channel, bitrate, listen_only=True)
        frames = 0
        try:
            deadline = clock() + duration_s
            while (remaining := deadline - clock()) > 0:
                if bus.recv(remaining) is None:
                    break
                frames += 1
            bus._check_state()
        finally:
            bus.close()
        return BusProbe(interface, channel, bitrate, duration_s, frames)

    @property
    def simulated(self) -> bool:
        return self._interface == "virtual"

    @property
    def listen_only(self) -> bool:
        """``True`` when opened for a probe: nothing can be transmitted."""
        return self._listen_only

    def send(self, frame: CanFrame) -> None:
        if self._listen_only:
            raise SafetyGuardError(
                f"{self._interface}:{self._channel} is open in listen-only mode; "
                "refusing to transmit"
            )
        import can

        message = can.Message(
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            is_extended_id=frame.is_extended,
        )
        try:
            self._bus.send(message, timeout=0.5)
        except can.CanError as exc:
            raise AdapterConnectionError(
                f"CAN transmit failed on {self._interface}:{self._channel}: {exc}; "
                "check the bitrate and the wiring - nothing is retried"
            ) from exc
        self._check_state()

    def recv(self, timeout_s: float) -> CanFrame | None:
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            message = self._bus.recv(timeout=remaining)
            if message is None:
                self._check_state()
                return None
            if message.is_error_frame:
                raise AdapterConnectionError(
                    f"CAN error frame on {self._interface}:{self._channel}; wrong bitrate or "
                    "wiring? The session is stopped and nothing is retried"
                )
            if message.is_remote_frame:
                continue
            return CanFrame(
                arbitration_id=message.arbitration_id,
                data=bytes(message.data),
                is_extended=bool(message.is_extended_id),
                timestamp=time.monotonic(),
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            # A vanished interface (USB unplugged) may fail to shut down; there is nothing
            # left to release then, and the error that led here must not be masked.
            with contextlib.suppress(Exception):
                self._bus.shutdown()

    def _check_state(self) -> None:
        """Raise when the controller reports a problem (only where the driver can tell).

        ``BusABC.state`` answers ``ACTIVE`` unless a driver overrides it, and the ``pcan``
        driver answers the *requested* mode there, so the PCAN-Basic status is asked too.
        """
        status_is_ok = getattr(self._bus, "status_is_ok", None)
        if callable(status_is_ok):
            try:
                healthy = bool(status_is_ok())
            except Exception as exc:  # the driver could not be asked: treat as broken
                raise AdapterConnectionError(
                    f"CAN interface {self._interface}:{self._channel} status unavailable: {exc}"
                ) from exc
            if not healthy:
                raise AdapterConnectionError(
                    f"CAN controller {self._interface}:{self._channel} reports an error status "
                    "(bus warning, error-passive or bus-off); the session is stopped and "
                    "nothing is retried"
                )
        if self._listen_only:
            return  # a listen-only controller reports PASSIVE by design
        try:
            state = self._bus.state
        except (AttributeError, NotImplementedError):
            return  # the interface cannot report its state
        name = getattr(state, "name", str(state))
        if name in ("ERROR", "PASSIVE"):
            raise AdapterConnectionError(
                f"CAN bus {self._interface}:{self._channel} is {name.lower()}; the session is "
                "stopped and nothing is retried"
            )
