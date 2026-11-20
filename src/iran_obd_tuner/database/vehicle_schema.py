"""Strict validator and parser of vehicle knowledge packs (the single schema authority).

A pack is one JSON document per manufacturer (``data/vehicles/<manufacturer>.json``). This
module turns it into :mod:`iran_obd_tuner.core.knowledge` models or refuses it with a
:class:`~iran_obd_tuner.core.exceptions.VehicleKnowledgeError` naming the pack and the JSON
path of the problem, e.g. ``ikco.json: vehicles[0].variants[1].obd_physical_layer: ...``.

What is enforced (see docs/development.md for the authoring guide):

1. exact key sets at every level (unknown keys are typos, missing list keys are refused);
2. slug ids, globally unique, vehicle ids prefixed with the manufacturer id;
3. claim invariants: ``unknown`` <=> ``value: null``, no source; any other level needs a
   value and a resolvable source; ``verified`` needs an evidence reference; ``unknown`` and
   ``unverified`` need a ``verify`` guidance key that exists in ``guidance.json``;
4. no disguised unknowns: placeholder strings (``"unknown"``, ``"n/a"``, ``"TBD"``, ``"?"``,
   ``"<...>"``, ...) and zero identifiers are refused as values;
5. the confidence <-> source-kind matrix (:data:`~iran_obd_tuner.core.knowledge.
   ALLOWED_SOURCE_KINDS`), and a standard never documents a vehicle-specific fact (every
   claim in a pack is vehicle specific);
6. types and ranges (CAN identifiers vs. identifier length, bitrates, DIDs, PIDs, globs);
7. read-only guardrail: manufacturer identifiers may only name the read services ``0x21`` /
   ``0x22`` (knowledge only: nothing in the project sends them);
8. sources: kinds, ``https://`` URLs with an access date, no unused source;
9. names: English (ASCII) and Persian (Arabic script) are both required.

Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from iran_obd_tuner.core.exceptions import VehicleKnowledgeError
from iran_obd_tuner.core.knowledge import (
    ALLOWED_SOURCE_KINDS,
    Claim,
    ConfidenceLevel,
    DidSpec,
    EcuDiagnosticProtocol,
    EcuProfile,
    EvidenceRef,
    LocalizedText,
    Manufacturer,
    ManufacturerPidSpec,
    ObdProtocolId,
    ProtocolFamily,
    Source,
    SourceKind,
    VehicleProfile,
    VehicleVariant,
    YearRange,
)
from iran_obd_tuner.core.pids import is_valid_pid, normalize_pid

__all__ = [
    "ALLOWED_CAN_BITRATES",
    "DISGUISED_UNKNOWNS",
    "GUIDANCE_SCHEMA",
    "PACK_SCHEMA",
    "SCHEMA_VERSION",
    "ParsedPack",
    "parse_guidance",
    "parse_pack",
]

PACK_SCHEMA = "iran-obd-tuner/vehicle-pack"
GUIDANCE_SCHEMA = "iran-obd-tuner/vehicle-guidance"
SCHEMA_VERSION = 1

ALLOWED_CAN_BITRATES = frozenset({125_000, 250_000, 500_000, 1_000_000})
#: Read services a manufacturer identifier may belong to (KWP2000 0x21, UDS/KWP 0x22).
READ_SERVICES = frozenset({0x21, 0x22})
#: Placeholder spellings that must never stand in for a concrete value.
DISGUISED_UNKNOWNS = frozenset(
    {
        "",
        "unknown",
        "n/a",
        "na",
        "n.a.",
        "tbd",
        "tba",
        "todo",
        "?",
        "??",
        "???",
        "-",
        "--",
        "none",
        "null",
        "nil",
        "xxx",
        "unspecified",
        "not known",
        "unbekannt",
        "نامشخص",
        "نامعلوم",
    }
)

_SLUG = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
_DOTTED_SLUG = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*(\.[a-z0-9]+(_[a-z0-9]+)*)+$")
_SOURCE_ID = re.compile(r"^src\.[a-z0-9]+(_[a-z0-9]+)*$")
_GUIDANCE_KEY = re.compile(r"^verify\.[a-z0-9]+(_[a-z0-9]+)*$")
_GLOB = re.compile(r"^[A-Za-z0-9 ._\-/*?]{1,64}$")
_WMI = re.compile(r"^[A-HJ-NPR-Z0-9]{3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARABIC_SCRIPT = re.compile(r"[؀-ۿ]")
_HEX = re.compile(r"^(0x)?[0-9A-Fa-f]{1,8}$")

_CLAIM_REQUIRED = ("value", "confidence")
_CLAIM_OPTIONAL = ("source", "notes", "verify", "evidence")


@dataclass(frozen=True, slots=True)
class ParsedPack:
    """Everything one pack defines."""

    name: str
    manufacturer: Manufacturer
    vehicles: tuple[VehicleProfile, ...]
    sources: Mapping[str, Source]


class _Ctx:
    def __init__(self, pack: str, guidance_keys: frozenset[str]) -> None:
        self.pack = pack
        self.guidance_keys = guidance_keys
        self.sources: dict[str, Source] = {}
        self.used_sources: set[str] = set()

    def error(self, path: str, message: str) -> VehicleKnowledgeError:
        return VehicleKnowledgeError(f"{self.pack}: {path}: {message}")


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #


def _obj(
    ctx: _Ctx,
    path: str,
    raw: Any,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ctx.error(path, "must be a JSON object")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ctx.error(path, f"missing key(s): {', '.join(missing)}")
    unknown = sorted(set(raw) - set(required) - set(optional))
    if unknown:
        raise ctx.error(path, f"unknown key(s): {', '.join(unknown)}")
    return raw


def _list(ctx: _Ctx, path: str, raw: Any) -> list[Any]:
    if not isinstance(raw, list):
        raise ctx.error(path, "must be a JSON list (write [] for 'nothing recorded')")
    return raw


def _text(ctx: _Ctx, path: str, raw: Any, *, max_len: int = 2000) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ctx.error(path, "must be a non-empty string")
    if len(raw) > max_len:
        raise ctx.error(path, f"is longer than {max_len} characters")
    return raw.strip()


def _optional_text(ctx: _Ctx, path: str, raw: Any) -> str | None:
    return None if raw is None else _text(ctx, path, raw)


def _concrete_text(ctx: _Ctx, path: str, raw: Any, *, max_len: int = 120) -> str:
    text = _text(ctx, path, raw, max_len=max_len)
    lowered = text.lower()
    if lowered in DISGUISED_UNKNOWNS or (text.startswith("<") and text.endswith(">")):
        raise ctx.error(
            path, f"{text!r} is a placeholder, not a value; use confidence 'unknown' instead"
        )
    return text


def _ascii_text(ctx: _Ctx, path: str, raw: Any) -> str:
    text = _concrete_text(ctx, path, raw)
    if not text.isascii():
        raise ctx.error(path, "must be ASCII")
    return text


def _integer(ctx: _Ctx, path: str, raw: Any) -> int:
    """An integer given as JSON number or as hex text (``"7E0"`` / ``"0x7E0"``)."""
    if isinstance(raw, bool):
        raise ctx.error(path, "must be an integer")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and _HEX.match(raw.strip()):
        return int(raw.strip(), 16)
    raise ctx.error(path, "must be an integer or hex text such as '7E0'")


def _names(ctx: _Ctx, path: str, raw: Any) -> LocalizedText:
    obj = _obj(ctx, path, raw, ("en", "fa"))
    en = _concrete_text(ctx, f"{path}.en", obj["en"])
    if not en.isascii():
        raise ctx.error(f"{path}.en", "must be ASCII (the CLI prints it)")
    fa = _text(ctx, f"{path}.fa", obj["fa"], max_len=120)
    if not _ARABIC_SCRIPT.search(fa):
        raise ctx.error(f"{path}.fa", "must contain Persian text")
    return LocalizedText(en=en, fa=fa)


def _slug(ctx: _Ctx, path: str, raw: Any, pattern: re.Pattern[str] = _SLUG) -> str:
    if not isinstance(raw, str) or not pattern.match(raw):
        raise ctx.error(path, f"{raw!r} is not a valid id")
    return raw


# --------------------------------------------------------------------------- #
# value parsers (one per claim field)
# --------------------------------------------------------------------------- #

ValueParser = Callable[[_Ctx, str, Any], Any]


def _engine_value(ctx: _Ctx, path: str, raw: Any) -> str:
    return _ascii_text(ctx, path, raw)


def _year(ctx: _Ctx, path: str, raw: Any) -> int | None:
    if raw is None:
        return None
    year = _integer(ctx, path, raw)
    if not 1950 <= year <= 2100:
        raise ctx.error(path, f"{year} is not a plausible Gregorian model year")
    return year


def _years_value(ctx: _Ctx, path: str, raw: Any) -> YearRange:
    obj = _obj(ctx, path, raw, ("first", "last"))
    try:
        return YearRange(
            _year(ctx, f"{path}.first", obj["first"]), _year(ctx, f"{path}.last", obj["last"])
        )
    except ValueError as exc:
        raise ctx.error(path, str(exc)) from exc


def _protocol_value(ctx: _Ctx, path: str, raw: Any) -> ObdProtocolId:
    try:
        return ObdProtocolId(raw)
    except ValueError as exc:
        choices = ", ".join(p.value for p in ObdProtocolId)
        raise ctx.error(path, f"{raw!r} is not one of: {choices}") from exc


def _bitrate_value(ctx: _Ctx, path: str, raw: Any) -> int:
    value = _integer(ctx, path, raw)
    if value not in ALLOWED_CAN_BITRATES:
        raise ctx.error(path, f"{value} is not a CAN bitrate this schema accepts")
    return value


def _pid_value(ctx: _Ctx, path: str, raw: Any) -> str:
    text = _ascii_text(ctx, path, raw)
    if not is_valid_pid(text):
        raise ctx.error(path, f"{text!r} is not a service 01 PID in request form (e.g. 010C)")
    return normalize_pid(text)


def _diag_protocol_value(ctx: _Ctx, path: str, raw: Any) -> EcuDiagnosticProtocol:
    try:
        return EcuDiagnosticProtocol(raw)
    except ValueError as exc:
        choices = ", ".join(p.value for p in EcuDiagnosticProtocol)
        raise ctx.error(path, f"{raw!r} is not one of: {choices}") from exc


def _can_id_value(ctx: _Ctx, path: str, raw: Any) -> int:
    value = _integer(ctx, path, raw)
    if value <= 0 or value > 0x1FFFFFFF:
        raise ctx.error(path, f"0x{value:X} is not a usable CAN identifier")
    return value


def _id_bits_value(ctx: _Ctx, path: str, raw: Any) -> int:
    value = _integer(ctx, path, raw)
    if value not in (11, 29):
        raise ctx.error(path, "must be 11 or 29")
    return value


def _glob_value(ctx: _Ctx, path: str, raw: Any) -> str:
    text = _ascii_text(ctx, path, raw)
    if not _GLOB.match(text):
        raise ctx.error(
            path, "only letters, digits, space . _ - / and the wildcards * ? are allowed"
        )
    if not text.strip("*? "):
        raise ctx.error(path, "a pattern of wildcards only matches everything")
    return text


def _did_value(ctx: _Ctx, path: str, raw: Any) -> DidSpec:
    obj = _obj(ctx, path, raw, ("did", "name"))
    did = _integer(ctx, f"{path}.did", obj["did"])
    if not 0 < did <= 0xFFFF:
        raise ctx.error(f"{path}.did", "must be 0001..FFFF")
    return DidSpec(did=did, name=_ascii_text(ctx, f"{path}.name", obj["name"]))


def _manufacturer_pid_value(ctx: _Ctx, path: str, raw: Any) -> ManufacturerPidSpec:
    obj = _obj(ctx, path, raw, ("service", "identifier", "name"))
    service = _integer(ctx, f"{path}.service", obj["service"])
    if service not in READ_SERVICES:
        raise ctx.error(
            f"{path}.service",
            f"0x{service:02X} is not a read service (only 0x21 and 0x22 may be recorded)",
        )
    identifier = _integer(ctx, f"{path}.identifier", obj["identifier"])
    limit = 0xFF if service == 0x21 else 0xFFFF
    if not 0 < identifier <= limit:
        raise ctx.error(f"{path}.identifier", f"must be 1..0x{limit:X} for service 0x{service:02X}")
    return ManufacturerPidSpec(service, identifier, _ascii_text(ctx, f"{path}.name", obj["name"]))


def _wmi_value(ctx: _Ctx, path: str, raw: Any) -> str:
    text = _ascii_text(ctx, path, raw).upper()
    if not _WMI.match(text):
        raise ctx.error(path, f"{text!r} is not a 3 character WMI")
    return text


# --------------------------------------------------------------------------- #
# claims and sources
# --------------------------------------------------------------------------- #


def _evidence(ctx: _Ctx, path: str, raw: Any) -> EvidenceRef:
    obj = _obj(ctx, path, raw, ("bundle", "sha256"))
    bundle = _text(ctx, f"{path}.bundle", obj["bundle"], max_len=200)
    if not bundle.startswith("evidence/") or not bundle.endswith(".json") or ".." in bundle:
        raise ctx.error(f"{path}.bundle", "must be a path like evidence/<bundle>.json")
    sha = obj["sha256"]
    if not isinstance(sha, str) or not _SHA256.match(sha):
        raise ctx.error(f"{path}.sha256", "must be 64 lowercase hex characters")
    return EvidenceRef(bundle=bundle, sha256=sha)


def _claim(ctx: _Ctx, path: str, raw: Any, parse_value: ValueParser) -> Claim[Any]:
    obj = _obj(ctx, path, raw, _CLAIM_REQUIRED, _CLAIM_OPTIONAL)
    try:
        confidence = ConfidenceLevel(obj["confidence"])
    except ValueError as exc:
        choices = ", ".join(c.value for c in ConfidenceLevel)
        raise ctx.error(
            f"{path}.confidence", f"{obj['confidence']!r} is not one of: {choices}"
        ) from exc

    raw_value = obj["value"]
    source_id = obj.get("source")
    verify = obj.get("verify")
    notes = _optional_text(ctx, f"{path}.notes", obj.get("notes"))

    if confidence is ConfidenceLevel.UNKNOWN:
        if raw_value is not None:
            raise ctx.error(path, "an unknown claim must have value null (do not guess)")
        if source_id is not None:
            raise ctx.error(path, "an unknown claim has no source")
        value = None
    else:
        if raw_value is None:
            raise ctx.error(path, f"a {confidence} claim needs a value (or use 'unknown')")
        value = parse_value(ctx, f"{path}.value", raw_value)
        if not isinstance(source_id, str) or source_id not in ctx.sources:
            raise ctx.error(f"{path}.source", f"{source_id!r} is not a source of this pack")
        source = ctx.sources[source_id]
        if source.kind is SourceKind.STANDARD:
            raise ctx.error(
                f"{path}.source",
                "a standard cannot document a vehicle-specific claim (it says what a protocol"
                " is, not that this vehicle uses it)",
            )
        if source.kind not in ALLOWED_SOURCE_KINDS[confidence]:
            allowed = ", ".join(sorted(k.value for k in ALLOWED_SOURCE_KINDS[confidence]))
            raise ctx.error(
                f"{path}.source",
                f"a {source.kind} source cannot support a {confidence} claim (allowed: {allowed})",
            )
        ctx.used_sources.add(source_id)

    evidence = None
    if obj.get("evidence") is not None:
        evidence = _evidence(ctx, f"{path}.evidence", obj["evidence"])
    if confidence is ConfidenceLevel.VERIFIED and evidence is None:
        raise ctx.error(
            path, "a verified claim needs an evidence reference (Field Evidence Bundle)"
        )
    if confidence is not ConfidenceLevel.VERIFIED and evidence is not None:
        raise ctx.error(path, "only verified claims carry evidence")

    if verify is not None:
        if not isinstance(verify, str) or not _GUIDANCE_KEY.match(verify):
            raise ctx.error(f"{path}.verify", f"{verify!r} is not a guidance key (verify.<name>)")
        if verify not in ctx.guidance_keys:
            raise ctx.error(f"{path}.verify", f"{verify!r} is not defined in guidance.json")
    if confidence in (ConfidenceLevel.UNKNOWN, ConfidenceLevel.UNVERIFIED) and verify is None:
        raise ctx.error(path, f"a {confidence} claim must say how to verify it ('verify')")

    try:
        return Claim(
            value=value,
            confidence=confidence,
            source=source_id if confidence is not ConfidenceLevel.UNKNOWN else None,
            notes=notes,
            verify=verify,
            evidence=evidence,
        )
    except ValueError as exc:  # pragma: no cover - the checks above are stricter
        raise ctx.error(path, str(exc)) from exc


def _claims(ctx: _Ctx, path: str, raw: Any, parse_value: ValueParser) -> tuple[Claim[Any], ...]:
    return tuple(
        _claim(ctx, f"{path}[{i}]", item, parse_value)
        for i, item in enumerate(_list(ctx, path, raw))
    )


def _source(ctx: _Ctx, path: str, raw: Any) -> Source:
    obj = _obj(ctx, path, raw, ("id", "kind", "title"), ("publisher", "url", "accessed", "notes"))
    source_id = _slug(ctx, f"{path}.id", obj["id"], _SOURCE_ID)
    try:
        kind = SourceKind(obj["kind"])
    except ValueError as exc:
        choices = ", ".join(k.value for k in SourceKind)
        raise ctx.error(f"{path}.kind", f"{obj['kind']!r} is not one of: {choices}") from exc
    url = obj.get("url")
    accessed = obj.get("accessed")
    if url is not None:
        if not isinstance(url, str) or not url.startswith("https://") or " " in url:
            raise ctx.error(f"{path}.url", "must be an https:// address")
        if accessed is None:
            raise ctx.error(f"{path}.accessed", "is required with a url (ISO date)")
    if accessed is not None:
        try:
            date.fromisoformat(str(accessed))
        except ValueError as exc:
            raise ctx.error(f"{path}.accessed", "must be an ISO date (YYYY-MM-DD)") from exc
    return Source(
        id=source_id,
        kind=kind,
        title=_text(ctx, f"{path}.title", obj["title"], max_len=200),
        publisher=_optional_text(ctx, f"{path}.publisher", obj.get("publisher")),
        url=url,
        accessed=accessed,
        notes=_optional_text(ctx, f"{path}.notes", obj.get("notes")),
    )


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #

_ECU_KEYS = (
    "role",
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
)
_VARIANT_KEYS = (
    "id",
    "names",
    "engine",
    "model_years",
    "obd_physical_layer",
    "can_bitrate_bps",
    "supported_pids",
    "ecus",
)


def _check_can_id(ctx: _Ctx, path: str, claim: Claim[int], id_bits: Claim[int]) -> None:
    if claim.value is not None and id_bits.value == 11 and claim.value > 0x7FF:
        raise ctx.error(path, f"0x{claim.value:X} does not fit an 11-bit identifier")


def _ecu(ctx: _Ctx, path: str, raw: Any) -> EcuProfile:
    obj = _obj(ctx, path, raw, _ECU_KEYS, ("notes",))
    id_bits = _claim(ctx, f"{path}.id_bits", obj["id_bits"], _id_bits_value)
    request_id = _claim(ctx, f"{path}.request_id", obj["request_id"], _can_id_value)
    response_id = _claim(ctx, f"{path}.response_id", obj["response_id"], _can_id_value)
    _check_can_id(ctx, f"{path}.request_id", request_id, id_bits)
    _check_can_id(ctx, f"{path}.response_id", response_id, id_bits)
    if request_id.value is not None and request_id.value == response_id.value:
        raise ctx.error(path, "request and response identifiers must differ")
    return EcuProfile(
        role=_slug(ctx, f"{path}.role", obj["role"]),
        family_candidates=_claims(
            ctx, f"{path}.family_candidates", obj["family_candidates"], _engine_value
        ),
        supplier=_claim(ctx, f"{path}.supplier", obj["supplier"], _engine_value),
        diagnostic_protocol=_claim(
            ctx, f"{path}.diagnostic_protocol", obj["diagnostic_protocol"], _diag_protocol_value
        ),
        request_id=request_id,
        response_id=response_id,
        id_bits=id_bits,
        part_number_patterns=_claims(
            ctx, f"{path}.part_number_patterns", obj["part_number_patterns"], _glob_value
        ),
        software_version_patterns=_claims(
            ctx, f"{path}.software_version_patterns", obj["software_version_patterns"], _glob_value
        ),
        system_name_patterns=_claims(
            ctx, f"{path}.system_name_patterns", obj["system_name_patterns"], _glob_value
        ),
        dids=_claims(ctx, f"{path}.dids", obj["dids"], _did_value),
        manufacturer_pids=_claims(
            ctx, f"{path}.manufacturer_pids", obj["manufacturer_pids"], _manufacturer_pid_value
        ),
        notes=_optional_text(ctx, f"{path}.notes", obj.get("notes")),
    )


def _variant(ctx: _Ctx, path: str, raw: Any) -> VehicleVariant:
    obj = _obj(ctx, path, raw, _VARIANT_KEYS, ("notes",))
    layer = _claim(ctx, f"{path}.obd_physical_layer", obj["obd_physical_layer"], _protocol_value)
    bitrate = _claim(ctx, f"{path}.can_bitrate_bps", obj["can_bitrate_bps"], _bitrate_value)
    if layer.value is not None and bitrate.value is not None:
        if layer.value.family is not ProtocolFamily.CAN_ISO15765:
            raise ctx.error(f"{path}.can_bitrate_bps", "a CAN bitrate on a non-CAN OBD port")
        implied = layer.value.can_bitrate_bps
        if implied is not None and implied != bitrate.value:
            raise ctx.error(
                f"{path}.can_bitrate_bps",
                f"{bitrate.value} contradicts {layer.value} ({implied} bit/s)",
            )
    ecus = tuple(
        _ecu(ctx, f"{path}.ecus[{i}]", item)
        for i, item in enumerate(_list(ctx, f"{path}.ecus", obj["ecus"]))
    )
    roles = [ecu.role for ecu in ecus]
    if len(roles) != len(set(roles)):
        raise ctx.error(f"{path}.ecus", "ECU roles must be unique within a variant")
    return VehicleVariant(
        id=_slug(ctx, f"{path}.id", obj["id"]),
        names=_names(ctx, f"{path}.names", obj["names"]),
        engine=_claim(ctx, f"{path}.engine", obj["engine"], _engine_value),
        model_years=_claim(ctx, f"{path}.model_years", obj["model_years"], _years_value),
        obd_physical_layer=layer,
        can_bitrate_bps=bitrate,
        supported_pids=_claims(ctx, f"{path}.supported_pids", obj["supported_pids"], _pid_value),
        ecus=ecus,
        notes=_optional_text(ctx, f"{path}.notes", obj.get("notes")),
    )


def _vehicle(ctx: _Ctx, path: str, raw: Any, manufacturer_id: str) -> VehicleProfile:
    obj = _obj(ctx, path, raw, ("id", "names", "variants"), ("notes",))
    vehicle_id = _slug(ctx, f"{path}.id", obj["id"], _DOTTED_SLUG)
    if not vehicle_id.startswith(f"{manufacturer_id}."):
        raise ctx.error(f"{path}.id", f"must start with the manufacturer id '{manufacturer_id}.'")
    raw_variants = _list(ctx, f"{path}.variants", obj["variants"])
    if not raw_variants:
        raise ctx.error(f"{path}.variants", "a vehicle needs at least one variant")
    variants = tuple(_variant(ctx, f"{path}.variants[{i}]", v) for i, v in enumerate(raw_variants))
    ids = [v.id for v in variants]
    if len(ids) != len(set(ids)):
        raise ctx.error(f"{path}.variants", "variant ids must be unique within a vehicle")
    return VehicleProfile(
        id=vehicle_id,
        manufacturer_id=manufacturer_id,
        names=_names(ctx, f"{path}.names", obj["names"]),
        variants=variants,
        pack=ctx.pack,
        notes=_optional_text(ctx, f"{path}.notes", obj.get("notes")),
    )


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


def _header(ctx: _Ctx, raw: dict[str, Any], schema: str) -> None:
    if raw.get("schema") != schema:
        raise ctx.error("schema", f"must be {schema!r}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ctx.error("schema_version", f"must be {SCHEMA_VERSION}")


def parse_guidance(raw: Any, name: str) -> dict[str, LocalizedText]:
    """Parse ``guidance.json``: ``verify.<name>`` -> English and Persian instructions."""
    ctx = _Ctx(name, frozenset())
    obj = _obj(ctx, "$", raw, ("schema", "schema_version", "guidance"))
    _header(ctx, obj, GUIDANCE_SCHEMA)
    entries = obj["guidance"]
    if not isinstance(entries, dict) or not entries:
        raise ctx.error("guidance", "must be a non-empty JSON object")
    result: dict[str, LocalizedText] = {}
    for key, value in entries.items():
        path = f"guidance.{key}"
        if not _GUIDANCE_KEY.match(key):
            raise ctx.error(path, "keys must look like verify.<name>")
        text = _obj(ctx, path, value, ("en", "fa"))
        en = _text(ctx, f"{path}.en", text["en"])
        if not en.isascii():
            raise ctx.error(f"{path}.en", "must be ASCII (the CLI prints it)")
        fa = _text(ctx, f"{path}.fa", text["fa"])
        if not _ARABIC_SCRIPT.search(fa):
            raise ctx.error(f"{path}.fa", "must contain Persian text")
        result[key] = LocalizedText(en=en, fa=fa)
    return result


def parse_pack(raw: Any, name: str, guidance_keys: frozenset[str]) -> ParsedPack:
    """Validate one pack document and build its models.

    Raises:
        VehicleKnowledgeError: On the first violation, naming the JSON path.
    """
    ctx = _Ctx(name, guidance_keys)
    obj = _obj(ctx, "$", raw, ("schema", "schema_version", "manufacturer", "sources", "vehicles"))
    _header(ctx, obj, PACK_SCHEMA)

    for i, item in enumerate(_list(ctx, "sources", obj["sources"])):
        source = _source(ctx, f"sources[{i}]", item)
        if source.id in ctx.sources:
            raise ctx.error(f"sources[{i}].id", f"duplicate source id {source.id!r}")
        ctx.sources[source.id] = source

    m = _obj(ctx, "manufacturer", obj["manufacturer"], ("id", "names", "wmi"), ("notes",))
    manufacturer_id = _slug(ctx, "manufacturer.id", m["id"])
    wmi = _claims(ctx, "manufacturer.wmi", m["wmi"], _wmi_value)

    vehicles = tuple(
        _vehicle(ctx, f"vehicles[{i}]", item, manufacturer_id)
        for i, item in enumerate(_list(ctx, "vehicles", obj["vehicles"]))
    )
    ids = [v.id for v in vehicles]
    duplicates = sorted({vid for vid in ids if ids.count(vid) > 1})
    if duplicates:
        raise ctx.error("vehicles", f"duplicate vehicle id(s): {', '.join(duplicates)}")

    unused = sorted(set(ctx.sources) - ctx.used_sources)
    if unused:
        raise ctx.error("sources", f"source(s) not cited by any claim: {', '.join(unused)}")

    sources = dict(ctx.sources)
    manufacturer = Manufacturer(
        id=manufacturer_id,
        names=_names(ctx, "manufacturer.names", m["names"]),
        wmi=wmi,
        pack=name,
        sources=sources,
        notes=_optional_text(ctx, "manufacturer.notes", m.get("notes")),
    )
    vehicles = tuple(
        VehicleProfile(
            id=v.id,
            manufacturer_id=v.manufacturer_id,
            names=v.names,
            variants=v.variants,
            pack=name,
            sources=sources,
            notes=v.notes,
        )
        for v in vehicles
    )
    return ParsedPack(name=name, manufacturer=manufacturer, vehicles=vehicles, sources=sources)
