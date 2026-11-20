"""0-100 km/h timer: state machine, interpolation, uncertainty and aborts (deterministic)."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.models import Measurement, Unit
from iran_obd_tuner.diagnostics.live_data import LiveSnapshot
from iran_obd_tuner.diagnostics.models import PidReading, PidStatus
from iran_obd_tuner.diagnostics.performance import (
    AbortReason,
    AccelerationTimer,
    TimerState,
)

A = 3.0  # m/s² = 10.8 km/h per second: 0-100 in 9.259 s, 0-60 in 5.556 s


def ideal_run(rate_hz: float, *, launch_at: float = 1.03, quantize: bool = True):
    """(t, speed) samples of a constant-acceleration launch at ``launch_at`` seconds."""
    samples = []
    t = 0.0
    while t < launch_at + 12:
        v = max(0.0, (t - launch_at) * A * 3.6)
        samples.append((t, float(int(v)) if quantize else v))  # ECU truncates to 1 km/h
        t = round(t + 1 / rate_hz, 6)
    return samples


def run(timer: AccelerationTimer, samples, simulated: bool = False) -> None:
    timer.arm()
    for t, v in samples:
        timer.feed(v, t, simulated=simulated)


def test_state_machine_happy_path() -> None:
    timer = AccelerationTimer()
    assert timer.state is TimerState.IDLE
    timer.feed(0.0, 0.0)
    assert timer.state is TimerState.IDLE  # not armed: ignored

    timer.arm()
    timer.feed(12.0, 0.0)
    assert timer.state is TimerState.ARMED  # still moving: waits for a standstill
    timer.feed(0.0, 0.5)
    assert timer.state is TimerState.ARMED_AT_STANDSTILL
    timer.feed(5.0, 1.0)
    assert timer.state is TimerState.RUNNING
    assert timer.wants_fast_poll


@pytest.mark.parametrize("rate_hz", [2.0, 4.0, 10.0])
def test_times_are_within_the_reported_uncertainty(rate_hz: float) -> None:
    timer = AccelerationTimer()
    run(timer, ideal_run(rate_hz))

    result = timer.result
    assert timer.state is TimerState.COMPLETED and result is not None
    true_100 = 100 / 3.6 / A
    true_60 = 60 / 3.6 / A
    assert abs(result.target_s - true_100) <= result.uncertainty_s
    assert abs(result.split_s - true_60) <= result.uncertainty_s
    assert result.distance_m == pytest.approx(0.5 * A * true_100**2, rel=0.08)
    assert result.estimated is True and result.simulated is False
    assert result.max_sample_interval_s == pytest.approx(1 / rate_hz)


def test_exact_samples_give_exact_times() -> None:
    timer = AccelerationTimer()
    run(timer, ideal_run(10.0, quantize=False))

    assert timer.result.target_s == pytest.approx(100 / 3.6 / A, abs=0.01)
    assert timer.result.split_s == pytest.approx(60 / 3.6 / A, abs=0.01)


def test_speed_drop_aborts() -> None:
    timer = AccelerationTimer()
    run(timer, [(0.0, 0.0), (0.5, 10.0), (1.0, 20.0), (1.5, 15.0)])

    assert timer.state is TimerState.ABORTED
    assert timer.abort_reason is AbortReason.SPEED_DROP
    assert timer.result is None


def test_missing_speed_samples_abort_after_the_gap_limit() -> None:
    timer = AccelerationTimer(max_gap_s=2.0)
    run(timer, [(0.0, 0.0), (0.5, 10.0), (1.5, None), (2.0, None), (2.6, None)])

    assert timer.abort_reason is AbortReason.SAMPLE_GAP


def test_timeout_and_disconnect() -> None:
    slow = AccelerationTimer(timeout_s=5.0)
    run(slow, [(0.0, 0.0)] + [(1.0 + i, 5.0 + i) for i in range(10)])
    assert slow.abort_reason is AbortReason.TIMEOUT

    timer = AccelerationTimer()
    run(timer, [(0.0, 0.0), (0.5, 10.0)])
    timer.abort(AbortReason.DISCONNECTED)
    assert timer.state is TimerState.ABORTED


def test_reset_and_live_elapsed() -> None:
    timer = AccelerationTimer()
    run(timer, [(0.0, 0.0), (1.0, 5.0)])
    assert timer.elapsed_s(3.5) == pytest.approx(2.5)

    timer.reset()
    assert timer.state is TimerState.IDLE and timer.elapsed_s(10.0) is None


def test_invalid_thresholds() -> None:
    with pytest.raises(ValueError):
        AccelerationTimer(split_kmh=120.0)


def test_snapshot_feeding_uses_per_reading_time_and_simulated_flag() -> None:
    def snap(t: float, speed: float | None) -> LiveSnapshot:
        if speed is None:
            reading = PidReading("010D", PidStatus.NO_RESPONSE, sampled_at_s=t)
        else:
            m = Measurement(
                pid="010D", name="Vehicle speed", value=speed, unit=Unit.KILOMETRE_PER_HOUR
            )
            reading = PidReading("010D", PidStatus.OK, measurement=m, sampled_at_s=t)
        return LiveSnapshot(readings=(reading,), simulated=True)

    timer = AccelerationTimer()
    timer.arm()
    for t, v in [(0.0, 0.0), (0.5, None), (1.0, 20.0), (2.0, 60.0), (3.0, 101.0)]:
        timer.feed_snapshot(snap(t, v))

    assert timer.state is TimerState.COMPLETED
    assert timer.result.simulated is True
