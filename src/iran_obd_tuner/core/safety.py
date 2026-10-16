"""Safety boundary between read-only diagnostics and state-changing ECU work.

Design rule of the project: *reading* vehicle data is always allowed, while anything that
changes vehicle state (clearing trouble codes, actuator tests, ECU flashing, calibration
writes) is denied unless a caller deliberately constructs a policy that allows it — and
even then the operation is only allowed if an implementation actually exists (none does in
this build).

The policy is intentionally a code-level object, never a value loaded from a configuration
file or environment variable: a mis-edited ``config/local.toml`` must not be able to arm
ECU writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from iran_obd_tuner.core.exceptions import SafetyGuardError

__all__ = [
    "DEFAULT_POLICY",
    "OperationClass",
    "SafetyPolicy",
    "require",
]


class OperationClass(StrEnum):
    """Risk class of an operation performed on (or against) a vehicle."""

    #: Reading data: trouble codes, measurements, identification. Always allowed.
    READ_ONLY = "read_only"
    #: Changes vehicle state but not its calibration, e.g. clearing trouble codes (service 04).
    STATE_CHANGING = "state_changing"
    #: Writes ECU calibration/flash memory, e.g. bench remapping.
    CALIBRATION_WRITE = "calibration_write"

    @property
    def is_destructive(self) -> bool:
        """``True`` for every class except :attr:`OperationClass.READ_ONLY`."""
        return self is not OperationClass.READ_ONLY


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """Set of operation classes a caller is allowed to perform.

    The default policy allows read-only work only, so no code path can change a vehicle
    unless it is handed a different, explicitly constructed policy.

    Attributes:
        allowed: Operation classes that may be performed.
        bench_mode: Whether the caller has confirmed that the ECU is on the bench (out of
            the vehicle). Informational only: it never grants write permission by itself.
    """

    allowed: frozenset[OperationClass] = field(
        default_factory=lambda: frozenset({OperationClass.READ_ONLY})
    )
    bench_mode: bool = False

    def allows(self, operation: OperationClass) -> bool:
        """Return ``True`` when ``operation`` may be performed under this policy."""
        return operation in self.allowed


#: Policy used everywhere unless a caller passes something else: read-only.
DEFAULT_POLICY: Final[SafetyPolicy] = SafetyPolicy()


def require(
    operation: OperationClass,
    *,
    policy: SafetyPolicy = DEFAULT_POLICY,
    detail: str = "",
) -> None:
    """Refuse ``operation`` unless ``policy`` allows it.

    Args:
        operation: The operation class about to be performed.
        policy: Policy that applies to the caller.
        detail: Short description of the concrete operation, used in the error message.

    Raises:
        SafetyGuardError: If ``policy`` does not allow ``operation``.
    """
    if policy.allows(operation):
        return

    what = detail.strip() or operation.value.replace("_", " ")
    raise SafetyGuardError(
        f"blocked {operation.value} operation ({what}): this build is read-only by default. "
        "State-changing and calibration-write features are not implemented; enabling them "
        "requires explicit developer/owner approval, a documented work order and controlled, "
        "vehicle-specific validation (see docs/architecture.md, section 'Safety boundary')."
    )
