"""Serial port enumeration: optional pyserial, nothing is ever opened.

pyserial is replaced by fake modules here, so these tests are deterministic and never touch
the machine's real ports.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from iran_obd_tuner.hardware.serial_ports import list_serial_ports


@dataclass
class _FakePortInfo:
    device: str


def _install_fake_pyserial(monkeypatch: pytest.MonkeyPatch, comports) -> None:
    serial_pkg = types.ModuleType("serial")
    tools_pkg = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    list_ports.comports = comports  # type: ignore[attr-defined]
    tools_pkg.list_ports = list_ports  # type: ignore[attr-defined]
    serial_pkg.tools = tools_pkg  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", serial_pkg)
    monkeypatch.setitem(sys.modules, "serial.tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", list_ports)


def test_ports_are_listed_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pyserial(
        monkeypatch,
        lambda: [_FakePortInfo("COM10"), _FakePortInfo("COM7"), _FakePortInfo("COM3")],
    )

    assert list_serial_ports() == ("COM3", "COM7", "COM10")  # numeric, not text, order


def test_missing_pyserial_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "serial", None)  # makes `import serial` fail

    assert list_serial_ports() == ()


def test_enumeration_failure_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[_FakePortInfo]:
        raise OSError("device manager unavailable")

    _install_fake_pyserial(monkeypatch, broken)

    assert list_serial_ports() == ()
