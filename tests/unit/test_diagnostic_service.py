"""The diagnostic workflow: one read-only session, start to report."""

from __future__ import annotations

import json

import pytest

from iran_obd_tuner.core.exceptions import AdapterTimeoutError
from iran_obd_tuner.core.models import Unit
from iran_obd_tuner.database.repository import JsonKnowledgeRepository
from iran_obd_tuner.diagnostics.models import DiagnosticReport, TroubleCode
from iran_obd_tuner.diagnostics.service import DiagnosticService
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario


@pytest.fixture
def service(adapter: MockObdAdapter, repository: JsonKnowledgeRepository) -> DiagnosticService:
    return DiagnosticService(adapter, repository)


def test_report_describes_the_simulated_vehicle(service: DiagnosticService) -> None:
    report = service.collect_report()

    assert isinstance(report, DiagnosticReport)
    assert report.vehicle.simulated is True
    assert report.vehicle.adapter_name == "mock"
    assert report.vehicle.vin == "SIMULATEDVIN00001"
    assert report.read_only is True
    assert report.duration_s >= 0.0


def test_measurements_use_the_configured_parameters(service: DiagnosticService) -> None:
    report = service.collect_report()

    assert [m.pid for m in report.measurements] == list(service.live_data_pids)
    speed = report.measurement_for("010D")
    assert speed is not None
    assert speed.unit is Unit.KILOMETRE_PER_HOUR
    assert report.measurement_for("015C") is None


def test_requested_parameters_can_be_overridden(service: DiagnosticService) -> None:
    report = service.collect_report(pids=["010c", "0142"])

    assert [m.pid for m in report.measurements] == ["010C", "0142"]


def test_unsupported_parameters_are_reported_not_hidden(service: DiagnosticService) -> None:
    report = service.collect_report(pids=["010C", "015C"])

    assert report.skipped_pids == ("015C",)
    assert "010C" in report.supported_pids


def test_trouble_codes_are_described_from_the_knowledge_database(
    service: DiagnosticService,
) -> None:
    report = service.collect_report()

    codes = [code.code for code in report.trouble_codes]
    assert codes == ["P0301", "P0135", "P0171"]
    first = report.trouble_codes[0]
    assert first.is_known is True
    assert first.description == "Cylinder 1 misfire detected"
    assert first.display_text.startswith("P0301 — Cylinder 1 misfire")
    assert report.knowledge_source is not None
    assert report.knowledge_metadata["authoritative"] is False


def test_unknown_trouble_codes_stay_unknown(repository: JsonKnowledgeRepository) -> None:
    scenario = MockScenario.default()
    adapter_with_unknown_code = MockObdAdapter(
        MockScenario(
            name="unknown-code",
            readings=scenario.readings,
            trouble_codes=("P1234",),
        )
    )
    service = DiagnosticService(adapter_with_unknown_code, repository)

    report = service.collect_report()

    code = report.trouble_codes[0]
    assert code.code == "P1234"
    assert code.is_known is False
    assert "description unknown" in code.display_text


def test_workflow_without_a_knowledge_repository(adapter: MockObdAdapter) -> None:
    report = DiagnosticService(adapter).collect_report()

    assert report.knowledge_source is None
    assert all(not code.is_known for code in report.trouble_codes)


def test_vin_can_be_skipped(service: DiagnosticService) -> None:
    report = service.collect_report(read_vin=False)

    assert report.vehicle.vin is None


def test_connection_is_closed_after_the_session(service: DiagnosticService) -> None:
    service.collect_report()

    assert service.adapter.is_connected is False


def test_connection_failure_is_reported_and_leaves_no_open_connection(
    fixture_scenario: MockScenario,
) -> None:
    adapter = MockObdAdapter(fixture_scenario.with_failure(FailureMode.CONNECT_TIMEOUT))
    service = DiagnosticService(adapter)

    with pytest.raises(AdapterTimeoutError):
        service.collect_report()

    assert adapter.is_connected is False


def test_describe_trouble_code_rejects_malformed_codes(service: DiagnosticService) -> None:
    with pytest.raises(ValueError, match="invalid diagnostic trouble code"):
        service.describe_trouble_code("nope")


def test_trouble_code_without_definition_is_constructible() -> None:
    code = TroubleCode.from_code("P0000")

    assert code.is_known is False
    assert code.code == "P0000"


def test_report_is_json_serialisable(service: DiagnosticService) -> None:
    payload = service.collect_report().as_dict()

    text = json.dumps(payload)
    assert '"read_only": true' in text
    assert payload["adapter"]["kind"] == "mock"
    assert payload["vehicle"]["simulated"] is True
