"""Calibration interfaces and the preconditions for any future ECU write.

This module is intentionally free of protocol code: it defines *what* would be done, and
*what must hold* before it may be done. Implementations (UDS over CAN, bootloader, ...) do
not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import ClassVar, Protocol, runtime_checkable

from iran_obd_tuner.core.bench import BenchAttestation
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy, require

__all__ = [
    "BenchMachineChecks",
    "BenchSafetyChecklist",
    "CalibrationImage",
    "CalibrationReadRequest",
    "CalibrationReader",
    "CalibrationWriter",
    "assert_write_allowed",
]


@dataclass(frozen=True, slots=True)
class BenchSafetyChecklist:
    """Preconditions that must hold before any ECU calibration write is attempted.

    Every item is a statement a human has to be able to answer with "yes, verified"; the
    checklist is passed explicitly to a writer so that skipping it is a visible decision,
    not an omission.

    Attributes:
        bench_connection_confirmed: ECU is on the bench, out of the vehicle, powered
            externally — never written through the vehicle harness.
        stock_backup_created: The original calibration image was read and stored.
        backup_read_back_and_verified: The backup was read back and compared, so a restore
            is actually possible.
        supply_voltage_stable: Supply voltage is within the ECU specification for the whole
            operation.
        checksum_strategy_known: Checksum algorithm and affected ranges are documented for
            this ECU family (evidence, not a guess).
        calibration_fits_ecu: The new image is structurally valid for this ECU (size,
            version, matching hardware/software identifiers).
        vehicle_specific_validation_done: The change was validated for this exact vehicle
            family and software version.
        owner_approval_recorded: An authorisation for this specific operation exists.
    """

    bench_connection_confirmed: bool = False
    stock_backup_created: bool = False
    backup_read_back_and_verified: bool = False
    supply_voltage_stable: bool = False
    checksum_strategy_known: bool = False
    calibration_fits_ecu: bool = False
    vehicle_specific_validation_done: bool = False
    owner_approval_recorded: bool = False

    #: Milestone 7 split: items only a human can confirm (a :class:`~iran_obd_tuner.core.bench.
    #: BenchAttestation`) and items the software proves itself (:class:`BenchMachineChecks`).
    HUMAN_ITEMS: ClassVar[tuple[str, ...]] = (
        "bench_connection_confirmed",
        "supply_voltage_stable",
        "owner_approval_recorded",
    )
    MACHINE_ITEMS: ClassVar[tuple[str, ...]] = (
        "stock_backup_created",
        "backup_read_back_and_verified",
        "checksum_strategy_known",
        "calibration_fits_ecu",
        "vehicle_specific_validation_done",
    )

    def unmet(self) -> tuple[str, ...]:
        """Names of the checklist items that are not satisfied."""
        return tuple(field.name for field in fields(self) if not getattr(self, field.name))

    @property
    def is_satisfied(self) -> bool:
        """``True`` when every precondition holds."""
        return not self.unmet()

    @classmethod
    def compose(
        cls, attestation: BenchAttestation, machine: BenchMachineChecks
    ) -> BenchSafetyChecklist:
        """The classic eight-item view of a Milestone 7 bench session (compatibility).

        Human items come from the attestation, the rest from what the software verified.
        ``vehicle_specific_validation_done`` is only ``True`` for a simulated target: there is
        no vehicle to validate for the virtual ECU, and a real target needs evidence first.
        """
        return cls(
            bench_connection_confirmed=attestation.ecu_on_bench_out_of_vehicle
            and attestation.wiring_verified,
            supply_voltage_stable=attestation.supply_stable_and_current_limited,
            owner_approval_recorded=attestation.owner_approval_recorded,
            stock_backup_created=machine.backup_created,
            backup_read_back_and_verified=machine.backup_verified,
            checksum_strategy_known=machine.checksums_valid,
            calibration_fits_ecu=machine.image_fits_target,
            vehicle_specific_validation_done=machine.target_simulated,
        )


@dataclass(frozen=True, slots=True)
class BenchMachineChecks:
    """What the software itself verified for a bench flash (Milestone 7).

    Attributes:
        backup_dir_writable: The backup directory (or its nearest existing parent) is
            writable by this process.
        free_bytes / required_bytes: Free space there, and what the backup needs.
        image_fits_target: The flash plan was accepted (size, regions, integrity).
        checksums_valid: Every configured checksum is ``valid`` (``False`` with the
            simulated-only override).
        target_simulated: The target is the simulated bench ECU.
        backup_created / backup_verified: The two read-outs matched, were written once and
            read back from disk with the same SHA-256 (set after stage 2).
    """

    backup_dir_writable: bool
    free_bytes: int
    required_bytes: int
    image_fits_target: bool
    checksums_valid: bool
    target_simulated: bool
    backup_created: bool = False
    backup_verified: bool = False

    @property
    def enough_space(self) -> bool:
        return self.free_bytes >= self.required_bytes

    def problems(self) -> tuple[str, ...]:
        """What blocks an authorisation (backup items are checked later, in stage 2)."""
        found = []
        if not self.backup_dir_writable:
            found.append("the backup directory is not writable")
        if not self.enough_space:
            found.append(
                f"not enough free space for the backup ({self.free_bytes} of "
                f"{self.required_bytes} bytes)"
            )
        if not self.image_fits_target:
            found.append("the image does not fit the target")
        if not self.target_simulated:
            found.append("the target is not simulated")
        return tuple(found)


@dataclass(frozen=True, slots=True)
class CalibrationReadRequest:
    """Request to read a region of ECU calibration memory on the bench.

    Attributes:
        ecu_family: Target ECU family identifier from the knowledge database.
        start_address: First address to read, in the ECU's own address space.
        length: Number of bytes to read.
    """

    ecu_family: str
    start_address: int
    length: int

    def __post_init__(self) -> None:
        if not self.ecu_family.strip():
            raise ValueError("ecu_family must not be empty")
        if self.start_address < 0:
            raise ValueError("start_address must not be negative")
        if self.length <= 0:
            raise ValueError("length must be positive")


@dataclass(frozen=True, slots=True)
class CalibrationImage:
    """A block of ECU calibration memory.

    Attributes:
        ecu_family: ECU family the image belongs to.
        start_address: Address of the first byte of ``data``.
        data: Raw bytes.
        checksum_verified: Whether a checksum was validated over the whole calibration
            region. Only ``True`` when that was actually done.
    """

    ecu_family: str
    start_address: int
    data: bytes
    checksum_verified: bool = False

    @property
    def size(self) -> int:
        """Number of bytes in the image."""
        return len(self.data)


@runtime_checkable
class CalibrationReader(Protocol):
    """Read-only bench access to ECU calibration memory.

    Reading is allowed by the default safety policy; no implementation exists yet.
    """

    def read_calibration(self, request: CalibrationReadRequest) -> CalibrationImage:
        """Read the requested region and return it."""
        ...


@runtime_checkable
class CalibrationWriter(Protocol):
    """Write access to ECU calibration memory. **Not implemented — blocked by policy.**

    Any implementation must call :func:`assert_write_allowed` before its first frame is
    sent. A writer that does not do this must not be merged.
    """

    def write_calibration(
        self,
        image: CalibrationImage,
        *,
        checklist: BenchSafetyChecklist,
        policy: SafetyPolicy = DEFAULT_POLICY,
    ) -> None:
        """Write ``image`` to the ECU after validating ``checklist``."""
        ...


def assert_write_allowed(
    checklist: BenchSafetyChecklist,
    *,
    policy: SafetyPolicy = DEFAULT_POLICY,
    detail: str = "write ECU calibration memory",
) -> None:
    """Refuse a calibration write unless the policy allows it and the checklist is complete.

    Args:
        checklist: Verified preconditions for this specific write.
        policy: Safety policy of the caller.
        detail: Description of the concrete operation, used in the error message.

    Raises:
        SafetyGuardError: If the policy denies calibration writes (always the case with the
            default policy), or if any precondition is unmet.
    """
    require(OperationClass.CALIBRATION_WRITE, policy=policy, detail=detail)

    unmet = checklist.unmet()
    if unmet:
        raise SafetyGuardError(
            "refusing to write ECU calibration memory, unmet precondition(s): "
            + ", ".join(unmet)
            + " (see docs/architecture.md, section 'Safety boundary')"
        )
