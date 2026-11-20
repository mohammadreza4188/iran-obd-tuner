"""Provenance-backed vehicle and ECU knowledge: confidence levels, claims and profiles.

Every vehicle-specific fact in the knowledge base is a :class:`Claim`: a value together with
how sure the project is about it (:class:`ConfidenceLevel`), where it comes from (a
:class:`Source` id), and, when it is not settled, how to check it on a real vehicle (a
``verify`` guidance key). "Not known" is a first-class value (``UNKNOWN`` with ``value=None``)
so that an unknown is never disguised as a concrete technical parameter.

The models are plain frozen dataclasses (standard library only, ``core`` rule): the database
layer builds them from JSON packs, the diagnostics layer matches observations against them,
and the UI renders them without importing ``database``.

Nothing here says that any vehicle uses any protocol, ECU or identifier. That is what the
packs (and their sources) say, with the confidence they state.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

__all__ = [
    "ALLOWED_SOURCE_KINDS",
    "Claim",
    "ClaimRow",
    "ConfidenceLevel",
    "DidSpec",
    "EcuDiagnosticProtocol",
    "EcuProfile",
    "EvidenceRef",
    "LocalizedText",
    "Manufacturer",
    "ManufacturerPidSpec",
    "ObdProtocolId",
    "ProtocolFamily",
    "Source",
    "SourceKind",
    "VehicleProfile",
    "VehicleVariant",
    "YearRange",
    "claim_rows",
    "format_claim_value",
]

T = TypeVar("T")


class ConfidenceLevel(StrEnum):
    """How well a claim is established, strongest first."""

    #: Confirmed by a physical hardware transcript (Field Evidence Bundle) in this repository.
    VERIFIED = "verified"
    #: From official / published technical documentation.
    DOCUMENTED = "documented"
    #: From a credible public reference, not verified on hardware by this project.
    REPORTED = "reported"
    #: A hypothesis or community claim that needs field verification.
    UNVERIFIED = "unverified"
    #: Not known; no reliable source. The value is always ``None``.
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        """4 (verified) ... 0 (unknown); higher is stronger."""
        return _RANKS[self]

    @property
    def match_factor(self) -> float:
        """How much a matching claim of this level counts in the vehicle matcher."""
        return _MATCH_FACTORS[self]


_RANKS = {
    ConfidenceLevel.VERIFIED: 4,
    ConfidenceLevel.DOCUMENTED: 3,
    ConfidenceLevel.REPORTED: 2,
    ConfidenceLevel.UNVERIFIED: 1,
    ConfidenceLevel.UNKNOWN: 0,
}
_MATCH_FACTORS = {
    ConfidenceLevel.VERIFIED: 1.0,
    ConfidenceLevel.DOCUMENTED: 0.8,
    ConfidenceLevel.REPORTED: 0.6,
    ConfidenceLevel.UNVERIFIED: 0.3,
    ConfidenceLevel.UNKNOWN: 0.0,
}


class SourceKind(StrEnum):
    """What kind of source backs a claim."""

    FIELD_EVIDENCE = "field_evidence"  # a Field Evidence Bundle stored in this repository
    MANUFACTURER_PUBLICATION = "manufacturer_publication"  # service literature, spec sheet
    STANDARD = "standard"  # ISO / SAE
    PUBLIC_REFERENCE = "public_reference"  # credible published reference
    COMMUNITY_REPORT = "community_report"  # forum, blog, video
    PROJECT_HYPOTHESIS = "project_hypothesis"  # reasoning by this project


#: Which source kinds may back a claim of each confidence level.
ALLOWED_SOURCE_KINDS: Mapping[ConfidenceLevel, frozenset[SourceKind]] = {
    ConfidenceLevel.VERIFIED: frozenset({SourceKind.FIELD_EVIDENCE}),
    ConfidenceLevel.DOCUMENTED: frozenset(
        {SourceKind.MANUFACTURER_PUBLICATION, SourceKind.STANDARD}
    ),
    ConfidenceLevel.REPORTED: frozenset(
        {SourceKind.PUBLIC_REFERENCE, SourceKind.MANUFACTURER_PUBLICATION}
    ),
    ConfidenceLevel.UNVERIFIED: frozenset(
        {SourceKind.COMMUNITY_REPORT, SourceKind.PROJECT_HYPOTHESIS}
    ),
    ConfidenceLevel.UNKNOWN: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Source:
    """Where a claim comes from.

    Attributes:
        id: Pack-unique id, e.g. ``"src.ikco_pars_product_page"``.
        kind: Kind of source (decides which confidence levels it can support).
        title: What the source is.
        publisher: Who published it, when known.
        url: ``https://`` address, when the source is online.
        accessed: ISO date the URL was read (required with ``url``).
        notes: What exactly the source says, or caveats.
    """

    id: str
    kind: SourceKind
    title: str
    publisher: str | None = None
    url: str | None = None
    accessed: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": str(self.kind),
            "title": self.title,
            "publisher": self.publisher,
            "url": self.url,
            "accessed": self.accessed,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Pointer from a ``VERIFIED`` claim to a Field Evidence Bundle.

    Attributes:
        bundle: Repository-relative path, e.g. ``"evidence/feb-....json"``.
        sha256: ``content_sha256`` of that bundle.
    """

    bundle: str
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {"bundle": self.bundle, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class Claim(Generic[T]):
    """One fact with its provenance.

    Invariants (also enforced by the pack validator):

    * ``UNKNOWN`` exactly when ``value`` is ``None``; it has no source and no evidence;
    * every other level has a value and a source id;
    * ``VERIFIED`` (and only ``VERIFIED``) carries an :class:`EvidenceRef`;
    * ``UNKNOWN`` and ``UNVERIFIED`` say how to verify the claim (``verify`` guidance key).
    """

    value: T | None
    confidence: ConfidenceLevel
    source: str | None = None
    notes: str | None = None
    verify: str | None = None
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, ConfidenceLevel):
            raise ValueError(f"confidence must be a ConfidenceLevel, got {self.confidence!r}")
        if self.confidence is ConfidenceLevel.UNKNOWN:
            if self.value is not None:
                raise ValueError("an unknown claim must have value None")
            if self.source is not None:
                raise ValueError("an unknown claim has no source")
        else:
            if self.value is None:
                raise ValueError(f"a {self.confidence} claim needs a value")
            if not self.source:
                raise ValueError(f"a {self.confidence} claim needs a source")
        if (self.evidence is not None) != (self.confidence is ConfidenceLevel.VERIFIED):
            raise ValueError("evidence is present exactly when the claim is verified")
        if (
            self.confidence in (ConfidenceLevel.UNKNOWN, ConfidenceLevel.UNVERIFIED)
            and not self.verify
        ):
            raise ValueError(f"a {self.confidence} claim needs a 'verify' guidance key")

    @property
    def is_known(self) -> bool:
        return self.confidence is not ConfidenceLevel.UNKNOWN

    @classmethod
    def unknown(cls, verify: str, notes: str | None = None) -> Claim[Any]:
        return cls(value=None, confidence=ConfidenceLevel.UNKNOWN, verify=verify, notes=notes)


# --------------------------------------------------------------------------- #
# protocol taxonomy
# --------------------------------------------------------------------------- #


class ProtocolFamily(StrEnum):
    """Physical layer family of an OBD port protocol."""

    J1850 = "j1850"
    K_LINE = "k_line"
    CAN_ISO15765 = "can_iso15765"
    J1939 = "j1939"


class ObdProtocolId(StrEnum):
    """Canonical OBD port protocol ids (the ELM327 protocol list), plus two family-level ids.

    ``k_line`` and ``can_iso15765`` express partial knowledge ("CAN, bitrate unknown").
    """

    SAE_J1850_PWM = "sae_j1850_pwm"
    SAE_J1850_VPW = "sae_j1850_vpw"
    ISO_9141_2 = "iso_9141_2"
    ISO_14230_4_KWP_5BAUD = "iso_14230_4_kwp_5baud"
    ISO_14230_4_KWP_FAST = "iso_14230_4_kwp_fast"
    ISO_15765_4_CAN_11_500 = "iso_15765_4_can_11_500"
    ISO_15765_4_CAN_29_500 = "iso_15765_4_can_29_500"
    ISO_15765_4_CAN_11_250 = "iso_15765_4_can_11_250"
    ISO_15765_4_CAN_29_250 = "iso_15765_4_can_29_250"
    SAE_J1939 = "sae_j1939"
    K_LINE = "k_line"
    CAN_ISO15765 = "can_iso15765"

    @property
    def family(self) -> ProtocolFamily:
        return _PROTOCOL_FAMILY[self]

    @property
    def is_family_level(self) -> bool:
        return self in (ObdProtocolId.K_LINE, ObdProtocolId.CAN_ISO15765)

    @property
    def can_bitrate_bps(self) -> int | None:
        """CAN bitrate implied by the id; ``None`` when not CAN or not specific."""
        return _CAN_DETAILS.get(self, (None, None))[1]

    @property
    def can_id_bits(self) -> int | None:
        """CAN identifier length implied by the id; ``None`` when not CAN or not specific."""
        return _CAN_DETAILS.get(self, (None, None))[0]

    def is_consistent_with(self, observed: ObdProtocolId) -> bool:
        """Whether an observed protocol agrees with this (claimed) one.

        A family-level claim agrees with any protocol of its family; a specific claim only
        with itself.
        """
        if self is observed:
            return True
        return self.is_family_level and observed.family is self.family


_PROTOCOL_FAMILY = {
    ObdProtocolId.SAE_J1850_PWM: ProtocolFamily.J1850,
    ObdProtocolId.SAE_J1850_VPW: ProtocolFamily.J1850,
    ObdProtocolId.ISO_9141_2: ProtocolFamily.K_LINE,
    ObdProtocolId.ISO_14230_4_KWP_5BAUD: ProtocolFamily.K_LINE,
    ObdProtocolId.ISO_14230_4_KWP_FAST: ProtocolFamily.K_LINE,
    ObdProtocolId.ISO_15765_4_CAN_11_500: ProtocolFamily.CAN_ISO15765,
    ObdProtocolId.ISO_15765_4_CAN_29_500: ProtocolFamily.CAN_ISO15765,
    ObdProtocolId.ISO_15765_4_CAN_11_250: ProtocolFamily.CAN_ISO15765,
    ObdProtocolId.ISO_15765_4_CAN_29_250: ProtocolFamily.CAN_ISO15765,
    ObdProtocolId.SAE_J1939: ProtocolFamily.J1939,
    ObdProtocolId.K_LINE: ProtocolFamily.K_LINE,
    ObdProtocolId.CAN_ISO15765: ProtocolFamily.CAN_ISO15765,
}
#: (identifier bits, bitrate) of the specific ISO 15765-4 ids.
_CAN_DETAILS = {
    ObdProtocolId.ISO_15765_4_CAN_11_500: (11, 500_000),
    ObdProtocolId.ISO_15765_4_CAN_29_500: (29, 500_000),
    ObdProtocolId.ISO_15765_4_CAN_11_250: (11, 250_000),
    ObdProtocolId.ISO_15765_4_CAN_29_250: (29, 250_000),
}


class EcuDiagnosticProtocol(StrEnum):
    """Diagnostic application protocol an ECU speaks beyond legislated OBD."""

    UDS_ISO14229 = "uds_iso14229"
    KWP2000_ISO14230 = "kwp2000_iso14230"
    OBD_ONLY = "obd_only"


# --------------------------------------------------------------------------- #
# claim values with structure
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class YearRange:
    """Gregorian model years; ``None`` on either side means open / not stated."""

    first: int | None
    last: int | None

    def __post_init__(self) -> None:
        if self.first is None and self.last is None:
            raise ValueError("a year range needs at least one bound")
        if self.first is not None and self.last is not None and self.first > self.last:
            raise ValueError("first year is after last year")


@dataclass(frozen=True, slots=True)
class DidSpec:
    """A data identifier an ECU is claimed to answer (read with ``0x22``)."""

    did: int
    name: str


@dataclass(frozen=True, slots=True)
class ManufacturerPidSpec:
    """A manufacturer-specific read identifier (knowledge only; nothing sends it).

    Attributes:
        service: ``0x21`` (KWP2000 local identifier) or ``0x22`` (data identifier).
        identifier: The local identifier / DID.
        name: What it is claimed to contain.
    """

    service: int
    identifier: int
    name: str


@dataclass(frozen=True, slots=True)
class LocalizedText:
    """English and Persian text; Persian falls back to English."""

    en: str
    fa: str | None = None

    def get(self, lang: str) -> str:
        if lang == "fa" and self.fa:
            return self.fa
        return self.en


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EcuProfile:
    """What is known about one ECU of a vehicle variant."""

    role: str
    family_candidates: tuple[Claim[str], ...]
    supplier: Claim[str]
    diagnostic_protocol: Claim[EcuDiagnosticProtocol]
    request_id: Claim[int]
    response_id: Claim[int]
    id_bits: Claim[int]
    part_number_patterns: tuple[Claim[str], ...] = ()
    software_version_patterns: tuple[Claim[str], ...] = ()
    system_name_patterns: tuple[Claim[str], ...] = ()
    dids: tuple[Claim[DidSpec], ...] = ()
    manufacturer_pids: tuple[Claim[ManufacturerPidSpec], ...] = ()
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class VehicleVariant:
    """One variant (engine / generation) of a vehicle model."""

    id: str
    names: LocalizedText
    engine: Claim[str]
    model_years: Claim[YearRange]
    obd_physical_layer: Claim[ObdProtocolId]
    can_bitrate_bps: Claim[int]
    supported_pids: tuple[Claim[str], ...]
    ecus: tuple[EcuProfile, ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    """A vehicle model and its variants."""

    id: str
    manufacturer_id: str
    names: LocalizedText
    variants: tuple[VehicleVariant, ...]
    pack: str = ""
    sources: Mapping[str, Source] = field(default_factory=dict)
    notes: str | None = None

    def variant(self, variant_id: str) -> VehicleVariant | None:
        return next((v for v in self.variants if v.id == variant_id), None)


@dataclass(frozen=True, slots=True)
class Manufacturer:
    """A manufacturer and the claims that belong to it (WMI codes)."""

    id: str
    names: LocalizedText
    wmi: tuple[Claim[str], ...]
    pack: str = ""
    sources: Mapping[str, Source] = field(default_factory=dict)
    notes: str | None = None


# --------------------------------------------------------------------------- #
# display helpers shared by the CLI and the UI
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClaimRow:
    """One displayable row: section, field key, and the claim (``None`` = empty list).

    ``field`` is a stable key (``engine``, ``obd_physical_layer``, ``request_id``, ...) that
    the CLI prints as is and the UI translates.
    """

    section: str
    field: str
    claim: Claim[Any] | None

    @property
    def confidence(self) -> ConfidenceLevel:
        return ConfidenceLevel.UNKNOWN if self.claim is None else self.claim.confidence


def _list_rows(section: str, name: str, claims: tuple[Claim[Any], ...]) -> Iterator[ClaimRow]:
    if not claims:
        yield ClaimRow(section, name, None)
        return
    for claim in claims:
        yield ClaimRow(section, name, claim)


def claim_rows(variant: VehicleVariant) -> tuple[ClaimRow, ...]:
    """Every claim of a variant in display order (empty lists become one ``None`` row)."""
    rows: list[ClaimRow] = [
        ClaimRow("vehicle", "engine", variant.engine),
        ClaimRow("vehicle", "model_years", variant.model_years),
        ClaimRow("obd_port", "obd_physical_layer", variant.obd_physical_layer),
        ClaimRow("obd_port", "can_bitrate_bps", variant.can_bitrate_bps),
    ]
    rows.extend(_list_rows("obd_port", "supported_pids", variant.supported_pids))
    for ecu in variant.ecus:
        section = f"ecu:{ecu.role}"
        rows.extend(_list_rows(section, "family_candidates", ecu.family_candidates))
        rows.append(ClaimRow(section, "supplier", ecu.supplier))
        rows.append(ClaimRow(section, "diagnostic_protocol", ecu.diagnostic_protocol))
        rows.append(ClaimRow(section, "request_id", ecu.request_id))
        rows.append(ClaimRow(section, "response_id", ecu.response_id))
        rows.append(ClaimRow(section, "id_bits", ecu.id_bits))
        rows.extend(_list_rows(section, "part_number_patterns", ecu.part_number_patterns))
        rows.extend(_list_rows(section, "software_version_patterns", ecu.software_version_patterns))
        rows.extend(_list_rows(section, "system_name_patterns", ecu.system_name_patterns))
        rows.extend(_list_rows(section, "dids", ecu.dids))
        rows.extend(_list_rows(section, "manufacturer_pids", ecu.manufacturer_pids))
    return tuple(rows)


def format_claim_value(field_name: str, value: Any) -> str:
    """ASCII rendering of a claim value (``"unknown"`` for ``None``)."""
    if value is None:
        return "unknown"
    if isinstance(value, YearRange):
        first = "?" if value.first is None else str(value.first)
        last = "" if value.last is None else str(value.last)
        return f"{first}-{last}"
    if isinstance(value, DidSpec):
        return f"{value.did:04X} {value.name}"
    if isinstance(value, ManufacturerPidSpec):
        width = 2 if value.service == 0x21 else 4
        return f"{value.service:02X} {value.identifier:0{width}X} {value.name}"
    if field_name in ("request_id", "response_id") and isinstance(value, int):
        return f"0x{value:03X}"
    if field_name == "can_bitrate_bps" and isinstance(value, int):
        return f"{value} bit/s"
    if field_name == "id_bits" and isinstance(value, int):
        return f"{value}-bit"
    return str(value)
