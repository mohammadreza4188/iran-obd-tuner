"""Desktop user interface (PySide6): the phase 1 diagnostics dashboard.

Modules:

* ``backend.py`` — the :class:`~iran_obd_tuner.ui.backend.DashboardBackend` contract the
  window needs; implemented by :mod:`iran_obd_tuner.app.dashboard` (no Qt import);
* ``i18n.py`` — Persian/English UI strings (no Qt import);
* ``gauges.py`` — circular gauge widgets with an explicit "no value" state;
* ``safety_modal.py`` — the refusal dialog for blocked (state-changing) actions;
* ``icon.py`` — the programmatic window icon;
* ``main_window.py`` — the dashboard window (gauges, live graph, trouble codes,
  identification, settings, event log, CSV recording).

Launch it with ``iran-obd-tuner-gui`` (see :mod:`iran_obd_tuner.app.gui_main`); it needs the
optional ``[gui]`` extra. This ``__init__`` deliberately imports nothing: importing the
package never requires Qt, never opens an adapter and never needs a display.

Import rules for this package (enforced by ``tests/unit/test_architecture.py``): may import
``core`` and ``diagnostics`` (and the package version); must not import ``hardware``,
``database``, ``tuning`` or ``app``; must not be imported by any layer except ``app``. All
vehicle data reaches the window through
:class:`~iran_obd_tuner.diagnostics.live_data.LiveDataSession`.
"""
