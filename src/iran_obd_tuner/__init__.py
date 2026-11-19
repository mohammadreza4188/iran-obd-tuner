"""Iran OBD Tuner — OBD-II diagnostics and future bench ECU calibration tooling.

The package is organised in layers that only depend downwards:

    app/         entry points and wiring          (imports everything below)
    ui/          PySide6 dashboard                (core + diagnostics; optional [gui])
    diagnostics/ read-only workflow + live data   (core + hardware + database)
    tuning/      bench/calibration interfaces     (core, interfaces only)
    hardware/    adapter interfaces + mock        (core)
    database/    knowledge database               (core)
    core/        config, logging, models, safety  (standard library only)

Nothing in this package talks to a real vehicle: the only adapter shipped today is an
in-memory simulator, and every operation that could change vehicle state is denied by
:mod:`iran_obd_tuner.core.safety` (no implementation for any of them exists). See
docs/architecture.md for the full picture.
"""

from __future__ import annotations

__all__ = ["__title__", "__version__"]

__title__ = "iran-obd-tuner"

# Single source of truth for the distribution version (read by pyproject.toml).
__version__ = "0.1.0"
