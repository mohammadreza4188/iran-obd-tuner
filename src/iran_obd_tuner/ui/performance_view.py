"""Performance tab: 0-100 km/h timer and virtual dyno (both passive and estimated).

Both tools consume the live snapshots the window already receives; they send nothing to
the vehicle. While a run is armed or a pull is being recorded, the window may poll only
rpm, speed and throttle ("fast polling") - fewer commands, not more - which raises the
sample rate. Every number shown here is labelled estimated, and simulated input is said
to be simulated.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from iran_obd_tuner.diagnostics.dyno import (
    DynoError,
    DynoRecorder,
    DynoResult,
    VehicleParameters,
    watts_to_ps,
)
from iran_obd_tuner.diagnostics.live_data import LiveSnapshot
from iran_obd_tuner.diagnostics.performance import AbortReason, AccelerationTimer, TimerState
from iran_obd_tuner.ui.chart import DualAxisChart
from iran_obd_tuner.ui.i18n import COPY, Lang

__all__ = ["FAST_POLL_PIDS", "PerformanceView"]

#: What the window polls while a measurement is armed/running and fast polling is on.
FAST_POLL_PIDS: tuple[str, ...] = ("010C", "010D", "0111")

#: name -> (minimum, maximum, decimals, step, scale from the spin box to the parameter)
_PARAMETER_FIELDS: dict[str, tuple[float, float, int, float, float]] = {
    "curb_mass_kg": (0.0, 5000.0, 0, 10.0, 1.0),  # 0 = not entered
    "extra_mass_kg": (0.0, 2000.0, 0, 5.0, 1.0),
    "drag_coefficient": (0.10, 1.50, 2, 0.01, 1.0),
    "frontal_area_m2": (0.5, 10.0, 2, 0.05, 1.0),
    "drivetrain_loss": (0.0, 59.0, 0, 1.0, 0.01),  # shown in percent
    "rolling_resistance": (0.0, 0.1, 3, 0.001, 1.0),
    "air_density_kg_m3": (0.5, 1.5, 2, 0.01, 1.0),
}


def _fmt(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"


class PerformanceView(QWidget):
    """The performance tab."""

    def __init__(self, defaults: VehicleParameters, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lang: Lang = "fa"
        self._defaults = defaults
        self.timer = AccelerationTimer()
        self.recorder = DynoRecorder()
        self._dyno_result: DynoResult | None = None
        self._dyno_error: str | None = None
        self._connected = False
        self._last_now = 0.0

        layout = QVBoxLayout(self)
        self.fast_poll = QCheckBox()
        self.fast_poll.setChecked(True)
        layout.addWidget(self.fast_poll)

        # --- 0-100 timer
        self.timer_box = QGroupBox()
        timer_layout = QVBoxLayout(self.timer_box)
        timer_row = QHBoxLayout()
        self.elapsed_label = QLabel("—")
        self.elapsed_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #6366f1;")
        self.timer_state_label = QLabel()
        self.arm_btn = QPushButton()
        self.arm_btn.clicked.connect(self.arm_timer)
        self.reset_btn = QPushButton()
        self.reset_btn.clicked.connect(self.reset_timer)
        timer_row.addWidget(self.elapsed_label)
        timer_row.addWidget(self.timer_state_label, 1)
        timer_row.addWidget(self.arm_btn)
        timer_row.addWidget(self.reset_btn)
        timer_layout.addLayout(timer_row)
        self.timer_result_label = QLabel()
        self.timer_quality_label = QLabel()
        self.timer_quality_label.setStyleSheet("color: #94a3b8;")
        self.timer_note = QLabel()
        self.timer_note.setWordWrap(True)
        self.timer_note.setStyleSheet("color: #fbbf24;")
        for widget in (self.timer_result_label, self.timer_quality_label, self.timer_note):
            timer_layout.addWidget(widget)
        layout.addWidget(self.timer_box)

        # --- virtual dyno
        self.dyno_box = QGroupBox()
        dyno_layout = QVBoxLayout(self.dyno_box)
        self.dyno_warning = QLabel()
        self.dyno_warning.setWordWrap(True)
        self.dyno_warning.setStyleSheet("color: #fbbf24; font-weight: bold;")
        dyno_layout.addWidget(self.dyno_warning)
        params_row = QHBoxLayout()
        self.form = QFormLayout()
        self.param_labels: dict[str, QLabel] = {}
        self.param_spins: dict[str, QDoubleSpinBox] = {}
        for name, (low, high, decimals, step, scale) in _PARAMETER_FIELDS.items():
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(decimals)
            spin.setSingleStep(step)
            value = getattr(defaults, name)
            spin.setValue(0.0 if value is None else value / scale)
            label = QLabel()
            self.param_labels[name] = label
            self.param_spins[name] = spin
            self.form.addRow(label, spin)
        params_row.addLayout(self.form)
        buttons = QVBoxLayout()
        self.dyno_start_btn = QPushButton()
        self.dyno_start_btn.clicked.connect(self.start_pull)
        self.dyno_finish_btn = QPushButton()
        self.dyno_finish_btn.clicked.connect(self.finish_pull)
        self.dyno_cancel_btn = QPushButton()
        self.dyno_cancel_btn.clicked.connect(self.cancel_pull)
        for button in (self.dyno_start_btn, self.dyno_finish_btn, self.dyno_cancel_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        params_row.addLayout(buttons)
        dyno_layout.addLayout(params_row)
        self.dyno_note = QLabel()
        self.dyno_note.setWordWrap(True)
        self.dyno_note.setStyleSheet("color: #94a3b8;")
        dyno_layout.addWidget(self.dyno_note)
        self.dyno_status_label = QLabel()
        self.dyno_peaks_label = QLabel()
        self.dyno_peaks_label.setStyleSheet("font-weight: bold;")
        self.dyno_quality_label = QLabel()
        self.dyno_quality_label.setWordWrap(True)
        for widget in (self.dyno_status_label, self.dyno_peaks_label, self.dyno_quality_label):
            dyno_layout.addWidget(widget)
        self.chart = DualAxisChart()
        dyno_layout.addWidget(self.chart, 1)
        layout.addWidget(self.dyno_box, 1)
        self._update_controls()

    # ------------------------------------------------------------------ public API
    @property
    def wants_fast_poll(self) -> bool:
        busy = self.timer.wants_fast_poll or self.recorder.recording
        return busy and self.fast_poll.isChecked()

    def vehicle_parameters(self) -> VehicleParameters:
        """Parameters currently entered (curb mass 0 = not entered)."""
        values: dict[str, float | None] = {}
        for name, (_low, _high, _dec, _step, scale) in _PARAMETER_FIELDS.items():
            values[name] = self.param_spins[name].value() * scale
        if not values["curb_mass_kg"]:
            values["curb_mass_kg"] = None
        return replace(self._defaults, **values)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.timer.abort(AbortReason.DISCONNECTED)
            if self.recorder.recording:
                self.recorder.cancel()
        self._update_controls()
        self._render()

    def feed_snapshot(self, snapshot: LiveSnapshot, now: float) -> None:
        self._last_now = now
        self.timer.feed_snapshot(snapshot)
        self.recorder.feed_snapshot(snapshot)
        self._render()

    def arm_timer(self) -> None:
        self.timer.arm()
        self._render()

    def reset_timer(self) -> None:
        self.timer.reset()
        self._render()

    def start_pull(self) -> None:
        self._dyno_result = None
        self._dyno_error = None
        self.chart.clear()
        self.recorder.start()
        self._update_controls()
        self._render()

    def finish_pull(self) -> None:
        try:
            self._dyno_result = self.recorder.finish(self.vehicle_parameters())
            self._dyno_error = None
        except (DynoError, ValueError) as exc:
            self._dyno_result = None
            self._dyno_error = str(exc)
        self._update_controls()
        self._render()

    def cancel_pull(self) -> None:
        self.recorder.cancel()
        self._update_controls()
        self._render()

    @property
    def dyno_result(self) -> DynoResult | None:
        return self._dyno_result

    def set_language(self, lang: Lang) -> None:
        self._lang = lang
        t = COPY[lang]
        self.fast_poll.setText(t["perf_fast_poll"])
        self.timer_box.setTitle(t["timer_title"])
        self.arm_btn.setText(t["timer_arm"])
        self.reset_btn.setText(t["timer_reset"])
        self.timer_note.setText(t["timer_note"])
        self.dyno_box.setTitle(t["dyno_title"])
        self.dyno_warning.setText(t["dyno_warning"])
        self.dyno_note.setText(t["dyno_note"])
        for name, label in self.param_labels.items():
            label.setText(t["dyno_params"][name])
        self.param_spins["curb_mass_kg"].setSpecialValueText(t["dyno_mass_unset"])
        self.dyno_start_btn.setText(t["dyno_start"])
        self.dyno_finish_btn.setText(t["dyno_finish"])
        self.dyno_cancel_btn.setText(t["dyno_cancel"])
        self.chart.set_titles(t["dyno_chart_rpm"], t["dyno_chart_power"], t["dyno_chart_torque"])
        self._render()

    # ------------------------------------------------------------------ rendering
    def _update_controls(self) -> None:
        recording = self.recorder.recording
        self.arm_btn.setEnabled(self._connected)
        self.dyno_start_btn.setEnabled(self._connected and not recording)
        self.dyno_finish_btn.setEnabled(recording)
        self.dyno_cancel_btn.setEnabled(recording)

    def _render(self) -> None:
        self._render_timer()
        self._render_dyno()

    def _render_timer(self) -> None:
        t = COPY[self._lang]
        state = self.timer.state
        text = t["timer_state"][state.value]
        if state is TimerState.ABORTED and self.timer.abort_reason is not None:
            text = text.format(t["timer_abort"][self.timer.abort_reason.value])
        self.timer_state_label.setText(text)
        elapsed = self.timer.elapsed_s(self._last_now)
        self.elapsed_label.setText("—" if elapsed is None else f"{elapsed:.2f} s")
        result = self.timer.result
        if result is None:
            self.timer_result_label.setText("")
            self.timer_quality_label.setText("")
            return
        self.timer_result_label.setText(
            t["timer_result"].format(
                _fmt(result.split_kmh, 0),
                _fmt(result.split_s),
                _fmt(result.target_kmh, 0),
                _fmt(result.target_s),
                _fmt(result.distance_m, 0),
            )
            + ("  " + t["perf_simulated"] if result.simulated else "")
        )
        self.timer_quality_label.setText(
            t["timer_quality"].format(
                _fmt(result.uncertainty_s), _fmt(result.max_sample_interval_s), result.sample_count
            )
        )

    def _render_dyno(self) -> None:
        t = COPY[self._lang]
        status = t["dyno_status"]
        if self.recorder.recording:
            self.dyno_status_label.setText(status["recording"].format(len(self.recorder.samples)))
        elif self._dyno_error is not None:
            self.dyno_status_label.setText(status["error"].format(self._dyno_error))
        elif self._dyno_result is not None:
            self.dyno_status_label.setText(status["done"])
        else:
            self.dyno_status_label.setText(status["idle"])
        result = self._dyno_result
        if result is None:
            self.dyno_peaks_label.setText("")
            self.dyno_quality_label.setText("")
            return
        power, torque = result.peak_power, result.peak_torque
        peaks = t["dyno_peaks"].format(
            _fmt(watts_to_ps(power.crank_power_w), 1),
            _fmt(power.crank_power_w / 1000, 1),
            _fmt(power.rpm, 0),
            _fmt(torque.crank_torque_nm, 1),
            _fmt(torque.rpm, 0),
        )
        self.dyno_peaks_label.setText(
            f"{peaks}  ({t['perf_estimated']})"
            + ("  " + t["perf_simulated"] if result.simulated else "")
        )
        quality = result.quality
        text = t["dyno_quality"].format(
            quality.sample_count, _fmt(quality.max_sample_interval_s), _fmt(quality.ratio_r2, 3)
        )
        if quality.low_quality:
            reasons = ", ".join(t["dyno_quality_reasons"][r] for r in quality.reasons)
            text += "\n" + t["dyno_quality_low"].format(reasons)
        self.dyno_quality_label.setText(text)
        curve = result.curve
        self.chart.set_series(
            [p.rpm for p in curve],
            [watts_to_ps(p.crank_power_w) for p in curve],
            [p.crank_torque_nm for p in curve],
        )
