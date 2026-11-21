"""Extended read-only diagnostics over the simulated adapter: 0101, 07/0A, freeze frame.

Each section reports its own outcome: an unsupported or unanswered section never breaks
the others, and it is never reported as "no codes" or as a number.
"""

from __future__ import annotations

import json

import pytest

from iran_obd_tuner.core.dtc import DtcStatus
from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.database.repository import JsonKnowledgeRepository
from iran_obd_tuner.diagnostics.live_data import LiveDataSession
from iran_obd_tuner.diagnostics.models import (
    REPORT_SCHEMA_VERSION,
    DtcCategoryResult,
    PidStatus,
    ReadOutcome,
)
from iran_obd_tuner.diagnostics.service import (
    FREEZE_FRAME_PIDS,
    DiagnosticService,
    read_dtc_categories,
    read_freeze_frame_snapshot,
)
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario


def _report(scenario: MockScenario | None = None, **kwargs):
    service = DiagnosticService(MockObdAdapter(scenario), JsonKnowledgeRepository())
    return service.collect_report(**kwargs)


def test_default_scenario_report_has_every_section() -> None:
    report = _report()

    monitor = report.monitor_status
    assert monitor is not None and monitor.outcome is ReadOutcome.OK
    assert monitor.status.mil_on and monitor.status.confirmed_dtc_count == 3
    codes = {c.status: [code.code for code in c.codes] for c in report.dtc_categories}
    assert codes == {
        DtcStatus.STORED: ["P0301", "P0135", "P0171"],
        DtcStatus.PENDING: ["P0420"],
        DtcStatus.PERMANENT: ["P0301"],
    }
    assert all(
        code.status is DtcStatus.PENDING for code in report.category(DtcStatus.PENDING).codes
    )
    assert [c.code for c in report.trouble_codes] == ["P0301", "P0135", "P0171"]  # compat
    frame = report.freeze_frame
    assert frame.present and frame.trigger.code == "P0301"
    assert frame.trigger.description == "Cylinder 1 misfire detected"
    assert [r.pid for r in frame.readings] == list(FREEZE_FRAME_PIDS)
    assert frame.reading_for("010C").measurement.value == 2410.0
    assert frame.reading_for("0111").status is PidStatus.UNSUPPORTED  # not stored


def test_an_unsupported_category_does_not_hide_the_others() -> None:
    report = _report(MockScenario.drive_cycle())

    permanent = report.category(DtcStatus.PERMANENT)
    assert permanent.outcome is ReadOutcome.UNSUPPORTED
    assert permanent.codes == ()
    assert "0A" in permanent.detail
    assert report.category(DtcStatus.STORED).outcome is ReadOutcome.OK
    assert report.category(DtcStatus.PENDING).outcome is ReadOutcome.OK  # () = real "none"


def test_read_timeouts_become_no_response_outcomes_not_empty_results() -> None:
    adapter = MockObdAdapter(MockScenario.default())
    adapter.connect()
    adapter._scenario = adapter.scenario.with_failure(FailureMode.READ_TIMEOUT)

    categories = read_dtc_categories(adapter, None)
    frame = read_freeze_frame_snapshot(adapter, None)

    assert {c.outcome for c in categories} == {ReadOutcome.NO_RESPONSE}
    assert frame.outcome is ReadOutcome.NO_RESPONSE and frame.readings == ()


def test_malformed_answers_become_error_outcomes() -> None:
    adapter = MockObdAdapter(MockScenario.default().with_failure(FailureMode.MALFORMED_RESPONSE))
    adapter.connect()

    categories = read_dtc_categories(adapter, None)

    assert {c.outcome for c in categories} == {ReadOutcome.ERROR}


class _LosingAdapter(MockObdAdapter):
    def read_trouble_codes(self, status: DtcStatus) -> tuple[str, ...]:
        if status is DtcStatus.PENDING:
            raise AdapterConnectionError("cable unplugged")
        return super().read_trouble_codes(status)


def test_a_lost_connection_still_aborts() -> None:
    adapter = _LosingAdapter()
    adapter.connect()

    with pytest.raises(AdapterConnectionError):
        read_dtc_categories(adapter, None)


def test_no_stored_freeze_frame_reads_nothing_else() -> None:
    from dataclasses import replace

    scenario = replace(MockScenario.default(), freeze_frame_trigger=None)
    frame = _report(scenario).freeze_frame

    assert frame.outcome is ReadOutcome.OK
    assert frame.present is False and frame.readings == ()


def test_unsupported_sections_on_a_minimal_vehicle() -> None:
    from dataclasses import replace

    scenario = replace(
        MockScenario.default(),
        monitor_status_hex=None,
        pending_codes=None,
        permanent_codes=None,
        supports_freeze_frame=False,
    )
    report = _report(scenario)

    assert report.monitor_status.outcome is ReadOutcome.UNSUPPORTED
    assert report.monitor_status.status is None
    assert report.category(DtcStatus.PENDING).outcome is ReadOutcome.UNSUPPORTED
    assert report.freeze_frame.outcome is ReadOutcome.UNSUPPORTED


def test_freeze_frame_can_be_skipped() -> None:
    assert _report(read_freeze_frame=False).freeze_frame is None


def test_json_report_schema_version_3() -> None:
    payload = json.loads(json.dumps(_report().as_dict()))

    assert payload["schema_version"] == REPORT_SCHEMA_VERSION == 3
    assert payload["unanswered_pids"] == []  # 3: timeouts are listed apart from unsupported
    assert payload["monitor_status"]["mil_on"] is True
    assert payload["monitor_status"]["outcome"] == "ok"
    assert [c["status"] for c in payload["dtc_categories"]] == ["stored", "pending", "permanent"]
    assert payload["dtc_categories"][1]["service"] == "07"
    assert payload["trouble_codes"][0]["status"] == "stored"
    unsupported = next(r for r in payload["freeze_frame"]["readings"] if r["pid"] == "0111")
    assert unsupported == {
        "pid": "0111",
        "status": "unsupported",
        "name": None,
        "value": None,  # never a fallback number
        "unit": None,
        "detail": "not stored in the freeze frame",
    }


def test_category_result_refuses_codes_without_success() -> None:
    from iran_obd_tuner.diagnostics.models import TroubleCode

    with pytest.raises(ValueError):
        DtcCategoryResult(
            DtcStatus.PENDING, ReadOutcome.NO_RESPONSE, (TroubleCode.from_code("P0420"),)
        )


def test_live_session_exposes_the_sections() -> None:
    session = LiveDataSession(MockObdAdapter(), JsonKnowledgeRepository())
    session.connect()

    assert session.read_monitor_status().status.mil_on is True
    assert len(session.read_trouble_code_categories()) == 3
    assert session.read_freeze_frame().present is True


def test_mock_contract_for_the_new_reads() -> None:
    adapter = MockObdAdapter(MockScenario.drive_cycle())
    adapter.connect()

    with pytest.raises(UnsupportedOperationError):
        adapter.read_trouble_codes(DtcStatus.PERMANENT)
    with pytest.raises(UnsupportedPidError):
        adapter.read_freeze_frame_value("0106")
    assert adapter.read_monitor_status().confirmed_dtc_count == 2


def test_scenario_round_trip_keeps_the_new_fields() -> None:
    for scenario in (MockScenario.default(), MockScenario.drive_cycle()):
        assert MockScenario.from_dict(scenario.as_dict()) == scenario
    with pytest.raises(ValueError):
        MockScenario(monitor_status_hex="0102")  # not 4 bytes
