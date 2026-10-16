"""Allow ``python -m iran_obd_tuner`` to run the command line interface."""

from __future__ import annotations

from iran_obd_tuner.app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
