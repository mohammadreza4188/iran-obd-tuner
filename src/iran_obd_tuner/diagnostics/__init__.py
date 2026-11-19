"""Vehicle diagnostics: the read-only workflow and its result models.

Import rules for this package:

* may import ``core``, ``hardware`` and ``database``,
* must not import ``tuning``, ``ui`` or ``app``,
* diagnostic interpretation lives here (code structure, descriptions, report assembly);
  raw transport lives in ``hardware``; no third-party package is imported here.

Modules: ``service.py`` (one-shot report workflow used by the CLI), ``live_data.py``
(persistent polling session used by the dashboard), ``recorder.py`` (CSV recording of live
snapshots), ``models.py`` (report models). Both workflows use the same
:class:`~iran_obd_tuner.hardware.base.ObdAdapter` contract.

Everything in this package is read-only. Nothing here sends a state-changing command: the
only such method declared so far (``ObdAdapter.clear_diagnostic_trouble_codes``) is refused
by the safety policy and has no implementation.
"""
