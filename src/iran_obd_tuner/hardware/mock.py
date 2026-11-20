"""In-memory simulated adapter — the only implemented adapter in this snapshot.

Purpose: develop and test the whole diagnostic workflow (and a future UI) without a
vehicle, without an OBD adapter and without any hardware driver. Nothing in this module
touches a serial port, a CAN interface or a network.

The simulated data is defined by a :class:`MockScenario`, either built in
(:meth:`MockScenario.default`, :meth:`MockScenario.drive_cycle`), loaded from JSON
(``tests/fixtures/simulated_vehicle.json``) or constructed in code. Scenarios can also inject
communication failures, which is how the timeout / refused / malformed-response paths of the
diagnostic workflow stay testable.

Two motion profiles exist (:class:`MotionProfile`): ``static`` answers the scenario values
unchanged (used by the CLI and most tests), ``drive_cycle`` animates the common dashboard
parameters as a deterministic function of the time elapsed since :meth:`ObdAdapter.connect`
(used by the dashboard's simulation mode). The clock is injectable, so the drive cycle is
fully reproducible in tests. Every value is simulated; the adapter declares
``AdapterInfo.simulated = True``.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.dtc import DtcStatus, parse_dtc_code
from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.models import Measurement
from iran_obd_tuner.core.monitors import MonitorStatus, decode_monitor_status
from iran_obd_tuner.core.pids import describe_pid, normalize_pid
from iran_obd_tuner.core.safety import DEFAULT_POLICY, SafetyPolicy
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter

__all__ = [
    "FailureMode",
    "MockObdAdapter",
    "MockScenario",
    "MotionProfile",
    "SimulatedReading",
]


class FailureMode(StrEnum):
    """Communication failure injected by a scenario, to exercise error handling."""

    NONE = "none"
    CONNECT_TIMEOUT = "connect_timeout"
    CONNECT_REFUSED = "connect_refused"
    READ_TIMEOUT = "read_timeout"
    MALFORMED_RESPONSE = "malformed_response"


class MotionProfile(StrEnum):
    """How simulated values evolve over time."""

    #: Every read returns the scenario value unchanged.
    STATIC = "static"
    #: Dashboard parameters follow a deterministic, time-based drive cycle (see
    #: :data:`_DRIVE_CYCLE`); other parameters keep their scenario value.
    DRIVE_CYCLE = "drive_cycle"


def _wave(t: float) -> float:
    return math.sin(t / 1.8)


def _burst(t: float) -> float:
    return max(0.0, math.sin(t / 0.9))


#: Drive cycle formulas, keyed by PID, as functions of the seconds since connect. The shape
#: (idle with periodic acceleration bursts) was taken over from the phase 1 dashboard
#: simulation so the dashboard looks the same; the values are invented, not captured.
_DRIVE_CYCLE: Mapping[str, Callable[[float], float]] = {
    "0104": lambda t: 18.0 + _burst(t) * 70.0,
    "0105": lambda t: 86.0 + math.sin(t / 4.0) * 4.0,
    "010C": lambda t: 780.0 + _burst(t) * 3200.0 + _wave(t) * 90.0,
    "010D": lambda t: max(0.0, _burst(t) * 118.0 + _wave(t) * 6.0),
    "010F": lambda t: 28.0 + math.sin(t / 3.0) * 3.0,
    "0111": lambda t: _burst(t) * 78.0,
    "011F": lambda t: float(int(t)),
    "012F": lambda t: max(5.0, 62.0 - t * 0.02),
    "0142": lambda t: 13.8 + math.sin(t / 1.4) * 0.25,
}


@dataclass(frozen=True, slots=True)
class SimulatedReading:
    """One simulated parameter value.

    Attributes:
        pid: Parameter identifier in request form; must exist in the PID catalogue.
        value: Simulated engineering value, expressed in the catalogue unit.
    """

    pid: str
    value: float

    def __post_init__(self) -> None:
        normalized = normalize_pid(self.pid)
        if describe_pid(normalized) is None:
            raise ValueError(
                f"unknown PID {self.pid!r}: simulated readings must use identifiers from "
                "iran_obd_tuner.core.pids.PID_CATALOG"
            )
        object.__setattr__(self, "pid", normalized)


@dataclass(frozen=True, slots=True)
class MockScenario:
    """Everything the simulated vehicle answers.

    Attributes:
        name: Scenario name, shown in reports.
        vin: Simulated VIN (17 characters) or ``None`` to simulate an unreadable VIN.
        protocol: Protocol string the adapter claims to be using.
        readings: Simulated parameter values.
        trouble_codes: Simulated stored trouble codes (service 03).
        failure: Communication failure to inject.
        latency_s: Artificial delay before every response, to simulate a slow adapter.
        notes: Free-form note carried into reports.
        motion: How values evolve over time; :attr:`MotionProfile.STATIC` by default.
        pending_codes: Pending trouble codes (service 07); ``None`` = service unsupported.
        permanent_codes: Permanent trouble codes (service 0A); ``None`` = unsupported.
        monitor_status_hex: The four data bytes of PID 0101 as hex, e.g. ``"83076504"``;
            ``None`` = PID 0101 unsupported. Decoded with the real decoder.
        supports_freeze_frame: Whether service 02 is answered at all.
        freeze_frame_trigger: DTC that stored freeze frame 00; ``None`` = no frame stored.
        freeze_frame: Simulated freeze frame 00 values (only these PIDs are "supported").
    """

    name: str = "simulated-generic-can"
    vin: str | None = "SIMULATEDVIN00001"
    protocol: str = "ISO 15765-4 CAN (11-bit ID, 500 kbit/s) [simulated]"
    readings: tuple[SimulatedReading, ...] = ()
    trouble_codes: tuple[str, ...] = ()
    failure: FailureMode = FailureMode.NONE
    latency_s: float = 0.0
    notes: str = "All values are simulated; they do not come from a vehicle."
    motion: MotionProfile = MotionProfile.STATIC
    pending_codes: tuple[str, ...] | None = ()
    permanent_codes: tuple[str, ...] | None = ()
    monitor_status_hex: str | None = None
    supports_freeze_frame: bool = True
    freeze_frame_trigger: str | None = None
    freeze_frame: tuple[SimulatedReading, ...] = ()

    def __post_init__(self) -> None:
        if self.latency_s < 0:
            raise ValueError("latency_s must not be negative")
        object.__setattr__(self, "trouble_codes", _normalize_codes(self.trouble_codes))
        if self.pending_codes is not None:
            object.__setattr__(self, "pending_codes", _normalize_codes(self.pending_codes))
        if self.permanent_codes is not None:
            object.__setattr__(self, "permanent_codes", _normalize_codes(self.permanent_codes))
        if self.freeze_frame_trigger is not None:
            object.__setattr__(
                self, "freeze_frame_trigger", parse_dtc_code(self.freeze_frame_trigger).code
            )
        if self.monitor_status_hex is not None:
            decode_monitor_status(bytes.fromhex(self.monitor_status_hex))  # validates
        for group in (self.readings, self.freeze_frame):
            seen: set[str] = set()
            for reading in group:
                if reading.pid in seen:
                    raise ValueError(f"duplicate simulated reading for PID {reading.pid}")
                seen.add(reading.pid)

    @property
    def supported_pids(self) -> tuple[str, ...]:
        """Parameters this simulated vehicle answers."""
        return tuple(reading.pid for reading in self.readings)

    def value_for(self, pid: str) -> float:
        """Return the simulated value for ``pid``.

        Raises:
            UnsupportedPidError: If this scenario has no reading for ``pid``.
        """
        normalized = normalize_pid(pid)
        for reading in self.readings:
            if reading.pid == normalized:
                return reading.value
        raise UnsupportedPidError(
            f"PID {normalized} is not supported by simulated vehicle {self.name!r}"
        )

    @classmethod
    def default(cls) -> MockScenario:
        """Return the built-in simulated vehicle (a plausible idling engine)."""
        return cls(
            name="simulated-idling-engine",
            readings=(
                SimulatedReading("0104", 21.2),
                SimulatedReading("0105", 89.0),
                SimulatedReading("010C", 842.0),
                SimulatedReading("010D", 0.0),
                SimulatedReading("010F", 34.0),
                SimulatedReading("0110", 3.42),
                SimulatedReading("0111", 12.5),
                SimulatedReading("011F", 412.0),
                SimulatedReading("0142", 14.1),
            ),
            trouble_codes=("P0301", "P0135", "P0171"),
            pending_codes=("P0420",),
            permanent_codes=("P0301",),
            # MIL on, 3 confirmed codes; spark ignition; misfire/fuel/components available and
            # complete; catalyst, evap, O2 sensor and heater available; evap not complete.
            monitor_status_hex="83076504",
            freeze_frame_trigger="P0301",
            freeze_frame=(
                SimulatedReading("0104", 45.1),
                SimulatedReading("0105", 88.0),
                SimulatedReading("0106", -3.1),
                SimulatedReading("0107", 4.7),
                SimulatedReading("010C", 2410.0),
                SimulatedReading("010D", 64.0),
                SimulatedReading("010F", 31.0),
            ),
        )

    @classmethod
    def drive_cycle(cls) -> MockScenario:
        """Return the time-varying simulated vehicle used by the dashboard simulation mode.

        It answers every parameter shown on the dashboard plus mass air flow; the readings
        hold the drive cycle values at ``t = 0``.
        """
        pids = ("0104", "0105", "010C", "010D", "010F", "0110", "0111", "011F", "012F", "0142")
        readings = tuple(
            SimulatedReading(pid, _DRIVE_CYCLE[pid](0.0) if pid in _DRIVE_CYCLE else 4.1)
            for pid in pids
        )
        return cls(
            name="simulated-drive-cycle",
            readings=readings,
            trouble_codes=("P0300", "P0171"),
            pending_codes=(),
            permanent_codes=None,  # demonstrates an ECU without service 0A
            monitor_status_hex="82076504",
            freeze_frame_trigger="P0300",
            freeze_frame=(
                SimulatedReading("0104", 72.5),
                SimulatedReading("0105", 91.0),
                SimulatedReading("010C", 3150.0),
                SimulatedReading("010D", 87.0),
                SimulatedReading("0111", 64.3),
            ),
            notes=(
                "All values are simulated (time-varying drive cycle); they do not come from "
                "a vehicle."
            ),
            motion=MotionProfile.DRIVE_CYCLE,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MockScenario:
        """Build a scenario from a mapping (see ``tests/fixtures/simulated_vehicle.json``).

        Raises:
            ValueError: On unknown keys or malformed values.
        """
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(
                f"unknown scenario key(s): {', '.join(unknown)}; valid keys are: "
                f"{', '.join(sorted(known))}"
            )
        payload = dict(data)
        if "readings" in payload:
            payload["readings"] = _parse_readings(payload["readings"])
        if "trouble_codes" in payload:
            payload["trouble_codes"] = tuple(str(code) for code in payload["trouble_codes"])
        for key in ("pending_codes", "permanent_codes"):
            if payload.get(key) is not None:
                payload[key] = tuple(str(code) for code in payload[key])
        if "freeze_frame" in payload:
            payload["freeze_frame"] = _parse_readings(payload["freeze_frame"])
        if "failure" in payload:
            payload["failure"] = FailureMode(str(payload["failure"]))
        if "motion" in payload:
            payload["motion"] = MotionProfile(str(payload["motion"]))
        return cls(**payload)

    @classmethod
    def from_json(cls, path: Path | str) -> MockScenario:
        """Load a scenario from a JSON file.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If the JSON is malformed or contains unknown keys.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"scenario file {path} must contain a JSON object")
        return cls.from_dict(raw)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the scenario."""
        return {
            "name": self.name,
            "vin": self.vin,
            "protocol": self.protocol,
            "readings": [{"pid": r.pid, "value": r.value} for r in self.readings],
            "trouble_codes": list(self.trouble_codes),
            "failure": str(self.failure),
            "latency_s": self.latency_s,
            "notes": self.notes,
            "motion": str(self.motion),
            "pending_codes": None if self.pending_codes is None else list(self.pending_codes),
            "permanent_codes": (
                None if self.permanent_codes is None else list(self.permanent_codes)
            ),
            "monitor_status_hex": self.monitor_status_hex,
            "supports_freeze_frame": self.supports_freeze_frame,
            "freeze_frame_trigger": self.freeze_frame_trigger,
            "freeze_frame": [{"pid": r.pid, "value": r.value} for r in self.freeze_frame],
        }

    def with_failure(self, failure: FailureMode) -> MockScenario:
        """Return a copy of this scenario injecting ``failure``."""
        return replace(self, failure=failure)


def _normalize_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(parse_dtc_code(code).code for code in codes)


def _parse_readings(raw: Any) -> tuple[SimulatedReading, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("'readings' must be a list of {'pid': ..., 'value': ...} objects")
    readings: list[SimulatedReading] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"readings[{index}] must be an object with 'pid' and 'value'")
        unknown = sorted(set(item) - {"pid", "value"})
        if unknown:
            raise ValueError(f"readings[{index}] has unknown key(s): {', '.join(unknown)}")
        try:
            readings.append(SimulatedReading(pid=str(item["pid"]), value=float(item["value"])))
        except KeyError as exc:
            raise ValueError(f"readings[{index}] is missing the key {exc}") from exc
    return tuple(readings)


class MockObdAdapter(ObdAdapter):
    """Simulated adapter answering from a :class:`MockScenario`.

    Args:
        scenario: Scenario to answer from; :meth:`MockScenario.default` when omitted.
        policy: Safety policy; the default denies every state-changing operation.
        clock: Monotonic clock in seconds, used by :attr:`MotionProfile.DRIVE_CYCLE`.
            Inject a fake clock to make time-varying values reproducible in tests.
    """

    def __init__(
        self,
        scenario: MockScenario | None = None,
        *,
        policy: SafetyPolicy = DEFAULT_POLICY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(policy=policy)
        self._scenario = MockScenario.default() if scenario is None else scenario
        self._clock = clock
        self._connected_at = 0.0

    @property
    def scenario(self) -> MockScenario:
        """Scenario currently answered by this adapter."""
        return self._scenario

    @property
    def active_protocol(self) -> str | None:
        """The scenario's (simulated) protocol."""
        return self._scenario.protocol

    @property
    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="mock",
            kind=AdapterKind.MOCK,
            description="In-memory simulated vehicle; needs no hardware.",
            implemented=True,
            protocols=(self._scenario.protocol,),
            hardware_required=False,
            notes=self._scenario.notes,
            simulated=True,
        )

    def _open(self, *, timeout_s: float | None) -> None:
        self._delay()
        if self._scenario.failure is FailureMode.CONNECT_TIMEOUT:
            raise AdapterTimeoutError(
                f"mock: no answer within {timeout_s}s while connecting (simulated failure)"
            )
        if self._scenario.failure is FailureMode.CONNECT_REFUSED:
            raise AdapterConnectionError("mock: adapter refused the connection (simulated failure)")
        self._connected_at = self._clock()

    def _close(self) -> None:
        """Nothing to release for the simulated adapter."""

    def _delay(self) -> None:
        if self._scenario.latency_s > 0:
            time.sleep(self._scenario.latency_s)

    def _guard_read(self) -> None:
        self._require_connected()
        self._delay()
        if self._scenario.failure is FailureMode.READ_TIMEOUT:
            raise AdapterTimeoutError(
                "mock: no answer to the diagnostic request (simulated failure)"
            )

    def _guard_decoding(self) -> None:
        if self._scenario.failure is FailureMode.MALFORMED_RESPONSE:
            raise ProtocolError("mock: malformed response frame (simulated failure)")

    def read_vin(self) -> str | None:
        self._guard_read()
        self._guard_decoding()
        return self._scenario.vin

    def read_supported_pids(self) -> tuple[str, ...]:
        self._guard_read()
        self._guard_decoding()
        return self._scenario.supported_pids

    def read_measurement(self, pid: str) -> Measurement:
        self._guard_read()
        normalized = normalize_pid(pid)
        value = self._scenario.value_for(normalized)
        self._guard_decoding()
        if self._scenario.motion is MotionProfile.DRIVE_CYCLE and normalized in _DRIVE_CYCLE:
            value = _DRIVE_CYCLE[normalized](self._clock() - self._connected_at)
        spec = describe_pid(normalized)
        if spec is None:  # pragma: no cover - guarded by MockScenario validation
            raise UnsupportedPidError(f"PID {normalized} is not in the PID catalogue")
        return Measurement(pid=spec.pid, name=spec.name, value=value, unit=spec.unit)

    def read_diagnostic_trouble_codes(self) -> tuple[str, ...]:
        self._guard_read()
        self._guard_decoding()
        return self._scenario.trouble_codes

    def read_trouble_codes(self, status: DtcStatus) -> tuple[str, ...]:
        if status is DtcStatus.STORED:
            return self.read_diagnostic_trouble_codes()
        self._guard_read()
        codes = (
            self._scenario.pending_codes
            if status is DtcStatus.PENDING
            else self._scenario.permanent_codes
        )
        if codes is None:
            raise UnsupportedOperationError(
                f"mock: simulated vehicle does not support service {status.service}"
            )
        self._guard_decoding()
        return codes

    def read_monitor_status(self) -> MonitorStatus:
        self._guard_read()
        if self._scenario.monitor_status_hex is None:
            raise UnsupportedPidError("PID 0101 is not supported by the simulated vehicle")
        self._guard_decoding()
        return decode_monitor_status(bytes.fromhex(self._scenario.monitor_status_hex))

    def read_freeze_frame_supported_pids(self) -> tuple[str, ...]:
        self._guard_read()
        self._require_freeze_frame_service()
        self._guard_decoding()
        return tuple(reading.pid for reading in self._scenario.freeze_frame)

    def read_freeze_frame_trigger(self) -> str | None:
        self._guard_read()
        self._require_freeze_frame_service()
        self._guard_decoding()
        return self._scenario.freeze_frame_trigger

    def read_freeze_frame_value(self, pid: str) -> Measurement:
        self._guard_read()
        self._require_freeze_frame_service()
        normalized = normalize_pid(pid)
        for reading in self._scenario.freeze_frame:
            if reading.pid == normalized:
                self._guard_decoding()
                spec = describe_pid(normalized)
                assert spec is not None  # guarded by SimulatedReading validation
                return Measurement(
                    pid=spec.pid, name=spec.name, value=reading.value, unit=spec.unit
                )
        raise UnsupportedPidError(f"PID {normalized} is not stored in the simulated freeze frame")

    def _require_freeze_frame_service(self) -> None:
        if not self._scenario.supports_freeze_frame:
            raise UnsupportedOperationError(
                "mock: simulated vehicle does not support service 02 (freeze frame)"
            )
