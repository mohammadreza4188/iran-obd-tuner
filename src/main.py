"""Entry point for Phase 1 diagnostics dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as: python src/main.py from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("iran-obd-tuner")
    app.setOrganizationName("iran-obd-tuner")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
