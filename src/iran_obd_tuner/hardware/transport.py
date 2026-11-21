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
from collections.abc import Callable
from typing import Any, Protocol

from iran_obd_tuner.core.exceptions import AdapterConnectionError, AdapterTimeoutError

__all__ = ["WRITE_TIMEOUT_S", "PySerialTransport", "Transport"]

#: Seconds a single serial read may block; reads loop until the overall deadline.
_READ_SLICE_S = 0.05
#: Seconds a write (handing the bytes to the driver, then draining the output buffer) may
#: take. A command is a few bytes; at 9600 baud it leaves the port in a few milliseconds.
WRITE_TIMEOUT_S = 1.0
#: Polling interval while waiting for the output buffer to drain.
_DRAIN_POLL_S = 0.005


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
    """:class:`Transport` over a pyserial port.

    Args:
        port: An open pyserial ``Serial`` object (or a test double with the same methods).
        name: Port name used in error messages.
        write_timeout_s: Upper bound of one :meth:`write`, drain included.
        clock / sleep: Injectable for deterministic tests.
    """

    def __init__(
        self,
        port: Any,
        name: str,
        *,
        write_timeout_s: float = WRITE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._port = port
        self._name = name
        self._write_timeout_s = write_timeout_s
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def open(cls, port: str, baudrate: int) -> PySerialTransport:
        """Open ``port`` at ``baudrate`` (8N1) with bounded read and write timeouts.

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
            handle = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=_READ_SLICE_S,
                write_timeout=WRITE_TIMEOUT_S,
            )
        except (OSError, ValueError) as exc:  # SerialException derives from OSError
            raise AdapterConnectionError(f"cannot open serial port {port}: {exc}") from exc
        return cls(handle, port)

    def write(self, data: bytes) -> None:
        """Send ``data`` and wait (bounded) until it has left the output buffer.

        Raises:
            AdapterConnectionError: If the device is gone, or the write or the drain did not
                complete within the write timeout (stalled adapter). Nothing is retried.
        """
        deadline = self._clock() + self._write_timeout_s
        try:
            written = self._port.write(data)
        except OSError as exc:  # SerialTimeoutException is a SerialException (OSError)
            raise AdapterConnectionError(
                f"{self._name}: write failed or timed out after {self._write_timeout_s:.1f}s "
                f"({exc}); the adapter may have stalled - nothing is retried"
            ) from exc
        if written is not None and written != len(data):
            raise AdapterConnectionError(
                f"{self._name}: only {written} of {len(data)} byte(s) were written; "
                "the adapter may have stalled - nothing is retried"
            )
        self._drain(deadline)

    def _drain(self, deadline: float) -> None:
        """Wait until the output buffer is empty, but never past ``deadline``."""
        while True:
            try:
                waiting = self._port.out_waiting
            except AttributeError:  # a port type that cannot report it: nothing to wait for
                return
            except OSError as exc:
                raise AdapterConnectionError(f"{self._name}: write failed: {exc}") from exc
            if not waiting:
                return
            if self._clock() >= deadline:
                raise AdapterConnectionError(
                    f"{self._name}: {waiting} byte(s) still unsent after "
                    f"{self._write_timeout_s:.1f}s; the adapter may have stalled - nothing is "
                    "retried"
                )
            self._sleep(_DRAIN_POLL_S)

    def read_until(self, terminator: bytes, timeout_s: float) -> bytes:
        deadline = self._clock() + timeout_s
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
            if self._clock() >= deadline:
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
