"""Calibration interfaces and the preconditions for any future ECU write.

This module is intentionally free of protocol code: it defines *what* would be done, and
*what must hold* before it may be done. Implementations (UDS over CAN, bootloader, ...) do
not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Protocol, runtime_checkable

from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy, require

__all__ = [
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

    def unmet(self) -> tuple[str, ...]:
        """Names of the checklist items that are not satisfied."""
        return tuple(field.name for field in fields(self) if not getattr(self, field.name))

    @property
    def is_satisfied(self) -> bool:
        """``True`` when every precondition holds."""
        return not self.unmet()


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
