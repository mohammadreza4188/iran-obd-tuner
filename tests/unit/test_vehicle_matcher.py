"""Passive vehicle matcher: protocol normalisation, signals, tiers, exclusion, determinism."""

from __future__ import annotations

from pathlib import Path

import pytest

from iran_obd_tuner.core.knowledge import ConfidenceLevel, ObdProtocolId
from iran_obd_tuner.core.models import VehicleInfo
from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase
from iran_obd_tuner.diagnostics.ecu_identification import DidReading, EcuIdentification
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.diagnostics.vehicle_matcher import (
    SIGNAL_WEIGHTS,
    MatchReport,
    MatchTier,
    ObservedFacts,
    SignalKind,
    SignalOutcome,
    normalize_protocol,
    rank_candidates,
)
from iran_obd_tuner.hardware.elm327_protocol import PROTOCOL_NAMES

FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "vehicle_packs"
CAN_11_500 = PROTOCOL_NAMES["6"]


@pytest.fixture(scope="module")
def kb() -> JsonVehicleKnowledgeBase:
    return JsonVehicleKnowledgeBase(FIXTURE_PACKS, include_bundled=False)


def _rank(kb: JsonVehicleKnowledgeBase, facts: ObservedFacts) -> MatchReport:
    return rank_candidates(kb.vehicles(), facts, kb.manufacturers(), knowledge_source=kb.source)


def _signal(report: MatchReport, key: str, signal: SignalKind):
    candidate = report.result_for(key)
    assert candidate is not None
    return next(s for s in candidate.signals if s.signal is signal)


# --------------------------------------------------------------------------- #
# protocol normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("1", ObdProtocolId.SAE_J1850_PWM),
        ("3", ObdProtocolId.ISO_9141_2),
        ("5", ObdProtocolId.ISO_14230_4_KWP_FAST),
        ("6", ObdProtocolId.ISO_15765_4_CAN_11_500),
        ("9", ObdProtocolId.ISO_15765_4_CAN_29_250),
        ("A", ObdProtocolId.SAE_J1939),
    ],
)
def test_every_elm327_protocol_name_normalises(number: str, expected: ObdProtocolId) -> None:
    assert normalize_protocol(PROTOCOL_NAMES[number]) is expected


def test_simulator_spelling_normalises() -> None:
    simulated = "ISO 15765-4 CAN (11-bit ID, 500 kbit/s) [simulated]"
    assert normalize_protocol(simulated) is ObdProtocolId.ISO_15765_4_CAN_11_500


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        PROTOCOL_NAMES["B"],  # user CAN: no canonical id
        "ISO 15765-2 / UDS (ISO 14229-1)",  # a UDS link, not an OBD port protocol
        "CAN",
        "iso 15765-4 can (11 bit id, 500 kbaud)",
    ],
)
def test_unrecognised_protocols_are_never_guessed(text: str | None) -> None:
    assert normalize_protocol(text) is None


def test_family_claims_accept_their_family_only() -> None:
    assert ObdProtocolId.K_LINE.is_consistent_with(ObdProtocolId.ISO_14230_4_KWP_FAST)
    assert not ObdProtocolId.K_LINE.is_consistent_with(ObdProtocolId.ISO_15765_4_CAN_11_500)
    assert not ObdProtocolId.ISO_15765_4_CAN_11_500.is_consistent_with(ObdProtocolId.CAN_ISO15765)


# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #


def test_no_observations_give_no_candidates(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts())
    assert report.candidates == () and report.excluded == ()
    assert report.unmatched_count == 3
    assert any("No observations" in note for note in report.notes)
    assert any("not an identification" in note for note in report.notes)


def test_protocol_alone_is_weak_and_never_identifying(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(active_protocol=CAN_11_500))
    keys = [c.key for c in report.candidates]
    assert keys == ["fixturemotors.alpha/can"]
    assert report.candidates[0].tier is MatchTier.WEAK
    assert report.candidates[0].score == pytest.approx(0.10 * 0.8)
    # a documented specific claim on another CAN flavour excludes nothing here (reported)
    beta = _signal(report, "fixturemotors.beta/default", SignalKind.PHYSICAL_LAYER)
    assert beta.outcome is SignalOutcome.CONFLICT and beta.confidence is ConfidenceLevel.REPORTED
    assert "fixturemotors.beta/default" not in [c.key for c in report.excluded]


def test_conflict_with_a_documented_claim_excludes(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(active_protocol=PROTOCOL_NAMES["5"]))  # KWP fast init
    excluded = {c.key: c for c in report.excluded}
    assert "fixturemotors.alpha/can" in excluded
    assert "documented" in (excluded["fixturemotors.alpha/can"].excluded_reason or "")
    # the k_line family claim agrees with KWP fast init
    assert [c.key for c in report.candidates] == ["fixturemotors.alpha/kline"]


def test_ecu_identification_makes_a_strong_candidate(kb: JsonVehicleKnowledgeBase) -> None:
    facts = ObservedFacts(
        active_protocol=CAN_11_500,
        vin="ZFX12345678901234",
        part_number="SIM-PART-0001",
        software_version="SIM-SW-1.0",
        system_name="simulated ecu",
        supported_pids=("010C", "010D", "0105"),
    )
    report = _rank(kb, facts)
    best = report.best
    assert best is not None and best.key == "fixturemotors.alpha/can"
    assert best.tier is MatchTier.STRONG
    expected = (
        0.30 * 0.6  # WMI reported
        + 0.30 * 0.6  # part number reported
        + 0.15 * 0.3  # software unverified
        + 0.10 * 0.6  # system name reported
        + 0.10 * 0.8  # physical layer documented
        + 0.05 * 0.6  # PIDs reported
    )
    assert best.score == pytest.approx(round(expected, 4))
    assert sum(SIGNAL_WEIGHTS.values()) == pytest.approx(1.0)


def test_wmi_alone_is_partial_for_every_variant_of_the_manufacturer(
    kb: JsonVehicleKnowledgeBase,
) -> None:
    report = _rank(kb, ObservedFacts(vin="ZFX12345678901234"))
    assert {c.key for c in report.candidates} == {
        "fixturemotors.alpha/can",
        "fixturemotors.alpha/kline",
        "fixturemotors.beta/default",
    }
    assert {c.tier for c in report.candidates} == {MatchTier.PARTIAL}


def test_other_wmi_conflicts_but_reported_wmi_does_not_exclude(
    kb: JsonVehicleKnowledgeBase,
) -> None:
    report = _rank(kb, ObservedFacts(vin="NAA12345678901234"))
    signal = _signal(report, "fixturemotors.alpha/can", SignalKind.VIN_WMI)
    assert signal.outcome is SignalOutcome.CONFLICT
    assert report.excluded == ()


def test_short_vin_has_no_wmi() -> None:
    assert ObservedFacts(vin="SHORT").wmi is None
    assert ObservedFacts(vin=" zfx12345678901234 ").wmi == "ZFX"


def test_part_number_mismatch_subtracts_but_never_excludes(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(active_protocol=CAN_11_500, part_number="9665000080"))
    signal = _signal(report, "fixturemotors.alpha/can", SignalKind.PART_NUMBER)
    assert signal.outcome is SignalOutcome.CONFLICT
    assert signal.contribution == pytest.approx(-0.30 * 0.6)
    assert report.excluded == ()
    assert report.candidates[0].score == 0.0  # floored, still "weak" (protocol agrees)


def test_unknown_claims_contribute_nothing(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(part_number="SIM-PART-0001"))
    signal = _signal(report, "fixturemotors.alpha/can", SignalKind.PART_NUMBER)
    assert signal.outcome is SignalOutcome.MATCH
    kline = report.result_for("fixturemotors.alpha/kline")
    assert kline is not None and kline.tier is MatchTier.NO_EVIDENCE
    assert _signal(report, "fixturemotors.alpha/kline", SignalKind.PART_NUMBER).outcome is (
        SignalOutcome.NO_CLAIM
    )
    assert report.unmatched_count == 2


def test_supported_pids_need_high_similarity(kb: JsonVehicleKnowledgeBase) -> None:
    close = _rank(kb, ObservedFacts(supported_pids=("010C", "010D", "0105", "010F", "0111")))
    far = _rank(kb, ObservedFacts(supported_pids=("010C", "0111", "0104", "0106")))
    assert _signal(close, "fixturemotors.alpha/can", SignalKind.SUPPORTED_PIDS).outcome is (
        SignalOutcome.CONFLICT  # 3/5 = 0.6 < 0.8
    )
    exact = _rank(kb, ObservedFacts(supported_pids=("0105", "010C", "010D")))
    assert _signal(exact, "fixturemotors.alpha/can", SignalKind.SUPPORTED_PIDS).outcome is (
        SignalOutcome.MATCH
    )
    assert far.candidates == ()


def test_ranking_is_deterministic(kb: JsonVehicleKnowledgeBase) -> None:
    facts = ObservedFacts(vin="ZFX12345678901234", active_protocol=CAN_11_500)
    first = _rank(kb, facts).as_dict()
    for _ in range(5):
        assert _rank(kb, facts).as_dict() == first
    keys = [c["vehicle_id"] + "/" + c["variant_id"] for c in first["candidates"]]
    assert keys[0] == "fixturemotors.alpha/can"  # WMI + protocol beats WMI alone
    assert keys[1:] == ["fixturemotors.alpha/kline", "fixturemotors.beta/default"]  # id order


def test_simulated_facts_are_labelled(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(active_protocol=CAN_11_500, simulated=True))
    assert report.simulated
    assert report.as_dict()["simulated"] is True
    assert any("SIMULATED" in note for note in report.notes)


def test_unrecognised_protocol_is_reported_not_compared(kb: JsonVehicleKnowledgeBase) -> None:
    report = _rank(kb, ObservedFacts(active_protocol="Some adapter text"))
    assert any("not recognised" in note for note in report.notes)
    assert report.candidates == ()


def test_bundled_knowledge_base_honestly_yields_no_evidence_for_a_can_car() -> None:
    kb = JsonVehicleKnowledgeBase()
    report = _rank(
        kb,
        ObservedFacts(active_protocol=CAN_11_500, vin="NAAN11CA0KK123456", part_number="X"),
    )
    assert report.candidates == () and report.excluded == ()
    assert any("need field evidence" in note for note in report.notes)


# --------------------------------------------------------------------------- #
# building facts
# --------------------------------------------------------------------------- #


def _identification(simulated: bool = True) -> EcuIdentification:
    def ok(did: int, name: str, text: str) -> DidReading:
        return DidReading(
            did, name, ReadOutcome.OK, raw=text.encode(), text=text, session="default"
        )

    return EcuIdentification(
        interface="virtual-sim",
        channel=None,
        request_id=0x7E0,
        response_id=0x7E8,
        simulated=simulated,
        dids=(
            ok(0xF190, "vin", "ZFX12345678901234"),
            ok(0xF187, "spare_part_number", "SIM-PART-0001"),
            DidReading(0xF189, "ecu_software_version", ReadOutcome.UNSUPPORTED, detail="NRC 31"),
            ok(0xF197, "system_name", "SIMULATED ECU"),
        ),
        dtc_outcome=ReadOutcome.UNSUPPORTED,
    )


def test_facts_from_a_session() -> None:
    facts = ObservedFacts.from_session(
        vehicle_info=VehicleInfo(protocol=CAN_11_500, adapter_name="elm327"),
        supported_pids=("010c", "bogus", "010D"),
        identification=_identification(),
    )
    assert facts.vin == "ZFX12345678901234"  # from F190 because OBD had none
    assert facts.part_number == "SIM-PART-0001"
    assert facts.software_version is None  # unsupported reads are not facts
    assert facts.system_name == "SIMULATED ECU"
    assert facts.supported_pids == ("010C", "010D")
    assert facts.uds_request_id == 0x7E0
    assert facts.simulated  # the identification was simulated
    assert facts.as_dict()["protocol_id"] == "iso_15765_4_can_11_500"


def test_facts_from_saved_documents() -> None:
    report = {
        "vehicle": {"vin": "ZFX12345678901234", "protocol": CAN_11_500, "simulated": False},
        "adapter": {"simulated": False},
        "supported_pids": ["010C"],
    }
    uds = _identification(simulated=False).as_dict()
    facts = ObservedFacts.from_documents(report, uds)
    assert facts.protocol_id is ObdProtocolId.ISO_15765_4_CAN_11_500
    assert facts.part_number == "SIM-PART-0001" and facts.uds_response_id == 0x7E8
    assert not facts.simulated
    with pytest.raises(ValueError, match="not a diagnostic report"):
        ObservedFacts.from_documents({"kind": "x"})
    with pytest.raises(ValueError, match="not a UDS identification"):
        ObservedFacts.from_documents(None, {"kind": "diagnostic_report"})
