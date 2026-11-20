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
from iran_obd_tuner.database.repository import JsonKnowledgeRepository  # noqa: E402
from iran_obd_tuner.diagnostics.live_data import LiveDataSession  # noqa: E402
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
    assert len(window.tab_buttons) == 7
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

    texts = [window.dtc_list.item(i).text() for i in range(window.dtc_list.count())]
    # descriptions come from the knowledge database, not from strings in the UI
    assert texts == [
        "P0300  —  Random/multiple cylinder misfire detected",
        "P0171  —  System too lean (bank 1)",
    ]


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
    assert window.dtc_list.item(0).text().startswith("P0133")

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
    dtc_text = window.dtc_list.item(0).text()
    assert dtc_text != COPY["en"]["no_dtc"]
    assert "does not mean there are none" in dtc_text


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
    codes_before = window.dtc_list.count()

    window._clear_dtc()  # must return: the dialog is opened non-blocking
    qt_app.processEvents()

    modal = window.findChild(SafetyModal)
    assert modal is not None and modal.isVisible()
    assert modal.message_label.text() == COPY["en"]["safety_clear_dtc_msg"]
    window._refresh_dtc()
    settle(window)
    assert window.dtc_list.count() == codes_before  # nothing was cleared
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
