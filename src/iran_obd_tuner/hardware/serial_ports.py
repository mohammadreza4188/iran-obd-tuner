"""Enumerate serial ports without opening them.

Used by the dashboard's port selector. :func:`list_serial_ports` asks the operating system
which serial devices exist (pyserial's ``serial.tools.list_ports.comports``); it does **not**
open any port and sends nothing, so it is safe to call at any time, including in tests.

pyserial is an optional dependency (``[obd]`` extra) imported lazily inside the function;
without it the function returns an empty tuple. Choosing and opening a port is the job of
a real adapter (planned: ELM327, milestone 2), not of this module.
"""

from __future__ import annotations

from iran_obd_tuner.core.logging import get_logger

__all__ = ["list_serial_ports"]

_LOG = get_logger("hardware.serial_ports")


def list_serial_ports() -> tuple[str, ...]:
    """Return the device names of the serial ports the OS reports, sorted.

    Returns an empty tuple when pyserial is not installed or enumeration fails.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        _LOG.debug("pyserial is not installed; no serial ports listed")
        return ()
    try:
        return tuple(sorted(port.device for port in list_ports.comports()))
    except OSError as exc:
        _LOG.warning("serial port enumeration failed: %s", exc)
        return ()
