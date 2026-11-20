"""Byte transport below the ELM327 adapter: a serial port, or a scripted fake in tests.

:class:`Transport` is the only thing :class:`~iran_obd_tuner.hardware.elm327.Elm327Adapter`
uses to talk to the outside world, so the whole adapter can be tested against recorded or
scripted adapter replies without any hardware.

:class:`PySerialTransport` is the real implementation. pyserial (``[obd]`` extra) is
imported lazily inside :meth:`PySerialTransport.open`, so this module imports on machines
without it, and a missing pyserial is reported as a clear connection error.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Protocol

from iran_obd_tuner.core.exceptions import AdapterConnectionError, AdapterTimeoutError

__all__ = ["PySerialTransport", "Transport"]

#: Seconds a single serial read may block; reads loop until the overall deadline.
_READ_SLICE_S = 0.05


class Transport(Protocol):
    """A bidirectional byte stream to an adapter."""

    def write(self, data: bytes) -> None:
        """Send ``data``.

        Raises:
            AdapterConnectionError: If the device is gone.
        """
        ...

    def read_until(self, terminator: bytes, timeout_s: float) -> bytes:
        """Read until ``terminator`` arrives and return everything before it.

        Raises:
            AdapterTimeoutError: If ``terminator`` does not arrive within ``timeout_s``.
            AdapterConnectionError: If the device is gone.
        """
        ...

    def discard_input(self) -> None:
        """Drop bytes that arrived but were not read (stale replies)."""
        ...

    def close(self) -> None:
        """Release the device. Safe to call twice."""
        ...


class PySerialTransport:
    """:class:`Transport` over a pyserial port."""

    def __init__(self, port: Any, name: str) -> None:
        self._port = port
        self._name = name

    @classmethod
    def open(cls, port: str, baudrate: int) -> PySerialTransport:
        """Open ``port`` at ``baudrate`` (8N1).

        Raises:
            AdapterConnectionError: If pyserial is missing or the port cannot be opened.
        """
        try:
            import serial
        except ImportError as exc:
            raise AdapterConnectionError(
                "pyserial is not installed; install the [obd] extra to use a serial adapter"
            ) from exc
        try:
            handle = serial.Serial(port=port, baudrate=baudrate, timeout=_READ_SLICE_S)
        except (OSError, ValueError) as exc:  # SerialException derives from OSError
            raise AdapterConnectionError(f"cannot open serial port {port}: {exc}") from exc
        return cls(handle, port)

    def write(self, data: bytes) -> None:
        try:
            self._port.write(data)
            self._port.flush()
        except OSError as exc:
            raise AdapterConnectionError(f"{self._name}: write failed: {exc}") from exc

    def read_until(self, terminator: bytes, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        buffer = bytearray()
        while True:
            try:
                chunk = self._port.read(self._port.in_waiting or 1)
            except OSError as exc:
                raise AdapterConnectionError(f"{self._name}: read failed: {exc}") from exc
            buffer.extend(chunk)
            index = buffer.find(terminator)
            if index >= 0:
                return bytes(buffer[:index])
            if time.monotonic() >= deadline:
                raise AdapterTimeoutError(
                    f"{self._name}: no complete reply within {timeout_s:.1f}s "
                    f"(received {bytes(buffer)!r})"
                )

    def discard_input(self) -> None:
        try:
            self._port.reset_input_buffer()
        except OSError as exc:
            raise AdapterConnectionError(f"{self._name}: {exc}") from exc

    def close(self) -> None:
        # Closing a vanished device can fail; there is nothing left to release then.
        with contextlib.suppress(OSError):
            self._port.close()
