"""``uds-ident`` and the dashboard's UDS wiring, end to end over python-can's virtual bus.

Only the in-process ``virtual`` interface is used (``virtual-sim``); a real interface is only
ever asked for without a channel, which must fail before anything is opened. Skipped
without the ``[can]`` extra.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.app.cli import main  # noqa: E402
from iran_obd_tuner.app.dashboard import AppDashboardBackend  # noqa: E402
from iran_obd_tuner.core.config import AppSettings  # noqa: E402
from iran_obd_tuner.core.exceptions import ConfigurationError  # noqa: E402
from iran_obd_tuner.database.repository import JsonKnowledgeRepository  # noqa: E402
from iran_obd_tuner.diagnostics.models import ReadOutcome  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_uds_ident_prints_a_labelled_simulated_identification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["uds-ident", "--discover"]) == 0
    out = capsys.readouterr().out

    assert "functional TesterPresent answered by: 7E8" in out
    assert "ECU identification (SIMULATED ECU - not vehicle data)" in out
    assert "F190 vin" in out and "SIMULATEDVIN00004" in out
    assert "F197 system_name" in out and "NOT AVAILABLE - unsupported" in out
    assert "P0301-00" in out
    assert "mode: read-only" in out


def test_uds_ident_extended_session_is_an_explicit_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["uds-ident", "--extended-session"]) == 0
    out = capsys.readouterr().out

    assert "SIMULATED ECU (extended session)" in out
    assert "returned to default" in out


def test_uds_ident_json_and_output_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    target = tmp_path / "out" / "ecu.json"
    assert main(["uds-ident", "--json", "--output", str(target)]) == 0
    out = capsys.readouterr().out

    printed = json.loads(out[: out.rindex("}") + 1])
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert printed["kind"] == saved["kind"] == "uds_ecu_identification"
    assert saved["simulated"] is True
    assert saved["vehicle"] == {
        "vin": "SIMULATEDVIN00004",
        "protocol": "ISO 15765-2 / UDS (ISO 14229-1)",
        "adapter_name": "can:virtual-sim",
        "simulated": True,
        "vin_source": "uds_f190",
    }
    assert "schema_version" in saved and "measurements" not in saved  # not a report


def test_uds_ident_real_interface_without_channel_never_simulates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["uds-ident", "--interface", "slcan"]) == 2
    captured = capsys.readouterr()

    assert "a CAN channel is required" in captured.err
    assert "Ignition ON, engine OFF, vehicle stationary" in captured.err
    assert "SIMULATED" not in captured.out


def test_uds_ident_rejects_unknown_interfaces() -> None:
    with pytest.raises(SystemExit):
        main(["uds-ident", "--interface", "virtual"])


# --------------------------------------------------------------------------- dashboard


@pytest.fixture
def backend() -> AppDashboardBackend:
    return AppDashboardBackend(AppSettings(), JsonKnowledgeRepository())


def test_backend_offers_the_settings_as_panel_defaults(backend: AppDashboardBackend) -> None:
    defaults = backend.can_defaults

    assert defaults.interface == defaults.simulated_interface == "virtual-sim"
    assert "slcan" in defaults.interfaces and "virtual" not in defaults.interfaces
    assert defaults.bitrate == 500_000


def test_backend_uds_session_is_simulated_and_logs_can_frames(
    backend: AppDashboardBackend,
) -> None:
    session = backend.create_uds_session(
        interface="virtual-sim", channel=None, bitrate=500_000, allow_extended_session=False
    )

    found = session.discover()
    result = session.identify()

    assert session.simulated and result.simulated
    assert found == (0x7E8,)
    assert result.vin == "SIMULATEDVIN00004"
    assert result.dtc_outcome is ReadOutcome.OK
    entries = backend.traffic_log.entries_since(0)
    assert entries and all(entry.simulated for entry in entries)
    assert all(entry.adapter == "can:virtual-sim" for entry in entries)
    assert any(entry.text.startswith("7DF") for entry in entries)


def test_backend_rejects_unknown_interfaces(backend: AppDashboardBackend) -> None:
    with pytest.raises(ConfigurationError):
        backend.create_uds_session(
            interface="virtual", channel=None, bitrate=500_000, allow_extended_session=False
        )
