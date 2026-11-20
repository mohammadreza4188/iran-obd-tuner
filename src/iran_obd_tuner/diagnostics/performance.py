"""Passive 0-100 km/h acceleration timer (estimated, from ECU-reported vehicle speed).

The timer only consumes vehicle speed samples the dashboard already polls (PID ``010D``,
each with its own monotonic timestamp). It sends nothing to the vehicle.

State machine::

    IDLE --arm()--> ARMED --speed == 0--> ARMED_AT_STANDSTILL --speed > 0--> RUNNING
    RUNNING --speed >= target--> COMPLETED
    RUNNING --drop / sample gap / timeout / disconnect--> ABORTED
    any --reset()--> IDLE

Honest timing with coarse samples (speed has 1 km/h resolution and arrives at roughly
1-4 Hz from an ELM327):

* **start**: the car started moving somewhere between the last zero sample and the first
  moving sample. The start is estimated by extending the line through the first two moving
  samples back to 0 km/h, clamped to that window; the window width is reported as the start
  uncertainty;
* **split and target times**: linear interpolation between the two samples around the
  threshold;
* **distance**: trapezoidal integration of speed from the estimated start to the target;
* every result carries the sample count, the largest sample interval and
  ``estimated=True``, and whether the speed was simulated.

Not implemented: GPS speed, wheel-slip detection, road grade correction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from iran_obd_tuner.diagnostics.models import PidStatus

__all__ = ["AbortReason", "AccelerationTimer", "RunResult", "SpeedSample", "TimerState"]

SPEED_PID = "010D"


class TimerState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"  # waiting for a standstill
    ARMED_AT_STANDSTILL = "armed_at_standstill"  # stopped, waiting for the launch
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"


class AbortReason(StrEnum):
    SPEED_DROP = "speed_drop"
    SAMPLE_GAP = "sample_gap"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class SpeedSample:
    t: float  # monotonic seconds
    speed_kmh: float


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome of a completed run. All times are estimates.

    Attributes:
        split_kmh / target_kmh: The thresholds used (default 60 and 100 km/h).
        split_s: Time from the estimated start to ``split_kmh``.
        target_s: Time from the estimated start to ``target_kmh``.
        distance_m: Distance covered from the estimated start to ``target_kmh``.
        start_uncertainty_s: Width of the window in which the launch happened.
        max_sample_interval_s: Largest gap between two speed samples during the run.
        sample_count: Speed samples used (including the last standstill sample).
        simulated: ``True`` when the speed came from the simulator.
        estimated: Always ``True``: these are not measured with a timing gate.
    """

    split_kmh: float
    target_kmh: float
    split_s: float
    target_s: float
    distance_m: float
    start_uncertainty_s: float
    max_sample_interval_s: float
    sample_count: int
    simulated: bool
    estimated: bool = True

    @property
    def uncertainty_s(self) -> float:
        """Conservative bound: start window plus the coarsest sample interval."""
        return self.start_uncertainty_s + self.max_sample_interval_s


class AccelerationTimer:
    """Deterministic 0-to-target timer fed with timestamped speed samples.

    Args:
        target_kmh: Speed that completes the run (100 km/h).
        split_kmh: Intermediate speed reported as a split (60 km/h).
        max_drop_kmh: A drop below the peak speed by more than this aborts the run.
        max_gap_s: A gap between samples longer than this aborts the run.
        timeout_s: Abort if the target is not reached within this time.
    """

    def __init__(
        self,
        *,
        target_kmh: float = 100.0,
        split_kmh: float = 60.0,
        max_drop_kmh: float = 3.0,
        max_gap_s: float = 2.0,
        timeout_s: float = 60.0,
    ) -> None:
        if not 0 < split_kmh < target_kmh:
            raise ValueError("0 < split_kmh < target_kmh is required")
        self._target = target_kmh
        self._split = split_kmh
        self._max_drop = max_drop_kmh
        self._max_gap = max_gap_s
        self._timeout = timeout_s
        self.reset()

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> TimerState:
        return self._state

    @property
    def abort_reason(self) -> AbortReason | None:
        return self._abort_reason

    @property
    def result(self) -> RunResult | None:
        return self._result

    @property
    def wants_fast_poll(self) -> bool:
        """Whether a run is armed or in progress (more speed samples help)."""
        return self._state in (
            TimerState.ARMED,
            TimerState.ARMED_AT_STANDSTILL,
            TimerState.RUNNING,
        )

    def elapsed_s(self, now: float) -> float | None:
        """Running time for a live display, from the first moving sample."""
        if self._state is TimerState.RUNNING and len(self._samples) >= 2:
            return max(0.0, now - self._samples[1].t)
        if self._state is TimerState.COMPLETED and self._result is not None:
            return self._result.target_s
        return None

    # ------------------------------------------------------------------ control
    def reset(self) -> None:
        self._state = TimerState.IDLE
        self._abort_reason: AbortReason | None = None
        self._result: RunResult | None = None
        self._samples: list[SpeedSample] = []
        self._last_zero: float | None = None
        self._peak = 0.0
        self._simulated = False

    def arm(self) -> None:
        """Start waiting for a standstill (from any state)."""
        self.reset()
        self._state = TimerState.ARMED

    def abort(self, reason: AbortReason) -> None:
        """Abort an armed or running timer (e.g. on disconnect)."""
        if self.wants_fast_poll:
            self._state = TimerState.ABORTED
            self._abort_reason = reason

    # ------------------------------------------------------------------ input
    def feed_snapshot(self, snapshot) -> None:  # LiveSnapshot (avoids an import cycle)
        """Feed the speed reading of a live snapshot (missing speed counts as a gap)."""
        reading = snapshot.reading_for(SPEED_PID)
        if reading is None or reading.sampled_at_s is None:
            return
        speed = reading.measurement.value if reading.status is PidStatus.OK else None
        self.feed(speed, reading.sampled_at_s, simulated=snapshot.simulated)

    def feed(self, speed_kmh: float | None, t: float, *, simulated: bool = False) -> None:
        """Feed one sample; ``None`` means the speed could not be read at time ``t``."""
        self._simulated = self._simulated or simulated
        state = self._state
        if state is TimerState.ARMED:
            if speed_kmh is not None and speed_kmh <= 0.0:
                self._state = TimerState.ARMED_AT_STANDSTILL
                self._last_zero = t
        elif state is TimerState.ARMED_AT_STANDSTILL:
            if speed_kmh is None:
                return
            if speed_kmh <= 0.0:
                self._last_zero = t
                return
            assert self._last_zero is not None
            self._samples = [SpeedSample(self._last_zero, 0.0), SpeedSample(t, speed_kmh)]
            self._peak = speed_kmh
            self._state = TimerState.RUNNING
            self._check_completed()
        elif state is TimerState.RUNNING:
            self._feed_running(speed_kmh, t)

    def _feed_running(self, speed_kmh: float | None, t: float) -> None:
        last = self._samples[-1]
        if t - last.t > self._max_gap:
            self.abort(AbortReason.SAMPLE_GAP)
            return
        if speed_kmh is None:
            return
        if t - self._samples[0].t > self._timeout:
            self.abort(AbortReason.TIMEOUT)
            return
        if speed_kmh < self._peak - self._max_drop:
            self.abort(AbortReason.SPEED_DROP)
            return
        self._samples.append(SpeedSample(t, speed_kmh))
        self._peak = max(self._peak, speed_kmh)
        self._check_completed()

    # ------------------------------------------------------------------ computation
    def _check_completed(self) -> None:
        if self._samples[-1].speed_kmh >= self._target:
            self._result = self._compute()
            self._state = TimerState.COMPLETED

    def _start_estimate(self) -> tuple[float, float]:
        zero, first = self._samples[0], self._samples[1]
        window = first.t - zero.t
        start = zero.t
        if len(self._samples) >= 3:
            second = self._samples[2]
            if second.speed_kmh > first.speed_kmh and second.t > first.t:
                slope = (second.speed_kmh - first.speed_kmh) / (second.t - first.t)
                start = first.t - first.speed_kmh / slope
        return min(max(start, zero.t), first.t), window

    def _crossing(self, threshold: float) -> float:
        samples = self._samples
        for previous, current in zip(samples, samples[1:], strict=False):
            if current.speed_kmh >= threshold > previous.speed_kmh:
                fraction = (threshold - previous.speed_kmh) / (
                    current.speed_kmh - previous.speed_kmh
                )
                return previous.t + fraction * (current.t - previous.t)
        raise AssertionError("threshold not crossed")  # pragma: no cover

    def _distance_to(self, t_end: float, start: float) -> float:
        points = [SpeedSample(start, 0.0)] + [s for s in self._samples[1:] if s.t < t_end]
        points.append(SpeedSample(t_end, self._target))
        metres = 0.0
        for a, b in zip(points, points[1:], strict=False):
            metres += (a.speed_kmh + b.speed_kmh) / 2 / 3.6 * (b.t - a.t)
        return metres

    def _compute(self) -> RunResult:
        start, window = self._start_estimate()
        t_split = self._crossing(self._split)
        t_target = self._crossing(self._target)
        intervals = [b.t - a.t for a, b in zip(self._samples, self._samples[1:], strict=False)]
        return RunResult(
            split_kmh=self._split,
            target_kmh=self._target,
            split_s=t_split - start,
            target_s=t_target - start,
            distance_m=self._distance_to(t_target, start),
            start_uncertainty_s=window,
            max_sample_interval_s=max(intervals),
            sample_count=len(self._samples),
            simulated=self._simulated,
        )
