"""Vehicle knowledge packs: bundled data integrity, honesty rules and schema rejections."""

from __future__ import annotations

import copy
import json
import logging
import tomllib
from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from iran_obd_tuner.core.exceptions import VehicleKnowledgeError
from iran_obd_tuner.core.knowledge import (
    ALLOWED_SOURCE_KINDS,
    Claim,
    ConfidenceLevel,
    SourceKind,
    claim_rows,
)
from iran_obd_tuner.database.vehicle_schema import DISGUISED_UNKNOWNS
from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase

FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "vehicle_packs"
FIXTURE_PACK = FIXTURE_PACKS / "vehicles" / "fixturemotors.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Fields whose value says something about the vehicle's bus or ECU (never from memory).
BUS_AND_ECU_FIELDS = {
    "obd_physical_layer",
    "can_bitrate_bps",
    "family_candidates",
    "supplier",
    "diagnostic_protocol",
    "request_id",
    "response_id",
    "id_bits",
    "part_number_patterns",
    "software_version_patterns",
    "system_name_patterns",
    "dids",
    "manufacturer_pids",
    "supported_pids",
}


@pytest.fixture(scope="module")
def bundled() -> JsonVehicleKnowledgeBase:
    return JsonVehicleKnowledgeBase()


def _all_claims(kb: JsonVehicleKnowledgeBase) -> Iterator[tuple[str, str, Claim[Any]]]:
    for manufacturer in kb.manufacturers():
        for claim in manufacturer.wmi:
            yield manufacturer.id, "wmi", claim
    for vehicle in kb.vehicles():
        for variant in vehicle.variants:
            for row in claim_rows(variant):
                if row.claim is not None:
                    yield f"{vehicle.id}/{variant.id}", row.field, row.claim


# --------------------------------------------------------------------------- #
# bundled data
# --------------------------------------------------------------------------- #


def test_bundled_packs_cover_the_milestone_vehicles(bundled: JsonVehicleKnowledgeBase) -> None:
    assert [m.id for m in bundled.manufacturers()] == ["ikco", "mvm", "saipa"]
    variants = {f"{v.id}/{x.id}" for v in bundled.vehicles() for x in v.variants}
    assert {
        "ikco.peugeot_pars/tu5",
        "ikco.peugeot_pars/xu7",
        "ikco.peugeot_206/type2",
        "ikco.peugeot_206/type5",
        "ikco.samand/ef7",
        "ikco.dena/ef7",
        "saipa.pride/default",
        "saipa.tiba/m15",
        "saipa.saina/m15",
        "saipa.quick/m15",
        "mvm.315/default",
        "mvm.x22/default",
    } <= variants
    assert bundled.source.startswith("bundled:ikco.json")


def test_packs_ship_as_package_data() -> None:
    names = {
        e.name for e in files("iran_obd_tuner.database").joinpath("data", "vehicles").iterdir()
    }
    assert {"ikco.json", "saipa.json", "mvm.json", "guidance.json"} <= names
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        "data/vehicles/*.json"
        in pyproject["tool"]["setuptools"]["package-data"]["iran_obd_tuner.database"]
    )


def test_every_bundled_claim_keeps_the_provenance_rules(bundled: JsonVehicleKnowledgeBase) -> None:
    sources = {source.id: source for v in bundled.vehicles() for source in v.sources.values()} | {
        source.id: source for m in bundled.manufacturers() for source in m.sources.values()
    }
    for where, field, claim in _all_claims(bundled):
        if claim.confidence is ConfidenceLevel.UNKNOWN:
            assert claim.value is None and claim.source is None, (where, field)
            assert bundled.guidance(claim.verify, "en"), (where, field)
            continue
        assert claim.source in sources, (where, field)
        kind = sources[claim.source].kind
        assert kind in ALLOWED_SOURCE_KINDS[claim.confidence], (where, field)
        assert kind is not SourceKind.STANDARD, (where, field)
        if isinstance(claim.value, str):
            assert claim.value.strip().lower() not in DISGUISED_UNKNOWNS, (where, field)


def test_nothing_bundled_is_verified_without_a_field_evidence_bundle(
    bundled: JsonVehicleKnowledgeBase,
) -> None:
    for where, field, claim in _all_claims(bundled):
        if claim.confidence is ConfidenceLevel.VERIFIED:
            assert claim.evidence is not None, (where, field)
            assert (REPO_ROOT / claim.evidence.bundle).is_file(), (where, field)


def test_bus_and_ecu_facts_are_not_claimed_from_memory(bundled: JsonVehicleKnowledgeBase) -> None:
    """No bundled bus/ECU fact above 'unverified' unless a field evidence bundle backs it."""
    for where, field, claim in _all_claims(bundled):
        if field in BUS_AND_ECU_FIELDS and claim.confidence is not ConfidenceLevel.VERIFIED:
            assert claim.confidence.rank <= ConfidenceLevel.UNVERIFIED.rank, (where, field)


def test_peugeot_pars_tu5_has_no_assumed_ecu_or_can_identifier(
    bundled: JsonVehicleKnowledgeBase,
) -> None:
    """CLAUDE.md: a generic ECU model or CAN id must not be treated as confirmed for it."""
    variant = bundled.variant("ikco.peugeot_pars", "tu5")
    assert variant is not None
    assert variant.engine.value == "TU5JP4"
    assert variant.obd_physical_layer.confidence is ConfidenceLevel.UNKNOWN
    assert variant.can_bitrate_bps.confidence is ConfidenceLevel.UNKNOWN
    (ecu,) = variant.ecus
    assert ecu.family_candidates == ()
    for claim in (ecu.request_id, ecu.response_id, ecu.id_bits, ecu.diagnostic_protocol):
        assert claim.confidence is ConfidenceLevel.UNKNOWN
    assert ecu.dids == () and ecu.manufacturer_pids == ()


def test_wmi_codes_are_not_taken_from_memory(bundled: JsonVehicleKnowledgeBase) -> None:
    for manufacturer in bundled.manufacturers():
        assert manufacturer.wmi == (), manufacturer.id


def test_bundled_sources_are_online_and_dated(bundled: JsonVehicleKnowledgeBase) -> None:
    for vehicle in bundled.vehicles():
        for source in vehicle.sources.values():
            assert source.url is not None and source.url.startswith("https://")
            assert source.accessed is not None


def test_guidance_is_bilingual(bundled: JsonVehicleKnowledgeBase) -> None:
    catalog = bundled.guidance_catalog()
    assert "verify.obd_protocol_atdpn" in catalog
    for key, text in catalog.items():
        assert text.en.isascii() and text.fa, key
    assert bundled.guidance("verify.uds_ident", "fa") != bundled.guidance("verify.uds_ident", "en")
    assert bundled.guidance(None) is None
    assert bundled.guidance("verify.does_not_exist") is None


def test_names_are_bilingual_and_lookups_work(bundled: JsonVehicleKnowledgeBase) -> None:
    pars = bundled.vehicle("ikco.peugeot_pars")
    assert pars is not None
    assert pars.names.get("en") == "Peugeot Pars"
    assert pars.names.get("fa") == "پژو پارس"
    assert bundled.vehicles("saipa") and all(
        v.manufacturer_id == "saipa" for v in bundled.vehicles("saipa")
    )
    assert bundled.vehicle("nope.nothing") is None
    assert bundled.variant("ikco.peugeot_pars", "nope") is None
    assert bundled.manufacturer("ikco") is not None


# --------------------------------------------------------------------------- #
# local packs
# --------------------------------------------------------------------------- #


def test_fixture_pack_loads_on_its_own() -> None:
    kb = JsonVehicleKnowledgeBase(FIXTURE_PACKS, include_bundled=False)
    assert [m.id for m in kb.manufacturers()] == ["fixturemotors"]
    alpha = kb.variant("fixturemotors.alpha", "can")
    assert alpha is not None
    assert alpha.ecus[0].request_id.value == 0x7E0
    assert alpha.ecus[0].manufacturer_pids[0].value.service == 0x22


def test_local_pack_adds_to_bundled() -> None:
    kb = JsonVehicleKnowledgeBase(FIXTURE_PACKS)
    assert {"ikco", "fixturemotors"} <= {m.id for m in kb.manufacturers()}


def test_local_pack_replaces_a_bundled_vehicle_and_logs_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pack = json.loads(FIXTURE_PACK.read_text(encoding="utf-8"))
    pack["manufacturer"]["id"] = "ikco"
    pack["vehicles"] = [pack["vehicles"][1]]
    pack["vehicles"][0]["id"] = "ikco.peugeot_pars"
    pack["sources"] = [s for s in pack["sources"] if s["id"] == "src.fixture_reference"]
    pack["manufacturer"]["wmi"] = []
    _write(tmp_path, pack)
    # configure_logging() (run by CLI tests) stops propagation to the root logger, so the
    # capture handler is attached to the module logger itself.
    logger = logging.getLogger("iran_obd_tuner.database.vehicles")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            kb = JsonVehicleKnowledgeBase(tmp_path)
    finally:
        logger.removeHandler(caplog.handler)
    pars = kb.vehicle("ikco.peugeot_pars")
    assert pars is not None and pars.variants[0].engine.value == "FX20"
    assert "replaces vehicle ikco.peugeot_pars" in caplog.text


def test_missing_vehicles_folder_means_bundled_only(tmp_path: Path) -> None:
    kb = JsonVehicleKnowledgeBase(tmp_path)
    assert kb.vehicle("ikco.peugeot_pars") is not None


# --------------------------------------------------------------------------- #
# schema rejections
# --------------------------------------------------------------------------- #


def _write(root: Path, pack: Any, name: str = "pack.json") -> None:
    folder = root / "vehicles"
    folder.mkdir(parents=True, exist_ok=True)
    text = pack if isinstance(pack, str) else json.dumps(pack, ensure_ascii=False)
    (folder / name).write_text(text, encoding="utf-8")


def _alpha(pack: dict[str, Any]) -> dict[str, Any]:
    return pack["vehicles"][0]["variants"][0]


def _ecu(pack: dict[str, Any]) -> dict[str, Any]:
    return _alpha(pack)["ecus"][0]


def _add_source(pack: dict[str, Any], kind: str) -> str:
    pack["sources"].append({"id": f"src.extra_{kind}", "kind": kind, "title": "Extra"})
    return f"src.extra_{kind}"


Mutation = Callable[[dict[str, Any]], None]


def _verified_without_evidence(p: dict[str, Any]) -> None:
    sid = _add_source(p, "field_evidence")
    _alpha(p)["engine"] = {"value": "FX16", "confidence": "verified", "source": sid}


def _verified_from_a_reference(p: dict[str, Any]) -> None:
    _alpha(p)["engine"] = {
        "value": "FX16",
        "confidence": "verified",
        "source": "src.fixture_reference",
    }


def _standard_documents_vehicle(p: dict[str, Any]) -> None:
    sid = _add_source(p, "standard")
    _alpha(p)["obd_physical_layer"]["source"] = sid


def _evidence_on_reported(p: dict[str, Any]) -> None:
    _alpha(p)["engine"]["evidence"] = {"bundle": "evidence/x.json", "sha256": "0" * 64}


def _bad_evidence_path(p: dict[str, Any]) -> None:
    sid = _add_source(p, "field_evidence")
    _alpha(p)["engine"] = {
        "value": "FX16",
        "confidence": "verified",
        "source": sid,
        "evidence": {"bundle": "../secret.json", "sha256": "0" * 64},
    }


def _drop_key(path: Callable[[dict[str, Any]], dict[str, Any]], key: str) -> Mutation:
    return lambda p: path(p).pop(key)


def _set(path: Callable[[dict[str, Any]], dict[str, Any]], key: str, value: Any) -> Mutation:
    def mutate(p: dict[str, Any]) -> None:
        path(p)[key] = value

    return mutate


REJECTIONS: list[tuple[str, Mutation, str]] = [
    ("verified_without_evidence", _verified_without_evidence, "needs an evidence reference"),
    ("verified_from_reference", _verified_from_a_reference, "cannot support a verified claim"),
    ("standard_documents_vehicle", _standard_documents_vehicle, "a standard cannot document"),
    ("evidence_on_reported", _evidence_on_reported, "only verified claims carry evidence"),
    ("bad_evidence_path", _bad_evidence_path, "evidence.bundle"),
    (
        "unknown_with_value",
        _set(
            _alpha,
            "obd_physical_layer",
            {"value": "k_line", "confidence": "unknown", "verify": "verify.obd_protocol_atdpn"},
        ),
        "must have value null",
    ),
    (
        "disguised_tbd",
        _set(
            _alpha,
            "engine",
            {"value": "TBD", "confidence": "reported", "source": "src.fixture_reference"},
        ),
        "placeholder",
    ),
    (
        "disguised_unknown",
        _set(
            _alpha,
            "engine",
            {"value": "Unknown", "confidence": "reported", "source": "src.fixture_reference"},
        ),
        "placeholder",
    ),
    (
        "disguised_angle",
        _set(
            _alpha,
            "engine",
            {"value": "<engine code>", "confidence": "reported", "source": "src.fixture_reference"},
        ),
        "placeholder",
    ),
    (
        "disguised_zero_id",
        _set(
            _ecu,
            "request_id",
            {"value": 0, "confidence": "documented", "source": "src.fixture_manual"},
        ),
        "not a usable CAN identifier",
    ),
    (
        "value_missing",
        _set(
            _alpha,
            "engine",
            {"value": None, "confidence": "reported", "source": "src.fixture_reference"},
        ),
        "needs a value",
    ),
    (
        "unknown_with_source",
        _set(
            _alpha,
            "model_years",
            {
                "value": None,
                "confidence": "unknown",
                "source": "src.fixture_manual",
                "verify": "verify.model_years",
            },
        ),
        "has no source",
    ),
    (
        "dangling_source",
        _set(_alpha, "engine", {"value": "FX16", "confidence": "reported", "source": "src.nope"}),
        "is not a source of this pack",
    ),
    (
        "community_documents",
        _set(
            _alpha,
            "engine",
            {"value": "FX16", "confidence": "documented", "source": "src.fixture_forum"},
        ),
        "cannot support a documented claim",
    ),
    (
        "claim_typo_key",
        _set(
            _alpha,
            "engine",
            {
                "value": "FX16",
                "confidance": "reported",
                "confidence": "reported",
                "source": "src.fixture_reference",
            },
        ),
        "unknown key(s): confidance",
    ),
    (
        "bad_confidence",
        _set(
            _alpha,
            "engine",
            {"value": "FX16", "confidence": "certain", "source": "src.fixture_reference"},
        ),
        "is not one of",
    ),
    (
        "unknown_without_verify",
        _set(_alpha, "model_years", {"value": None, "confidence": "unknown"}),
        "how to verify",
    ),
    (
        "unverified_without_verify",
        _set(
            _alpha,
            "engine",
            {"value": "FX16", "confidence": "unverified", "source": "src.fixture_forum"},
        ),
        "how to verify",
    ),
    (
        "undefined_guidance",
        _set(
            _alpha,
            "model_years",
            {"value": None, "confidence": "unknown", "verify": "verify.nowhere"},
        ),
        "not defined in guidance.json",
    ),
    ("missing_list_key", _drop_key(_alpha, "supported_pids"), "missing key(s): supported_pids"),
    ("list_as_null", _set(_alpha, "supported_pids", None), "must be a JSON list"),
    (
        "write_service_pid",
        lambda p: _ecu(p)["manufacturer_pids"][0]["value"].update(service="2E"),
        "not a read service",
    ),
    (
        "bad_pid",
        lambda p: _alpha(p)["supported_pids"][0].update(value="0D"),
        "not a service 01 PID",
    ),
    (
        "eleven_bit_overflow",
        lambda p: _ecu(p)["request_id"].update(value="800"),
        "does not fit an 11-bit",
    ),
    ("same_request_response", lambda p: _ecu(p)["response_id"].update(value="7E0"), "must differ"),
    (
        "bitrate_contradiction",
        lambda p: _alpha(p)["can_bitrate_bps"].update(value=250000),
        "contradicts",
    ),
    (
        "odd_bitrate",
        lambda p: _alpha(p)["can_bitrate_bps"].update(value=123456),
        "not a CAN bitrate",
    ),
    (
        "bad_protocol",
        lambda p: _alpha(p)["obd_physical_layer"].update(value="can"),
        "is not one of",
    ),
    (
        "wildcard_only",
        lambda p: _ecu(p)["part_number_patterns"][0].update(value="*"),
        "wildcards only",
    ),
    (
        "regex_pattern",
        lambda p: _ecu(p)["part_number_patterns"][0].update(value="^96.+$"),
        "only letters",
    ),
    ("bad_wmi", lambda p: p["manufacturer"]["wmi"][0].update(value="IOQ"), "not a 3 character WMI"),
    ("http_url", lambda p: p["sources"][0].update(url="http://example.com"), "https://"),
    ("url_without_date", lambda p: p["sources"][0].pop("accessed"), "is required with a url"),
    (
        "unused_source",
        lambda p: p["sources"].append(
            {"id": "src.orphan", "kind": "public_reference", "title": "x"}
        ),
        "not cited",
    ),
    (
        "duplicate_source",
        lambda p: p["sources"].append(dict(p["sources"][0])),
        "duplicate source id",
    ),
    ("bad_source_kind", lambda p: p["sources"][0].update(kind="blog"), "is not one of"),
    ("missing_fa_name", lambda p: p["vehicles"][0]["names"].pop("fa"), "missing key(s): fa"),
    (
        "latin_fa_name",
        lambda p: p["vehicles"][0]["names"].update(fa="Alpha"),
        "must contain Persian",
    ),
    ("non_ascii_en_name", lambda p: p["vehicles"][0]["names"].update(en="آلفا"), "must be ASCII"),
    (
        "duplicate_vehicle",
        lambda p: p["vehicles"].append(copy.deepcopy(p["vehicles"][0])),
        "duplicate vehicle id",
    ),
    (
        "vehicle_prefix",
        lambda p: p["vehicles"][0].update(id="other.alpha"),
        "must start with the manufacturer id",
    ),
    (
        "bad_vehicle_id",
        lambda p: p["vehicles"][0].update(id="fixturemotors.Alpha"),
        "is not a valid id",
    ),
    (
        "duplicate_variant",
        lambda p: p["vehicles"][0]["variants"].append(copy.deepcopy(_alpha(p))),
        "variant ids must be unique",
    ),
    ("no_variants", lambda p: p["vehicles"][0].update(variants=[]), "at least one variant"),
    ("schema_version", lambda p: p.update(schema_version=2), "schema_version"),
    (
        "schema_name",
        lambda p: p.update(schema="something-else"),
        "must be 'iran-obd-tuner/vehicle-pack'",
    ),
    ("top_level_typo", lambda p: p.update(vehicels=[]), "unknown key(s): vehicels"),
    (
        "year_out_of_range",
        lambda p: _alpha(p)["model_years"].update(value={"first": 1400, "last": None}),
        "plausible Gregorian",
    ),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(m, msg, id=name) for name, m, msg in REJECTIONS],
)
def test_schema_rejects(tmp_path: Path, mutate: Mutation, message: str) -> None:
    pack = json.loads(FIXTURE_PACK.read_text(encoding="utf-8"))
    mutate(pack)
    _write(tmp_path, pack)
    with pytest.raises(VehicleKnowledgeError) as info:
        JsonVehicleKnowledgeBase(tmp_path, include_bundled=False)
    assert message in str(info.value)
    assert "pack.json" in str(info.value)


def test_error_names_the_json_path(tmp_path: Path) -> None:
    pack = json.loads(FIXTURE_PACK.read_text(encoding="utf-8"))
    _alpha(pack)["engine"]["value"] = "n/a"
    _write(tmp_path, pack)
    with pytest.raises(VehicleKnowledgeError, match=r"vehicles\[0\]\.variants\[0\]\.engine\.value"):
        JsonVehicleKnowledgeBase(tmp_path, include_bundled=False)


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "{not json")
    with pytest.raises(VehicleKnowledgeError, match="not valid JSON"):
        JsonVehicleKnowledgeBase(tmp_path, include_bundled=False)


def test_a_vehicle_defined_twice_across_local_packs_replaces_and_does_not_fail(
    tmp_path: Path,
) -> None:
    pack = json.loads(FIXTURE_PACK.read_text(encoding="utf-8"))
    _write(tmp_path, pack, "a.json")
    _write(tmp_path, pack, "b.json")
    kb = JsonVehicleKnowledgeBase(tmp_path, include_bundled=False)
    assert kb.vehicle("fixturemotors.alpha") is not None
    assert kb.source.endswith("b.json")


def test_local_guidance_must_be_bilingual(tmp_path: Path) -> None:
    folder = tmp_path / "vehicles"
    folder.mkdir()
    (folder / "guidance.json").write_text(
        json.dumps(
            {
                "schema": "iran-obd-tuner/vehicle-guidance",
                "schema_version": 1,
                "guidance": {"verify.local": {"en": "Check it.", "fa": "check"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VehicleKnowledgeError, match="must contain Persian"):
        JsonVehicleKnowledgeBase(tmp_path)


def test_claim_model_refuses_the_same_violations_in_code() -> None:
    with pytest.raises(ValueError, match="value None"):
        Claim("x", ConfidenceLevel.UNKNOWN, verify="verify.x")
    with pytest.raises(ValueError, match="needs a source"):
        Claim("x", ConfidenceLevel.REPORTED)
    with pytest.raises(ValueError, match="evidence"):
        Claim("x", ConfidenceLevel.VERIFIED, source="s")
    with pytest.raises(ValueError, match="verify"):
        Claim("x", ConfidenceLevel.UNVERIFIED, source="s")


# --------------------------------------------------------------------------- #
# verified claims vs. their Field Evidence Bundles
# --------------------------------------------------------------------------- #

#: claim field -> key in ``observed_values`` (equality of the formatted value).
_EQUAL_FIELDS = {
    "obd_physical_layer": "obd_physical_layer",
    "can_bitrate_bps": "can_bitrate_bps",
    "request_id": "request_id",
    "response_id": "response_id",
    "supported_pids": "supported_pids",
}
#: pattern field -> observed DID text the glob must match.
_GLOB_FIELDS = {
    "part_number_patterns": "part_number",
    "software_version_patterns": "software_version",
    "system_name_patterns": "system_name",
}


def verified_claim_problems(kb: JsonVehicleKnowledgeBase, root: Path) -> list[str]:
    """Every verified claim must equal what its (intact, non-simulated) bundle observed."""
    from fnmatch import fnmatchcase

    from iran_obd_tuner.core.exceptions import EvidenceExportError
    from iran_obd_tuner.core.knowledge import format_claim_value
    from iran_obd_tuner.diagnostics.evidence_export import load_bundle, observed_values

    problems: list[str] = []
    for where, field, claim in _all_claims(kb):
        if claim.confidence is not ConfidenceLevel.VERIFIED:
            continue
        assert claim.evidence is not None
        label = f"{where}:{field}"
        try:
            bundle = load_bundle(root / claim.evidence.bundle)
        except EvidenceExportError as exc:
            problems.append(f"{label}: {exc}")
            continue
        if bundle.sha256 != claim.evidence.sha256:
            problems.append(f"{label}: sha256 differs from the bundle")
        if bundle.simulated or not bundle.document.get("usable_for_verification"):
            problems.append(f"{label}: the bundle is simulated")
        observed = observed_values(bundle.document)
        if field in _EQUAL_FIELDS:
            shown = format_claim_value(field, claim.value)
            if shown not in observed.get(_EQUAL_FIELDS[field], set()):
                problems.append(f"{label}: {shown} was not observed in the bundle")
        elif field in _GLOB_FIELDS:
            texts = observed.get(_GLOB_FIELDS[field], set())
            if not any(fnmatchcase(t.upper(), str(claim.value).upper()) for t in texts):
                problems.append(f"{label}: no observed value matches {claim.value}")
        elif field == "dids":
            if f"{claim.value.did:04X}" not in observed.get("dids", set()):
                problems.append(f"{label}: DID {claim.value.did:04X} did not answer")
        elif not claim.notes:
            problems.append(f"{label}: not machine-checkable; notes must explain the evidence")
    return problems


def test_bundled_verified_claims_match_their_evidence(bundled: JsonVehicleKnowledgeBase) -> None:
    assert verified_claim_problems(bundled, REPO_ROOT) == []


def _evidence_root(tmp_path: Path, *, simulated: bool, layer: str) -> Path:
    from iran_obd_tuner.core.models import VehicleInfo
    from iran_obd_tuner.diagnostics.evidence_export import build_evidence_bundle, write_bundle
    from iran_obd_tuner.hardware.elm327_protocol import PROTOCOL_NAMES

    bundle = build_evidence_bundle(
        tool_version="test",
        vehicle_info=VehicleInfo(protocol=PROTOCOL_NAMES["6"], simulated=simulated),
    )
    write_bundle(tmp_path / "evidence" / "feb-test.json", bundle)
    pack = json.loads(FIXTURE_PACK.read_text(encoding="utf-8"))
    pack["sources"].append({"id": "src.field_test", "kind": "field_evidence", "title": "Bench"})
    _alpha(pack)["obd_physical_layer"] = {
        "value": layer,
        "confidence": "verified",
        "source": "src.field_test",
        "evidence": {"bundle": "evidence/feb-test.json", "sha256": bundle.sha256},
    }
    _write(tmp_path, pack)
    return tmp_path


def test_a_verified_claim_backed_by_a_real_bundle_passes(tmp_path: Path) -> None:
    root = _evidence_root(tmp_path, simulated=False, layer="iso_15765_4_can_11_500")
    kb = JsonVehicleKnowledgeBase(root, include_bundled=False)
    assert verified_claim_problems(kb, root) == []


def test_verified_claim_problems_are_found(tmp_path: Path) -> None:
    root = _evidence_root(tmp_path, simulated=True, layer="iso_15765_4_can_29_500")
    kb = JsonVehicleKnowledgeBase(root, include_bundled=False)
    problems = verified_claim_problems(kb, root)
    assert any("simulated" in p for p in problems)
    assert any("was not observed" in p for p in problems)
    (root / "evidence" / "feb-test.json").write_text("{}", encoding="utf-8")
    assert any("kind" in p for p in verified_claim_problems(kb, root))
