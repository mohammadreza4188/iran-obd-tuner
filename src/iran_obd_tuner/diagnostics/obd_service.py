"""OBD connection layer for the dashboard: real python-OBD or simulation fallback.

Scope and status:

* implemented — simulated live data and simulated trouble codes (no vehicle involved),
  port listing, and the python-OBD read path (VIN-less live data + stored trouble codes);
* **read-only by default** — :meth:`ObdService.clear_dtc` passes through
  :func:`iran_obd_tuner.core.safety.require` and is refused by the shipped policy;
* not implemented — measurement models and units shared with the rest of the project
  (this module keeps its own :class:`LiveSample`), and unification with
  :class:`iran_obd_tuner.hardware.base.ObdAdapter` /
  :class:`iran_obd_tuner.diagnostics.service.DiagnosticService`. Tracked in
  docs/architecture.md.

Import safety: ``obd`` (python-OBD) is imported lazily inside a ``try`` block and is an
optional dependency, and no adapter is opened at import time — a missing python-OBD or a
missing adapter degrades to "not available" instead of failing or connecting.
"""

from __future__ import annotations

import contextlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy, require

try:
    import obd  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only without the optional extra
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
    """Thin wrapper around python-OBD with a simulation mode for UI work.

    Args:
        policy: Safety policy for state-changing operations. The default policy allows
            read-only work only, so :meth:`clear_dtc` refuses unless a caller passes an
            explicitly permissive policy (which the application never does).
    """

    def __init__(self, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy
        self._conn = None
        self._sim = False
        self._connected = False
        self._t0 = time.time()
        self._sim_codes: list[DtcItem] = []
        self.on_log: Callable[..., None] = lambda *_a, **_k: None

    @property
    def policy(self) -> SafetyPolicy:
        """Safety policy enforced for state-changing operations."""
        return self._policy

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def simulation(self) -> bool:
        return self._sim

    def list_ports(self) -> list[str]:
        ports = ["Auto"]
        if obd is None:
            return ports
        try:
            for p in obd.scan_serial():
                ports.append(str(p))
        except Exception as exc:  # pragma: no cover
            self.on_log("msg.port_scan_failed", exc)
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
            self.on_log("msg.sim_started")
            return True

        if obd is None:
            self.on_log("msg.obd_missing")
            return False

        try:
            self.on_log("msg.opening_port", port)
            if port == "Auto":
                self._conn = obd.OBD(fast=False)
            else:
                self._conn = obd.OBD(portstr=port, fast=False)

            if not self._conn.is_connected():
                self.on_log("msg.no_response")
                self._conn = None
                return False

            self._connected = True
            self.on_log("msg.connected_via", self._conn.port_name())
            self.on_log("msg.protocol", self._conn.protocol_name())
            return True
        except Exception as exc:
            self.on_log("msg.connect_error", exc)
            self._conn = None
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        if self._connected:
            self.on_log("msg.adapter_closed")
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

    def read_dtc(self) -> list[DtcItem]:
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
            items: list[DtcItem] = []
            for code, desc in resp.value or []:
                items.append(DtcItem(str(code), str(desc or "")))
            return items
        except Exception as exc:
            self.on_log("msg.dtc_read_error", exc)
            return []

    def clear_dtc(self) -> bool:
        """Clear stored trouble codes (OBD service 04).

        **Refused in this build.** Clearing codes changes vehicle state, so the call goes
        through :func:`iran_obd_tuner.core.safety.require` first: with the shipped read-only
        policy it raises :class:`~iran_obd_tuner.core.exceptions.SafetyGuardError` before any
        command is prepared. The python-OBD call below is kept (and stays unreachable) so the
        work is not lost; activating it requires an explicit code-level policy plus team
        approval — see docs/architecture.md, section "Safety boundary".

        Returns:
            ``True`` when the (simulated) trouble codes were actually cleared.

        Raises:
            SafetyGuardError: Under the shipped policy, i.e. always in this build.
        """
        require(
            OperationClass.STATE_CHANGING,
            policy=self._policy,
            detail="clear diagnostic trouble codes (OBD service 04)",
        )

        if self._sim:
            self._sim_codes.clear()
            self.on_log("msg.dtc_cleared_sim")
            return True

        if self._conn is None or obd is None:
            return False

        try:
            self._conn.query(obd.commands.CLEAR_DTC)
            self.on_log("msg.clear_dtc_requested")
            return True
        except Exception as exc:
            self.on_log("msg.clear_dtc_error", exc)
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
