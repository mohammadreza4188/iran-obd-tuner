"""Desktop user interface — **planned, not implemented**.

The roadmap (phase 1) targets PySide6 + pyqtgraph for the live dashboard, driven by
:class:`iran_obd_tuner.diagnostics.service.DiagnosticService` and the measurement models in
``core``. There is no UI code in this snapshot, on purpose: the diagnostic workflow and its
data models are meant to stabilise against the simulated adapter first (see
docs/architecture.md).

Until then the interim interface is the command line:

    python -m iran_obd_tuner diagnose        # or: iran-obd-tuner diagnose

Import rules for this package (once it exists): may import ``core``, ``diagnostics`` and
``app``; must not be imported by any other layer (the UI is a leaf).
"""
