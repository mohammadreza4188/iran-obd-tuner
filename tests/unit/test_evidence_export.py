"""Field Evidence Bundle: contents, VIN redaction (fail closed), integrity and promotion rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iran_obd_tuner.core.exceptions import EvidenceExportError
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase
from iran_obd_tuner.diagnostics.ecu_identification import DidReading, EcuIdentification
from iran_obd_tuner.diagnostics.evidence_export import (
    REDACTED_VIN_RESPONSE,
    OperatorContext,
    build_evidence_bundle,
    content_sha256,
    load_bundle,
    mask_vin,
    observed_values,
    reconstruct_pid_bitmasks,
    verify_bundle,
    write_bundle,
)
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.hardware.elm327_protocol import PROTOCOL_NAMES

VIN = "ZFX1234567AB98765"
SERIAL_HEX = "AB98765"[-6:].encode().hex().upper()  # "B98765"
FIXED = datetime(2026, 11, 21, 10, 30, tzinfo=UTC)
FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "vehicle_packs"


def _clock() -> datetime:
    return FIXED


def _identification(simulated: bool = False) -> EcuIdentification:
    def ok(did: int, name: str, text: str) -> DidReading:
        return DidReading(
            did, name, ReadOutcome.OK, raw=text.encode(), text=text, session="default"
        )

    return EcuIdentification(
        interface="pcan",
        channel="PCAN_USBBUS1",
        request_id=0x7E0,
        response_id=0x7E8,
        simulated=simulated,
        dids=(
            ok(0xF190, "vin", VIN),
            ok(0xF187, "spare_part_number", "SIM-PART-0001"),
            ok(0xF189, "ecu_software_version", "SIM-SW-1.0"),
            DidReading(0xF191, "ecu_hardware_number", ReadOutcome.NO_RESPONSE, detail="timeout"),
        ),
        dtc_outcome=ReadOutcome.OK,
        timestamp=FIXED,
    )


def _traffic() -> TrafficLog:
    log = TrafficLog(clock=lambda: 1.0, wall_clock=_clock)
    add = log.append
    # ELM327: VIN reply in one RX line (hex) plus an ASCII echo in an INFO note
    add(Direction.TX, "0902", adapter="elm327", simulated=False)
    add(
        Direction.RX,
        "49 02 01 5A 46 58 31⏎49 02 02 32 33 34 35⏎49 02 03 42 39 38 37 36 35",
        adapter="elm327",
        simulated=False,
    )
    add(Direction.TX, "010C", adapter="elm327", simulated=False)
    add(Direction.RX, "41 0C 0B B8", adapter="elm327", simulated=False)
    add(Direction.INFO, f"note mentions {VIN.lower()}", adapter="elm327", simulated=False)
    # UDS: 22 F1 90 multi-frame with flow control
    add(Direction.TX, "7E0  03 22 F1 90 CC CC CC CC", adapter="can:pcan", simulated=False)
    add(Direction.RX, "7E8  03 7F 22 78 CC CC CC CC", adapter="can:pcan", simulated=False)
    add(Direction.RX, "7E8  10 14 62 F1 90 5A 46 58", adapter="can:pcan", simulated=False)
    add(Direction.TX, "7E0  30 00 00 CC CC CC CC CC", adapter="can:pcan", simulated=False)
    add(Direction.RX, "7E8  21 31 32 33 34 35 36 37", adapter="can:pcan", simulated=False)
    add(Direction.RX, "7E8  22 41 42 39 38 37 36 35", adapter="can:pcan", simulated=False)
    add(Direction.TX, "7E0  03 22 F1 87 CC CC CC CC", adapter="can:pcan", simulated=False)
    add(Direction.RX, "7E8  10 10 62 F1 87 53 49 4D", adapter="can:pcan", simulated=False)
    return log


def _bundle(**overrides):
    kwargs = {
        "tool_version": "0.1.0",
        "vehicle_info": VehicleInfo(
            vin=VIN, protocol=PROTOCOL_NAMES["6"], adapter_name="elm327", vin_source="obd_09"
        ),
        "supported_pids": ("010C", "010D", "0105", "0142"),
        "identification": _identification(),
        "traffic": _traffic().snapshot(),
        "clock": _clock,
    }
    kwargs.update(overrides)
    return build_evidence_bundle(**kwargs)


def test_mask_vin_hides_the_serial() -> None:
    assert mask_vin(VIN) == "ZFX1234567A******"
    assert mask_vin("ABC") == "***"


def test_bundle_structure_and_labels() -> None:
    doc = _bundle().document
    assert doc["kind"] == "field_evidence_bundle" and doc["schema_version"] == 1
    assert doc["read_only"] is True and doc["simulated"] is False
    assert doc["usable_for_verification"] is True
    assert doc["bundle_id"].startswith("feb-20261121T103000Z-")
    assert doc["obd"]["protocol_id"] == "iso_15765_4_can_11_500"
    assert doc["obd"]["can_bitrate_bps"] == 500000
    assert doc["obd"]["supported_pids"] == ["0105", "010C", "010D", "0142"]
    assert (
        doc["obd"]["supported_pid_bitmasks"]["derivation"] == "reconstructed_from_decoded_pid_list"
    )
    assert doc["operator_context"]["provenance"] == "operator_reported"
    assert doc["uds"]["dids"][3]["outcome"] == "no_response"
    assert doc["traffic"]["total_given"] == 13 and not doc["traffic"]["truncated"]


def test_vin_is_redacted_everywhere_by_default() -> None:
    bundle = _bundle()
    text = bundle.to_json()
    assert VIN not in text and VIN.lower() not in text
    assert "ZFX1234567A******" in text
    assert bundle.document["vin_redacted"] is True
    entries = [e["text"] for e in bundle.document["traffic"]["entries"]]
    assert entries[1] == REDACTED_VIN_RESPONSE  # ELM327 0902 reply
    assert entries[3] == "41 0C 0B B8"  # other replies untouched
    assert entries[6:8] == [REDACTED_VIN_RESPONSE] * 2  # pending + first frame
    assert entries[8].startswith("7E0  30")  # our flow control stays visible
    assert entries[9:11] == [REDACTED_VIN_RESPONSE] * 2  # consecutive frames
    assert entries[12].startswith("7E8  10 10 62 F1 87")  # next DID is kept
    f190 = bundle.document["uds"]["dids"][0]
    assert f190["raw_hex"] is None and f190["redacted"] is True
    assert SERIAL_HEX not in text.replace(" ", "")


def test_redaction_can_be_turned_off_explicitly() -> None:
    bundle = _bundle(redact_vin=False)
    assert bundle.document["vin_redacted"] is False
    assert VIN in bundle.to_json()


def test_redaction_fails_closed_if_the_serial_survives() -> None:
    log = TrafficLog(clock=lambda: 1.0, wall_clock=_clock)
    # a reply that arrives outside any VIN request window still carries the serial bytes
    log.append(Direction.TX, "7E0  03 22 F1 87 CC CC CC CC", adapter="can:x", simulated=False)
    log.append(Direction.RX, "7E8  07 62 F1 87 42 39 38 37 36 35", adapter="can:x", simulated=False)
    with pytest.raises(EvidenceExportError, match="VIN serial"):
        _bundle(traffic=log.snapshot())


def test_hash_is_deterministic_and_detects_tampering(tmp_path: Path) -> None:
    first, second = _bundle(), _bundle()
    assert first.sha256 == second.sha256 == content_sha256(first.document)
    path = write_bundle(tmp_path / "out" / "bundle.json", first)
    loaded = load_bundle(path)
    assert loaded.bundle_id == first.bundle_id
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["obd"]["protocol_id"] = "k_line"
    with pytest.raises(EvidenceExportError, match="was modified"):
        verify_bundle(tampered)
    with pytest.raises(EvidenceExportError, match="kind"):
        verify_bundle({"kind": "other"})


def test_simulated_input_is_labelled_and_unusable() -> None:
    doc = _bundle(identification=_identification(simulated=True)).document
    assert doc["simulated"] is True and doc["usable_for_verification"] is False
    assert doc["uds"]["simulated"] is True
    assert any("SIMULATED" in note for note in doc["notes"])


def test_nothing_to_export_is_an_error() -> None:
    with pytest.raises(EvidenceExportError, match="nothing to export"):
        build_evidence_bundle(tool_version="0.1.0")


def test_traffic_is_limited_to_the_most_recent_entries() -> None:
    doc = _bundle(traffic_limit=3).document
    assert doc["traffic"]["truncated"] is True
    assert [e["seq"] for e in doc["traffic"]["entries"]] == [11, 12, 13]
    # the VIN window opened before the excerpt and still redacts its last frame
    assert doc["traffic"]["entries"][0]["text"] == REDACTED_VIN_RESPONSE
    assert _bundle(traffic_limit=0).document["traffic"]["entries"] == []


def test_pid_bitmask_reconstruction() -> None:
    assert reconstruct_pid_bitmasks(()) == {}
    words = reconstruct_pid_bitmasks(("0101", "010C", "0120", "0142"))
    assert words["0100"] == f"{(1 << 31) | (1 << 20) | 1:08X}"
    assert words["0120"] == "00000001"  # nothing in 21..40, but a later range is listed
    assert words["0140"] == f"{1 << 30:08X}"  # PID 42
    assert reconstruct_pid_bitmasks(("0141",))["0140"] == f"{1 << 31:08X}"


def test_observed_values_are_machine_checkable() -> None:
    values = observed_values(_bundle().document)
    assert values["obd_physical_layer"] == {"iso_15765_4_can_11_500"}
    assert values["can_bitrate_bps"] == {"500000 bit/s"}
    assert values["request_id"] == {"0x7E0"} and values["response_id"] == {"0x7E8"}
    assert values["part_number"] == {"SIM-PART-0001"}
    assert "F191" not in values["dids"]  # not answered


def test_promotion_candidates_compare_with_the_claimed_variant() -> None:
    kb = JsonVehicleKnowledgeBase(FIXTURE_PACKS, include_bundled=False)
    doc = _bundle(
        operator=OperatorContext("fixturemotors.alpha", "can", "2018", "label: FX"),
        knowledge=kb.vehicles(),
    ).document
    rows = {row["claim"]: row for row in doc["promotion_candidates"]}
    layer = rows["fixturemotors.alpha/can:obd_physical_layer"]
    assert layer["agrees"] is True and layer["current_confidence"] == "documented"
    assert rows["fixturemotors.alpha/can:ecus[engine].request_id"]["agrees"] is True
    part = rows["fixturemotors.alpha/can:ecus[engine].part_number_patterns"]
    assert part["agrees"] is True and part["observed"] == "SIM-PART-0001"
    assert rows["fixturemotors.alpha/can:supported_pids"]["agrees"] is False
    unclaimed = _bundle(operator=OperatorContext("nope", "x"), knowledge=kb.vehicles())
    assert unclaimed.document["promotion_candidates"] == []
