"""PySerialTransport over a fake pyserial port: every call is bounded in time (audit H1).

No serial port is opened. The fake port mimics the pyserial attributes the transport uses
(``write``, ``out_waiting``, ``read``, ``in_waiting``, ``reset_input_buffer``, ``close``),
and a fake clock makes every timeout deterministic.
"""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import AdapterConnectionError, AdapterTimeoutError
from iran_obd_tuner.hardware import transport as transport_module
from iran_obd_tuner.hardware.transport import WRITE_TIMEOUT_S, PySerialTransport


class SerialTimeoutError(OSError):
    """Stands in for pyserial's SerialTimeoutException (a SerialException, i.e. OSError)."""


class FakePort:
    def __init__(self, clock, *, stuck_bytes: int = 0, write_error: BaseException | None = None):
        self.clock = clock
        self.stuck_bytes = stuck_bytes
        self.write_error = write_error
        self.written: list[bytes] = []
        self.flush_called = False
        self.closed = False
        self.incoming = b""

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error
        self.written.append(data)
        return len(data)

    def flush(self) -> None:  # must never be called: unbounded on Windows
        self.flush_called = True
        raise AssertionError("flush() must not be used")

    @property
    def out_waiting(self) -> int:
        return self.stuck_bytes

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def read(self, size: int) -> bytes:
        chunk, self.incoming = self.incoming[:size], self.incoming[size:]
        if not chunk:
            self.clock.advance(0.05)  # a read slice elapses without data
        return chunk

    def reset_input_buffer(self) -> None:
        self.incoming = b""

    def close(self) -> None:
        self.closed = True


def make(fake_clock, **port_kwargs):
    port = FakePort(fake_clock, **port_kwargs)
    return port, PySerialTransport(port, "COM9", clock=fake_clock, sleep=fake_clock.advance)


def test_write_never_calls_the_unbounded_flush(fake_clock) -> None:
    port, transport = make(fake_clock)

    transport.write(b"0100\r")

    assert port.written == [b"0100\r"]
    assert port.flush_called is False


def test_a_write_timeout_becomes_a_connection_error(fake_clock) -> None:
    port, transport = make(fake_clock, write_error=SerialTimeoutError("Write timeout"))

    with pytest.raises(AdapterConnectionError, match="timed out.*nothing is retried"):
        transport.write(b"ATZ\r")


def test_an_output_buffer_that_never_drains_fails_within_the_write_timeout(fake_clock) -> None:
    port, transport = make(fake_clock, stuck_bytes=5)
    started = fake_clock()

    with pytest.raises(AdapterConnectionError, match="still unsent"):
        transport.write(b"ATZ\r")

    assert fake_clock() - started <= WRITE_TIMEOUT_S + 0.01


def test_a_partial_write_is_refused(fake_clock) -> None:
    port, transport = make(fake_clock)
    port.write = lambda data: len(data) - 1  # type: ignore[method-assign]

    with pytest.raises(AdapterConnectionError, match="only 4 of 5"):
        transport.write(b"0100\r")


def test_read_until_is_bounded_by_its_deadline(fake_clock) -> None:
    port, transport = make(fake_clock)
    port.incoming = b"SEARCHING..."  # never followed by the prompt

    with pytest.raises(AdapterTimeoutError, match="no complete reply within 0.5s"):
        transport.read_until(b">", 0.5)


def test_read_until_returns_the_reply_before_the_prompt(fake_clock) -> None:
    port, transport = make(fake_clock)
    port.incoming = b"41 00 BE 3E B8 11\r\r>"

    assert transport.read_until(b">", 2.0) == b"41 00 BE 3E B8 11\r\r"


def test_a_vanished_device_is_a_connection_error_on_every_call(fake_clock) -> None:
    port, transport = make(fake_clock, write_error=OSError("device disconnected"))

    with pytest.raises(AdapterConnectionError):
        transport.write(b"010C\r")

    def gone(*_args):
        raise OSError("ClearCommError failed")

    port.read = gone  # type: ignore[method-assign]
    port.reset_input_buffer = gone  # type: ignore[method-assign]
    with pytest.raises(AdapterConnectionError):
        transport.read_until(b">", 1.0)
    with pytest.raises(AdapterConnectionError):
        transport.discard_input()

    port.close = gone  # type: ignore[method-assign]
    transport.close()  # closing a vanished device is silent


def test_open_passes_a_write_timeout_to_pyserial(monkeypatch) -> None:
    serial = pytest.importorskip("serial", reason="needs the optional [obd] extra")
    captured: dict[str, object] = {}

    class FakeSerial:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(serial, "Serial", FakeSerial)

    PySerialTransport.open("COM9", 38400)

    assert captured["write_timeout"] == WRITE_TIMEOUT_S
    assert captured["timeout"] == transport_module._READ_SLICE_S
    assert captured["port"] == "COM9" and captured["baudrate"] == 38400
