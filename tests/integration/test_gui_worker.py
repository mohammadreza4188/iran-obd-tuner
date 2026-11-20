"""Background runner: one worker thread, jobs in order, results back on the GUI thread."""

from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="the background runner needs the optional [gui] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iran_obd_tuner.ui.worker import BackgroundRunner  # noqa: E402


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def _wait_idle(app: QApplication, runner: BackgroundRunner, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while runner.pending:
        assert time.monotonic() < deadline, "runner did not finish in time"
        app.processEvents()
        time.sleep(0.002)


def test_jobs_run_in_order_on_one_other_thread(qt_app: QApplication) -> None:
    runner = BackgroundRunner()
    gui_thread = threading.get_ident()
    job_threads: list[int] = []
    callback_threads: list[int] = []
    results: list[int] = []

    def job(n: int):
        def run() -> int:
            job_threads.append(threading.get_ident())
            time.sleep(0.01 * (3 - n))  # later jobs are faster; order must still hold
            return n

        return run

    def on_done(value: object) -> None:
        callback_threads.append(threading.get_ident())
        results.append(value)  # type: ignore[arg-type]

    for n in range(3):
        runner.submit(job(n), on_done, pytest.fail)
    _wait_idle(qt_app, runner)
    runner.shutdown()

    assert results == [0, 1, 2]
    assert len(set(job_threads)) == 1 and job_threads[0] != gui_thread
    assert set(callback_threads) == {gui_thread}


def test_errors_are_delivered_to_the_error_callback(qt_app: QApplication) -> None:
    runner = BackgroundRunner()
    errors: list[BaseException] = []

    def broken() -> None:
        raise ValueError("boom")

    runner.submit(broken, pytest.fail, errors.append)
    _wait_idle(qt_app, runner)
    runner.shutdown()

    assert len(errors) == 1 and str(errors[0]) == "boom"


def test_shutdown_runs_queued_jobs_so_ports_are_closed(qt_app: QApplication) -> None:
    runner = BackgroundRunner()
    release = threading.Event()
    ran: list[str] = []

    def blocking() -> None:
        release.wait(2)
        ran.append("blocking")

    runner.submit(blocking, lambda _r: None, pytest.fail)
    runner.submit(lambda: ran.append("disconnect"), lambda _r: None, pytest.fail)
    release.set()
    runner.shutdown()

    assert ran == ["blocking", "disconnect"]
    with pytest.raises(RuntimeError, match="shut down"):
        runner.submit(lambda: None, lambda _r: None, pytest.fail)
