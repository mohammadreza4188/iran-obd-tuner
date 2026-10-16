"""Graphical entry point: the PySide6 phase 1 diagnostics dashboard.

Run as ``iran-obd-tuner-gui`` (or ``python -m iran_obd_tuner.app.gui_main``). It needs the
optional ``[gui]`` extra (PySide6); the command line interface (``iran-obd-tuner``) keeps
working without it.

The dashboard is read-only: it displays live data and trouble codes. Its single
state-changing action (clearing trouble codes) is refused by the default safety policy —
see :mod:`iran_obd_tuner.diagnostics.obd_service`.

Nothing happens at import time: the ``QApplication`` is created inside :func:`main`, so tests
and CI can import this module in a headless environment (no display, no adapter).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from iran_obd_tuner.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """Start the dashboard and return the process exit code."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("iran-obd-tuner")
    app.setOrganizationName("iran-obd-tuner")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
