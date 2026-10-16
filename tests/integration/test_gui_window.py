"""Headless smoke test of the PySide6 dashboard.

Skipped when PySide6 (the optional ``[gui]`` extra) is not installed, and run on the
"offscreen" Qt platform so it needs no display, no desktop session and no OBD adapter.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytest.importorskip("PySide6", reason="the dashboard needs the optional [gui] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from iran_obd_tuner.ui.i18n import COPY  # noqa: E402
from iran_obd_tuner.ui.main_window import MainWindow  # noqa: E402

LAYOUT_LTR = Qt.LayoutDirection.LeftToRight
LAYOUT_RTL = Qt.LayoutDirection.RightToLeft


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """One QApplication for the whole module (Qt allows a single instance)."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    win = MainWindow()
    qt_app.processEvents()
    yield win
    win.close()


def test_window_builds_offline(window: MainWindow) -> None:
    assert window.windowTitle() == COPY["fa"]["title"]
    assert len(window.gauges) == 8
    assert len(window.tab_buttons) == 5
    assert window.service.connected is False


def test_constructing_the_window_does_not_open_an_adapter(window: MainWindow) -> None:
    """Only the port selector is populated; nothing connects until the user asks for it."""
    assert window.service.connected is False
    assert window.port_combo.count() >= 1
    assert window.timer.isActive() is False


def test_language_switch_updates_labels_and_direction(window: MainWindow) -> None:
    window.lang_combo.setCurrentIndex(1)  # English

    assert window.windowTitle() == COPY["en"]["title"]
    assert window.layoutDirection() is LAYOUT_LTR

    window.lang_combo.setCurrentIndex(0)  # Persian, right to left

    assert window.windowTitle() == COPY["fa"]["title"]
    assert window.layoutDirection() is LAYOUT_RTL


def test_simulated_session_drives_gauges_and_codes(
    window: MainWindow, qt_app: QApplication
) -> None:
    window.mode_combo.setCurrentIndex(0)  # simulation (no hardware)

    window.connect_btn.click()
    qt_app.processEvents()

    assert window.service.connected is True
    assert window.service.simulation is True
    assert window.timer.isActive() is True

    window._tick()
    assert window.gauges["rpm"].gauge._value > 0
    assert window.gauges["coolant"].gauge._value > 0

    window._switch_tab("diag")
    window._refresh_dtc()
    assert window.dtc_list.count() == 2
    assert "P0300" in window.dtc_list.item(0).text()


def test_clear_codes_is_refused_and_reported(
    window: MainWindow,
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []

    def fake_information(_parent: object, _title: str, text: str) -> None:
        shown.append(text)

    monkeypatch.setattr(QMessageBox, "information", staticmethod(fake_information))

    window.lang_combo.setCurrentIndex(1)  # English, so the assertion is readable
    window.mode_combo.setCurrentIndex(0)
    window.connect_btn.click()
    window._refresh_dtc()
    codes_before = window.dtc_list.count()

    window._clear_dtc()
    qt_app.processEvents()

    assert len(shown) == 1
    assert shown[0] == COPY["en"]["clear_blocked"]
    assert "read-only" in shown[0]
    assert window.dtc_list.count() == codes_before  # nothing was cleared
    assert "blocked" in window.log_list.item(0).text()


def test_disconnect_stops_the_session_and_zeroes_gauges(
    window: MainWindow, qt_app: QApplication
) -> None:
    window.mode_combo.setCurrentIndex(0)
    window.connect_btn.click()
    window._tick()
    assert window.gauges["rpm"].gauge._value > 0

    window.connect_btn.click()  # same button disconnects
    qt_app.processEvents()

    assert window.service.connected is False
    assert window.timer.isActive() is False
    assert window.gauges["rpm"].gauge._value == 0
