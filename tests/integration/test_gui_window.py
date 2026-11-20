"""Headless tests of the PySide6 dashboard over the real application wiring.

Skipped when PySide6 (the optional ``[gui]`` extra) is not installed, and run on the
"offscreen" Qt platform so they need no display and no OBD adapter. The window is built with
:class:`AppDashboardBackend` (the production wiring); only the serial port list and, for
specific scenarios, the adapter are replaced. Real-adapter tests use the ELM327 adapter over
a scripted transport. Nothing here opens a serial port.

Adapter calls run on the window's background thread, so the tests wait for them with
:func:`wait_until` instead of assuming they completed synchronously.
"""

from __future__ import annotations

import csv
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the dashboard needs the optional [gui] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from iran_obd_tuner import __version__  # noqa: E402
from iran_obd_tuner.app.dashboard import AppDashboardBackend  # noqa: E402
from iran_obd_tuner.core.config import AppSettings  # noqa: E402
from iran_obd_tuner.core.exceptions import AdapterConnectionError  # noqa: E402
from iran_obd_tuner.core.knowledge import ConfidenceLevel  # noqa: E402
from iran_obd_tuner.database.repository import JsonKnowledgeRepository  # noqa: E402
from iran_obd_tuner.diagnostics.live_data import LiveDataSession, LiveSnapshot  # noqa: E402
from iran_obd_tuner.hardware.base import ObdAdapter  # noqa: E402
from iran_obd_tuner.hardware.elm327 import Elm327Adapter  # noqa: E402
from iran_obd_tuner.hardware.mock import FailureMode, MockObdAdapter, MockScenario  # noqa: E402
from iran_obd_tuner.ui.i18n import COPY  # noqa: E402
from iran_obd_tuner.ui.main_window import DASHBOARD_PIDS, MainWindow  # noqa: E402
from iran_obd_tuner.ui.safety_modal import SafetyModal  # noqa: E402

LAYOUT_LTR = Qt.LayoutDirection.LeftToRight
LAYOUT_RTL = Qt.LayoutDirection.RightToLeft


def wait_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    """Process Qt events until ``condition()`` holds; fail after ``timeout_s``."""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        app.processEvents()
        time.sleep(0.002)


def settle(window: MainWindow) -> None:
    """Wait until every queued adapter job has delivered its result."""
    wait_until(lambda: not window.busy)


class TestBackend(AppDashboardBackend):
    """Production wiring with a fixed port list and optional adapter overrides."""

    __test__ = False  # not a pytest test class

    def __init__(self, output_dir: Path) -> None:
        super().__init__(AppSettings(output_dir=output_dir), JsonKnowledgeRepository())
        self.adapter_factory: Callable[[], ObdAdapter] | None = None
        self.real_adapter_factory: Callable[[str], ObdAdapter] | None = None
        self.sessions_created = 0

    def list_serial_ports(self) -> tuple[str, ...]:
        return ("COM3", "COM4")

    def create_session(self, *, simulation: bool, port: str | None) -> LiveDataSession:
        self.sessions_created += 1
        if simulation and self.adapter_factory is not None:
            return LiveDataSession(self.adapter_factory(), JsonKnowledgeRepository())
        if not simulation and port is not None and self.real_adapter_factory is not None:
            return LiveDataSession(self.real_adapter_factory(port), JsonKnowledgeRepository())
        return super().create_session(simulation=simulation, port=port)


class DroppingAdapter(MockObdAdapter):
    """Connects, then loses the connection on the first measurement."""

    def read_measurement(self, pid: str):  # type: ignore[override]
        raise AdapterConnectionError("simulated cable unplug")


class SlowConnectAdapter(MockObdAdapter):
    """Takes a while to connect, like an ELM327 searching for a protocol."""

    def _open(self, *, timeout_s: float | None) -> None:
        time.sleep(0.4)
        super()._open(timeout_s=timeout_s)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """One QApplication for the whole module (Qt allows a single instance)."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def backend(tmp_path: Path) -> TestBackend:
    return TestBackend(tmp_path / "output")


@pytest.fixture
def window(qt_app: QApplication, backend: TestBackend) -> Iterator[MainWindow]:
    win = MainWindow(backend)
    qt_app.processEvents()
    yield win
    win.close()


@pytest.fixture
def warnings_shown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    shown: list[str] = []

    def fake_warning(_parent: object, _title: str, text: str) -> None:
        shown.append(text)

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    return shown


def _connect_simulation(window: MainWindow) -> None:
    window.mode_combo.setCurrentIndex(0)  # simulation
    window.connect_btn.click()
    settle(window)


def _log_texts(window: MainWindow) -> list[str]:
    return [window.log_list.item(i).text() for i in range(window.log_list.count())]


def test_window_builds_offline(window: MainWindow, backend: TestBackend) -> None:
    assert window.windowTitle() == COPY["fa"]["title"]
    assert len(window.gauges) == 8
    assert len(window.tab_buttons) == 9
    assert window.connected is False
    assert window.timer.isActive() is False
    assert backend.sessions_created == 0  # constructing the window opens nothing
    assert window.version_label.text() == f"v{__version__}"
    assert all(card.gauge.value is None for card in window.gauges.values())


def test_port_selector_lists_a_placeholder_and_the_backend_ports(window: MainWindow) -> None:
    labels = [window.port_combo.itemText(i) for i in range(window.port_combo.count())]

    assert labels == [COPY["fa"]["auto"], "COM3", "COM4"]


def test_language_switch_updates_labels_and_direction(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)  # English
    assert window.windowTitle() == COPY["en"]["title"]
    assert window.layoutDirection() is LAYOUT_LTR
    assert window.bench_title.text() == COPY["en"]["bench_title"]

    window.lang_combo.setCurrentIndex(0)  # Persian, right to left
    assert window.windowTitle() == COPY["fa"]["title"]
    assert window.layoutDirection() is LAYOUT_RTL
    assert window.bench_title.text() == COPY["fa"]["bench_title"]  # Persian, not English


def test_simulated_session_drives_gauges_codes_and_banner(window: MainWindow) -> None:
    _connect_simulation(window)

    assert window.connected is True
    assert window.session is not None and window.session.simulated is True
    assert window.timer.isActive() is True
    assert window.sim_banner.isVisibleTo(window) is True
    assert window.status_pill.text() == COPY["fa"]["sim"]
    assert window.gauges["rpm"].gauge.value is not None
    assert window.gauges["rpm"].gauge.value > 0
    assert window.gauges["coolant"].gauge.value is not None

    texts = window.diag_view.item_texts()
    # descriptions come from the knowledge database, not from strings in the UI
    assert "    P0300  —  Random/multiple cylinder misfire detected" in texts
    assert "    P0171  —  System too lean (bank 1)" in texts
    t = COPY["fa"]
    assert t["dtc_category_header"].format(t["dtc_category"]["stored"], "03", 2) in texts
    assert t["dtc_category_header"].format(t["dtc_category"]["pending"], "07", 0) in texts
    # the drive cycle ECU does not support service 0A: said so, not "no codes"
    unsupported = t["dtc_category_unavailable"].format(
        t["dtc_category"]["permanent"], "0A", t["outcome"]["unsupported"]
    )
    assert unsupported in texts
    assert window.mil_badge.text() == t["mil"]["on"].format(2)


def test_connecting_does_not_block_the_window(window: MainWindow, backend: TestBackend) -> None:
    backend.adapter_factory = SlowConnectAdapter
    window.mode_combo.setCurrentIndex(0)

    started = time.monotonic()
    window.connect_btn.click()
    returned_after = time.monotonic() - started

    assert returned_after < 0.2  # the click returns while the adapter is still connecting
    assert window.connected is False
    assert window.connect_btn.isEnabled() is False
    assert window.connect_btn.text() == COPY["fa"]["connecting"]
    wait_until(lambda: window.connected)
    settle(window)
    assert window.connect_btn.text() == COPY["fa"]["disconnect"]


def test_unsupported_parameter_is_shown_as_unavailable(
    window: MainWindow, backend: TestBackend
) -> None:
    backend.adapter_factory = MockObdAdapter  # default scenario: no fuel level (012F)
    _connect_simulation(window)

    fuel = window.gauges["fuel"].gauge
    assert fuel.value is None
    assert fuel.display_text == "—"
    assert fuel.caption == COPY["fa"]["pid_status"]["unsupported"]
    assert window.gauges["rpm"].gauge.value == 842.0


def test_real_adapter_without_a_port_is_refused_and_never_simulated(
    window: MainWindow, warnings_shown: list[str]
) -> None:
    window.lang_combo.setCurrentIndex(1)
    window.mode_combo.setCurrentIndex(1)  # real adapter, port selector on the placeholder
    window.connect_btn.click()

    assert window.connected is False
    assert window.session is None
    assert len(warnings_shown) == 1
    assert "explicit serial port" in warnings_shown[0]
    assert window.sim_banner.isVisibleTo(window) is False


def test_real_elm327_session_shows_vehicle_data_without_simulation_labels(
    window: MainWindow,
    backend: TestBackend,
    scripted_transport,
    can_vehicle_script,
) -> None:
    transports = []

    def real_adapter(port: str) -> ObdAdapter:
        transport = scripted_transport(can_vehicle_script)
        transports.append(transport)
        return Elm327Adapter(port, transport_factory=lambda _port, _baud: transport)

    backend.real_adapter_factory = real_adapter
    window.lang_combo.setCurrentIndex(1)
    window.mode_combo.setCurrentIndex(1)  # real adapter
    window.port_combo.setCurrentIndex(window.port_combo.findData("COM3"))
    window.connect_btn.click()
    wait_until(lambda: window.connected)
    settle(window)

    assert window.session is not None and window.session.simulated is False
    assert window.sim_banner.isVisibleTo(window) is False
    assert window.status_pill.text() == COPY["en"]["connected"]
    assert window.gauges["rpm"].gauge.value == 1726.0
    assert window.gauges["coolant"].gauge.value == 83.0
    speed = window.gauges["speed"].gauge  # 010D answers NO DATA in the script
    assert speed.value is None
    assert speed.caption == COPY["en"]["pid_status"]["no_response"]
    assert any("ISO 15765-4 CAN (11 bit ID, 500 kbaud)" in text for text in _log_texts(window))
    assert "    P0133  —  O2 sensor circuit slow response (bank 1, sensor 1)" in (
        window.diag_view.item_texts()
    )
    assert window.mil_badge.text() == COPY["en"]["mil"]["on"].format(2)

    window.connect_btn.click()  # disconnect
    settle(window)
    assert transports[0].closed is True
    assert not any(command.startswith("04") for command in transports[0].written)


def test_connection_failure_is_reported(
    window: MainWindow, backend: TestBackend, warnings_shown: list[str]
) -> None:
    scenario = MockScenario.default().with_failure(FailureMode.CONNECT_TIMEOUT)
    backend.adapter_factory = lambda: MockObdAdapter(scenario)
    _connect_simulation(window)

    assert window.connected is False
    assert window.timer.isActive() is False
    assert window.connect_btn.isEnabled() is True
    assert len(warnings_shown) == 1
    assert "simulated failure" in warnings_shown[0]


def test_read_failures_are_never_shown_as_values_or_as_no_codes(
    window: MainWindow, backend: TestBackend
) -> None:
    scenario = MockScenario.default().with_failure(FailureMode.READ_TIMEOUT)
    backend.adapter_factory = lambda: MockObdAdapter(scenario)
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)

    assert window.connected is True
    rpm = window.gauges["rpm"].gauge
    assert rpm.value is None
    assert rpm.caption == COPY["en"]["pid_status"]["no_response"]
    dtc_text = window.diag_view.item_texts()[0]
    assert dtc_text != COPY["en"]["no_dtc"]
    assert "does not mean there are none" in dtc_text
    assert window.mil_badge.text() == COPY["en"]["mil"]["unknown"]


def test_lost_connection_disconnects_cleanly(window: MainWindow, backend: TestBackend) -> None:
    backend.adapter_factory = DroppingAdapter
    _connect_simulation(window)  # the first poll loses the connection
    settle(window)

    assert window.connected is False
    assert window.timer.isActive() is False
    assert any("simulated cable unplug" in text for text in _log_texts(window))


def test_clear_codes_is_refused_and_explained_without_blocking(
    window: MainWindow, qt_app: QApplication
) -> None:
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    codes_before = window.diag_view.item_texts()

    window._clear_dtc()  # must return: the dialog is opened non-blocking
    qt_app.processEvents()

    modal = window.findChild(SafetyModal)
    assert modal is not None and modal.isVisible()
    assert modal.message_label.text() == COPY["en"]["safety_clear_dtc_msg"]
    window._refresh_dtc()
    settle(window)
    assert window.diag_view.item_texts() == codes_before  # nothing was cleared
    assert any("refused" in text for text in _log_texts(window))
    modal.accept()


def test_identification_is_labelled_as_simulated(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    window._read_identification()
    assert COPY["en"]["ident_not_connected"] in window.ident_result_label.text()

    _connect_simulation(window)
    window._read_identification()
    settle(window)
    text = window.ident_result_label.text()
    assert "SIMULATEDVIN00001" in text
    assert "Simulated data" in text


def test_recording_writes_a_labelled_csv(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "capture.csv"
    offered: list[str] = []

    def fake_dialog(_parent: object, _title: str, default: str, _filter: str):
        offered.append(default)
        return str(target), _filter

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    _connect_simulation(window)

    window.record_btn.click()
    assert window.recorder.recording is True
    wait_until(lambda: window.recorder.count >= 2)
    window.record_btn.click()

    assert window.recorder.recording is False
    assert offered[0].endswith("_simulated.csv")
    with target.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows[0]) == 2 + len(DASHBOARD_PIDS)
    assert len(rows) >= 3
    assert {row[1] for row in rows[1:]} == {"true"}


def test_disconnect_stops_the_session_and_clears_gauges(window: MainWindow) -> None:
    _connect_simulation(window)
    assert window.gauges["rpm"].gauge.value is not None

    window.connect_btn.click()  # the same button disconnects
    settle(window)

    assert window.connected is False
    assert window.timer.isActive() is False
    assert window.gauges["rpm"].gauge.value is None
    assert window.sim_banner.isVisibleTo(window) is False


def test_closing_the_window_stops_recording_and_closes_the_adapter(
    qt_app: QApplication,
    backend: TestBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "on-close.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *_a: (str(target), "")))
    adapters: list[MockObdAdapter] = []

    def tracked() -> MockObdAdapter:
        adapters.append(MockObdAdapter(MockScenario.drive_cycle()))
        return adapters[-1]

    backend.adapter_factory = tracked
    win = MainWindow(backend)
    _connect_simulation(win)
    win.record_btn.click()
    assert win.recorder.recording is True

    win.close()

    assert win.recorder.recording is False
    assert win.connected is False
    assert adapters[0].is_connected is False  # the queued disconnect ran during shutdown
    assert target.read_text(encoding="utf-8").startswith("timestamp_utc,simulated,")


# --------------------------------------------------------------------------- #
# M3 part A: MIL badge, DTC categories, freeze frame
# --------------------------------------------------------------------------- #


def test_mil_badge_follows_the_connection(window: MainWindow, backend: TestBackend) -> None:
    t = COPY["en"]["mil"]
    window.lang_combo.setCurrentIndex(1)
    assert window.mil_badge.text() == t["disconnected"]

    backend.adapter_factory = MockObdAdapter  # default scenario: MIL on, 3 codes
    _connect_simulation(window)
    assert window.mil_badge.text() == t["on"].format(3)
    assert "evaporative_system" in window.mil_badge.toolTip()

    window.connect_btn.click()
    settle(window)
    assert window.mil_badge.text() == t["disconnected"]


def test_mil_unknown_when_pid_0101_is_unsupported(window: MainWindow, backend: TestBackend) -> None:
    from dataclasses import replace

    scenario = replace(MockScenario.default(), monitor_status_hex=None)
    backend.adapter_factory = lambda: MockObdAdapter(scenario)
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)

    assert window.mil_badge.text() == COPY["en"]["mil"]["unknown"]


def test_category_filter_shows_one_category(window: MainWindow, backend: TestBackend) -> None:
    backend.adapter_factory = MockObdAdapter
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    view = window.diag_view

    view.filter_combo.setCurrentIndex(view.filter_combo.findData("pending"))

    assert view.item_texts() == [
        COPY["en"]["dtc_category_header"].format("Pending", "07", 1),
        "    P0420  —  Catalyst system efficiency below threshold (bank 1)",
    ]


def test_freeze_frame_table_shows_values_and_dashes(
    window: MainWindow, backend: TestBackend
) -> None:
    backend.adapter_factory = MockObdAdapter
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    view = window.diag_view
    assert view.freeze_status.text() == COPY["en"]["ff_not_read"]

    view.freeze_btn.click()
    settle(window)

    assert view.freeze_status.text().startswith("Triggered by: P0301")
    rows = {row[0]: row for row in view.freeze_rows()}
    assert rows["Engine speed"][1:] == ["2410.0", "rpm", "Read"]
    assert rows["Coolant temperature"][2] == "°C"
    assert rows["Short term fuel trim (bank 1)"][1] == "-3.1"
    assert rows["Throttle position"][1:] == ["—", "", "Not supported"]  # never a number


def test_freeze_frame_not_stored_is_said_plainly(window: MainWindow, backend: TestBackend) -> None:
    from dataclasses import replace

    scenario = replace(MockScenario.default(), freeze_frame_trigger=None)
    backend.adapter_factory = lambda: MockObdAdapter(scenario)
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)

    window.diag_view.freeze_btn.click()
    settle(window)

    assert window.diag_view.freeze_status.text() == COPY["en"]["ff_none_stored"]
    assert window.diag_view.freeze_rows() == []


def test_freeze_frame_button_needs_a_connection(window: MainWindow) -> None:
    assert window.diag_view.freeze_btn.isEnabled() is False


# --------------------------------------------------------------------------- #
# M3 part B: raw traffic inspector
# --------------------------------------------------------------------------- #


def test_traffic_tab_shows_simulated_exchanges(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)  # drive cycle via the production wiring (shared log)
    view = window.traffic_view

    view.refresh()

    lines = view.lines()
    assert any(" TX   010C" in line for line in lines)
    assert any(" RX   " in line and "rpm" in line for line in lines)
    assert all(line.endswith(COPY["en"]["traffic_sim_tag"]) for line in lines)
    assert window.log_tabs.tabText(1) == COPY["en"]["log_tab_traffic"]


def test_traffic_pause_clear_and_copy(window: MainWindow, qt_app: QApplication) -> None:
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    view = window.traffic_view
    view.refresh()

    view.pause_btn.click()
    frozen = view.lines()
    window._tick()
    settle(window)
    view.refresh()
    assert view.paused and view.lines() == frozen
    assert view.pause_btn.text() == COPY["en"]["traffic_resume"]

    view.pause_btn.click()  # resume: catches up
    assert len(view.lines()) > len(frozen)

    view.copy_btn.click()
    assert QApplication.clipboard().text() == view.text.toPlainText()

    view.clear_btn.click()
    assert view.lines() == []
    assert len(window._backend.traffic_log) == 0


# --------------------------------------------------------------------------- #
# M3 part C: performance tab (0-100 timer, virtual dyno, fast polling)
# --------------------------------------------------------------------------- #


def _speed_snapshot(t: float, speed: float, rpm: float = 3000.0) -> LiveSnapshot:
    from iran_obd_tuner.core.models import Measurement, Unit
    from iran_obd_tuner.diagnostics.models import PidReading, PidStatus

    def reading(pid: str, value: float, unit: Unit) -> PidReading:
        m = Measurement(pid=pid, name=pid, value=value, unit=unit)
        return PidReading(pid, PidStatus.OK, measurement=m, sampled_at_s=t)

    return LiveSnapshot(
        readings=(
            reading("010C", rpm, Unit.RPM),
            reading("010D", speed, Unit.KILOMETRE_PER_HOUR),
            reading("0111", 100.0, Unit.PERCENT),
        ),
        simulated=True,
    )


def test_performance_tab_timer_flow(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    view = window.perf_view
    t = COPY["en"]
    assert view.arm_btn.isEnabled() is False  # needs a connection

    view.set_connected(True)
    view.arm_timer()
    assert view.timer_state_label.text() == t["timer_state"]["armed"]
    for i, speed in enumerate([0, 0, 12, 25, 38, 50, 62, 75, 87, 99, 104]):
        view.feed_snapshot(_speed_snapshot(i * 0.5, float(speed)), i * 0.5)

    assert view.timer_state_label.text() == t["timer_state"]["completed"]
    assert view.timer_result_label.text().startswith("0–60: ")
    assert t["perf_simulated"] in view.timer_result_label.text()
    assert "uncertainty ±" in view.timer_quality_label.text()


def test_performance_tab_timer_abort_is_explained(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    view = window.perf_view
    view.set_connected(True)
    view.arm_timer()
    for i, speed in enumerate([0, 20, 40, 30]):
        view.feed_snapshot(_speed_snapshot(i * 0.5, float(speed)), i * 0.5)

    assert view.timer_state_label.text() == "Aborted: speed dropped"


def test_virtual_dyno_needs_a_curb_mass_and_then_plots(window: MainWindow) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "unit"))
    import test_dyno as reference  # noqa: E402  (the synthetic reference pull)

    window.lang_combo.setCurrentIndex(1)
    view = window.perf_view
    view.set_connected(True)
    pull = reference.simulate_pull(rate_hz=4.0)

    def record() -> None:
        view.start_pull()
        for s in pull:
            view.feed_snapshot(_speed_snapshot(s.t, s.speed_kmh, s.rpm), s.t)
        view.finish_pull()

    record()  # no curb mass entered: refused, nothing invented
    assert view.dyno_result is None
    assert "curb mass" in view.dyno_status_label.text()

    view.param_spins["curb_mass_kg"].setValue(1150.0)
    record()
    assert view.dyno_result is not None
    assert view.chart.point_count > 10
    assert "PS" in view.dyno_peaks_label.text()
    assert COPY["en"]["perf_estimated"] in view.dyno_peaks_label.text()
    assert view.dyno_warning.text() == COPY["en"]["dyno_warning"]


def test_fast_polling_reads_fewer_parameters_and_says_so(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    window.perf_view.arm_timer()

    window._tick()
    settle(window)

    coolant = window.gauges["coolant"].gauge
    assert coolant.value is None
    assert coolant.caption == COPY["en"]["pid_status"]["fast_mode"]
    assert {r.pid for r in window._last_snapshot.readings} == {"010C", "010D", "0111"}

    window.perf_view.fast_poll.setChecked(False)
    window._tick()
    settle(window)
    assert window.gauges["coolant"].gauge.value is not None


def test_disconnect_aborts_a_running_measurement(window: MainWindow) -> None:
    _connect_simulation(window)
    window.perf_view.arm_timer()

    window.connect_btn.click()
    settle(window)

    assert window.perf_view.timer.state.value == "aborted"


# --------------------------------------------------------------------------- UDS panel


def _uds_view_rows(window: MainWindow) -> list[list[str]]:
    table = window.uds_view.did_table
    return [
        [table.item(row, col).text() for col in range(table.columnCount())]
        for row in range(table.rowCount())
    ]


def test_uds_panel_reads_the_simulated_ecu_and_says_so(window: MainWindow) -> None:
    pytest.importorskip("can", reason="needs the optional [can] extra")
    window.lang_combo.setCurrentIndex(1)  # English
    view = window.uds_view

    assert view.extended_check.isChecked() is False  # opt-in, off by default
    assert COPY["en"]["uds_safety"] == view.safety_label.text()
    view.discover_btn.click()
    assert not view.read_btn.isEnabled()  # one UDS job at a time
    settle(window)
    view.read_btn.click()
    settle(window)

    rows = _uds_view_rows(window)
    assert rows[0][:3] == ["F190", "VIN", "SIMULATEDVIN00004"]
    assert rows[-1][0] == "F197" and rows[-1][2] == "—"  # extended-only, not requested
    assert view.banner.text() == COPY["en"]["uds_banner_sim"]
    assert "7E8" in view.status_label.text()
    assert view.dtc_table.rowCount() == 2
    assert any("UDS ECU identification read" in line for line in _log_texts(window))


def test_uds_panel_extended_session_checkbox_enables_the_fallback(window: MainWindow) -> None:
    pytest.importorskip("can", reason="needs the optional [can] extra")
    window.lang_combo.setCurrentIndex(1)
    window.uds_view.extended_check.setChecked(True)
    window.uds_view.read_btn.click()
    settle(window)

    last = _uds_view_rows(window)[-1]
    assert last[2] == "SIMULATED ECU"
    assert COPY["en"]["uds_session_ext"] in last[3]
    assert COPY["en"]["uds_ext_used"] in window.uds_view.status_label.text()


def test_uds_panel_real_interface_without_channel_fails_without_simulating(
    window: MainWindow,
) -> None:
    window.lang_combo.setCurrentIndex(1)
    view = window.uds_view
    view.interface_combo.setCurrentIndex(view.interface_combo.findData("slcan"))

    assert view.channel_edit.isEnabled()
    assert view.banner.text() == COPY["en"]["uds_banner_real"]
    view.read_btn.click()
    settle(window)

    assert "a CAN channel is required" in view.status_label.text()
    assert view.did_table.rowCount() == 0
    assert view.banner.text() == COPY["en"]["uds_banner_real"]
    assert view.read_btn.isEnabled()  # not left busy


def test_uds_panel_is_translated(window: MainWindow) -> None:
    view = window.uds_view
    assert view.read_btn.text() == COPY["fa"]["uds_read_btn"]
    window.lang_combo.setCurrentIndex(1)
    assert view.read_btn.text() == COPY["en"]["uds_read_btn"]
    assert view.interface_combo.itemText(0) == COPY["en"]["uds_iface_sim"]


# --------------------------------------------------------------------------- #
# Milestone 5: Vehicle Knowledge tab
# --------------------------------------------------------------------------- #

FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "vehicle_packs"


@pytest.fixture
def fixture_window(qt_app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """A window whose knowledge base also holds the fictional fixture packs."""
    from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase

    backend = TestBackend(tmp_path / "output")
    backend._vehicles = JsonVehicleKnowledgeBase(FIXTURE_PACKS)
    win = MainWindow(backend)
    qt_app.processEvents()
    yield win
    win.close()


def _claims(window: MainWindow) -> dict[str, tuple[str, str, str]]:
    """Claims table rows: field label -> (value, badge text, badge level)."""
    table = window.kb_view.claims_table
    rows = {}
    for row in range(table.rowCount()):
        badge = table.item(row, 2)
        rows[table.item(row, 0).text()] = (
            table.item(row, 1).text(),
            badge.text(),
            badge.data(Qt.ItemDataRole.UserRole),
        )
    return rows


def test_knowledge_tab_browses_profiles_with_confidence_badges(window: MainWindow) -> None:
    from iran_obd_tuner.ui.knowledge_view import CONFIDENCE_COLORS

    window.lang_combo.setCurrentIndex(1)  # English
    window._switch_tab("kb")
    view = window.kb_view
    assert window.stack.currentWidget().widget() is view
    assert view.tree.topLevelItemCount() == 3
    assert view.header_label.text() == COPY["en"]["kb_select_hint"]

    assert view.select_variant("ikco.peugeot_pars", "tu5")
    assert view.header_label.text() == "Peugeot Pars — Pars TU5"
    claims = _claims(window)
    assert claims["Vehicle / Engine"] == ("TU5JP4", "Reported", "reported")
    assert claims["OBD port / OBD physical layer / protocol"] == ("unknown", "Unknown", "unknown")
    assert claims["ECU (engine) / Request ID (TX)"][2] == "unknown"
    assert claims["ECU (engine) / UDS DIDs"][0] == COPY["en"]["kb_nothing_recorded"]
    badge = view.claims_table.item(0, 2)
    assert badge.background().color().name() == CONFIDENCE_COLORS[ConfidenceLevel.REPORTED]
    notes = view.claims_table.item(2, 4).text()  # obd_physical_layer
    assert "How to verify:" in notes and "AT DPN" in notes
    assert view.claims_table.item(0, 3).text() == "Peugeot Pars · Wikipedia (English)"
    assert "https://en.wikipedia.org/wiki/Peugeot_Pars" in view.claims_table.item(0, 3).toolTip()
    assert [view.legend_badges[level].text() for level in ConfidenceLevel] == [
        "Verified",
        "Documented",
        "Reported",
        "Unverified",
        "Unknown",
    ]


def test_knowledge_tab_filters_and_is_bilingual(window: MainWindow) -> None:
    view = window.kb_view
    view.select_variant("saipa.tiba", "m15")
    assert window.layoutDirection() == LAYOUT_RTL
    assert "تیبا" in view.header_label.text()
    assert _claims(window)["خودرو / موتور"] == ("M15", "گزارش‌شده", "reported")
    assert view.legend_badges[ConfidenceLevel.UNKNOWN].text() == "نامعلوم"

    view.manufacturer_combo.setCurrentIndex(view.manufacturer_combo.findData("ikco"))
    assert view.tree.topLevelItemCount() == 1
    assert view.selected is None  # Tiba is filtered out
    window.lang_combo.setCurrentIndex(1)
    assert window.layoutDirection() == LAYOUT_LTR
    assert view.manufacturer_combo.currentText() == "Iran Khodro (IKCO)"


def test_match_and_export_need_session_data(window: MainWindow) -> None:
    view = window.kb_view
    assert not view.match_btn.isEnabled() and not view.export_btn.isEnabled()
    assert view.match_btn.toolTip() == COPY["fa"]["kb_match_unavailable"]
    _connect_simulation(window)
    assert view.match_btn.isEnabled() and view.export_btn.isEnabled()
    window.connect_btn.click()
    settle(window)
    assert not view.match_btn.isEnabled()


def test_match_on_a_simulated_session_is_labelled_and_honest(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    window.kb_view.match_btn.click()
    settle(window)
    report = window.kb_view.match_report
    assert report is not None and not isinstance(report, str)
    assert report.simulated
    assert report.facts.protocol_id is not None  # the simulator's CAN spelling is understood
    assert report.candidates == ()  # bundled packs: bus facts are unknown, nothing to compare
    status = window.kb_view.match_status.text()
    assert COPY["en"]["kb_match_simulated"] in status
    assert COPY["en"]["kb_match_none"] in status
    assert any("Vehicle match: 0 candidate(s)" in line for line in _log_texts(window))


def test_match_ranks_fixture_profiles_from_obd_and_uds(fixture_window: MainWindow) -> None:
    pytest.importorskip("can", reason="needs the optional [can] extra")
    window = fixture_window
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    window.uds_view.read_btn.click()
    settle(window)
    window.kb_view.match_btn.click()
    settle(window)
    report = window.kb_view.match_report
    assert report is not None and not isinstance(report, str)
    best = report.best
    assert best is not None and best.key == "fixturemotors.alpha/can"
    assert window.kb_view.match_table.item(0, 1).text() == "Fixture Alpha - Alpha CAN"
    assert "Part number: match" in window.kb_view.match_table.item(0, 4).text()


def test_export_writes_a_redacted_simulated_bundle(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    target = tmp_path / "bundle.json"
    offered: list[str] = []

    def fake_dialog(_parent: object, _title: str, default: str, _filter: str):
        offered.append(default)
        return str(target), _filter

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    window.lang_combo.setCurrentIndex(1)
    _connect_simulation(window)
    window._read_identification()
    settle(window)
    view = window.kb_view
    assert view.set_claimed_variant("ikco.peugeot_pars", "tu5")
    assert not view.set_claimed_variant("ikco.nothing", "x")
    view.model_year_edit.setText("1400")
    assert view.redact_check.isChecked()  # privacy by default
    view.export_btn.click()
    settle(window)

    assert offered[0].endswith("_simulated.json")
    text = target.read_text(encoding="utf-8")
    assert "SIMULATEDVIN00001" not in text
    doc = json.loads(text)
    assert doc["kind"] == "field_evidence_bundle"
    assert doc["simulated"] is True and doc["usable_for_verification"] is False
    assert doc["obd"]["vin"] == "SIMULATEDVI******"
    assert doc["operator_context"]["vehicle_id"] == "ikco.peugeot_pars"
    assert doc["operator_context"]["model_year"] == "1400"
    assert doc["traffic"]["entries"], "the simulator exchanges are included"
    rows = {row["claim"]: row for row in doc["promotion_candidates"]}
    assert rows["ikco.peugeot_pars/tu5:obd_physical_layer"]["current_confidence"] == "unknown"
    assert str(target) in view.export_status.text()
    assert "SIMULATED" in view.export_status.text()


def test_export_cancelled_writes_nothing(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *_args: ("", "")))
    _connect_simulation(window)
    window.kb_view.export_btn.click()
    settle(window)
    assert window.kb_view.export_status.text() == ""
    assert list(tmp_path.glob("*.json")) == []
