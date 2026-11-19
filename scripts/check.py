#!/usr/bin/env python3
"""Developer check script: what has to pass before a change leaves the machine.

Runs the same three gates as CI, in the same order, using the interpreter that runs this
script (so it works with any virtual environment, on Windows, Linux and macOS):

1. byte compile the sources (catches syntax errors everywhere, even in files no test imports)
2. ``ruff check`` and ``ruff format --check``
3. ``pytest``

A gate whose tool (ruff, pytest) is not installed counts as a **failure** by default: a
skipped gate is not a passed gate, and reporting success without running the checks would
be a false green. Pass ``--allow-missing`` to downgrade missing tools to "skipped" (for
example on a machine that only has the standard library); the summary still lists them.

Usage::

    python scripts/check.py                  # strict (default, what a reviewer expects)
    python scripts/check.py --allow-missing  # tolerate missing tools, report them as skipped
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPILE_TARGETS = ("src", "tests", "scripts")


def _run(
    label: str,
    command: list[str],
    *,
    required_module: str | None = None,
    allow_missing: bool = False,
) -> str:
    """Run one check. Returns ``"ok"``, ``"failed"`` or ``"skipped"``."""
    if required_module is not None and importlib.util.find_spec(required_module) is None:
        if allow_missing:
            print(f"[skip] {label}: module {required_module!r} is not installed")
            return "skipped"
        print(f"[fail] {label}: module {required_module!r} is not installed")
        return "failed"

    print(f"[run ] {label}: {' '.join(command)}")
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if completed.returncode == 0:
        print(f"[ok  ] {label}")
        return "ok"
    print(f"[fail] {label} (exit code {completed.returncode})")
    return "failed"


def main(argv: Sequence[str] | None = None) -> int:
    """Run every check and report a summary. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Run the repository quality gates.")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="report gates whose tool is not installed as skipped instead of failed",
    )
    args = parser.parse_args(argv)
    lenient = args.allow_missing

    results = [
        _run("byte compile", [sys.executable, "-m", "compileall", "-q", *COMPILE_TARGETS]),
        _run(
            "ruff lint",
            [sys.executable, "-m", "ruff", "check", "."],
            required_module="ruff",
            allow_missing=lenient,
        ),
        _run(
            "ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            required_module="ruff",
            allow_missing=lenient,
        ),
        _run(
            "pytest",
            [sys.executable, "-m", "pytest", "-q"],
            required_module="pytest",
            allow_missing=lenient,
        ),
    ]

    failed = results.count("failed")
    skipped = results.count("skipped")
    print(f"\nsummary: {results.count('ok')} ok, {failed} failed, {skipped} skipped")
    if failed or skipped:
        print('missing tools? install the development extra: pip install -e ".[dev]"')
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
