"""Classic CAN frames and the bus abstraction used by ISO-TP and UDS.

:class:`CanBus` is the only thing the ISO-TP layer talks to. :class:`PythonCanBus` implements
it over python-can (``[can]`` extra), imported lazily inside :meth:`PythonCanBus.open` so
this module imports without python-can.

Safety rules implemented here:

* the python-can interface is **always** passed explicitly (python-can would otherwise pick
  one from ``~/.canrc`` or the environment, which could silently be a real vehicle bus);
  only :data:`SUPPORTED_INTERFACES` are accepted;
* a received error frame, a send failure or a bus that went error-passive / bus-off stops
  the session immediately with :class:`AdapterConnectionError` - a wrong bitrate on a real
  vehicle bus disturbs other ECUs, so nothing is retried automatically;
* a hardware receive filter is installed for the response identifiers, so the rest of the
  bus traffic never reaches the application.

Not implemented: CAN FD, remote frames (ignored on receipt), automatic bitrate detection.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from iran_obd_tuner.core.exceptions import AdapterConnectionError, ConfigurationError

__all__ = ["SUPPORTED_INTERFACES", "CanBus", "CanFrame", "PythonCanBus"]

#: python-can interfaces the application may open. ``virtual`` is in-process (tests and the
#: simulated ECU); the others need their vendor driver or extra package.
SUPPORTED_INTERFACES: tuple[str, ...] = (
    "virtual",
    "slcan",
    "pcan",
    "gs_usb",
    "kvaser",
    "socketcan",
)

_MAX_STANDARD_ID = 0x7FF
_MAX_EXTENDED_ID = 0x1FFFFFFF


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

    def __init__(self, bus: Any, interface: str, channel: str) -> None:
        self._bus = bus
        self._interface = interface
        self._channel = channel
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
    ) -> PythonCanBus:
        """Open ``interface``/``channel`` at ``bitrate`` with a receive filter.

        Raises:
            ConfigurationError: For an interface outside :data:`SUPPORTED_INTERFACES` or a
                non-positive bitrate.
            AdapterConnectionError: If python-can is missing or the interface cannot open.
        """
        if interface not in SUPPORTED_INTERFACES:
            raise ConfigurationError(
                f"unsupported CAN interface {interface!r}; supported: "
                + ", ".join(SUPPORTED_INTERFACES)
            )
        if bitrate <= 0:
            raise ConfigurationError("the CAN bitrate must be positive")
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
        try:
            bus = can.Bus(
                interface=interface, channel=channel, bitrate=bitrate, can_filters=filters
            )
        except (can.CanError, OSError, ValueError) as exc:
            raise AdapterConnectionError(
                f"cannot open CAN interface {interface}:{channel}: {exc}"
            ) from exc
        return cls(bus, interface, channel)

    @property
    def simulated(self) -> bool:
        return self._interface == "virtual"

    def send(self, frame: CanFrame) -> None:
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
            self._bus.shutdown()

    def _check_state(self) -> None:
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
