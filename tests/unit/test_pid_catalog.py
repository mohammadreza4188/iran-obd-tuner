"""PID catalogue: identifiers, units and the configured live data list."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.models import Unit
from iran_obd_tuner.core.pids import (
    DEFAULT_LIVE_DATA_PIDS,
    PID_CATALOG,
    PidSpec,
    describe_pid,
    is_valid_pid,
    normalize_pid,
)


def test_catalog_is_consistent() -> None:
    codes = [spec.pid for spec in PID_CATALOG]
    assert len(codes) == len(set(codes)), "duplicate PID in the catalogue"
    assert all(spec.name and spec.description for spec in PID_CATALOG)


def test_live_data_defaults_exist_in_catalog() -> None:
    for pid in DEFAULT_LIVE_DATA_PIDS:
        assert describe_pid(pid) is not None, f"{pid} missing from the PID catalogue"


def test_describe_pid_is_case_insensitive_and_normalises() -> None:
    spec = describe_pid("010c")

    assert spec is not None
    assert spec.pid == "010C"
    assert spec.unit is Unit.RPM
    assert normalize_pid(" 010c ") == "010C"


def test_describe_pid_returns_none_for_unknown_parameter() -> None:
    assert describe_pid("01FF") is None


@pytest.mark.parametrize("pid", ["010C", "0142", "0100"])
def test_valid_pids(pid: str) -> None:
    assert is_valid_pid(pid)


@pytest.mark.parametrize("pid", ["", "   ", "0C", "010", "01GG", "01C", "020C", "010C0"])
def test_invalid_pids(pid: str) -> None:
    assert is_valid_pid(pid) is False


def test_pid_spec_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="invalid PID"):
        PidSpec("nope", "Nonsense", Unit.COUNT, "Not a real parameter")
