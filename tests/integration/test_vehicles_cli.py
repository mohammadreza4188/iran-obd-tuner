"""``iran-obd-tuner vehicles list|show|match`` (in process, no hardware, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iran_obd_tuner.app.cli import main
from iran_obd_tuner.hardware.elm327_protocol import PROTOCOL_NAMES

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "vehicle_packs"


@pytest.fixture
def with_fixture_packs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRAN_OBD_TUNER_KNOWLEDGE_DIR", str(FIXTURE_PACKS))


def test_list_shows_every_manufacturer_with_confidence_badges(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["vehicles", "list"]) == 0
    out = capsys.readouterr().out
    assert "read-only" in out
    for maker in ("ikco  Iran Khodro (IKCO)", "saipa  SAIPA", "mvm  Modiran"):
        assert maker in out
    assert "ikco.peugeot_pars/tu5" in out
    assert "engine TU5JP4 [REPORTED]" in out
    assert "OBD port unknown [UNKNOWN]" in out
    assert "claims by confidence: verified 0, documented 0" in out
    assert out.isascii()  # the CLI prints ASCII only (Persian names are in --json)


def test_list_filters_by_manufacturer(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "list", "--manufacturer", "saipa"]) == 0
    out = capsys.readouterr().out
    assert "saipa.tiba/m15" in out and "ikco." not in out.split(chr(10), 1)[1]


def test_list_rejects_an_unknown_manufacturer(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "list", "--manufacturer", "nobody"]) == 2
    assert "unknown manufacturer 'nobody'" in capsys.readouterr().err


def test_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "vehicle_knowledge_list" and payload["read_only"] is True
    pars = next(v for v in payload["vehicles"] if v["id"] == "ikco.peugeot_pars")
    assert pars["names"]["fa"] == "پژو پارس"
    claims = {c["field"]: c for c in pars["variants"][0]["claims"]}
    assert claims["obd_physical_layer"]["confidence"] == "unknown"
    assert claims["obd_physical_layer"]["value"] is None
    assert "AT DPN" in claims["obd_physical_layer"]["verify_text"]
    assert claims["supported_pids"]["recorded"] is False
    assert payload["claims_by_confidence"]["verified"] == 0


def test_show_prints_claims_sources_and_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "show", "ikco.peugeot_206"]) == 0
    out = capsys.readouterr().out
    assert "variant type2: 206 Type 2" in out
    assert "TU3" in out and "[REPORTED]" in out and "src.khodro45_206_type2_type5" in out
    assert "verify: Connect a read-only ELM327 adapter" in out
    assert "https://khodro45.com/mag/peugeot-206-type2-vs-type5/ (accessed 2026-11-21)" in out


def test_show_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "show", "saipa.pride", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "vehicle_profile"
    engine = payload["variants"][0]["claims"][0]
    assert engine["field"] == "engine" and engine["confidence"] == "unknown"
    assert "M13" in engine["notes"]  # says the name is unconfirmed instead of claiming it


def test_show_unknown_vehicle_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "show", "ikco.nothing"]) == 2
    assert "unknown vehicle 'ikco.nothing'" in capsys.readouterr().err


def test_broken_local_pack_is_a_usage_error_naming_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "vehicles").mkdir()
    (tmp_path / "vehicles" / "bad.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("IRAN_OBD_TUNER_KNOWLEDGE_DIR", str(tmp_path))
    assert main(["vehicles", "list"]) == 2
    assert "bad.json: $: missing key(s)" in capsys.readouterr().err


def _write_documents(tmp_path: Path) -> tuple[Path, Path]:
    report = tmp_path / "report.json"
    uds = tmp_path / "uds.json"
    report.write_text(
        json.dumps(
            {
                "vehicle": {
                    "vin": "ZFX12345678901234",
                    "protocol": PROTOCOL_NAMES["6"],
                    "simulated": False,
                },
                "adapter": {"simulated": False},
                "supported_pids": ["010C", "010D", "0105"],
            }
        ),
        encoding="utf-8",
    )
    uds.write_text(
        json.dumps(
            {
                "kind": "uds_ecu_identification",
                "simulated": False,
                "request_id": "7E0",
                "response_id": "7E8",
                "dids": [{"did": "F187", "outcome": "ok", "text": "SIM-PART-0001"}],
            }
        ),
        encoding="utf-8",
    )
    return report, uds


def test_match_ranks_fixture_profiles(
    with_fixture_packs: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, uds = _write_documents(tmp_path)
    assert main(["vehicles", "match", "--report", str(report), "--uds", str(uds)]) == 0
    out = capsys.readouterr().out
    assert "1. fixturemotors.alpha/can  Fixture Alpha - Alpha CAN  PARTIAL score 0.470" in out
    assert "supported_pids 0105,010C,010D" in out
    assert "part_number      match" in out
    assert "not an identification" in out


def test_match_json_and_documents_from_the_cli_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real 'diagnose --output' file (simulated adapter) is accepted and labelled."""
    report = tmp_path / "diag.json"
    assert main(["diagnose", "--output", str(report)]) == 0
    capsys.readouterr()
    assert main(["vehicles", "match", "--report", str(report), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "vehicle_match_report"
    assert payload["simulated"] is True
    assert payload["candidates"] == []  # the bundled packs have no comparable claims yet


def test_match_needs_a_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vehicles", "match"]) == 2
    assert "--report and/or --uds" in capsys.readouterr().err


def test_match_rejects_the_wrong_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"kind": "something"}), encoding="utf-8")
    assert main(["vehicles", "match", "--uds", str(path)]) == 2
    assert "not a UDS identification" in capsys.readouterr().err
    assert main(["vehicles", "match", "--report", str(tmp_path / "missing.json")]) == 2
