"""CAN / UDS settings: parsing, validation, and what deliberately is *not* a setting."""

from __future__ import annotations

from pathlib import Path

import pytest

from iran_obd_tuner.app.application import uds_link_config
from iran_obd_tuner.core.config import (
    CAN_INTERFACE_CHOICES,
    FIELD_CASTERS,
    AppSettings,
    load_settings,
)
from iran_obd_tuner.core.exceptions import ConfigurationError
from iran_obd_tuner.hardware.uds_link import LINK_INTERFACES


def _load(tmp_path: Path, body: str, env: dict[str, str] | None = None) -> AppSettings:
    path = tmp_path / "settings.toml"
    path.write_text(body, encoding="utf-8")
    return load_settings(path, env=env or {}, use_default_config_file=False)


def test_defaults_are_the_simulated_ecu_and_iso_15765_4_identifiers() -> None:
    settings = AppSettings()

    assert settings.can_interface == "virtual-sim"
    assert settings.can_channel is None
    assert settings.can_bitrate == 500_000
    assert (settings.uds_request_id, settings.uds_response_id) == (0x7E0, 0x7E8)
    assert settings.isotp_padding == 0xCC


def test_identifiers_accept_hex_text_and_integers(tmp_path: Path) -> None:
    settings = _load(
        tmp_path,
        'can_interface = "PCAN"\ncan_channel = "PCAN_USBBUS1"\ncan_bitrate = 250000\n'
        'uds_request_id = "0x7E1"\nuds_response_id = 2025\nisotp_padding = "none"\n',
    )

    assert settings.can_interface == "pcan"
    assert settings.can_channel == "PCAN_USBBUS1"
    assert settings.can_bitrate == 250_000
    assert (settings.uds_request_id, settings.uds_response_id) == (0x7E1, 0x7E9)
    assert settings.isotp_padding is None


def test_environment_overrides_the_interface(tmp_path: Path) -> None:
    settings = _load(tmp_path, "", env={"IRAN_OBD_TUNER_CAN_INTERFACE": "slcan"})

    assert settings.can_interface == "slcan"


@pytest.mark.parametrize(
    "body",
    [
        'can_interface = "virtual"',
        'can_interface = "socketcand"',
        "can_bitrate = 0",
        'uds_request_id = "0x800"',
        'uds_request_id = "seven"',
        "uds_request_id = true",
        'uds_request_id = "0x7E8"',  # equal to the response identifier
        'isotp_padding = "0x100"',
    ],
)
def test_invalid_can_settings_are_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigurationError):
        _load(tmp_path, body + "\n")


def test_core_interface_list_matches_the_hardware_layer() -> None:
    assert CAN_INTERFACE_CHOICES == LINK_INTERFACES


def test_no_setting_can_enable_a_session_change_or_a_write() -> None:
    """The extended session is a per-run opt-in, never a file or environment setting."""
    forbidden = ("extended", "session", "write", "flash", "security", "unlock", "program")
    assert [key for key in FIELD_CASTERS if any(word in key for word in forbidden)] == []


def test_link_config_takes_settings_and_per_run_overrides() -> None:
    settings = AppSettings(can_interface="slcan", can_channel="COM5", isotp_padding=None)

    from_settings = uds_link_config(settings)
    overridden = uds_link_config(settings, interface="virtual-sim", bitrate=250_000)

    assert (from_settings.interface, from_settings.channel) == ("slcan", "COM5")
    assert from_settings.padding is None
    assert overridden.interface == "virtual-sim" and overridden.simulated
    assert overridden.bitrate == 250_000
