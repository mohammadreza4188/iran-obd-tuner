"""End-to-end checks of the command line interface (in process, no hardware)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iran_obd_tuner import __version__
from iran_obd_tuner.app.cli import main

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_version_works_without_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_adapters_lists_status(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["adapters"]) == 0
    out = capsys.readouterr().out
    assert "declared adapters: 3 (2 implemented)" in out
    assert "elm327" in out
    assert "NOT implemented" in out


def test_config_prints_resolved_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "adapter = mock" in out
    assert "config_file = None" in out


def test_diagnose_prints_a_human_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose"]) == 0
    out = capsys.readouterr().out
    assert "vehicle (SIMULATED)" in out
    assert "P0301 — Cylinder 1 misfire detected" in out
    assert "mode: read-only" in out
    assert "knowledge database:" in out


def test_diagnose_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["vehicle"]["simulated"] is True
    assert payload["read_only"] is True
    assert len(payload["measurements"]) == 6
    assert payload["trouble_codes"][0]["code"] == "P0301"


def test_diagnose_writes_a_report_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "reports" / "run.json"

    assert main(["diagnose", "--json", "--output", str(target)]) == 0

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["adapter"]["name"] == "mock"
    assert "report written to" in capsys.readouterr().out


def test_diagnose_accepts_an_explicit_parameter_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--pids", "010C,010D", "--no-vin"]) == 0
    out = capsys.readouterr().out
    assert "vin=unknown" in out
    assert "010C" in out
    assert "0111  Throttle position" not in out  # not requested as a measurement


def test_declared_but_unimplemented_adapter_fails_with_a_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["diagnose", "--adapter", "can-bench"]) == 1
    assert "not implemented" in capsys.readouterr().err


def test_elm327_without_a_port_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--adapter", "elm327"]) == 2
    assert "explicit serial port" in capsys.readouterr().err


def test_unknown_adapter_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--adapter", "obdlink"]) == 2
    assert "unknown adapter" in capsys.readouterr().err


def test_invalid_parameter_list_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--pids", "010C,zzz"]) == 2
    assert "invalid parameter identifier" in capsys.readouterr().err


def test_missing_configuration_file_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(tmp_path / "absent.toml"), "diagnose"]) == 2
    assert "not found" in capsys.readouterr().err


def test_configuration_file_is_honoured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config").mkdir()
    config = tmp_path / "config" / "local.toml"
    config.write_text('live_data_pids = ["010C"]\nlog_level = "WARNING"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["diagnose", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [m["pid"] for m in payload["measurements"]] == ["010C"]


def test_diagnose_prints_mil_categories_and_freeze_frame(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["diagnose"]) == 0
    out = capsys.readouterr().out

    assert "MIL (check engine): ON; confirmed codes: 3" in out
    assert "pending trouble codes (service 07) (1):" in out
    assert "permanent trouble codes (service 0A) (1):" in out
    assert "freeze frame 00 (trigger P0301):" in out
    assert "(unsupported)" in out  # throttle is not in the simulated freeze frame


def test_diagnose_no_live_reads_no_live_parameters(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--no-live", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["measurements"] == []
    assert payload["unanswered_pids"] == [] and payload["skipped_pids"] == []
    assert payload["supported_pids"]  # still known from the connect-time bitmasks
    assert payload["dtc_categories"] and payload["monitor_status"] is not None
    assert payload["adapter"]["simulated"] is True


def test_diagnose_no_live_and_pids_are_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose", "--no-live", "--pids", "010C"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
