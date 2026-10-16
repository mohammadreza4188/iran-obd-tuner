#!/usr/bin/env python3
"""Developer check script: what has to pass before a change leaves the machine.

Runs the same three gates as CI, in the same order, using the interpreter that runs this
script (so it works with any virtual environment, on Windows, Linux and macOS):

1. byte compile the sources (catches syntax errors everywhere, even in files no test imports)
2. ``ruff check`` and ``ruff format --check``
3. ``pytest``

Optional tools are reported as skipped instead of failing when they are not installed, so
the script is usable right after cloning with ``pip install -e ".[dev]"`` or offline with
just ``python -m pytest``.

Usage::

    python scripts/check.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPILE_TARGETS = ("src", "tests", "scripts")


def _run(label: str, command: list[str], *, optional_module: str | None = None) -> str:
    """Run one check. Returns ``"ok"``, ``"failed"`` or ``"skipped"``."""
    if optional_module is not None and importlib.util.find_spec(optional_module) is None:
        print(f"[skip] {label}: module {optional_module!r} is not installed")
        return "skipped"

    print(f"[run ] {label}: {' '.join(command)}")
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if completed.returncode == 0:
        print(f"[ok  ] {label}")
        return "ok"
    print(f"[fail] {label} (exit code {completed.returncode})")
    return "failed"


def main() -> int:
    """Run every check and report a summary. Returns the process exit code."""
    results = [
        _run("byte compile", [sys.executable, "-m", "compileall", "-q", *COMPILE_TARGETS]),
        _run("ruff lint", [sys.executable, "-m", "ruff", "check", "."], optional_module="ruff"),
        _run(
            "ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            optional_module="ruff",
        ),
        _run("pytest", [sys.executable, "-m", "pytest", "-q"], optional_module="pytest"),
    ]

    failed = results.count("failed")
    skipped = results.count("skipped")
    print(f"\nsummary: {results.count('ok')} ok, {failed} failed, {skipped} skipped")
    if skipped:
        print('install the development extra to run everything: pip install -e ".[dev]"')
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
