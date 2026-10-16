"""Desktop user interface (PySide6): the phase 1 diagnostics dashboard.

Modules:

* ``gauges.py`` — circular gauge widgets (``GaugeWidget``, ``GaugeCard``);
* ``i18n.py`` — Persian/English UI strings;
* ``main_window.py`` — the dashboard window (gauges, live graph, DTC list, log, settings).

Launch it with ``iran-obd-tuner-gui`` (see :mod:`iran_obd_tuner.app.gui_main`); it needs the
optional ``[gui]`` extra (PySide6, pyqtgraph). This ``__init__`` deliberately imports nothing:
importing the package never requires Qt, never opens an adapter and never needs a display —
only the submodules above import PySide6.

Import rules for this package: may import ``core`` and ``diagnostics``; must not be imported
by any other layer (the UI is a leaf). The command line interface remains the reference
interface and stays fully hardware- and Qt-free.

Known integration debt (tracked in docs/architecture.md, section "Known debt"):

* ``i18n.SAMPLE_DTC`` duplicates trouble code labels that belong in the knowledge database
  (:mod:`iran_obd_tuner.database`), which is the single source of truth for code meanings;
* ``obd_service.LiveSample`` mirrors :class:`iran_obd_tuner.core.models.Measurement`
  (value + unit + timestamp) instead of reusing it;
* ``obd_service.ObdService`` talks to python-OBD directly instead of going through
  :class:`iran_obd_tuner.hardware.base.ObdAdapter`.
"""
