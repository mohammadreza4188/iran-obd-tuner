"""Live data session: per-parameter status, no fallback values, honest failures."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterTimeoutError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedPidError,
    VehicleSilentError,
)
from iran_obd_tuner.core.models import Measurement, Unit
from iran_obd_tuner.core.pids import describe_pid
from iran_obd_tuner.database.repository import JsonKnowledgeRepository
from iran_obd_tuner.diagnostics.live_data import (
    SILENT_POLL_LIMIT,
    LiveDataSession,
    LiveSnapshot,
    PidReading,
    PidStatus,
)
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario


class ScriptedAdapter(ObdAdapter):
    """Adapter answering each PID from a script: a value or an exception to raise.

    It follows the same contract as the real and mock adapters (connection required,
    typed exceptions), so the session is exercised exactly as in production.
    """

    def __init__(self, script: Mapping[str, float | Exception]) -> None:
        super().__init__()
        self._script = dict(script)

    @property
    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="scripted",
            kind=AdapterKind.MOCK,
            description="test double",
            implemented=True,
            simulated=True,
        )

    def _open(self, *, timeout_s: float | None) -> None:
        """Nothing to open."""

    def _close(self) -> None:
        """Nothing to close."""

    def read_vin(self) -> str | None:
        self._require_connected()
        return None

    def read_supported_pids(self) -> tuple[str, ...]:
        self._require_connected()
        return tuple(self._script)

    def read_measurement(self, pid: str) -> Measurement:
        self._require_connected()
        answer = self._script.get(pid, UnsupportedPidError(f"{pid} unsupported"))
        if isinstance(answer, Exception):
            raise answer
        spec = describe_pid(pid)
        assert spec is not None
        return Measurement(pid=pid, name=spec.name, value=answer, unit=spec.unit)

    def read_diagnostic_trouble_codes(self) -> tuple[str, ...]:
        self._require_connected()
        return ()


def _session(adapter: ObdAdapter, **kwargs: object) -> LiveDataSession:
    return LiveDataSession(adapter, JsonKnowledgeRepository(), **kwargs)  # type: ignore[arg-type]


def test_session_does_not_connect_on_construction() -> None:
    session = _session(MockObdAdapter())

    assert session.is_connected is False
    assert session.simulated is True


def test_poll_returns_one_reading_per_parameter_in_order() -> None:
    session = _session(MockObdAdapter())
    session.connect()
    try:
        snapshot = session.poll(["010c", "0105"])
    finally:
        session.disconnect()

    assert [reading.pid for reading in snapshot.readings] == ["010C", "0105"]
    assert all(reading.status is PidStatus.OK for reading in snapshot.readings)
    assert snapshot.value_for("010C") == 842.0
    assert snapshot.reading_for("0105").measurement.unit is Unit.CELSIUS  # type: ignore[union-attr]
    assert snapshot.simulated is True
    assert snapshot.timestamp.tzinfo is not None
    assert snapshot.has_values is True


def test_failed_parameters_have_a_status_and_no_value() -> None:
    adapter = ScriptedAdapter(
        {
            "010C": 900.0,
            "010D": AdapterTimeoutError("NO DATA"),
            "0105": ProtocolError("garbled frame"),
        }
    )
    session = _session(adapter)
    session.connect()
    snapshot = session.poll(["010C", "010D", "0105", "012F"])

    statuses = {reading.pid: reading.status for reading in snapshot.readings}
    assert statuses == {
        "010C": PidStatus.OK,
        "010D": PidStatus.NO_RESPONSE,
        "0105": PidStatus.MALFORMED,
        "012F": PidStatus.UNSUPPORTED,
    }
    for pid in ("010D", "0105", "012F"):
        reading = snapshot.reading_for(pid)
        assert reading is not None
        assert reading.measurement is None  # never a fallback value
        assert snapshot.value_for(pid) is None
        assert reading.detail
    assert snapshot.value_for("010C") == 900.0


def test_a_lost_connection_aborts_the_poll() -> None:
    adapter = ScriptedAdapter({"010C": AdapterConnectionError("cable unplugged")})
    session = _session(adapter)
    session.connect()

    with pytest.raises(AdapterConnectionError, match="cable unplugged"):
        session.poll(["010C"])


def test_polling_without_a_connection_is_an_error() -> None:
    session = _session(MockObdAdapter())

    with pytest.raises(AdapterConnectionError, match="not connected"):
        session.poll(["010C"])


def test_connection_failures_are_raised_and_leave_the_session_closed() -> None:
    for failure in (FailureMode.CONNECT_TIMEOUT, FailureMode.CONNECT_REFUSED):
        session = _session(MockObdAdapter(MockScenario.default().with_failure(failure)))
        with pytest.raises((AdapterTimeoutError, AdapterConnectionError)):
            session.connect()
        assert session.is_connected is False


def test_read_timeouts_are_per_parameter_during_a_poll() -> None:
    scenario = MockScenario.default().with_failure(FailureMode.READ_TIMEOUT)
    session = _session(MockObdAdapter(scenario))
    session.connect()

    snapshot = session.poll(["010C", "0105"])

    assert {r.status for r in snapshot.readings} == {PidStatus.NO_RESPONSE}
    assert snapshot.has_values is False


def test_trouble_codes_are_described_from_the_knowledge_database() -> None:
    session = _session(MockObdAdapter())
    session.connect()

    codes = session.read_trouble_codes()

    assert [code.code for code in codes] == ["P0301", "P0135", "P0171"]
    assert codes[0].description == "Cylinder 1 misfire detected"


def test_a_failed_trouble_code_read_is_an_error_not_an_empty_list() -> None:
    scenario = MockScenario.default().with_failure(FailureMode.MALFORMED_RESPONSE)
    session = _session(MockObdAdapter(scenario))
    session.connect()

    with pytest.raises(ProtocolError):
        session.read_trouble_codes()


def test_vehicle_info_carries_the_simulated_flag() -> None:
    session = _session(MockObdAdapter())
    session.connect()

    info = session.read_vehicle_info()

    assert info.vin == "SIMULATEDVIN00001"
    assert info.simulated is True
    assert info.adapter_name == "mock"


def test_clear_request_is_refused_and_changes_nothing() -> None:
    session = _session(MockObdAdapter())
    session.connect()
    before = session.read_trouble_codes()

    with pytest.raises(SafetyGuardError, match="read-only"):
        session.request_clear_trouble_codes()

    assert session.read_trouble_codes() == before


def test_reading_validates_measurement_presence() -> None:
    with pytest.raises(ValueError):
        PidReading(pid="010C", status=PidStatus.OK)
    with pytest.raises(ValueError):
        PidReading(
            pid="010C",
            status=PidStatus.UNSUPPORTED,
            measurement=Measurement(pid="010C", name="Engine speed", value=1.0, unit=Unit.RPM),
        )


def test_snapshot_lookup_of_unrequested_parameter() -> None:
    snapshot = LiveSnapshot(readings=(), simulated=False)

    assert snapshot.reading_for("010C") is None
    assert snapshot.value_for("010C") is None
    assert snapshot.has_values is False


# --------------------------------------------------------------------------- circuit breaker


class SwitchableAdapter(ScriptedAdapter):
    """Answers until ``ignition_off`` is set, then every request goes unanswered."""

    def __init__(self) -> None:
        super().__init__({"010C": 900.0, "0105": 85.0})
        self.ignition_off = False
        self.requests = 0

    def read_measurement(self, pid: str) -> Measurement:
        self.requests += 1
        if self.ignition_off:
            raise AdapterTimeoutError("NO DATA")
        return super().read_measurement(pid)


def test_a_silent_vehicle_trips_the_breaker_after_the_limit() -> None:
    adapter = SwitchableAdapter()
    session = _session(adapter, silent_poll_limit=3)
    session.connect()
    assert session.poll(["010C", "0105"]).has_values

    adapter.ignition_off = True
    session.poll(["010C", "0105"])
    session.poll(["010C", "0105"])
    assert session.silent_polls == 2
    with pytest.raises(VehicleSilentError, match="ignition turned off") as tripped:
        session.poll(["010C", "0105"])

    assert isinstance(tripped.value, AdapterConnectionError)  # the dashboard disconnects
    assert adapter.requests == 2 + 3 * 2  # bounded: no request after the breaker tripped


def test_any_answer_resets_the_breaker() -> None:
    adapter = SwitchableAdapter()
    session = _session(adapter, silent_poll_limit=2)
    session.connect()
    adapter.ignition_off = True
    session.poll(["010C"])
    adapter.ignition_off = False
    session.poll(["010C"])  # one answer: the vehicle is alive
    adapter.ignition_off = True
    session.poll(["010C"])

    assert session.silent_polls == 1  # never two in a row: no trip


def test_unsupported_only_polls_do_not_count_as_silence() -> None:
    session = _session(ScriptedAdapter({}), silent_poll_limit=1)
    session.connect()

    snapshot = session.poll(["0110", "0111"])  # nothing was asked of the vehicle

    assert all(r.status is PidStatus.UNSUPPORTED for r in snapshot.readings)
    assert session.silent_polls == 0


def test_garbled_answers_count_as_silence() -> None:
    session = _session(ScriptedAdapter({"010C": ProtocolError("STOPPED")}), silent_poll_limit=1)
    session.connect()

    with pytest.raises(VehicleSilentError):
        session.poll(["010C"])


def test_reconnecting_resets_the_breaker_and_the_limit_is_validated() -> None:
    adapter = SwitchableAdapter()
    session = _session(adapter, silent_poll_limit=5)
    session.connect()
    adapter.ignition_off = True
    session.poll(["010C"])
    session.disconnect()
    session.connect()

    assert session.silent_polls == 0
    assert SILENT_POLL_LIMIT >= 2  # one missed reply must never disconnect
    with pytest.raises(ValueError):
        _session(adapter, silent_poll_limit=0)
