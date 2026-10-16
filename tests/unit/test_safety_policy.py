"""The safety boundary: read-only by default, writes refused."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy, require
from iran_obd_tuner.tuning.interfaces import (
    BenchSafetyChecklist,
    CalibrationImage,
    CalibrationReadRequest,
    assert_write_allowed,
)

ALLOWING_POLICY = SafetyPolicy(allowed=frozenset(OperationClass))


def test_default_policy_allows_read_only_only() -> None:
    assert DEFAULT_POLICY.allows(OperationClass.READ_ONLY)
    assert not DEFAULT_POLICY.allows(OperationClass.STATE_CHANGING)
    assert not DEFAULT_POLICY.allows(OperationClass.CALIBRATION_WRITE)


def test_read_only_operations_are_allowed() -> None:
    require(OperationClass.READ_ONLY, detail="read stored trouble codes")


@pytest.mark.parametrize(
    "operation", [OperationClass.STATE_CHANGING, OperationClass.CALIBRATION_WRITE]
)
def test_destructive_operations_are_refused_with_a_reason(operation: OperationClass) -> None:
    with pytest.raises(SafetyGuardError) as caught:
        require(operation, detail="unit test")

    message = str(caught.value)
    assert operation.value in message
    assert "read-only" in message
    assert operation.is_destructive is True


def test_explicit_policy_can_allow_write_classes() -> None:
    """The guard is the only barrier; it is bypassable only by an explicit code-level policy."""
    require(OperationClass.CALIBRATION_WRITE, policy=ALLOWING_POLICY, detail="unit test")


def test_complete_checklist_is_required_even_with_an_allowing_policy() -> None:
    empty = BenchSafetyChecklist()
    assert empty.is_satisfied is False
    assert "stock_backup_created" in empty.unmet()

    with pytest.raises(SafetyGuardError, match="unmet precondition"):
        assert_write_allowed(empty, policy=ALLOWING_POLICY)


def test_write_is_refused_by_default_policy_before_the_checklist_matters() -> None:
    complete = BenchSafetyChecklist(
        bench_connection_confirmed=True,
        stock_backup_created=True,
        backup_read_back_and_verified=True,
        supply_voltage_stable=True,
        checksum_strategy_known=True,
        calibration_fits_ecu=True,
        vehicle_specific_validation_done=True,
        owner_approval_recorded=True,
    )
    assert complete.is_satisfied is True

    with pytest.raises(SafetyGuardError, match="calibration_write"):
        assert_write_allowed(complete)

    # With an explicitly allowing policy the checklist passes: the mechanics are sound,
    # only the policy stops it (and no writer implementation exists).
    assert_write_allowed(complete, policy=ALLOWING_POLICY)


def test_read_request_validates_its_arguments() -> None:
    request = CalibrationReadRequest(ecu_family="example-family", start_address=0x8000, length=512)
    assert request.length == 512

    with pytest.raises(ValueError):
        CalibrationReadRequest(ecu_family=" ", start_address=0, length=1)
    with pytest.raises(ValueError):
        CalibrationReadRequest(ecu_family="example-family", start_address=-1, length=1)
    with pytest.raises(ValueError):
        CalibrationReadRequest(ecu_family="example-family", start_address=0, length=0)


def test_calibration_image_reports_its_size_and_is_unverified_by_default() -> None:
    image = CalibrationImage(ecu_family="example-family", start_address=0, data=b"\x00\x01\x02")

    assert image.size == 3
    assert image.checksum_verified is False
