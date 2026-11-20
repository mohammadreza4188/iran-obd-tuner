"""Field Evidence Bundle: a read-only, self-verifying record of what a session observed.

A bundle packages the observed OBD protocol, the supported PIDs, UDS identification DIDs and
an excerpt of the raw adapter traffic into one JSON document. It is the only way a
knowledge-base claim can become ``verified``: a reviewer commits the bundle under
``evidence/`` and cites it (path + ``content_sha256``) from the claim. Nothing here edits
the knowledge base, and nothing here talks to a vehicle - it only reads objects a session
already produced (``hardware`` is imported for nothing but protocol names, via the matcher).

Privacy: with ``redact_vin=True`` (the default) the last six VIN characters (the serial
number) are masked in every structured field, ASCII occurrences of the VIN are masked in the
traffic, and the replies to VIN requests (OBD ``0902``, UDS ``22 F1 90`` including ISO-TP
consecutive frames) are replaced by ``[redacted: VIN response]``. A final check refuses to
produce the bundle if the VIN or the hex of its serial is still present (fail closed).
Only the VIN is redacted; review a bundle before sharing it.

Simulated input is labelled at the top level and per section; a simulated bundle can never
back a ``verified`` claim (``usable_for_verification: false``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.exceptions import EvidenceExportError
from iran_obd_tuner.core.knowledge import VehicleProfile, format_claim_value
from iran_obd_tuner.core.models import VehicleInfo, utc_now
from iran_obd_tuner.core.pids import is_valid_pid, normalize_pid
from iran_obd_tuner.core.traffic import Direction, TrafficEntry
from iran_obd_tuner.diagnostics.ecu_identification import EcuIdentification
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.diagnostics.vehicle_matcher import normalize_protocol

__all__ = [
    "BUNDLE_KIND",
    "BUNDLE_SCHEMA_VERSION",
    "DEFAULT_TRAFFIC_LIMIT",
    "REDACTED_VIN_RESPONSE",
    "FieldEvidenceBundle",
    "OperatorContext",
    "build_evidence_bundle",
    "content_sha256",
    "load_bundle",
    "mask_vin",
    "observed_values",
    "reconstruct_pid_bitmasks",
    "verify_bundle",
    "write_bundle",
]

BUNDLE_KIND = "field_evidence_bundle"
BUNDLE_SCHEMA_VERSION = 1
DEFAULT_TRAFFIC_LIMIT = 500
REDACTED_VIN_RESPONSE = "[redacted: VIN response]"
_HASH_FIELD = "content_sha256"
_CAN_LINE = re.compile(r"^([0-9A-F]{3}|[0-9A-F]{8})  ((?:[0-9A-F]{2})(?: [0-9A-F]{2})*)$")


@dataclass(frozen=True, slots=True)
class OperatorContext:
    """What the person who captured the evidence says about the vehicle (not verified).

    Attributes:
        vehicle_id / variant_id: The knowledge-base variant the operator believes this is.
        model_year: Free text, e.g. ``"1400"`` or ``"2021"``.
        notes: Anything else (ECU label text, mileage, adapter model, ...).
    """

    vehicle_id: str | None = None
    variant_id: str | None = None
    model_year: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance": "operator_reported",
            "vehicle_id": self.vehicle_id,
            "variant_id": self.variant_id,
            "model_year": self.model_year,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class FieldEvidenceBundle:
    """A built bundle: the JSON document and its identity."""

    document: Mapping[str, Any]

    @property
    def bundle_id(self) -> str:
        return str(self.document["bundle_id"])

    @property
    def sha256(self) -> str:
        return str(self.document[_HASH_FIELD])

    @property
    def simulated(self) -> bool:
        return bool(self.document["simulated"])

    def to_json(self) -> str:
        return json.dumps(self.document, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def mask_vin(vin: str) -> str:
    """Mask the last six characters (the serial number of a 17 character VIN)."""
    vin = vin.strip()
    if len(vin) <= 6:
        return "*" * len(vin)
    return vin[:-6] + "*" * 6


def content_sha256(document: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of ``document`` without its ``content_sha256`` field."""
    body = {k: v for k, v in document.items() if k != _HASH_FIELD}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reconstruct_pid_bitmasks(supported_pids: Sequence[str]) -> dict[str, str]:
    """Service 01 support bitmasks (``0100``, ``0120``, ...) rebuilt from a decoded PID list.

    The adapters keep only the decoded list, so this is a reconstruction: each word has the
    bits of the listed PIDs, and the range-continuation bit (PID ``0120`` in word ``0100``,
    ...) is set when a PID of a later range is listed.
    """
    numbers = sorted({int(normalize_pid(p)[2:], 16) for p in supported_pids if is_valid_pid(p)})
    if not numbers:
        return {}
    words: dict[str, str] = {}
    highest = max(numbers)
    for base in range(0x00, 0xE0, 0x20):
        if base > highest:
            break
        word = 0
        for number in numbers:
            if base < number <= base + 0x20:
                word |= 1 << (32 - (number - base))
        if highest > base + 0x20:
            word |= 1  # next range supported
        words[f"01{base:02X}"] = f"{word:08X}"
    return words


def _redact_traffic(texts: list[tuple[Direction, str]], vins: Sequence[str]) -> list[str]:
    """Replace VIN response windows and mask ASCII VINs (see the module docstring)."""
    out: list[str] = []
    window = False
    for direction, text in texts:
        upper = text.strip().upper()
        if direction is Direction.TX:
            frame = _CAN_LINE.match(upper)
            if frame is not None:
                data = bytes.fromhex(frame.group(2))
                kind = data[0] >> 4 if data else 0
                if kind == 3:  # ISO-TP flow control: part of the exchange, keeps the window
                    pass
                elif kind == 0:  # single frame request
                    payload = data[1 : 1 + (data[0] & 0x0F)]
                    window = _requests_vin(payload)
                elif kind == 1 and len(data) >= 2:  # first frame of a long request
                    window = _requests_vin(data[2:])
                else:
                    window = False
            else:
                window = upper.replace(" ", "") == "0902"
            out.append(_mask_ascii(text, vins))
        elif direction is Direction.RX and window:
            out.append(REDACTED_VIN_RESPONSE)
        else:
            out.append(_mask_ascii(text, vins))
    return out


def _requests_vin(payload: bytes) -> bool:
    if not payload or payload[0] != 0x22:
        return False
    dids = payload[1:]
    return any(dids[i : i + 2] == b"\xf1\x90" for i in range(0, len(dids) - 1, 2))


def _mask_ascii(text: str, vins: Sequence[str]) -> str:
    for vin in vins:
        text = re.sub(re.escape(vin), mask_vin(vin), text, flags=re.IGNORECASE)
    return text


def _leak_check(document: Mapping[str, Any], vins: Sequence[str]) -> None:
    serialized = json.dumps(document, ensure_ascii=False).upper()
    hex_blobs = [
        str(entry.get("text", "")).replace(" ", "").upper()
        for entry in document.get("traffic", {}).get("entries", ())
    ]
    for did in (document.get("uds") or {}).get("dids", ()):
        if did.get("raw_hex"):
            hex_blobs.append(str(did["raw_hex"]).replace(" ", "").upper())
    for vin in vins:
        if vin.upper() in serialized:
            raise EvidenceExportError("VIN redaction failed: the VIN is still in the bundle")
        serial_hex = vin[-6:].encode("ascii", errors="replace").hex().upper()
        if len(vin) >= 6 and any(serial_hex in blob for blob in hex_blobs):
            raise EvidenceExportError(
                "VIN redaction failed: the VIN serial is still in the raw traffic"
            )


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #


def _uds_section(
    identification: EcuIdentification, vins: Sequence[str], redact: bool
) -> dict[str, Any]:
    section = identification.as_dict()
    if redact:
        for did in section["dids"]:
            if did.get("text"):
                did["text"] = _mask_ascii(did["text"], vins)
            if did["did"] == "F190" and did.get("raw_hex") is not None:
                did["raw_hex"] = None
                did["redacted"] = True
        vehicle = section.get("vehicle") or {}
        if vehicle.get("vin"):
            vehicle["vin"] = mask_vin(vehicle["vin"])
    return section


def _promotion_candidates(
    knowledge: Sequence[VehicleProfile],
    operator: OperatorContext,
    obd: Mapping[str, Any],
    uds: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    vehicle = next((v for v in knowledge if v.id == operator.vehicle_id), None)
    variant = None if vehicle is None else vehicle.variant(operator.variant_id or "")
    if vehicle is None or variant is None:
        return []
    base = f"{vehicle.id}/{variant.id}"
    rows: list[dict[str, Any]] = []

    def add(path: str, claim: Any, observed: str, agrees: Callable[[Any], bool] | None) -> None:
        value = getattr(claim, "value", None)
        rows.append(
            {
                "claim": f"{base}:{path}",
                "current_confidence": str(claim.confidence) if claim is not None else "unknown",
                "current_value": None
                if value is None
                else format_claim_value(path.rsplit(".", 1)[-1], value),
                "observed": observed,
                "agrees": None if value is None or agrees is None else agrees(value),
            }
        )

    protocol = obd.get("protocol_id")
    if protocol:
        add(
            "obd_physical_layer", variant.obd_physical_layer, protocol, lambda v: str(v) == protocol
        )
        bitrate = obd.get("can_bitrate_bps")
        if bitrate:
            add("can_bitrate_bps", variant.can_bitrate_bps, str(bitrate), lambda v: v == bitrate)
    if obd.get("supported_pids"):
        observed = ",".join(obd["supported_pids"])
        known = {str(c.value) for c in variant.supported_pids if c.is_known}
        rows.append(
            {
                "claim": f"{base}:supported_pids",
                "current_confidence": "unknown" if not known else "listed",
                "current_value": ",".join(sorted(known)) or None,
                "observed": observed,
                "agrees": None if not known else known == set(obd["supported_pids"]),
            }
        )
    if uds is not None and variant.ecus:
        ecu = variant.ecus[0]
        prefix = f"ecus[{ecu.role}]"
        req, resp = int(uds["request_id"], 16), int(uds["response_id"], 16)
        add(f"{prefix}.request_id", ecu.request_id, f"0x{req:03X}", lambda v: v == req)
        add(f"{prefix}.response_id", ecu.response_id, f"0x{resp:03X}", lambda v: v == resp)
        patterns = {
            "F187": ("part_number_patterns", ecu.part_number_patterns),
            "F189": ("software_version_patterns", ecu.software_version_patterns),
            "F197": ("system_name_patterns", ecu.system_name_patterns),
        }
        for did in uds["dids"]:
            if did["outcome"] != str(ReadOutcome.OK):
                continue
            if did["did"] in patterns and did.get("text"):
                name, claims = patterns[did["did"]]
                text = did["text"]
                known = [c for c in claims if c.is_known]
                rows.append(
                    {
                        "claim": f"{base}:{prefix}.{name}",
                        "current_confidence": "unknown" if not known else "listed",
                        "current_value": ",".join(str(c.value) for c in known) or None,
                        "observed": text,
                        "agrees": None
                        if not known
                        else any(
                            fnmatchcase(text.strip().upper(), str(c.value).upper()) for c in known
                        ),
                    }
                )
            if did["did"] != "F190":
                rows.append(
                    {
                        "claim": f"{base}:{prefix}.dids",
                        "current_confidence": "listed" if ecu.dids else "unknown",
                        "current_value": None,
                        "observed": f"{did['did']} answered in the {did['session']} session",
                        "agrees": None,
                    }
                )
    return rows


def build_evidence_bundle(
    *,
    tool_version: str,
    vehicle_info: VehicleInfo | None = None,
    adapter_name: str | None = None,
    active_protocol: str | None = None,
    supported_pids: Sequence[str] = (),
    obd_simulated: bool = False,
    identification: EcuIdentification | None = None,
    traffic: Sequence[TrafficEntry] = (),
    dropped_traffic: int = 0,
    operator: OperatorContext | None = None,
    knowledge: Sequence[VehicleProfile] = (),
    redact_vin: bool = True,
    traffic_limit: int = DEFAULT_TRAFFIC_LIMIT,
    clock: Callable[[], datetime] = utc_now,
) -> FieldEvidenceBundle:
    """Package what a session observed (see the module docstring).

    Raises:
        EvidenceExportError: Nothing was observed, or redaction would leak the VIN.
    """
    has_obd = vehicle_info is not None or active_protocol is not None or bool(supported_pids)
    if not has_obd and identification is None and not traffic:
        raise EvidenceExportError("nothing to export: no session data was given")
    if traffic_limit < 0:
        raise EvidenceExportError("traffic_limit must not be negative")

    vins: list[str] = []
    for candidate in (
        vehicle_info.vin if vehicle_info is not None else None,
        identification.vin if identification is not None else None,
    ):
        if candidate and candidate not in vins:
            vins.append(candidate)
    if identification is not None:
        reading = identification.did(0xF190)
        text = reading.text.strip() if reading is not None and reading.text else ""
        if len(text) >= 6 and text not in vins:
            vins.append(text)
    redacting = redact_vin and bool(vins)

    created = clock()
    obd_sim = obd_simulated or bool(vehicle_info is not None and vehicle_info.simulated)
    protocol_text = active_protocol or (vehicle_info.protocol if vehicle_info else None)
    protocol_id = normalize_protocol(protocol_text)
    pids = sorted({normalize_pid(p) for p in supported_pids if is_valid_pid(p)})
    obd: dict[str, Any] | None = None
    if has_obd:
        vin = vehicle_info.vin if vehicle_info is not None else None
        obd = {
            "simulated": obd_sim,
            "adapter": adapter_name or (vehicle_info.adapter_name if vehicle_info else None),
            "active_protocol": protocol_text,
            "protocol_id": None if protocol_id is None else str(protocol_id),
            "can_bitrate_bps": None if protocol_id is None else protocol_id.can_bitrate_bps,
            "supported_pids": pids,
            "supported_pid_bitmasks": {
                "derivation": "reconstructed_from_decoded_pid_list",
                "words": reconstruct_pid_bitmasks(pids),
            },
            "vin": None if vin is None else (mask_vin(vin) if redacting else vin),
            "vin_source": vehicle_info.vin_source if vehicle_info is not None else None,
        }

    uds = None if identification is None else _uds_section(identification, vins, redacting)

    # Redact over the whole log first: a VIN response window may open before the excerpt.
    everything = list(traffic)
    texts = [(e.direction, e.text) for e in everything]
    redacted = _redact_traffic(texts, vins) if redacting else [t for _d, t in texts]
    start = max(0, len(everything) - traffic_limit)
    kept = everything[start:]
    shown = redacted[start:]
    traffic_sim = any(e.simulated for e in kept)
    entries = [
        {
            "seq": e.seq,
            "time": e.wall_time.isoformat(),
            "direction": e.direction.value,
            "adapter": e.adapter,
            "simulated": e.simulated,
            "text": text,
        }
        for e, text in zip(kept, shown, strict=True)
    ]

    simulated = obd_sim or bool(identification and identification.simulated) or traffic_sim
    operator = operator or OperatorContext()
    document: dict[str, Any] = {
        "kind": BUNDLE_KIND,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": "",
        "created_at": created.isoformat(),
        "tool_version": tool_version,
        "read_only": True,
        "simulated": simulated,
        "usable_for_verification": not simulated,
        "vin_redacted": redacting,
        "operator_context": operator.as_dict(),
        "obd": obd,
        "uds": uds,
        "traffic": {
            "simulated": traffic_sim,
            "limit": traffic_limit,
            "total_given": len(traffic),
            "truncated": len(traffic) > len(kept),
            "dropped_by_log": dropped_traffic,
            "entries": entries,
        },
        "promotion_candidates": _promotion_candidates(knowledge, operator, obd or {}, uds),
        "notes": [
            "Operator context is what the person capturing this says; it is not verified.",
            "Promotion to 'verified' is a reviewed edit of a vehicle pack that cites this file.",
        ]
        + (
            ["SIMULATED: no vehicle was read; this bundle cannot verify anything."]
            if simulated
            else []
        ),
    }
    if redacting:
        _leak_check(document, vins)
    digest = content_sha256(document)
    document["bundle_id"] = f"feb-{created.strftime('%Y%m%dT%H%M%SZ')}-{digest[:8]}"
    document[_HASH_FIELD] = content_sha256(document)
    return FieldEvidenceBundle(document=document)


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #


def write_bundle(path: Path | str, bundle: FieldEvidenceBundle) -> Path:
    """Write the bundle as UTF-8 JSON (creates the parent folder)."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(bundle.to_json(), encoding="utf-8")
    except OSError as exc:
        raise EvidenceExportError(f"could not write {target}: {exc}") from exc
    return target


def verify_bundle(document: Mapping[str, Any]) -> None:
    """Check kind, version and ``content_sha256``.

    Raises:
        EvidenceExportError: The document is not an intact Field Evidence Bundle.
    """
    if document.get("kind") != BUNDLE_KIND:
        raise EvidenceExportError("not a Field Evidence Bundle (kind)")
    if document.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise EvidenceExportError("unsupported Field Evidence Bundle schema_version")
    if document.get(_HASH_FIELD) != content_sha256(document):
        raise EvidenceExportError("content_sha256 does not match: the bundle was modified")


def load_bundle(path: Path | str) -> FieldEvidenceBundle:
    """Read and verify a bundle file."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceExportError(f"could not read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise EvidenceExportError(f"{path} does not contain a JSON object")
    verify_bundle(document)
    return FieldEvidenceBundle(document=document)


def observed_values(document: Mapping[str, Any]) -> dict[str, set[str]]:
    """Machine-checkable observations, keyed by claim field, as display strings.

    Used by the knowledge-base tests to check that a ``verified`` claim equals what its
    bundle observed (values formatted like :func:`~iran_obd_tuner.core.knowledge.
    format_claim_value`).
    """
    values: dict[str, set[str]] = {}

    def put(key: str, value: str | None) -> None:
        if value:
            values.setdefault(key, set()).add(value)

    obd = document.get("obd") or {}
    put("obd_physical_layer", obd.get("protocol_id"))
    if obd.get("can_bitrate_bps"):
        put("can_bitrate_bps", format_claim_value("can_bitrate_bps", obd["can_bitrate_bps"]))
    for pid in obd.get("supported_pids", ()):
        put("supported_pids", pid)
    uds = document.get("uds")
    if uds:
        put("request_id", format_claim_value("request_id", int(uds["request_id"], 16)))
        put("response_id", format_claim_value("response_id", int(uds["response_id"], 16)))
        names = {
            "F187": "part_number",
            "F189": "software_version",
            "F191": "hardware_number",
            "F197": "system_name",
        }
        for did in uds.get("dids", ()):
            if did.get("outcome") != str(ReadOutcome.OK):
                continue
            put("dids", did["did"])
            if did["did"] in names:
                put(names[did["did"]], did.get("text"))
    return values
