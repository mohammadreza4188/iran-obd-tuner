"""OBD connection layer: real python-OBD or simulation fallback."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

try:
    import obd  # type: ignore
except ImportError:  # pragma: no cover
    obd = None


@dataclass
class LiveSample:
    rpm: float = 0.0
    speed: float = 0.0
    coolant: float = 0.0
    voltage: float = 0.0
    fuel: float = 0.0
    throttle: float = 0.0
    iat: float = 0.0
    load: float = 0.0


@dataclass
class DtcItem:
    code: str
    description: str


class ObdService:
    """Thin wrapper around python-OBD with a simulation mode for UI work."""

    def __init__(self) -> None:
        self._conn = None
        self._sim = False
        self._connected = False
        self._t0 = time.time()
        self._sim_codes: List[DtcItem] = []
        self.on_log: Callable[[str], None] = lambda _msg: None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def simulation(self) -> bool:
        return self._sim

    def list_ports(self) -> List[str]:
        ports = ["Auto"]
        if obd is None:
            return ports
        try:
            for p in obd.scan_serial():
                ports.append(str(p))
        except Exception as exc:  # pragma: no cover
            self.on_log(f"Port scan failed: {exc}")
        return ports

    def connect(self, port: str = "Auto", simulation: bool = False) -> bool:
        self.disconnect()
        self._sim = simulation

        if simulation:
            self._connected = True
            self._t0 = time.time()
            self._sim_codes = [
                DtcItem("P0300", "Random misfire"),
                DtcItem("P0171", "System too lean bank 1"),
            ]
            self.on_log("Simulation mode started")
            return True

        if obd is None:
            self.on_log("python-obd is not installed")
            return False

        try:
            self.on_log(f"Opening port {port}")
            if port == "Auto":
                self._conn = obd.OBD(fast=False)
            else:
                self._conn = obd.OBD(portstr=port, fast=False)

            if not self._conn.is_connected():
                self.on_log("No response from adapter")
                self._conn = None
                return False

            self._connected = True
            self.on_log(f"Connected via {self._conn.port_name()}")
            self.on_log(f"Protocol: {self._conn.protocol_name()}")
            return True
        except Exception as exc:
            self.on_log(f"Connect error: {exc}")
            self._conn = None
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._connected:
            self.on_log("Adapter closed")
        self._connected = False
        self._sim = False

    def read_live(self) -> LiveSample:
        if not self._connected:
            return LiveSample()

        if self._sim:
            return self._sim_sample()

        return LiveSample(
            rpm=self._query(obd.commands.RPM),
            speed=self._query(obd.commands.SPEED),
            coolant=self._query(obd.commands.COOLANT_TEMP),
            voltage=self._query(obd.commands.CONTROL_MODULE_VOLTAGE, fallback=12.6),
            fuel=self._query(obd.commands.FUEL_LEVEL, fallback=0.0),
            throttle=self._query(obd.commands.THROTTLE_POS),
            iat=self._query(obd.commands.INTAKE_TEMP),
            load=self._query(obd.commands.ENGINE_LOAD),
        )

    def read_dtc(self) -> List[DtcItem]:
        if not self._connected:
            return []

        if self._sim:
            return list(self._sim_codes)

        if self._conn is None or obd is None:
            return []

        try:
            resp = self._conn.query(obd.commands.GET_DTC)
            if resp.is_null():
                return []
            items: List[DtcItem] = []
            for code, desc in resp.value or []:
                items.append(DtcItem(str(code), str(desc or "")))
            return items
        except Exception as exc:
            self.on_log(f"DTC read error: {exc}")
            return []

    def clear_dtc(self) -> bool:
        if not self._connected:
            return False

        if self._sim:
            self._sim_codes.clear()
            self.on_log("Simulation DTCs cleared")
            return True

        if self._conn is None or obd is None:
            return False

        try:
            self._conn.query(obd.commands.CLEAR_DTC)
            self.on_log("Clear DTC requested")
            return True
        except Exception as exc:
            self.on_log(f"Clear DTC error: {exc}")
            return False

    def _query(self, command, fallback: float = 0.0) -> float:
        if self._conn is None:
            return fallback
        try:
            resp = self._conn.query(command)
            if resp.is_null() or resp.value is None:
                return fallback
            value = resp.value
            # python-obd often returns Pint quantities
            if hasattr(value, "magnitude"):
                return float(value.magnitude)
            return float(value)
        except Exception:
            return fallback

    def _sim_sample(self) -> LiveSample:
        t = time.time() - self._t0
        wave = math.sin(t / 1.8)
        burst = max(0.0, math.sin(t / 0.9))
        rpm = 780 + burst * 3200 + wave * 90
        speed = max(0.0, burst * 118 + wave * 6)
        return LiveSample(
            rpm=rpm,
            speed=speed,
            coolant=86 + math.sin(t / 4.0) * 4,
            voltage=13.8 + math.sin(t / 1.4) * 0.25,
            fuel=max(5.0, 62 - t * 0.02),
            throttle=burst * 78,
            iat=28 + math.sin(t / 3.0) * 3,
            load=18 + burst * 70,
        )
