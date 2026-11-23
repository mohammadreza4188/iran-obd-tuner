"""Bench (off-vehicle) ECU programming: shared models, capabilities and ports (Phase 5).

This module only *describes* bench programming; it sends nothing and writes no file. It is
the contract shared by the layers that will implement Milestone 7:

* ``hardware`` implements :class:`BenchProgrammerPort` (the only place that will ever compose
  a programming request);
* ``tuning`` drives the four-stage pipeline against that port and is the only place that
  mints the two capabilities below;
* ``app`` wires them; ``ui`` sees neither.

Safety design (see docs/architecture.md, section "Safety boundary"):

* **Mode** - :attr:`~iran_obd_tuner.core.safety.OperationClass.BENCH_FLASH` is granted only by
  :func:`bench_flash_policy`, from a complete :class:`BenchAttestation` made by a human for
  one session. It is never read from a configuration file or an environment variable, and
  :data:`~iran_obd_tuner.core.safety.DEFAULT_POLICY` never contains it.
* **Capabilities** - every request that changes ECU memory or state needs a
  :class:`FlashAuthorization`; erasing and downloading also need a :class:`BackupReceipt`
  (read-before-write lock). Both can only be constructed with a private mint token, which an
  architecture test allows exactly one ``tuning`` module each to import. Each capability
  re-validates itself on construction and is checked again where it is used.
* **Targets** - in this build a :class:`BenchTargetProfile` must be simulated:
  constructing a real one raises :class:`~iran_obd_tuner.core.exceptions.
  BenchTargetNotQualifiedError`. A real ECU needs a documented, evidence-backed profile
  (memory layout, routines, an authorised seed-key provider) and a separate approval.
* **Seed-key** - :class:`SeedKeyProvider` is an interface only. The project ships no real
  algorithm and no mechanism to load one; the only provider (simulated) arrives with the
  virtual programmable ECU.

The Flash Transaction Journal states and their allowed transitions are defined here
(:data:`JOURNAL_TRANSITIONS`), so the journal writer, the orchestrator and the dashboard
share one state machine.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import ClassVar, Final, Protocol, runtime_checkable

from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, SafetyGuardError
from iran_obd_tuner.core.safety import OperationClass, SafetyPolicy

__all__ = [
    "BENCH_CONFIRMATION_PHRASE",
    "DIRTY_JOURNAL_STATES",
    "ERASE_PROGRESS_MESSAGE",
    "JOURNAL_TRANSITIONS",
    "SIMULATED_BENCH_TARGET",
    "SOFTWARE_IDENTIFICATION_DIDS",
    "TERMINAL_JOURNAL_STATES",
    "BackupReceipt",
    "BenchAttestation",
    "BenchProgrammerPort",
    "BenchTargetProfile",
    "EcuIdentity",
    "FlashAuthorization",
    "FlashProgress",
    "FlashStage",
    "JournalState",
    "MemoryRegion",
    "ProgressSink",
    "SeedKeyProvider",
    "bench_flash_policy",
    "check_journal_transition",
]

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _require_sha256(value: str, what: str) -> None:
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"{what} must be a lower-case SHA-256 hex digest, got {value!r}")


def _require_aware(moment: datetime, what: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{what} must be timezone aware")


# --------------------------------------------------------------------------- #
# pipeline stages and the Flash Transaction Journal state machine
# --------------------------------------------------------------------------- #


class FlashStage(StrEnum):
    """The four stages of a bench flash (and the recovery that replays stages 3-4)."""

    PRE_FLIGHT = "pre_flight"  # validation only; nothing that changes the ECU is sent
    BACKUP = "backup"  # mandatory full read-out, verified and persisted before any erase
    PROGRAMMING = "programming"  # erase, then UDS 0x34 / 0x36 / 0x37 (download, transfer, exit)
    VERIFY = "verify"  # check routine, read-back comparison, reset, identity re-read
    RECOVERY = "recovery"  # stages 3-4 again with the verified backup image


class JournalState(StrEnum):
    """State of one flash transaction, persisted atomically before each step."""

    PENDING = "pending"  # plan recorded; the ECU memory is untouched
    BACKUP_VERIFIED = "backup_verified"  # backup read twice, persisted, re-read from disk
    ERASING = "erasing"  # from here on the ECU memory may be incomplete
    TRANSFERRING = "transferring"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ABORTED = "aborted"  # stopped before any erase: the ECU memory is untouched
    INTERRUPTED = "interrupted"  # stopped after the erase started: recovery needed
    RECOVERING = "recovering"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"  # still dirty: recovery may be attempted again


#: Allowed transitions. Anything else is a programming error and is refused.
JOURNAL_TRANSITIONS: Final[Mapping[JournalState, frozenset[JournalState]]] = {
    JournalState.PENDING: frozenset({JournalState.BACKUP_VERIFIED, JournalState.ABORTED}),
    JournalState.BACKUP_VERIFIED: frozenset({JournalState.ERASING, JournalState.ABORTED}),
    JournalState.ERASING: frozenset({JournalState.TRANSFERRING, JournalState.INTERRUPTED}),
    JournalState.TRANSFERRING: frozenset({JournalState.VERIFYING, JournalState.INTERRUPTED}),
    JournalState.VERIFYING: frozenset({JournalState.COMPLETED, JournalState.INTERRUPTED}),
    JournalState.INTERRUPTED: frozenset({JournalState.RECOVERING}),
    JournalState.RECOVERING: frozenset({JournalState.RECOVERED, JournalState.RECOVERY_FAILED}),
    JournalState.RECOVERY_FAILED: frozenset({JournalState.RECOVERING}),
    JournalState.COMPLETED: frozenset(),
    JournalState.ABORTED: frozenset(),
    JournalState.RECOVERED: frozenset(),
}

#: States in which the ECU memory may be incomplete: a new flash is refused and the
#: dashboard shows the emergency recovery banner. (An unreadable journal counts as dirty.)
DIRTY_JOURNAL_STATES: Final[frozenset[JournalState]] = frozenset(
    {
        JournalState.ERASING,
        JournalState.TRANSFERRING,
        JournalState.VERIFYING,
        JournalState.INTERRUPTED,
        JournalState.RECOVERING,
        JournalState.RECOVERY_FAILED,
    }
)

#: States a transaction ends in (the journal is archived).
TERMINAL_JOURNAL_STATES: Final[frozenset[JournalState]] = frozenset(
    state for state, targets in JOURNAL_TRANSITIONS.items() if not targets
)


def check_journal_transition(current: JournalState, new: JournalState) -> None:
    """Refuse a journal transition that the state machine does not allow.

    Raises:
        SafetyGuardError: For any transition not in :data:`JOURNAL_TRANSITIONS`.
    """
    if new not in JOURNAL_TRANSITIONS[current]:
        allowed = ", ".join(sorted(JOURNAL_TRANSITIONS[current])) or "none (terminal)"
        raise SafetyGuardError(
            f"flash journal: transition {current} -> {new} is not allowed (allowed: {allowed})"
        )


# --------------------------------------------------------------------------- #
# target description
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MemoryRegion:
    """A contiguous block of ECU flash memory and where it sits in the image file.

    Attributes:
        start_address: First address in the ECU's address space.
        size: Number of bytes.
        image_offset: Offset of the region's first byte in the ROM image.
    """

    start_address: int
    size: int
    image_offset: int = 0

    def __post_init__(self) -> None:
        if self.start_address < 0 or self.image_offset < 0:
            raise ValueError("addresses and offsets must not be negative")
        if self.size <= 0:
            raise ValueError("a memory region must have a positive size")
        if self.end_address > 0xFFFF_FFFF:
            raise ValueError("a memory region must fit a 32-bit address space")

    @property
    def end_address(self) -> int:
        """One past the last address."""
        return self.start_address + self.size

    @property
    def image_end(self) -> int:
        """One past the last image offset."""
        return self.image_offset + self.size

    def overlaps(self, other: MemoryRegion) -> bool:
        """Whether the two regions share an address."""
        return self.start_address < other.end_address and other.start_address < self.end_address

    def contains(self, other: MemoryRegion) -> bool:
        """Whether ``other`` lies completely inside this region."""
        return self.start_address <= other.start_address and other.end_address <= self.end_address

    def describe(self) -> str:
        return f"{self.start_address:#010x}..{self.end_address:#010x} ({self.size} bytes)"


def _did_digest(pairs: Iterable[tuple[int, str]]) -> str:
    text = "\n".join(f"{did:04X}={value}" for did, value in sorted(pairs))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: ISO 14229-1 software identification DIDs (boot / application software and data ids,
#: manufacturer and supplier software numbers and versions). A flash changes them, so they
#: can never identify the physical unit.
SOFTWARE_IDENTIFICATION_DIDS: Final[frozenset[int]] = frozenset(
    {0xF180, 0xF181, 0xF182, 0xF188, 0xF189, 0xF194, 0xF195}
)


@dataclass(frozen=True, slots=True)
class EcuIdentity:
    """Identification DIDs read from an ECU (printable text values only).

    Attributes:
        dids: DID -> decoded text, as read; nothing is inferred.
    """

    dids: Mapping[int, str]

    def fingerprint(self) -> str:
        """SHA-256 over all sorted ``DID=value`` pairs (the record of what was read).

        It includes the software DIDs, so it changes with every flash; a backup is bound to
        the :meth:`unit_fingerprint` instead.
        """
        return _did_digest(self.dids.items())

    def unit_fingerprint(self, dids: Iterable[int]) -> str | None:
        """SHA-256 over the unit DIDs (those a flash cannot change): binds a backup to one
        physical ECU. ``None`` when one of them was not read (the unit is then not proven).
        """
        wanted = sorted(set(dids))
        if not wanted or any(did not in self.dids for did in wanted):
            return None
        return _did_digest((did, self.dids[did]) for did in wanted)

    def mismatches(self, patterns: Mapping[int, str]) -> tuple[str, ...]:
        """Human-readable reasons why this identity does not match ``patterns`` (glob)."""
        problems = []
        for did, pattern in sorted(patterns.items()):
            value = self.dids.get(did)
            if value is None:
                problems.append(f"DID {did:04X} was not read (expected {pattern!r})")
            elif not fnmatchcase(value, pattern):
                problems.append(f"DID {did:04X} is {value!r}, expected {pattern!r}")
        return tuple(problems)


@dataclass(frozen=True, slots=True)
class BenchTargetProfile:
    """Everything the programmer needs to know about one ECU family on the bench.

    In this build only simulated profiles can be constructed (``simulated=True``); a real
    profile raises :class:`BenchTargetNotQualifiedError`, because no real ECU family has the
    documented evidence (memory layout, routines, an authorised seed-key) that one needs.

    Attributes:
        target_id: Stable identifier, e.g. ``simulated.bench_ecu_v1``.
        display_name: Shown to the user.
        simulated: ``True`` for the virtual programmable ECU.
        regions: Flash regions, ordered by image offset, non-overlapping, sector aligned; the
            image must be exactly their total size.
        sector_size: Erase granularity in bytes.
        identity_patterns: DID -> glob the application identity must match.
        bootloader_identity_patterns: DID -> glob accepted while the ECU runs its bootloader
            (e.g. after an interrupted flash). Such an identity must *not* also match
            ``identity_patterns``: an ECU that shows its application is not in its bootloader.
        unit_identity_dids: DIDs that identify the physical unit and that no flash changes
            (e.g. hardware number and serial number). A backup is bound to them, so the
            same ECU is recognised after its software changed (Milestone 7e.1); software
            identification DIDs (:data:`SOFTWARE_IDENTIFICATION_DIDS`) are refused.
        security_level: Odd ``requestSeed`` sub-function (the key uses ``level + 1``).
        seed_key_provider_id: Which :class:`SeedKeyProvider` computes the key.
        erase_routine_id / check_routine_id: UDS 0x31 routine identifiers (target specific).
        address_and_length_format: ``addressAndLengthFormatIdentifier`` for 0x34/0x35.
        max_erase_s: Ceiling for one erase, responsePending answers included.
        evidence: Where the profile's facts are documented (required for a real target).
    """

    target_id: str
    display_name: str
    simulated: bool
    regions: tuple[MemoryRegion, ...]
    sector_size: int
    identity_patterns: Mapping[int, str]
    bootloader_identity_patterns: Mapping[int, str]
    unit_identity_dids: tuple[int, ...]
    security_level: int
    seed_key_provider_id: str
    erase_routine_id: int
    check_routine_id: int
    address_and_length_format: int = 0x44
    max_erase_s: float = 30.0
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not self.simulated:
            raise BenchTargetNotQualifiedError(
                f"bench target {self.target_id!r} is not simulated: no real ECU is qualified "
                "for bench programming in this build (needs an evidence-backed profile and a "
                "separate owner approval)"
            )
        if not self.target_id.strip() or not self.display_name.strip():
            raise ValueError("a bench target needs an id and a display name")
        if not self.seed_key_provider_id.strip():
            raise ValueError("a bench target needs a seed-key provider id")
        if not self.regions:
            raise ValueError("a bench target needs at least one flash region")
        if self.sector_size <= 0:
            raise ValueError("sector_size must be positive")
        if self.security_level % 2 != 1 or not 0x01 <= self.security_level <= 0x41:
            raise ValueError("security_level must be an odd requestSeed level (0x01..0x41)")
        for name in ("erase_routine_id", "check_routine_id"):
            if not 0 <= getattr(self, name) <= 0xFFFF:
                raise ValueError(f"{name} must be a 16-bit routine identifier")
        if not self.unit_identity_dids:
            raise ValueError("a bench target needs unit identity DIDs to bind backups to")
        if len(set(self.unit_identity_dids)) != len(self.unit_identity_dids):
            raise ValueError("unit identity DIDs must be unique")
        for did in self.unit_identity_dids:
            if not 0 <= did <= 0xFFFF:
                raise ValueError("unit identity DIDs must be 16-bit identifiers")
            if did in SOFTWARE_IDENTIFICATION_DIDS:
                raise ValueError(
                    f"DID {did:04X} identifies software, which a flash changes; it cannot "
                    "identify the unit"
                )
        if self.erase_routine_id == self.check_routine_id:
            raise ValueError("erase and check routines must differ")
        if self.address_and_length_format != 0x44:
            raise ValueError("only addressAndLengthFormatIdentifier 0x44 is supported")
        if self.max_erase_s <= 0:
            raise ValueError("max_erase_s must be positive")
        expected_offset = 0
        for index, region in enumerate(self.regions):
            if region.image_offset != expected_offset:
                raise ValueError("regions must cover the image contiguously, in image order")
            expected_offset = region.image_end
            if region.start_address % self.sector_size or region.size % self.sector_size:
                raise ValueError(f"region {region.describe()} is not sector aligned")
            for other in self.regions[index + 1 :]:
                if region.overlaps(other):
                    raise ValueError(f"regions {region.describe()} and {other.describe()} overlap")

    @property
    def image_size(self) -> int:
        """The exact ROM image size this target accepts."""
        return sum(region.size for region in self.regions)

    def shows_application(self, identity: EcuIdentity) -> bool:
        """The ECU runs this target's application (its identity patterns match)."""
        return not identity.mismatches(self.identity_patterns)

    def shows_bootloader(self, identity: EcuIdentity) -> bool:
        """The ECU runs this target's bootloader: the bootloader patterns match and the
        application ones do not (Milestone 7e.1: the bootloader patterns alone also match an
        application identity)."""
        return not identity.mismatches(
            self.bootloader_identity_patterns
        ) and not self.shows_application(identity)

    def unit_fingerprint(self, identity: EcuIdentity) -> str | None:
        """:meth:`EcuIdentity.unit_fingerprint` over this target's unit DIDs."""
        return identity.unit_fingerprint(self.unit_identity_dids)


#: The virtual programmable ECU (Milestone 7b): one 64 KiB region in four 16 KiB sectors,
#: matching the SIMULATED demo ROM (``tuning.demo.DEMO_ROM_SIZE``). Every value is invented
#: and labelled simulated; it describes no real ECU.
SIMULATED_BENCH_TARGET: Final[BenchTargetProfile] = BenchTargetProfile(
    target_id="simulated.bench_ecu_v1",
    display_name="SIMULATED bench ECU (virtual, not a real ECU)",
    simulated=True,
    regions=(MemoryRegion(start_address=0x0008_0000, size=64 * 1024, image_offset=0),),
    sector_size=16 * 1024,
    identity_patterns={0xF187: "SIM-PART-*", 0xF191: "SIM-HW-*"},
    bootloader_identity_patterns={0xF191: "SIM-HW-*"},
    unit_identity_dids=(0xF191, 0xF18C),  # hardware number, serial number
    security_level=0x01,
    seed_key_provider_id="simulated-xor",
    erase_routine_id=0xFF00,
    check_routine_id=0xFF01,
)


# --------------------------------------------------------------------------- #
# human attestation and the BENCH_FLASH policy
# --------------------------------------------------------------------------- #

#: What the operator types to confirm the attestation (the dashboard explains it in Persian
#: and English; the phrase itself is fixed so it cannot be mistranslated into a yes).
BENCH_CONFIRMATION_PHRASE: Final = "BENCH FLASH"


@dataclass(frozen=True, slots=True)
class BenchAttestation:
    """What a human confirms before a bench flash session; recorded in the journal.

    The software cannot measure these facts (it cannot see the bench or the supply), so they
    are explicit statements, not checks. Machine-verifiable preconditions (ROM validity,
    backup directory, link stability, the backup itself) are verified by the pipeline.

    Attributes:
        ecu_on_bench_out_of_vehicle: The ECU is on the bench, never in a vehicle harness.
        supply_stable_and_current_limited: A stable, current-limited bench supply powers it.
        wiring_verified: Power, ground and CAN wiring were checked.
        owner_approval_recorded: The project owner authorised this specific operation.
        operator: Who made the attestation (free text, kept in the journal).
        confirmation_phrase: Must be exactly :data:`BENCH_CONFIRMATION_PHRASE`.
        attested_at: When (timezone aware).
        simulated: ``True`` for a session against the virtual programmable ECU.
    """

    ecu_on_bench_out_of_vehicle: bool
    supply_stable_and_current_limited: bool
    wiring_verified: bool
    owner_approval_recorded: bool
    operator: str
    confirmation_phrase: str
    attested_at: datetime
    simulated: bool

    def __post_init__(self) -> None:
        _require_aware(self.attested_at, "attested_at")

    #: The yes/no statements; every one must be ``True``.
    STATEMENTS: ClassVar[tuple[str, ...]] = (
        "ecu_on_bench_out_of_vehicle",
        "supply_stable_and_current_limited",
        "wiring_verified",
        "owner_approval_recorded",
    )

    def unmet(self) -> tuple[str, ...]:
        """Names of the items that are not satisfied."""
        missing = [name for name in self.STATEMENTS if getattr(self, name) is not True]
        if not self.operator.strip():
            missing.append("operator")
        if self.confirmation_phrase != BENCH_CONFIRMATION_PHRASE:
            missing.append("confirmation_phrase")
        return tuple(missing)

    @property
    def is_complete(self) -> bool:
        return not self.unmet()


def bench_flash_policy(attestation: BenchAttestation) -> SafetyPolicy:
    """The only way to obtain a policy that allows ``BENCH_FLASH`` (code-level, per session).

    Raises:
        SafetyGuardError: If any attestation item is unmet.
    """
    unmet = attestation.unmet()
    if unmet:
        raise SafetyGuardError(
            "refusing to enable bench flash mode, unmet attestation item(s): " + ", ".join(unmet)
        )
    return SafetyPolicy(
        allowed=frozenset({OperationClass.READ_ONLY, OperationClass.BENCH_FLASH}),
        bench_mode=True,
    )


# --------------------------------------------------------------------------- #
# capabilities (minted only in tuning; see the module docstring)
# --------------------------------------------------------------------------- #


class _MintToken:
    """Private proof that a capability was created by its minting module."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<bench capability mint token>"


_MINT: Final = _MintToken()


def _require_mint(token: object, what: str) -> None:
    if token is not _MINT:
        raise SafetyGuardError(
            f"a {what} can only be issued by its minting module (it is a capability, not data)"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FlashAuthorization:
    """Permission to change one bench ECU's memory for one plan (one image, one target).

    Issued only by ``tuning/flash_gate.py``; every request that changes ECU state checks it
    with :meth:`check_for`.

    Attributes:
        policy: The session policy; must allow ``BENCH_FLASH``.
        attestation: The complete human attestation it was issued for.
        target_id: The bench target it is valid for.
        image_sha256: The image it is valid for.
        simulated: ``True`` for the virtual programmable ECU; must equal the attestation's.
        nonce: Unique per authorisation (recorded in the journal).
        issued_at: When it was issued (timezone aware).
    """

    policy: SafetyPolicy
    attestation: BenchAttestation
    target_id: str
    image_sha256: str
    simulated: bool
    nonce: str
    issued_at: datetime
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_mint(self._token, "FlashAuthorization")
        if not self.policy.allows(OperationClass.BENCH_FLASH):
            raise SafetyGuardError("the session policy does not allow bench flashing")
        if not self.attestation.is_complete:
            raise SafetyGuardError(
                "the attestation is incomplete: " + ", ".join(self.attestation.unmet())
            )
        if self.simulated != self.attestation.simulated:
            raise SafetyGuardError("authorisation and attestation disagree about simulation")
        if not self.simulated:
            raise BenchTargetNotQualifiedError("only simulated bench flashing exists in this build")
        _require_sha256(self.image_sha256, "image_sha256")
        if len(self.nonce) < 16:
            raise ValueError("the authorisation nonce is too short")
        _require_aware(self.issued_at, "issued_at")

    def check_for(self, *, target_id: str, image_sha256: str, simulated: bool) -> None:
        """Refuse use for another target, another image or the other kind of link.

        Raises:
            SafetyGuardError: On any mismatch, or if the policy no longer allows the mode.
        """
        if not self.policy.allows(OperationClass.BENCH_FLASH):
            raise SafetyGuardError("the session policy does not allow bench flashing")
        if target_id != self.target_id:
            raise SafetyGuardError(
                f"authorisation is for target {self.target_id!r}, not {target_id!r}"
            )
        if image_sha256 != self.image_sha256:
            raise SafetyGuardError("authorisation was issued for a different image")
        if simulated != self.simulated:
            raise SafetyGuardError(
                "authorisation was issued for a "
                + ("simulated" if self.simulated else "real")
                + " link"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class BackupReceipt:
    """Proof that the target's memory was read, verified and persisted before any erase.

    Issued only by ``tuning/backup_vault.py`` after the read-out was taken twice with equal
    hashes, written once to disk and read back from disk with the same SHA-256.

    Attributes:
        path: Where the backup image is stored.
        sha256: SHA-256 of the backup image.
        size: Its size in bytes.
        regions: The regions it contains (in image order).
        target_id: The bench target it was read from.
        identity_fingerprint: :meth:`EcuIdentity.fingerprint` of the ECU it was read from
            (the full record, software DIDs included).
        unit_fingerprint: :meth:`BenchTargetProfile.unit_fingerprint` of that ECU - what
            the backup is bound to (the same unit, whatever software it runs now).
        simulated: ``True`` when read from the virtual programmable ECU.
        created_at: When it was verified (timezone aware).
    """

    path: str
    sha256: str
    size: int
    regions: tuple[MemoryRegion, ...]
    target_id: str
    identity_fingerprint: str
    unit_fingerprint: str
    simulated: bool
    created_at: datetime
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_mint(self._token, "BackupReceipt")
        if not self.path.strip():
            raise ValueError("a backup receipt needs the backup path")
        _require_sha256(self.sha256, "sha256")
        _require_sha256(self.identity_fingerprint, "identity_fingerprint")
        _require_sha256(self.unit_fingerprint, "unit_fingerprint")
        if not self.regions:
            raise ValueError("a backup receipt must cover at least one region")
        if self.size != sum(region.size for region in self.regions):
            raise ValueError("backup size does not equal the size of its regions")
        _require_aware(self.created_at, "created_at")

    def covers(self, region: MemoryRegion) -> bool:
        """Whether ``region`` lies completely inside one backed-up region."""
        return any(backed.contains(region) for backed in self.regions)

    def check_covers(self, regions: Iterable[MemoryRegion]) -> None:
        """Refuse to erase or write anything the backup does not contain.

        Raises:
            SafetyGuardError: For the first region that is not covered.
        """
        for region in regions:
            if not self.covers(region):
                raise SafetyGuardError(
                    f"region {region.describe()} is not covered by backup {self.path}: "
                    "refusing to change memory that cannot be restored"
                )

    def check_for(self, *, target_id: str, unit_fingerprint: str | None, simulated: bool) -> None:
        """Refuse a receipt that belongs to another target, another unit or the other link.

        ``unit_fingerprint`` is ``None`` when the connected ECU's unit DIDs were not all read:
        the unit is not proven, so that is refused too.

        Raises:
            SafetyGuardError: On any mismatch.
        """
        if target_id != self.target_id:
            raise SafetyGuardError(f"backup is of target {self.target_id!r}, not {target_id!r}")
        if unit_fingerprint is None:
            raise SafetyGuardError(
                "the connected ECU did not report all unit identity DIDs: it cannot be shown to "
                "be the ECU the backup was read from"
            )
        if unit_fingerprint != self.unit_fingerprint:
            raise SafetyGuardError("backup was read from a different ECU (unit identity mismatch)")
        if simulated != self.simulated:
            raise SafetyGuardError("backup and link disagree about simulation")


# --------------------------------------------------------------------------- #
# progress and ports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FlashProgress:
    """One progress report from the pipeline (for the CLI and the dashboard).

    Attributes:
        stage: Current stage.
        done_bytes / total_bytes: Progress of the current stage's data movement (``0``/``0``
            for stages that move no data).
        message: Short English description (the dashboard translates by ``stage``).
        block_sequence: Last acknowledged UDS 0x36 block sequence counter, if any.
    """

    stage: FlashStage
    done_bytes: int = 0
    total_bytes: int = 0
    message: str = ""
    block_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.done_bytes < 0 or self.total_bytes < 0 or self.done_bytes > self.total_bytes:
            raise ValueError("progress byte counts must satisfy 0 <= done <= total")

    @property
    def fraction(self) -> float:
        return 1.0 if self.total_bytes == 0 else self.done_bytes / self.total_bytes


ProgressSink = Callable[[FlashProgress], None]

#: ``FlashProgress.message`` of the report sent right after ``erasing`` was journaled and
#: before the first erase request: the point of no return (the UI locks "cancel" there).
ERASE_PROGRESS_MESSAGE: Final = "erase"


@runtime_checkable
class SeedKeyProvider(Protocol):
    """Computes the UDS 0x27 key for a seed. **Interface only** in this project.

    The only implementation is the simulated one used by the virtual programmable ECU. No
    real ECU algorithm is shipped, guessed or reverse engineered here, and there is no
    plugin mechanism to load one. A provider is tried exactly once per unlock: a wrong key
    aborts (it is never retried, so an ECU's attempt counter cannot be run down).
    """

    @property
    def provider_id(self) -> str: ...

    @property
    def simulated(self) -> bool: ...

    def compute_key(self, level: int, seed: bytes) -> bytes:
        """Key for ``seed`` at requestSeed ``level`` (odd)."""
        ...


@runtime_checkable
class BenchProgrammerPort(Protocol):
    """Bench programming primitives, implemented in ``hardware`` (Milestone 7c).

    Methods that change ECU state take a :class:`FlashAuthorization`; methods that erase or
    write memory also take the :class:`BackupReceipt`. Implementations check both before
    the first frame of each request (an architecture test enforces the signatures).
    """

    @property
    def target(self) -> BenchTargetProfile: ...

    @property
    def simulated(self) -> bool: ...

    def check_link(self, round_trips: int) -> float:
        """TesterPresent round trips; returns the slowest one in seconds."""
        ...

    def read_identity(self, dids: Iterable[int]) -> EcuIdentity:
        """Read identification DIDs (read-only)."""
        ...

    def enter_programming_session(self, *, authorization: FlashAuthorization) -> None: ...

    def unlock(self, *, authorization: FlashAuthorization, provider: SeedKeyProvider) -> None: ...

    def upload_region(
        self,
        region: MemoryRegion,
        *,
        authorization: FlashAuthorization,
        progress: ProgressSink | None = None,
    ) -> bytes:
        """Read ``region`` out of the ECU (UDS 0x35 upload); used for backup and read-back."""
        ...

    def erase_region(
        self,
        region: MemoryRegion,
        *,
        authorization: FlashAuthorization,
        backup: BackupReceipt,
    ) -> None: ...

    def download_region(
        self,
        region: MemoryRegion,
        data: bytes,
        *,
        authorization: FlashAuthorization,
        backup: BackupReceipt,
        progress: ProgressSink | None = None,
    ) -> None: ...

    def check_memory(
        self, region: MemoryRegion, crc32: int, *, authorization: FlashAuthorization
    ) -> bool: ...

    def hard_reset(self, *, authorization: FlashAuthorization) -> None: ...

    def return_to_default_session(self) -> None: ...

    def close(self) -> None: ...
