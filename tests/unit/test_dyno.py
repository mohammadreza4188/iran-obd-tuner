"""Virtual dyno: the estimator must recover a known engine from realistic, coarse OBD data.

A reference vehicle with a known torque curve is simulated with the same physics at 1 ms
steps, then sampled like an ELM327 would deliver it (a few Hz, speed rounded to 1 km/h, rpm
to 0.25 rpm). The estimator must find the peak power and torque within stated tolerances.
"""

from __future__ import annotations

import math

import pytest

from iran_obd_tuner.diagnostics.dyno import (
    DynoError,
    DynoRecorder,
    DynoSample,
    VehicleParameters,
    compute_dyno,
    local_quadratic,
    watts_to_hp,
    watts_to_ps,
)

PARAMS = VehicleParameters(curb_mass_kg=1150.0, extra_mass_kg=75.0)
# 3rd gear of a small car: about 25 km/h per 1000 rpm -> v[m/s] = K * rpm
K = 25.0 / 3.6 / 1000.0


def torque_curve(rpm: float) -> float:
    """Reference crank torque in N·m: peak 140 N·m at 4000 rpm."""
    return 140.0 - 12e-6 * (rpm - 4000.0) ** 2


def simulate_pull(
    *,
    rate_hz: float = 4.0,
    rpm_from: float = 1800.0,
    rpm_to: float = 6000.0,
    quantize: bool = True,
    params: VehicleParameters = PARAMS,
) -> list[DynoSample]:
    """Integrate the reference vehicle and sample it like an OBD adapter would."""
    mass = params.total_mass_kg
    m_eff = mass * (1 + params.rotational_inertia_factor)
    aero = 0.5 * params.air_density_kg_m3 * params.drag_coefficient * params.frontal_area_m2
    rolling = params.rolling_resistance * mass * 9.80665
    dt = 0.001
    t, rpm = 0.0, rpm_from
    next_sample = 0.0
    samples: list[DynoSample] = []
    while rpm < rpm_to:
        assert t < 120.0, "the reference car stopped accelerating (test setup error)"
        v = K * rpm
        crank_power = torque_curve(rpm) * 2 * math.pi * rpm / 60
        wheel_force = crank_power * (1 - params.drivetrain_loss) / v
        a = (wheel_force - aero * v * v - rolling) / m_eff
        rpm += a / K * dt
        t += dt
        if t >= next_sample:
            speed = v * 3.6
            samples.append(
                DynoSample(
                    t,
                    round(rpm * 4) / 4 if quantize else rpm,
                    float(round(speed)) if quantize else speed,
                    100.0,
                )
            )
            next_sample += 1.0 / rate_hz
    return samples


def reference_peak_power() -> tuple[float, float]:
    best = max(range(1800, 6001), key=lambda r: torque_curve(r) * r)
    return best, torque_curve(best) * 2 * math.pi * best / 60


def test_recovers_a_known_engine_from_coarse_obd_samples() -> None:
    result = compute_dyno(simulate_pull(rate_hz=4.0), PARAMS)

    peak_rpm, peak_w = reference_peak_power()
    assert result.peak_power.crank_power_w == pytest.approx(peak_w, rel=0.05)
    assert result.peak_torque.crank_torque_nm == pytest.approx(140.0, rel=0.05)
    assert abs(result.peak_torque.rpm - 4000) < 400
    assert abs(result.peak_power.rpm - peak_rpm) < 600  # power peak is flat near the top
    assert result.estimated is True
    assert result.quality.ratio_r2 > 0.99


def test_two_hz_sampling_is_still_within_ten_percent() -> None:
    result = compute_dyno(simulate_pull(rate_hz=2.0), PARAMS)

    assert result.peak_torque.crank_torque_nm == pytest.approx(140.0, rel=0.10)


def test_exact_data_gives_near_exact_results() -> None:
    result = compute_dyno(simulate_pull(rate_hz=10.0, quantize=False), PARAMS)

    for point in result.points[2:-2]:
        assert point.crank_torque_nm == pytest.approx(torque_curve(point.rpm), rel=0.02)


def test_gear_change_keeps_only_the_longest_gear() -> None:
    third = simulate_pull(rate_hz=4.0, rpm_to=5500.0)
    shift_t = third[-1].t
    # 4th gear: 40 km/h per 1000 rpm, rpm drops after the shift
    fourth = [
        DynoSample(
            shift_t + 0.25 * (i + 1), 4200.0 + 60 * i, round((4200.0 + 60 * i) * 0.040), 100.0
        )
        for i in range(4)
    ]
    result = compute_dyno(third + fourth, PARAMS)

    assert result.quality.gear_segments == 2
    assert result.quality.sample_count == len([s for s in third if s.speed_kmh >= 10])


def test_part_throttle_and_slow_samples_are_excluded() -> None:
    pull = simulate_pull(rate_hz=4.0)
    lifted = [DynoSample(s.t, s.rpm, s.speed_kmh, 40.0) for s in pull[:5]] + pull[5:]

    result = compute_dyno(lifted, PARAMS)

    assert result.quality.sample_count == len(pull) - 5


def test_quality_flags() -> None:
    no_throttle = [DynoSample(s.t, s.rpm, s.speed_kmh, None) for s in simulate_pull(rate_hz=0.9)]

    quality = compute_dyno(no_throttle, PARAMS).quality

    assert quality.low_quality
    assert {"slow_sampling", "throttle_unknown"} <= set(quality.reasons)


def test_curb_mass_is_required_and_never_assumed() -> None:
    with pytest.raises(DynoError, match="curb mass"):
        compute_dyno(simulate_pull(), VehicleParameters())


def test_not_enough_data() -> None:
    with pytest.raises(DynoError, match="usable sample"):
        compute_dyno(simulate_pull()[:4], PARAMS)
    with pytest.raises(DynoError, match="no samples"):
        compute_dyno([DynoSample(0.0, 900.0, 0.0, 100.0)], PARAMS)


def test_parameter_validation() -> None:
    with pytest.raises(ValueError):
        VehicleParameters(curb_mass_kg=50.0)
    with pytest.raises(ValueError):
        VehicleParameters(drivetrain_loss=0.9)


def test_curve_is_resampled_every_100_rpm() -> None:
    curve = compute_dyno(simulate_pull(), PARAMS).curve

    rpms = [p.rpm for p in curve]
    assert all(b - a == pytest.approx(100.0) for a, b in zip(rpms, rpms[1:], strict=False))


def test_unit_conversions() -> None:
    assert watts_to_hp(745.6999) == pytest.approx(1.0)
    assert watts_to_ps(735.49875) == pytest.approx(1.0)


def test_local_quadratic_recovers_value_and_slope_of_a_parabola() -> None:
    t = [0.0, 0.3, 0.5, 0.9, 1.2, 1.6, 1.8]  # uneven spacing
    y = [3 * x * x + 2 * x + 1 for x in t]

    for (value, slope), x, expected in zip(local_quadratic(t, y), t, y, strict=True):
        assert value == pytest.approx(expected, abs=1e-6)
        assert slope == pytest.approx(6 * x + 2, abs=1e-6)


def test_recorder_only_collects_while_recording() -> None:
    from iran_obd_tuner.core.models import Measurement, Unit
    from iran_obd_tuner.diagnostics.live_data import LiveSnapshot
    from iran_obd_tuner.diagnostics.models import PidReading, PidStatus

    def snapshot(t: float, rpm: float, speed: float) -> LiveSnapshot:
        def reading(pid: str, name: str, value: float, unit: Unit) -> PidReading:
            m = Measurement(pid=pid, name=name, value=value, unit=unit)
            return PidReading(pid, PidStatus.OK, measurement=m, sampled_at_s=t)

        return LiveSnapshot(
            readings=(
                reading("010C", "Engine speed", rpm, Unit.RPM),
                reading("010D", "Vehicle speed", speed, Unit.KILOMETRE_PER_HOUR),
                PidReading("0111", PidStatus.UNSUPPORTED),
            ),
            simulated=True,
        )

    recorder = DynoRecorder()
    recorder.feed_snapshot(snapshot(0.0, 2000, 60))
    assert recorder.samples == []
    recorder.start()
    recorder.feed_snapshot(snapshot(0.5, 2100, 63))
    assert recorder.samples == [DynoSample(0.5, 2100, 63, None)]
    assert recorder.simulated is True
