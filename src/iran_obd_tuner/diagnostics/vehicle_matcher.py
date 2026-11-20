"""Passive vehicle / ECU matcher: rank knowledge-base profiles against observed facts.

Input is what a read-only session already produced (the protocol the adapter reported, the
supported service 01 PIDs, the VIN, UDS identification DIDs). Nothing here sends anything:
the module imports only protocol *names* from ``hardware`` (enforced by
``tests/unit/test_architecture.py``).

How a variant is scored (fixed and deterministic):

=================  ======  ==========================================================
signal             weight  compared with
=================  ======  ==========================================================
VIN WMI            0.30    the manufacturer's WMI claims
part number F187   0.30    ``part_number_patterns`` (glob, e.g. ``9665*``)
software F189      0.15    ``software_version_patterns``
system name F197   0.10    ``system_name_patterns``
physical layer     0.10    ``obd_physical_layer`` (a family-level claim accepts its family)
supported PIDs     0.05    ``supported_pids`` (Jaccard similarity >= 0.8)
=================  ======  ==========================================================

A match adds ``weight x confidence factor`` (verified 1.0 ... unverified 0.3, unknown 0).
A conflict subtracts the same, and a conflict with a ``verified`` / ``documented`` WMI or
physical layer claim excludes the variant. Pattern lists are never complete, so a pattern
mismatch only subtracts. The score is not normalised: a variant that only agrees on the
protocol can never look like an identification. Tiers:

* ``strong``: an identity signal (WMI or an ECU pattern) matches at ``reported`` or better
  and the score is at least 0.5;
* ``partial``: some identity signal matches;
* ``weak``: only the physical layer / PIDs agree ("consistent with", not identifying);
* ``no_evidence``: nothing comparable.

Simulated facts are labelled as such in the report; they never feed claim promotion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any

from iran_obd_tuner.core.knowledge import (
    Claim,
    ConfidenceLevel,
    LocalizedText,
    Manufacturer,
    ObdProtocolId,
    VehicleProfile,
    VehicleVariant,
    format_claim_value,
)
from iran_obd_tuner.core.models import VIN_LENGTH, VehicleInfo
from iran_obd_tuner.core.pids import is_valid_pid, normalize_pid
from iran_obd_tuner.diagnostics.ecu_identification import EcuIdentification
from iran_obd_tuner.diagnostics.models import ReadOutcome
from iran_obd_tuner.hardware.elm327_protocol import PROTOCOL_NAMES

__all__ = [
    "MATCH_REPORT_SCHEMA_VERSION",
    "SIGNAL_WEIGHTS",
    "MatchReport",
    "MatchTier",
    "ObservedFacts",
    "SignalKind",
    "SignalOutcome",
    "SignalResult",
    "VehicleCandidate",
    "normalize_protocol",
    "rank_candidates",
]

MATCH_REPORT_SCHEMA_VERSION = 1
PID_SIMILARITY_THRESHOLD = 0.8
STRONG_SCORE = 0.5

#: ELM327 protocol number (data sheet Figure 2) -> canonical id. User CAN (B, C) has none.
_ELM_NUMBERS: Mapping[str, ObdProtocolId] = {
    "1": ObdProtocolId.SAE_J1850_PWM,
    "2": ObdProtocolId.SAE_J1850_VPW,
    "3": ObdProtocolId.ISO_9141_2,
    "4": ObdProtocolId.ISO_14230_4_KWP_5BAUD,
    "5": ObdProtocolId.ISO_14230_4_KWP_FAST,
    "6": ObdProtocolId.ISO_15765_4_CAN_11_500,
    "7": ObdProtocolId.ISO_15765_4_CAN_29_500,
    "8": ObdProtocolId.ISO_15765_4_CAN_11_250,
    "9": ObdProtocolId.ISO_15765_4_CAN_29_250,
    "A": ObdProtocolId.SAE_J1939,
}
_BY_NAME: Mapping[str, ObdProtocolId] = {
    PROTOCOL_NAMES[number]: protocol for number, protocol in _ELM_NUMBERS.items()
}
_CAN_PATTERN = re.compile(
    r"^ISO 15765-4 CAN \((11|29)[ -]bit ID, (500|250) k(?:baud|bit/s)\)(?: \[simulated\])?$"
)
_CAN_IDS = {
    ("11", "500"): ObdProtocolId.ISO_15765_4_CAN_11_500,
    ("29", "500"): ObdProtocolId.ISO_15765_4_CAN_29_500,
    ("11", "250"): ObdProtocolId.ISO_15765_4_CAN_11_250,
    ("29", "250"): ObdProtocolId.ISO_15765_4_CAN_29_250,
}


def normalize_protocol(text: str | None) -> ObdProtocolId | None:
    """Canonical id of a protocol description, ``None`` when it is not recognised.

    Accepts the ELM327 names (``AT DPN``) and the simulator's spelling of ISO 15765-4. It
    never guesses: anything else (UDS link descriptions, user CAN, typos) is ``None``.
    """
    if not text:
        return None
    text = text.strip()
    if text in _BY_NAME:
        return _BY_NAME[text]
    match = _CAN_PATTERN.match(text)
    return None if match is None else _CAN_IDS[(match.group(1), match.group(2))]


# --------------------------------------------------------------------------- #
# observations
# --------------------------------------------------------------------------- #


def _did_text(identification: EcuIdentification, did: int) -> str | None:
    reading = identification.did(did)
    if reading is None or reading.outcome is not ReadOutcome.OK or not reading.text:
        return None
    return reading.text.strip() or None


@dataclass(frozen=True, slots=True)
class ObservedFacts:
    """What a read-only session observed. Every field is optional.

    Attributes:
        active_protocol: Protocol description reported by the OBD adapter (raw text).
        supported_pids: Service 01 PIDs the vehicle declared.
        vin: VIN (service 09 or UDS F190).
        part_number / software_version / hardware_number / system_name: UDS DIDs
            F187 / F189 / F191 / F197 as text.
        uds_request_id / uds_response_id: The pair a UDS identification used.
        simulated: ``True`` when any of it came from a simulator.
    """

    active_protocol: str | None = None
    supported_pids: tuple[str, ...] = ()
    vin: str | None = None
    part_number: str | None = None
    software_version: str | None = None
    hardware_number: str | None = None
    system_name: str | None = None
    uds_request_id: int | None = None
    uds_response_id: int | None = None
    simulated: bool = False

    def __post_init__(self) -> None:
        pids = tuple(sorted({normalize_pid(p) for p in self.supported_pids if is_valid_pid(p)}))
        object.__setattr__(self, "supported_pids", pids)
        if self.vin is not None:
            object.__setattr__(self, "vin", self.vin.strip().upper() or None)

    @property
    def protocol_id(self) -> ObdProtocolId | None:
        return normalize_protocol(self.active_protocol)

    @property
    def wmi(self) -> str | None:
        """First three VIN characters, only for a 17 character VIN."""
        if self.vin is None or len(self.vin) != VIN_LENGTH:
            return None
        return self.vin[:3]

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.active_protocol,
                self.supported_pids,
                self.vin,
                self.part_number,
                self.software_version,
                self.hardware_number,
                self.system_name,
            )
        )

    @classmethod
    def from_session(
        cls,
        *,
        vehicle_info: VehicleInfo | None = None,
        active_protocol: str | None = None,
        supported_pids: Sequence[str] = (),
        identification: EcuIdentification | None = None,
        simulated: bool = False,
    ) -> ObservedFacts:
        """Collect facts from the objects a dashboard / CLI session already holds."""
        vin = vehicle_info.vin if vehicle_info is not None else None
        sim = simulated or bool(vehicle_info is not None and vehicle_info.simulated)
        if active_protocol is None and vehicle_info is not None:
            active_protocol = vehicle_info.protocol
        values: dict[str, Any] = {}
        if identification is not None:
            sim = sim or identification.simulated
            vin = vin or identification.vin
            values = {
                "part_number": _did_text(identification, 0xF187),
                "software_version": _did_text(identification, 0xF189),
                "hardware_number": _did_text(identification, 0xF191),
                "system_name": _did_text(identification, 0xF197),
                "uds_request_id": identification.request_id,
                "uds_response_id": identification.response_id,
            }
        return cls(
            active_protocol=active_protocol,
            supported_pids=tuple(supported_pids),
            vin=vin,
            simulated=sim,
            **values,
        )

    @classmethod
    def from_documents(
        cls, report: Mapping[str, Any] | None = None, uds: Mapping[str, Any] | None = None
    ) -> ObservedFacts:
        """Facts from saved JSON: a ``diagnose`` report and/or a ``uds-ident`` document.

        Raises:
            ValueError: A document does not look like the expected kind.
        """
        values: dict[str, Any] = {}
        simulated = False
        if report is not None:
            vehicle = report.get("vehicle")
            if not isinstance(vehicle, Mapping) or "supported_pids" not in report:
                raise ValueError("not a diagnostic report (missing 'vehicle'/'supported_pids')")
            values["active_protocol"] = vehicle.get("protocol")
            values["vin"] = vehicle.get("vin")
            values["supported_pids"] = tuple(str(p) for p in report.get("supported_pids", ()))
            simulated = bool(vehicle.get("simulated")) or bool(
                (report.get("adapter") or {}).get("simulated")
            )
        if uds is not None:
            if uds.get("kind") != "uds_ecu_identification":
                raise ValueError("not a UDS identification document (kind)")
            simulated = simulated or bool(uds.get("simulated"))
            texts = {
                str(d.get("did", "")).upper(): d.get("text")
                for d in uds.get("dids", ())
                if isinstance(d, Mapping) and d.get("outcome") == str(ReadOutcome.OK)
            }
            values["part_number"] = texts.get("F187")
            values["software_version"] = texts.get("F189")
            values["hardware_number"] = texts.get("F191")
            values["system_name"] = texts.get("F197")
            values["vin"] = values.get("vin") or (uds.get("vehicle") or {}).get("vin")
            for key, name in (("request_id", "uds_request_id"), ("response_id", "uds_response_id")):
                raw = uds.get(key)
                values[name] = int(str(raw), 16) if raw else None
        return cls(simulated=simulated, **values)

    def as_dict(self) -> dict[str, Any]:
        protocol = self.protocol_id
        return {
            "active_protocol": self.active_protocol,
            "protocol_id": None if protocol is None else str(protocol),
            "supported_pids": list(self.supported_pids),
            "vin_wmi": self.wmi,
            "part_number": self.part_number,
            "software_version": self.software_version,
            "hardware_number": self.hardware_number,
            "system_name": self.system_name,
            "uds_request_id": None if self.uds_request_id is None else f"{self.uds_request_id:03X}",
            "uds_response_id": None
            if self.uds_response_id is None
            else f"{self.uds_response_id:03X}",
            "simulated": self.simulated,
        }


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


class SignalKind(StrEnum):
    VIN_WMI = "vin_wmi"
    PART_NUMBER = "part_number"
    SOFTWARE_VERSION = "software_version"
    SYSTEM_NAME = "system_name"
    PHYSICAL_LAYER = "physical_layer"
    SUPPORTED_PIDS = "supported_pids"


SIGNAL_WEIGHTS: Mapping[SignalKind, float] = {
    SignalKind.VIN_WMI: 0.30,
    SignalKind.PART_NUMBER: 0.30,
    SignalKind.SOFTWARE_VERSION: 0.15,
    SignalKind.SYSTEM_NAME: 0.10,
    SignalKind.PHYSICAL_LAYER: 0.10,
    SignalKind.SUPPORTED_PIDS: 0.05,
}
_IDENTITY_SIGNALS = frozenset(
    {
        SignalKind.VIN_WMI,
        SignalKind.PART_NUMBER,
        SignalKind.SOFTWARE_VERSION,
        SignalKind.SYSTEM_NAME,
    }
)
_HARD = frozenset({ConfidenceLevel.VERIFIED, ConfidenceLevel.DOCUMENTED})


class SignalOutcome(StrEnum):
    MATCH = "match"
    CONFLICT = "conflict"
    NOT_OBSERVED = "not_observed"  # the session did not produce this fact
    NO_CLAIM = "no_claim"  # the knowledge base has nothing (or only 'unknown') to compare


class MatchTier(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"
    WEAK = "weak"
    NO_EVIDENCE = "no_evidence"
    EXCLUDED = "excluded"


_TIER_ORDER = {
    MatchTier.STRONG: 0,
    MatchTier.PARTIAL: 1,
    MatchTier.WEAK: 2,
    MatchTier.NO_EVIDENCE: 3,
    MatchTier.EXCLUDED: 4,
}


@dataclass(frozen=True, slots=True)
class SignalResult:
    """How one signal compared for one variant."""

    signal: SignalKind
    outcome: SignalOutcome
    observed: str | None
    claimed: str | None
    confidence: ConfidenceLevel | None
    source: str | None
    weight: float
    contribution: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": str(self.signal),
            "outcome": str(self.outcome),
            "observed": self.observed,
            "claimed": self.claimed,
            "confidence": None if self.confidence is None else str(self.confidence),
            "source": self.source,
            "weight": self.weight,
            "contribution": round(self.contribution, 4),
        }


@dataclass(frozen=True, slots=True)
class VehicleCandidate:
    """One variant and how well it agrees with the observations."""

    vehicle_id: str
    variant_id: str
    vehicle_names: LocalizedText
    variant_names: LocalizedText
    score: float
    tier: MatchTier
    signals: tuple[SignalResult, ...]
    excluded_reason: str | None = None

    @property
    def key(self) -> str:
        return f"{self.vehicle_id}/{self.variant_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "variant_id": self.variant_id,
            "name": f"{self.vehicle_names.en} - {self.variant_names.en}",
            "score": round(self.score, 4),
            "tier": str(self.tier),
            "excluded_reason": self.excluded_reason,
            "signals": [s.as_dict() for s in self.signals],
        }


@dataclass(frozen=True, slots=True)
class MatchReport:
    """Ranked candidates for one set of observations."""

    facts: ObservedFacts
    candidates: tuple[VehicleCandidate, ...]
    excluded: tuple[VehicleCandidate, ...]
    unmatched_count: int
    knowledge_source: str = ""
    notes: tuple[str, ...] = field(default=())
    #: Every variant that was evaluated (all tiers), in ranking order.
    evaluated: tuple[VehicleCandidate, ...] = ()

    @property
    def simulated(self) -> bool:
        return self.facts.simulated

    @property
    def best(self) -> VehicleCandidate | None:
        return self.candidates[0] if self.candidates else None

    def result_for(self, key: str) -> VehicleCandidate | None:
        """The evaluation of ``vehicle_id/variant_id``, whatever its tier."""
        return next((c for c in self.evaluated if c.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "vehicle_match_report",
            "schema_version": MATCH_REPORT_SCHEMA_VERSION,
            "simulated": self.simulated,
            "read_only": True,
            "knowledge_source": self.knowledge_source,
            "facts": self.facts.as_dict(),
            "candidates": [c.as_dict() for c in self.candidates],
            "excluded": [c.as_dict() for c in self.excluded],
            "unmatched_count": self.unmatched_count,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


def _result(
    signal: SignalKind,
    outcome: SignalOutcome,
    observed: str | None,
    claim: Claim[Any] | None,
    field_name: str = "",
) -> SignalResult:
    weight = SIGNAL_WEIGHTS[signal]
    factor = 0.0 if claim is None else claim.confidence.match_factor
    if outcome is SignalOutcome.MATCH:
        contribution = weight * factor
    elif outcome is SignalOutcome.CONFLICT:
        contribution = -weight * factor
    else:
        contribution = 0.0
    return SignalResult(
        signal=signal,
        outcome=outcome,
        observed=observed,
        claimed=None if claim is None else format_claim_value(field_name, claim.value),
        confidence=None if claim is None else claim.confidence,
        source=None if claim is None else claim.source,
        weight=weight,
        contribution=contribution,
    )


def _strongest(claims: Iterable[Claim[Any]]) -> Claim[Any] | None:
    known = [c for c in claims if c.is_known]
    return max(known, key=lambda c: c.confidence.rank) if known else None


def _value_signal(
    signal: SignalKind,
    observed: str | None,
    claims: Sequence[Claim[str]],
    matches: Any,
) -> SignalResult:
    known = [c for c in claims if c.is_known]
    if observed is None:
        return _result(signal, SignalOutcome.NOT_OBSERVED, None, _strongest(known))
    if not known:
        return _result(signal, SignalOutcome.NO_CLAIM, observed, None)
    hits = [c for c in known if matches(observed, c.value)]
    if hits:
        return _result(signal, SignalOutcome.MATCH, observed, _strongest(hits))
    return _result(signal, SignalOutcome.CONFLICT, observed, _strongest(known))


def _glob_match(observed: str, pattern: str) -> bool:
    return fnmatchcase(observed.strip().upper(), pattern.upper())


def _protocol_signal(facts: ObservedFacts, variant: VehicleVariant) -> SignalResult:
    claim = variant.obd_physical_layer
    observed_id = facts.protocol_id
    shown = None if observed_id is None else str(observed_id)
    if observed_id is None:
        return _result(
            SignalKind.PHYSICAL_LAYER,
            SignalOutcome.NOT_OBSERVED,
            facts.active_protocol,
            claim if claim.is_known else None,
        )
    if not claim.is_known:
        return _result(SignalKind.PHYSICAL_LAYER, SignalOutcome.NO_CLAIM, shown, None)
    assert claim.value is not None
    outcome = (
        SignalOutcome.MATCH
        if claim.value.is_consistent_with(observed_id)
        else SignalOutcome.CONFLICT
    )
    return _result(SignalKind.PHYSICAL_LAYER, outcome, shown, claim)


def _pids_signal(facts: ObservedFacts, variant: VehicleVariant) -> SignalResult:
    known = [c for c in variant.supported_pids if c.is_known]
    observed = set(facts.supported_pids)
    shown = ",".join(sorted(observed)) or None
    if not observed:
        return _result(SignalKind.SUPPORTED_PIDS, SignalOutcome.NOT_OBSERVED, None, None)
    if not known:
        return _result(SignalKind.SUPPORTED_PIDS, SignalOutcome.NO_CLAIM, shown, None)
    claimed = {str(c.value) for c in known}
    similarity = len(observed & claimed) / len(observed | claimed)
    weakest = min(known, key=lambda c: c.confidence.rank)
    outcome = (
        SignalOutcome.MATCH if similarity >= PID_SIMILARITY_THRESHOLD else SignalOutcome.CONFLICT
    )
    result = _result(SignalKind.SUPPORTED_PIDS, outcome, shown, weakest)
    return SignalResult(
        signal=result.signal,
        outcome=result.outcome,
        observed=result.observed,
        claimed=",".join(sorted(claimed)),
        confidence=result.confidence,
        source=result.source,
        weight=result.weight,
        contribution=result.contribution,
    )


def _evaluate(
    vehicle: VehicleProfile,
    variant: VehicleVariant,
    manufacturer: Manufacturer | None,
    facts: ObservedFacts,
) -> VehicleCandidate:
    ecus = variant.ecus
    signals = (
        _value_signal(
            SignalKind.VIN_WMI,
            facts.wmi,
            manufacturer.wmi if manufacturer is not None else (),
            lambda observed, value: observed == value,
        ),
        _value_signal(
            SignalKind.PART_NUMBER,
            facts.part_number,
            [c for e in ecus for c in e.part_number_patterns],
            _glob_match,
        ),
        _value_signal(
            SignalKind.SOFTWARE_VERSION,
            facts.software_version,
            [c for e in ecus for c in e.software_version_patterns],
            _glob_match,
        ),
        _value_signal(
            SignalKind.SYSTEM_NAME,
            facts.system_name,
            [c for e in ecus for c in e.system_name_patterns],
            _glob_match,
        ),
        _protocol_signal(facts, variant),
        _pids_signal(facts, variant),
    )

    excluded_reason = None
    for s in signals:
        if (
            s.outcome is SignalOutcome.CONFLICT
            and s.signal in (SignalKind.VIN_WMI, SignalKind.PHYSICAL_LAYER)
            and s.confidence in _HARD
        ):
            excluded_reason = (
                f"{s.signal}: observed {s.observed} contradicts the {s.confidence} claim"
                f" {s.claimed}"
            )
            break

    score = max(0.0, sum(s.contribution for s in signals))
    identity = [
        s for s in signals if s.signal in _IDENTITY_SIGNALS and s.outcome is SignalOutcome.MATCH
    ]
    if excluded_reason is not None:
        tier = MatchTier.EXCLUDED
    elif (
        any(
            s.confidence is not None and s.confidence.rank >= ConfidenceLevel.REPORTED.rank
            for s in identity
        )
        and score >= STRONG_SCORE
    ):
        tier = MatchTier.STRONG
    elif identity:
        tier = MatchTier.PARTIAL
    elif any(s.outcome is SignalOutcome.MATCH for s in signals):
        tier = MatchTier.WEAK
    else:
        tier = MatchTier.NO_EVIDENCE
    return VehicleCandidate(
        vehicle_id=vehicle.id,
        variant_id=variant.id,
        vehicle_names=vehicle.names,
        variant_names=variant.names,
        score=round(score, 4),
        tier=tier,
        signals=signals,
        excluded_reason=excluded_reason,
    )


def rank_candidates(
    vehicles: Sequence[VehicleProfile],
    facts: ObservedFacts,
    manufacturers: Sequence[Manufacturer] = (),
    *,
    knowledge_source: str = "",
) -> MatchReport:
    """Rank every variant of ``vehicles`` against ``facts`` (see the module docstring)."""
    by_id = {m.id: m for m in manufacturers}
    evaluated = [
        _evaluate(vehicle, variant, by_id.get(vehicle.manufacturer_id), facts)
        for vehicle in vehicles
        for variant in vehicle.variants
    ]
    evaluated.sort(key=lambda c: (_TIER_ORDER[c.tier], -c.score, c.vehicle_id, c.variant_id))
    candidates = tuple(
        c for c in evaluated if c.tier in (MatchTier.STRONG, MatchTier.PARTIAL, MatchTier.WEAK)
    )
    excluded = tuple(c for c in evaluated if c.tier is MatchTier.EXCLUDED)
    unmatched = sum(1 for c in evaluated if c.tier is MatchTier.NO_EVIDENCE)

    notes = ["Candidates are consistent with the observations; this is not an identification."]
    if facts.simulated:
        notes.append("SIMULATED observations: no vehicle was read; never use this for claims.")
    if facts.is_empty:
        notes.append("No observations were given.")
    elif facts.active_protocol and facts.protocol_id is None:
        notes.append(f"Protocol {facts.active_protocol!r} is not recognised; it was not compared.")
    if not candidates:
        notes.append(
            "No profile has comparable claims yet: most bus/ECU facts in the knowledge base are"
            " still unknown and need field evidence."
        )
    return MatchReport(
        facts=facts,
        candidates=candidates,
        excluded=excluded,
        unmatched_count=unmatched,
        knowledge_source=knowledge_source,
        notes=tuple(notes),
        evaluated=tuple(evaluated),
    )
