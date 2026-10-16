"""Configuration loading: defaults, file, environment, and rejection of bad input."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from iran_obd_tuner.core.config import (
    ENV_PREFIX,
    FIELD_CASTERS,
    SOURCE_FIELD,
    AppSettings,
    config_file_for,
    load_settings,
)
from iran_obd_tuner.core.exceptions import ConfigurationError

VALID_CONFIG = """
adapter = "elm327"
serial_port = "COM7"
serial_baudrate = 115200
connect_timeout_s = 3.5
command_timeout_s = 0.75
live_data_pids = ["010C", "010D"]
log_level = "debug"
output_dir = "artifacts"
"""


def _load(**kwargs: object) -> AppSettings:
    kwargs.setdefault("env", {})
    kwargs.setdefault("use_default_config_file", False)
    return load_settings(**kwargs)  # type: ignore[arg-type]


def test_defaults_are_safe_and_hardware_free() -> None:
    settings = _load()
    assert settings.adapter == "mock"
    assert settings.serial_port is None
    assert settings.log_level == "INFO"
    assert settings.config_file is None
    assert "010C" in settings.live_data_pids


def test_file_values_are_loaded_and_recorded(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(VALID_CONFIG, encoding="utf-8")

    settings = _load(config_file=path, env={})

    assert settings.config_file == path
    assert settings.adapter == "elm327"
    assert settings.serial_port == "COM7"
    assert settings.serial_baudrate == 115200
    assert settings.connect_timeout_s == 3.5
    assert settings.command_timeout_s == 0.75
    assert settings.live_data_pids == ("010C", "010D")
    assert settings.log_level == "DEBUG"
    assert settings.output_dir == Path("artifacts")


def test_environment_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(VALID_CONFIG, encoding="utf-8")

    settings = _load(
        config_file=path,
        env={
            f"{ENV_PREFIX}ADAPTER": "mock",
            f"{ENV_PREFIX}LIVE_DATA_PIDS": "010c, 010d ,0105",
            f"{ENV_PREFIX}CONNECT_TIMEOUT_S": "9",
            f"{ENV_PREFIX}LOG_FILE": "logs/run.log",
        },
    )

    assert settings.adapter == "mock"
    assert settings.live_data_pids == ("010C", "010D", "0105")
    assert settings.connect_timeout_s == 9.0
    assert settings.log_file == Path("logs/run.log")


def test_default_config_file_is_picked_up_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text(
        'adapter = "from-default-file"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env={})

    assert settings.adapter == "from-default-file"
    assert settings.config_file == Path("config/local.toml")


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text('adapterr = "mock"\n', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unknown key"):
        _load(config_file=path, env={})


def test_malformed_toml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text("adapter = \n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not valid TOML"):
        _load(config_file=path, env={})


def test_missing_explicit_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        _load(config_file=tmp_path / "absent.toml", env={})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LIVE_DATA_PIDS", "010C,not-a-pid"),
        ("CONNECT_TIMEOUT_S", "0"),
        ("CONNECT_TIMEOUT_S", "soon"),
        ("SERIAL_BAUDRATE", "-1"),
        ("LOG_LEVEL", "verbose"),
        ("ADAPTER", "   "),
    ],
)
def test_invalid_environment_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        _load(env={f"{ENV_PREFIX}{key}": value})


def test_settings_validate_direct_construction() -> None:
    with pytest.raises(ConfigurationError, match="timeouts must be positive"):
        AppSettings(connect_timeout_s=0.0)
    with pytest.raises(ConfigurationError, match="invalid PID"):
        AppSettings(live_data_pids=("nope",))


def test_every_setting_has_a_caster() -> None:
    """Guards against adding a field that can never be configured."""
    field_names = {field.name for field in fields(AppSettings)} - {SOURCE_FIELD}
    assert field_names == set(FIELD_CASTERS)


def test_as_dict_is_serialisable() -> None:
    view = _load(env={f"{ENV_PREFIX}LOG_FILE": "log.txt"}).as_dict()
    assert set(view) == {field.name for field in fields(AppSettings)}
    assert view["log_file"] == "log.txt"
    assert isinstance(view["live_data_pids"], list)


def test_config_file_for_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        config_file_for(tmp_path / "absent.toml")
    assert config_file_for(None, use_default_config_file=False) is None
