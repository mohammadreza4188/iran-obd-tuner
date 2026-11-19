"""Graphical entry point: the PySide6 diagnostics dashboard.

Run as ``iran-obd-tuner-gui`` (or ``python -m iran_obd_tuner.app.gui_main``). It needs the
optional ``[gui]`` extra (PySide6); the command line interface (``iran-obd-tuner``) keeps
working without it.

Startup mirrors the CLI: settings (defaults ← ``config/local.toml`` ← environment), logging,
knowledge database, then :class:`~iran_obd_tuner.app.dashboard.AppDashboardBackend` wires the
window to the adapter registry. The dashboard is read-only: clearing trouble codes is
refused by the adapter's safety gate and no implementation for it exists.

Nothing happens at import time: the ``QApplication`` is created inside :func:`main`, so tests
and CI can import this module in a headless environment (no display, no adapter).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from iran_obd_tuner.app.dashboard import AppDashboardBackend
from iran_obd_tuner.core.config import load_settings
from iran_obd_tuner.core.exceptions import ConfigurationError, KnowledgeDatabaseError
from iran_obd_tuner.core.logging import configure_logging, get_logger
from iran_obd_tuner.ui.i18n import COPY
from iran_obd_tuner.ui.main_window import MainWindow

EXIT_USAGE = 2

_LOG = get_logger("app.gui")


def main(argv: list[str] | None = None) -> int:
    """Start the dashboard and return the process exit code."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("iran-obd-tuner")
    app.setOrganizationName("iran-obd-tuner")

    try:
        settings = load_settings()
        configure_logging(settings.log_level, settings.log_file, force=True)
        backend = AppDashboardBackend(settings)
    except (ConfigurationError, KnowledgeDatabaseError) as exc:
        # No language has been chosen yet, so the heading is shown in both languages; the
        # error detail itself is technical English from the configuration layer.
        heading = f"{COPY['fa']['startup_failed']} / {COPY['en']['startup_failed']}"
        QMessageBox.critical(None, "iran-obd-tuner", f"{heading}\n\n{exc}")
        return EXIT_USAGE

    _LOG.info("dashboard starting (settings from %s)", settings.config_file or "defaults")
    window = MainWindow(backend)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
