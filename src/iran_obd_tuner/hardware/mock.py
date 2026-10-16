"""In-memory simulated adapter — the only implemented adapter in this snapshot.

Purpose: develop and test the whole diagnostic workflow (and a future UI) without a
vehicle, without an OBD adapter and without any hardware driver. Nothing in this module
touches a serial port, a CAN interface or a network.

The simulated data is defined by a :class:`MockScenario`, either built in
(:meth:`MockScenario.default`), loaded from JSON (``tests/fixtures/simulated_vehicle.json``)
or constructed in code. Scenarios can also inject communication failures, which is how the
timeout / refused / malformed-response paths of the diagnostic workflow stay testable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.dtc import parse_dtc_code
from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.models import Measurement
from iran_obd_tuner.core.pids import describe_pid, normalize_pid
from iran_obd_tuner.core.safety import DEFAULT_POLICY, SafetyPolicy
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter

__all__ = ["FailureMode", "MockObdAdapter", "MockScenario", "SimulatedReading"]


class FailureMode(StrEnum):
    """Communication failure injected by a scenario, to exercise error handling."""

    NONE = "none"
    CONNECT_TIMEOUT = "connect_timeout"
    CONNECT_REFUSED = "connect_refused"
    READ_TIMEOUT = "read_timeout"
    MALFORMED_RESPONSE = "malformed_response"


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
    """

    name: str = "simulated-generic-can"
    vin: str | None = "SIMULATEDVIN00001"
    protocol: str = "ISO 15765-4 CAN (11-bit ID, 500 kbit/s) [simulated]"
    readings: tuple[SimulatedReading, ...] = ()
    trouble_codes: tuple[str, ...] = ()
    failure: FailureMode = FailureMode.NONE
    latency_s: float = 0.0
    notes: str = "All values are simulated; they do not come from a vehicle."

    def __post_init__(self) -> None:
        if self.latency_s < 0:
            raise ValueError("latency_s must not be negative")
        normalized_codes = []
        for code in self.trouble_codes:
            normalized_codes.append(parse_dtc_code(code).code)
        object.__setattr__(self, "trouble_codes", tuple(normalized_codes))
        seen: set[str] = set()
        for reading in self.readings:
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
        if "failure" in payload:
            payload["failure"] = FailureMode(str(payload["failure"]))
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
        }

    def with_failure(self, failure: FailureMode) -> MockScenario:
        """Return a copy of this scenario injecting ``failure``."""
        return replace(self, failure=failure)


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
    """

    def __init__(
        self,
        scenario: MockScenario | None = None,
        *,
        policy: SafetyPolicy = DEFAULT_POLICY,
    ) -> None:
        super().__init__(policy=policy)
        self._scenario = MockScenario.default() if scenario is None else scenario

    @property
    def scenario(self) -> MockScenario:
        """Scenario currently answered by this adapter."""
        return self._scenario

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
        )

    def _open(self, *, timeout_s: float | None) -> None:
        self._delay()
        if self._scenario.failure is FailureMode.CONNECT_TIMEOUT:
            raise AdapterTimeoutError(
                f"mock: no answer within {timeout_s}s while connecting (simulated failure)"
            )
        if self._scenario.failure is FailureMode.CONNECT_REFUSED:
            raise AdapterConnectionError("mock: adapter refused the connection (simulated failure)")

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
        spec = describe_pid(normalized)
        if spec is None:  # pragma: no cover - guarded by MockScenario validation
            raise UnsupportedPidError(f"PID {normalized} is not in the PID catalogue")
        return Measurement(pid=spec.pid, name=spec.name, value=value, unit=spec.unit)

    def read_diagnostic_trouble_codes(self) -> tuple[str, ...]:
        self._guard_read()
        self._guard_decoding()
        return self._scenario.trouble_codes
