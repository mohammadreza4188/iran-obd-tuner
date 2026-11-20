"""Virtual dyno: *estimated* power and torque from a full-throttle pull (passive).

Inputs are the samples the dashboard already polls - engine speed (``010C``), vehicle speed
(``010D``) and throttle (``0111``), each with its own monotonic timestamp - plus vehicle
parameters entered by the user. Nothing is sent to the vehicle.

Why acceleration comes from RPM, not from vehicle speed: the speed PID has 1 km/h
resolution and arrives at roughly 1-4 Hz, so its derivative is mostly quantisation noise.
Engine speed has 0.25 rpm resolution. Within one gear (clutch closed, no slip) vehicle
speed is proportional to engine speed, ``v = k · rpm``; ``k`` is fitted by least squares on
the pull's (rpm, speed) pairs and the speed is then taken as ``k · rpm``.

Physics (flat road, no wind - stated in the UI)::

    m      = curb_mass + extra_mass
    m_eff  = m · (1 + rotational_inertia_factor)
    a      = k · d(rpm)/dt                          (m/s²)
    F      = m_eff·a + ½·ρ·Cd·A·v² + Crr·m·g
    P_wheel = F · v ;  P_crank = P_wheel / (1 − drivetrain_loss)
    T_crank = P_crank / (2π·rpm/60)

Smoothing: a locally weighted quadratic regression of rpm(t) over a few neighbouring samples
(works with uneven sample spacing, gives the derivative analytically, and keeps peaks
instead of flattening them like a moving average).

Every result is labelled estimated, carries quality indicators (sample count, largest
sample interval, R² of the gear fit, gear changes) and the simulated flag.

Not implemented: road grade, wind, tyre slip, temperature/pressure correction (SAE J1349).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from iran_obd_tuner.diagnostics.models import PidStatus

__all__ = [
    "DynoError",
    "DynoPoint",
    "DynoQuality",
    "DynoRecorder",
    "DynoResult",
    "DynoSample",
    "VehicleParameters",
    "compute_dyno",
    "watts_to_hp",
    "watts_to_ps",
]

G = 9.80665  # m/s²
W_PER_HP = 745.6999  # mechanical horsepower
W_PER_PS = 735.49875  # metric horsepower ("اسب بخار")

RPM_PID, SPEED_PID, THROTTLE_PID = "010C", "010D", "0111"


def watts_to_hp(watts: float) -> float:
    return watts / W_PER_HP


def watts_to_ps(watts: float) -> float:
    return watts / W_PER_PS


class DynoError(ValueError):
    """The pull cannot be evaluated (missing parameter or not enough usable data)."""


@dataclass(frozen=True, slots=True)
class VehicleParameters:
    """User-supplied vehicle data. Only generic defaults are provided.

    ``curb_mass_kg`` has **no default**: the vehicle's mass must come from the user (its
    registration document or the manufacturer), never from this program.
    """

    curb_mass_kg: float | None = None
    extra_mass_kg: float = 75.0  # one driver, generic
    drag_coefficient: float = 0.33  # generic small sedan
    frontal_area_m2: float = 2.0  # generic small sedan
    drivetrain_loss: float = 0.15  # generic manual front-wheel drive
    rolling_resistance: float = 0.013  # generic tyres on asphalt
    air_density_kg_m3: float = 1.06  # about 1200 m altitude (Tehran)
    rotational_inertia_factor: float = 0.05

    def __post_init__(self) -> None:
        if self.curb_mass_kg is not None and not 300 <= self.curb_mass_kg <= 5000:
            raise ValueError("curb_mass_kg must be between 300 and 5000 kg")
        checks = {
            "extra_mass_kg": 0 <= self.extra_mass_kg <= 2000,
            "drag_coefficient": 0.1 <= self.drag_coefficient <= 1.5,
            "frontal_area_m2": 0.5 <= self.frontal_area_m2 <= 10,
            "drivetrain_loss": 0 <= self.drivetrain_loss < 0.6,
            "rolling_resistance": 0 <= self.rolling_resistance <= 0.1,
            "air_density_kg_m3": 0.5 <= self.air_density_kg_m3 <= 1.5,
            "rotational_inertia_factor": 0 <= self.rotational_inertia_factor <= 0.5,
        }
        for name, ok in checks.items():
            if not ok:
                raise ValueError(f"{name} is out of range: {getattr(self, name)}")

    @property
    def total_mass_kg(self) -> float:
        if self.curb_mass_kg is None:
            raise DynoError("the vehicle's curb mass is required")
        return self.curb_mass_kg + self.extra_mass_kg


@dataclass(frozen=True, slots=True)
class DynoSample:
    t: float
    rpm: float
    speed_kmh: float
    throttle_pct: float | None = None


@dataclass(frozen=True, slots=True)
class DynoPoint:
    rpm: float
    crank_power_w: float
    crank_torque_nm: float
    wheel_power_w: float


@dataclass(frozen=True, slots=True)
class DynoQuality:
    sample_count: int
    max_sample_interval_s: float
    ratio_r2: float
    rpm_min: float
    rpm_max: float
    gear_segments: int
    throttle_known: bool
    reasons: tuple[str, ...] = ()

    @property
    def low_quality(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True, slots=True)
class DynoResult:
    points: tuple[DynoPoint, ...]  # per sample, smoothed
    curve: tuple[DynoPoint, ...]  # resampled every 100 rpm, for plotting
    peak_power: DynoPoint
    peak_torque: DynoPoint
    quality: DynoQuality
    parameters: VehicleParameters
    simulated: bool
    estimated: bool = True


# --------------------------------------------------------------------------- #
# numerics (pure Python, standard library only)
# --------------------------------------------------------------------------- #


def _solve3(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a 3x3 linear system with partial pivoting; ``None`` if singular."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, 3):
            f = a[r][col] / a[col][col]
            for c in range(col, 4):
                a[r][c] -= f * a[col][c]
    x = [0.0, 0.0, 0.0]
    for r in (2, 1, 0):
        x[r] = (a[r][3] - sum(a[r][c] * x[c] for c in range(r + 1, 3))) / a[r][r]
    return x


def local_quadratic(
    t: list[float], y: list[float], half_window: int = 2
) -> list[tuple[float, float]]:
    """Smoothed value and derivative at each sample (locally weighted quadratic fit)."""
    n = len(t)
    width = 2 * half_window + 1
    out: list[tuple[float, float]] = []
    for i in range(n):
        lo = max(0, min(i - half_window, n - width))
        hi = min(n, lo + width)
        xs = [t[j] - t[i] for j in range(lo, hi)]
        ys = [y[j] for j in range(lo, hi)]
        span = max(abs(x) for x in xs) * 1.2 or 1.0
        ws = [(1 - (abs(x) / span) ** 3) ** 3 for x in xs]
        s = [sum(w * x**p for w, x in zip(ws, xs, strict=True)) for p in range(5)]
        r = [sum(w * x**p * yy for w, x, yy in zip(ws, xs, ys, strict=True)) for p in range(3)]
        solution = _solve3([[s[0], s[1], s[2]], [s[1], s[2], s[3]], [s[2], s[3], s[4]]], r)
        if solution is None:  # fewer than 3 distinct times: fall back to a secant
            j = min(i + 1, n - 1)
            k = max(i - 1, 0)
            slope = (y[j] - y[k]) / (t[j] - t[k]) if t[j] != t[k] else 0.0
            out.append((y[i], slope))
        else:
            out.append((solution[0], solution[1]))
    return out


def _gear_segments(samples: list[DynoSample], tolerance: float = 0.05) -> list[list[DynoSample]]:
    """Split where speed/rpm changes by more than ``tolerance`` (a gear change or slip)."""
    segments: list[list[DynoSample]] = []
    for sample in samples:
        ratio = sample.speed_kmh / sample.rpm
        if segments:
            current = segments[-1]
            reference = sorted(s.speed_kmh / s.rpm for s in current)[len(current) // 2]
            if abs(ratio - reference) / reference <= tolerance:
                current.append(sample)
                continue
        segments.append([sample])
    return segments


def _fit_ratio(segment: list[DynoSample]) -> tuple[float, float]:
    """Least-squares ``v = k·rpm`` through the origin (v in m/s); returns ``(k, R²)``."""
    v = [s.speed_kmh / 3.6 for s in segment]
    rpm = [s.rpm for s in segment]
    k = sum(a * b for a, b in zip(v, rpm, strict=True)) / sum(r * r for r in rpm)
    mean = sum(v) / len(v)
    ss_tot = sum((x - mean) ** 2 for x in v)
    ss_res = sum((x - k * r) ** 2 for x, r in zip(v, rpm, strict=True))
    return k, (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _interpolate(points: list[DynoPoint], step: float = 100.0) -> tuple[DynoPoint, ...]:
    ordered = sorted(points, key=lambda p: p.rpm)
    if len(ordered) < 2:
        return tuple(ordered)
    start = math.ceil(ordered[0].rpm / step) * step
    curve: list[DynoPoint] = []
    rpm = start
    j = 0
    while rpm <= ordered[-1].rpm:
        while ordered[j + 1].rpm < rpm:
            j += 1
        a, b = ordered[j], ordered[j + 1]
        f = 0.0 if b.rpm == a.rpm else (rpm - a.rpm) / (b.rpm - a.rpm)
        curve.append(
            DynoPoint(
                rpm=rpm,
                crank_power_w=a.crank_power_w + f * (b.crank_power_w - a.crank_power_w),
                crank_torque_nm=a.crank_torque_nm + f * (b.crank_torque_nm - a.crank_torque_nm),
                wheel_power_w=a.wheel_power_w + f * (b.wheel_power_w - a.wheel_power_w),
            )
        )
        rpm += step
    return tuple(curve)


# --------------------------------------------------------------------------- #
# computation
# --------------------------------------------------------------------------- #

MIN_SAMPLES = 6
MIN_SPEED_KMH = 10.0  # below this the clutch may still be slipping


def compute_dyno(
    samples: list[DynoSample],
    parameters: VehicleParameters,
    *,
    throttle_min_pct: float = 85.0,
    simulated: bool = False,
) -> DynoResult:
    """Evaluate one pull.

    Raises:
        DynoError: Without a curb mass, or with fewer than :data:`MIN_SAMPLES` usable
            samples in one gear at full throttle.
    """
    mass = parameters.total_mass_kg  # raises DynoError when the curb mass is missing
    throttle_known = all(s.throttle_pct is not None for s in samples)
    usable = [
        s
        for s in samples
        if s.rpm > 0
        and s.speed_kmh >= MIN_SPEED_KMH
        and (s.throttle_pct is None or s.throttle_pct >= throttle_min_pct)
    ]
    segments = _gear_segments(usable)
    if not segments:
        raise DynoError("no samples at full throttle above 10 km/h")
    segment = max(segments, key=len)
    if len(segment) < MIN_SAMPLES:
        raise DynoError(
            f"only {len(segment)} usable sample(s) in one gear; at least {MIN_SAMPLES} needed"
        )
    k, r2 = _fit_ratio(segment)
    t = [s.t for s in segment]
    smoothed = local_quadratic(t, [s.rpm for s in segment])

    m_eff = mass * (1 + parameters.rotational_inertia_factor)
    aero = 0.5 * parameters.air_density_kg_m3 * parameters.drag_coefficient
    aero *= parameters.frontal_area_m2
    rolling = parameters.rolling_resistance * mass * G
    points: list[DynoPoint] = []
    for rpm, drpm_dt in smoothed:
        v = k * rpm
        force = m_eff * k * drpm_dt + aero * v * v + rolling
        wheel = force * v
        crank = wheel / (1 - parameters.drivetrain_loss)
        omega = 2 * math.pi * rpm / 60
        if crank <= 0 or omega <= 0:
            continue  # not accelerating: not part of the pull
        points.append(DynoPoint(rpm, crank, crank / omega, wheel))
    if len(points) < MIN_SAMPLES // 2:
        raise DynoError("the pull shows no sustained acceleration")

    intervals = [b - a for a, b in zip(t, t[1:], strict=False)]
    reasons: list[str] = []
    if len(segment) < 8:
        reasons.append("few_samples")
    if r2 < 0.99:
        reasons.append("gear_fit")
    if max(intervals) > 1.0:
        reasons.append("slow_sampling")
    if not throttle_known:
        reasons.append("throttle_unknown")
    quality = DynoQuality(
        sample_count=len(segment),
        max_sample_interval_s=max(intervals),
        ratio_r2=r2,
        rpm_min=min(p.rpm for p in points),
        rpm_max=max(p.rpm for p in points),
        gear_segments=len(segments),
        throttle_known=throttle_known,
        reasons=tuple(reasons),
    )
    return DynoResult(
        points=tuple(points),
        curve=_interpolate(points),
        peak_power=max(points, key=lambda p: p.crank_power_w),
        peak_torque=max(points, key=lambda p: p.crank_torque_nm),
        quality=quality,
        parameters=parameters,
        simulated=simulated,
    )


@dataclass
class DynoRecorder:
    """Collects samples between :meth:`start` and :meth:`finish` (passive)."""

    samples: list[DynoSample] = field(default_factory=list)
    recording: bool = False
    simulated: bool = False

    def start(self) -> None:
        self.samples = []
        self.recording = True
        self.simulated = False

    def cancel(self) -> None:
        self.recording = False
        self.samples = []

    def feed_snapshot(self, snapshot) -> None:  # LiveSnapshot (avoids an import cycle)
        """Add one sample when rpm and speed were both read in this poll."""
        if not self.recording:
            return
        rpm = snapshot.reading_for(RPM_PID)
        speed = snapshot.reading_for(SPEED_PID)
        if rpm is None or speed is None:
            return
        if rpm.status is not PidStatus.OK or speed.status is not PidStatus.OK:
            return
        throttle = snapshot.reading_for(THROTTLE_PID)
        throttle_pct = (
            throttle.measurement.value
            if throttle is not None and throttle.status is PidStatus.OK
            else None
        )
        t = rpm.sampled_at_s if rpm.sampled_at_s is not None else speed.sampled_at_s
        if t is None:
            return
        self.simulated = self.simulated or snapshot.simulated
        self.samples.append(
            DynoSample(t, rpm.measurement.value, speed.measurement.value, throttle_pct)
        )

    def finish(
        self, parameters: VehicleParameters, *, throttle_min_pct: float = 85.0
    ) -> DynoResult:
        """Stop recording and evaluate the pull.

        Raises:
            DynoError: See :func:`compute_dyno`.
        """
        self.recording = False
        return compute_dyno(
            self.samples, parameters, throttle_min_pct=throttle_min_pct, simulated=self.simulated
        )
