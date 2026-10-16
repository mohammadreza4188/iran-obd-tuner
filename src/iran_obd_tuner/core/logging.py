"""Logging setup shared by every entry point.

The project uses the standard :mod:`logging` module only. Library code asks for a logger
with :func:`get_logger`; only entry points (CLI, tests, future UI) call
:func:`configure_logging`, so importing the package never changes global logging state.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from iran_obd_tuner.core.exceptions import ConfigurationError

__all__ = [
    "DATE_FORMAT",
    "LOG_FORMAT",
    "LOGGER_NAMESPACE",
    "configure_logging",
    "get_logger",
    "is_configured",
    "normalize_log_level",
]

#: Root logger namespace of the application; every project logger lives below it.
LOGGER_NAMESPACE = "iran_obd_tuner"

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def normalize_log_level(level: str) -> str:
    """Validate a level name and return it uppercased.

    Raises:
        ConfigurationError: If ``level`` is not a known logging level.
    """
    name = str(level).strip().upper()
    if name not in logging.getLevelNamesMapping():
        valid = ", ".join(sorted(logging.getLevelNamesMapping()))
        raise ConfigurationError(f"unknown log level {level!r}; expected one of: {valid}")
    return name


def configure_logging(
    level: str = "INFO",
    log_file: Path | str | None = None,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the application logger.

    Args:
        level: Level name (``"DEBUG"`` ... ``"CRITICAL"``).
        log_file: Optional file to append log records to. Parent directories are created.
        stream: Stream for the console handler, defaults to ``sys.stderr``.
        force: Re-configure even when logging is already configured (used by tests and by
            repeated CLI invocations in one process).

    Returns:
        The configured root logger of the application.

    Raises:
        ConfigurationError: If ``level`` is unknown.
        OSError: If the log file cannot be opened.
    """
    global _configured

    logger = logging.getLogger(LOGGER_NAMESPACE)
    if _configured and not force:
        return logger

    resolved_level = normalize_log_level(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(resolved_level)
    # The application logger owns its handlers; do not duplicate output through the root.
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr if stream is None else stream)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        path = Path(log_file)
        if path.parent != Path():
            path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _configured = True
    logger.debug("logging configured at level %s", resolved_level)
    return logger


def is_configured() -> bool:
    """Return ``True`` once :func:`configure_logging` has run in this process."""
    return _configured


def get_logger(name: str) -> logging.Logger:
    """Return a logger inside the application namespace.

    ``get_logger("hardware.mock")`` returns the logger ``iran_obd_tuner.hardware.mock``.
    Names that already start with the namespace are used unchanged.
    """
    if name == LOGGER_NAMESPACE or name.startswith(f"{LOGGER_NAMESPACE}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")
