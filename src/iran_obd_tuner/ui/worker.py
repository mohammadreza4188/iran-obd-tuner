"""Run blocking adapter calls on one background thread, one at a time.

A real adapter can take seconds to answer (an ELM327 protocol search can take tens of
seconds), so the dashboard never calls a :class:`LiveDataSession` on the GUI thread.
:class:`BackgroundRunner` owns a single worker thread and a FIFO job queue:

* jobs run strictly one after another, so the (not thread-safe) session and adapter are
  only ever touched by one thread at a time;
* results and errors are delivered back on the GUI thread through Qt queued signals, to
  the callbacks given to :meth:`BackgroundRunner.submit`;
* :meth:`BackgroundRunner.shutdown` stops the thread and then runs any job still queued
  (typically a disconnect) on the calling thread, so no serial port is left open.

Errors are passed to the error callback as exception objects; the runner never hides them.

:class:`ProgressRelay` (Milestone 7f) carries progress reports from a running job to the GUI
thread: the job calls :meth:`ProgressRelay.report` on the worker thread, and the relay - a
``QObject`` living on the GUI thread - re-emits it through a queued signal, so widgets are
only ever touched on the GUI thread. :class:`CancelFlag` is the matching thread-safe "please
stop" request polled by the job.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from itertools import count
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from iran_obd_tuner.core.logging import get_logger

__all__ = ["BackgroundRunner", "CancelFlag", "ProgressRelay"]

_LOG = get_logger("ui.worker")

Job = Callable[[], Any]
OnDone = Callable[[Any], None]
OnError = Callable[[BaseException], None]


class ProgressRelay(QObject):
    """Deliver ``report(x)`` calls made on any thread as ``progressed(x)`` on the GUI thread."""

    progressed = Signal(object)

    def report(self, value: object) -> None:
        """Callable from the worker thread (Qt queues the signal to this object's thread)."""
        self.progressed.emit(value)


class CancelFlag:
    """A thread-safe, one-way "cancel requested" flag (reset for every new job)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    def __call__(self) -> bool:
        return self._event.is_set()


class _Worker(QObject):
    """Lives on the worker thread and drains the shared queue."""

    finished = Signal(int, object)
    failed = Signal(int, object)

    def __init__(self, queue: deque[tuple[int, Job]], lock: threading.Lock) -> None:
        super().__init__()
        self._queue = queue
        self._lock = lock

    @Slot()
    def drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                job_id, job = self._queue.popleft()
            try:
                result = job()
            except Exception as exc:  # delivered to the caller's error callback
                self.failed.emit(job_id, exc)
            else:
                self.finished.emit(job_id, result)


class BackgroundRunner(QObject):
    """Serial job runner with a dedicated worker thread."""

    _wake = Signal()

    def __init__(self, parent: QObject | None = None, *, name: str = "adapter-io") -> None:
        super().__init__(parent)
        self._queue: deque[tuple[int, Job]] = deque()
        self._lock = threading.Lock()
        self._ids = count(1)
        self._callbacks: dict[int, tuple[OnDone, OnError]] = {}
        self._thread = QThread()
        self._thread.setObjectName(f"iran-obd-tuner-{name}")
        self._worker = _Worker(self._queue, self._lock)
        self._worker.moveToThread(self._thread)
        self._wake.connect(self._worker.drain)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()
        self._running = True

    @property
    def pending(self) -> int:
        """Jobs submitted whose callback has not run yet."""
        return len(self._callbacks)

    def submit(self, job: Job, on_done: OnDone, on_error: OnError) -> None:
        """Queue ``job``; its result or exception is delivered on the GUI thread.

        Raises:
            RuntimeError: After :meth:`shutdown`.
        """
        if not self._running:
            raise RuntimeError("the background runner has been shut down")
        job_id = next(self._ids)
        self._callbacks[job_id] = (on_done, on_error)
        with self._lock:
            self._queue.append((job_id, job))
        self._wake.emit()

    def shutdown(self, timeout_ms: int = 45_000) -> None:
        """Stop the worker thread, then run still-queued jobs here without callbacks.

        Waits up to ``timeout_ms`` for a running job (e.g. a protocol search) to finish.
        """
        if not self._running:
            return
        self._running = False
        self._thread.quit()
        if not self._thread.wait(timeout_ms):
            _LOG.warning("adapter I/O thread did not stop within %d ms", timeout_ms)
            return
        with self._lock:
            leftovers = list(self._queue)
            self._queue.clear()
        for _job_id, job in leftovers:
            try:
                job()
            except Exception as exc:  # shutting down: report, do not raise
                _LOG.warning("queued adapter job failed during shutdown: %s", exc)
        self._callbacks.clear()

    @Slot(int, object)
    def _on_finished(self, job_id: int, result: object) -> None:
        callbacks = self._callbacks.pop(job_id, None)
        if callbacks is not None:
            callbacks[0](result)

    @Slot(int, object)
    def _on_failed(self, job_id: int, error: object) -> None:
        callbacks = self._callbacks.pop(job_id, None)
        if callbacks is not None and isinstance(error, BaseException):
            callbacks[1](error)
