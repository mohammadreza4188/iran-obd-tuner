"""Core knowledge models: confidence ranks, protocol taxonomy, claim rows and formatting."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.knowledge import (
    ALLOWED_SOURCE_KINDS,
    Claim,
    ConfidenceLevel,
    DidSpec,
    EcuDiagnosticProtocol,
    EcuProfile,
    EvidenceRef,
    LocalizedText,
    ManufacturerPidSpec,
    ObdProtocolId,
    ProtocolFamily,
    SourceKind,
    VehicleVariant,
    YearRange,
    claim_rows,
    format_claim_value,
)


def test_confidence_levels_are_ordered() -> None:
    ranks = [level.rank for level in ConfidenceLevel]
    assert ranks == sorted(ranks, reverse=True) == [4, 3, 2, 1, 0]
    factors = [level.match_factor for level in ConfidenceLevel]
    assert factors == sorted(factors, reverse=True)
    assert ConfidenceLevel.UNKNOWN.match_factor == 0.0


def test_only_field_evidence_can_verify() -> None:
    assert ALLOWED_SOURCE_KINDS[ConfidenceLevel.VERIFIED] == {SourceKind.FIELD_EVIDENCE}
    assert ALLOWED_SOURCE_KINDS[ConfidenceLevel.UNKNOWN] == frozenset()
    for level, kinds in ALLOWED_SOURCE_KINDS.items():
        if level is not ConfidenceLevel.VERIFIED:
            assert SourceKind.FIELD_EVIDENCE not in kinds


def test_verified_claim_carries_evidence() -> None:
    claim = Claim(
        "iso_15765_4_can_11_500",
        ConfidenceLevel.VERIFIED,
        source="src.field",
        evidence=EvidenceRef("evidence/feb.json", "a" * 64),
    )
    assert claim.is_known and claim.evidence is not None
    unknown = Claim.unknown("verify.x", notes="n")
    assert not unknown.is_known and unknown.notes == "n"


def test_protocol_taxonomy() -> None:
    can = ObdProtocolId.ISO_15765_4_CAN_29_250
    assert can.family is ProtocolFamily.CAN_ISO15765
    assert (can.can_id_bits, can.can_bitrate_bps) == (29, 250_000)
    assert ObdProtocolId.ISO_9141_2.can_bitrate_bps is None
    assert ObdProtocolId.CAN_ISO15765.is_family_level
    assert ObdProtocolId.CAN_ISO15765.can_bitrate_bps is None
    assert not ObdProtocolId.SAE_J1939.is_consistent_with(ObdProtocolId.ISO_15765_4_CAN_29_250)


def test_year_range_and_localized_text() -> None:
    with pytest.raises(ValueError):
        YearRange(None, None)
    with pytest.raises(ValueError):
        YearRange(2020, 2010)
    text = LocalizedText(en="Pars", fa=None)
    assert text.get("fa") == "Pars"
    assert LocalizedText("Pars", "پارس").get("fa") == "پارس"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("engine", None, "unknown"),
        ("model_years", YearRange(2000, None), "2000-"),
        ("model_years", YearRange(None, 2010), "?-2010"),
        ("dids", DidSpec(0xF190, "vin"), "F190 vin"),
        ("manufacturer_pids", ManufacturerPidSpec(0x21, 0x01, "x"), "21 01 x"),
        ("manufacturer_pids", ManufacturerPidSpec(0x22, 0x1234, "y"), "22 1234 y"),
        ("request_id", 0x7E0, "0x7E0"),
        ("can_bitrate_bps", 500_000, "500000 bit/s"),
        ("id_bits", 11, "11-bit"),
        ("obd_physical_layer", ObdProtocolId.K_LINE, "k_line"),
    ],
)
def test_format_claim_value(field: str, value: object, expected: str) -> None:
    assert format_claim_value(field, value) == expected


def test_claim_rows_cover_every_field_and_mark_empty_lists() -> None:
    unknown = Claim.unknown("verify.x")
    variant = VehicleVariant(
        id="v",
        names=LocalizedText("V", "و"),
        engine=Claim("E", ConfidenceLevel.REPORTED, source="s"),
        model_years=unknown,
        obd_physical_layer=unknown,
        can_bitrate_bps=unknown,
        supported_pids=(),
        ecus=(
            EcuProfile(
                role="engine",
                family_candidates=(),
                supplier=unknown,
                diagnostic_protocol=Claim(
                    EcuDiagnosticProtocol.UDS_ISO14229, ConfidenceLevel.DOCUMENTED, source="m"
                ),
                request_id=unknown,
                response_id=unknown,
                id_bits=unknown,
            ),
        ),
    )
    rows = claim_rows(variant)
    assert [r.field for r in rows][:4] == [
        "engine",
        "model_years",
        "obd_physical_layer",
        "can_bitrate_bps",
    ]
    assert len(rows) == 5 + 11
    empty = [r for r in rows if r.claim is None]
    assert {r.field for r in empty} >= {"supported_pids", "family_candidates", "dids"}
    assert all(r.confidence is ConfidenceLevel.UNKNOWN for r in empty)
    assert {r.section for r in rows} == {"vehicle", "obd_port", "ecu:engine"}
