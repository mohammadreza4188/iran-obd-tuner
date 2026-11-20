"""Bounded, thread-safe record of adapter traffic (TX requests, RX replies, notes).

Adapters append on the adapter I/O thread; the dashboard reads on the GUI thread with
:meth:`TrafficLog.entries_since`, so both sides only exchange immutable
:class:`TrafficEntry` objects under one lock.

What is recorded:

* real adapters (ELM327): every command *after* it passed the read-only allowlist (refused
  commands never reach the log because they are never sent) and the raw reply text;
* the simulator: the request it was asked and the decoded simulated answer, flagged
  ``simulated=True``. It records **no hex frames**: the simulator has no bus, and inventing
  frames that look like real traffic would be fabricated data.

Privacy: replies may contain the VIN. The log stays in memory and is only copied out when
the user asks for it.

Standard library only (``core`` rule).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from iran_obd_tuner.core.models import utc_now

__all__ = ["DEFAULT_TRAFFIC_CAPACITY", "Direction", "TrafficEntry", "TrafficLog"]

#: Entries kept before the oldest ones are dropped.
DEFAULT_TRAFFIC_CAPACITY = 2000


class Direction(StrEnum):
    """Who sent the line."""

    TX = "tx"  # request sent to the adapter
    RX = "rx"  # reply received from the adapter
    INFO = "info"  # timeout, error or other note about the exchange


@dataclass(frozen=True, slots=True)
class TrafficEntry:
    """One recorded line.

    Attributes:
        seq: Increasing sequence number (1, 2, ...), never reused after :meth:`clear`.
        monotonic_s: Monotonic timestamp in seconds.
        wall_time: Aware UTC time, for display.
        direction: TX, RX or INFO.
        text: The command, the raw reply, or the note.
        adapter: Registry name of the adapter.
        simulated: ``True`` when the line comes from the simulator, not a vehicle bus.
    """

    seq: int
    monotonic_s: float
    wall_time: datetime
    direction: Direction
    text: str
    adapter: str
    simulated: bool

    def format_line(self, simulated_tag: str = "[SIM]") -> str:
        """``HH:MM:SS.mmm TX  010C`` (local time), with ``simulated_tag`` when simulated."""
        stamp = self.wall_time.astimezone().strftime("%H:%M:%S.%f")[:-3]
        tag = f"  {simulated_tag}" if self.simulated else ""
        return f"{stamp} {self.direction.value.upper():<4} {self.text}{tag}"


class TrafficLog:
    """Ring buffer of :class:`TrafficEntry` objects.

    Args:
        capacity: Maximum number of entries kept.
        clock: Monotonic clock (injectable for tests).
        wall_clock: UTC clock (injectable for tests).
    """

    def __init__(
        self,
        capacity: int = DEFAULT_TRAFFIC_CAPACITY,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._entries: deque[TrafficEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._clock = clock
        self._wall_clock = wall_clock
        self._seq = 0
        self._dropped = 0

    @property
    def capacity(self) -> int:
        return self._entries.maxlen or 0

    @property
    def dropped_count(self) -> int:
        """Entries evicted because the buffer was full (not counting :meth:`clear`)."""
        with self._lock:
            return self._dropped

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def append(
        self, direction: Direction, text: str, *, adapter: str, simulated: bool
    ) -> TrafficEntry:
        """Record one line and return it."""
        with self._lock:
            self._seq += 1
            entry = TrafficEntry(
                seq=self._seq,
                monotonic_s=self._clock(),
                wall_time=self._wall_clock(),
                direction=direction,
                text=text,
                adapter=adapter,
                simulated=simulated,
            )
            if len(self._entries) == self._entries.maxlen:
                self._dropped += 1
            self._entries.append(entry)
            return entry

    def entries_since(self, seq: int) -> tuple[TrafficEntry, ...]:
        """Entries with a sequence number greater than ``seq``, oldest first."""
        with self._lock:
            return tuple(entry for entry in self._entries if entry.seq > seq)

    def snapshot(self) -> tuple[TrafficEntry, ...]:
        """All entries currently kept, oldest first."""
        return self.entries_since(0)

    def clear(self) -> None:
        """Forget every entry (sequence numbers keep increasing)."""
        with self._lock:
            self._entries.clear()
            self._dropped = 0
