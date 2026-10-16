"""Command line interface — read-only, hardware independent by default.

Commands:

* ``version``  — version and interpreter information (works without any configuration).
* ``adapters`` — every declared adapter and whether it is implemented.
* ``config``   — resolved settings and where they came from.
* ``diagnose`` — run one read-only diagnostic session and print or save the report
  (uses the simulated adapter by default; no vehicle is required).

Output is ASCII English so that it stays readable in any terminal, in logs and in CI.
Persian language output belongs to the future UI layer, not to the CLI.

Exit codes: ``0`` success, ``1`` runtime failure, ``2`` usage or configuration error,
``130`` interrupted (Ctrl+C).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from iran_obd_tuner import __title__, __version__
from iran_obd_tuner.app.application import Application
from iran_obd_tuner.core.config import AppSettings, load_settings
from iran_obd_tuner.core.exceptions import (
    AdapterNotFoundError,
    ConfigurationError,
    IranObdTunerError,
)
from iran_obd_tuner.core.logging import configure_logging, get_logger, normalize_log_level
from iran_obd_tuner.core.pids import is_valid_pid, normalize_pid
from iran_obd_tuner.diagnostics.models import DiagnosticReport
from iran_obd_tuner.hardware.registry import declared_adapters, implemented_adapters

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

_LOG = get_logger("app.cli")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the ``iran-obd-tuner`` command."""
    parser = argparse.ArgumentParser(
        prog="iran-obd-tuner",
        description="Read-only OBD-II diagnostics for the Iranian market (no vehicle required "
        "with the simulated adapter).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="configuration file (default: config/local.toml when it exists)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        metavar="LEVEL",
        help="override the configured log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print version information")

    subparsers.add_parser("adapters", help="list declared adapters and their status")

    subparsers.add_parser("config", help="print the resolved settings")

    diagnose = subparsers.add_parser(
        "diagnose",
        help="run one read-only diagnostic session (simulated adapter by default)",
    )
    diagnose.add_argument(
        "--adapter",
        default=None,
        metavar="NAME",
        help="adapter to use, overriding the configured one (e.g. mock)",
    )
    diagnose.add_argument(
        "--pids",
        default=None,
        metavar="LIST",
        help="comma separated parameter identifiers to read, e.g. 010C,010D",
    )
    diagnose.add_argument(
        "--json",
        action="store_true",
        help="print the report as JSON",
    )
    diagnose.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="also write the JSON report to this file",
    )
    diagnose.add_argument(
        "--no-vin",
        action="store_true",
        help="skip the vehicle identification request",
    )
    return parser


def _cmd_version() -> int:
    print(f"{__title__} {__version__}")
    print(f"python {platform.python_version()} ({platform.system()} {platform.machine()})")
    return EXIT_OK


def _cmd_adapters() -> int:
    declared = declared_adapters()
    implemented = implemented_adapters()
    print(f"declared adapters: {len(declared)} ({len(implemented)} implemented)")
    for info in declared:
        status = "implemented" if info.implemented else "NOT implemented"
        print(
            f"  {info.name:<10} kind={str(info.kind):<7} {status:<15} "
            f"hardware={info.hardware_required}"
        )
        print(f"      {info.description}")
        if info.protocols:
            print(f"      protocols: {', '.join(info.protocols)}")
        if info.notes:
            print(f"      note: {info.notes}")
    print("only implemented adapters can be selected with 'diagnose --adapter <name>'")
    return EXIT_OK


def _cmd_config(settings: AppSettings) -> int:
    origin = "defaults only" if settings.config_file is None else str(settings.config_file)
    print(f"# configuration file: {origin} (+ IRAN_OBD_TUNER_* environment overrides)")
    for key, value in sorted(settings.as_dict().items()):
        if isinstance(value, list):
            value = ", ".join(value)
        print(f"{key} = {value}")
    return EXIT_OK


def _render_report(report: DiagnosticReport) -> tuple[str, ...]:
    """Render a report as plain text lines, for terminals, logs and issue reports."""
    vehicle = report.vehicle
    lines: list[str] = []
    origin = "SIMULATED" if vehicle.simulated else "vehicle"
    lines.append(f"vehicle ({origin}): vin={vehicle.vin or 'unknown'}")
    lines.append(f"  protocol: {vehicle.protocol or 'unknown'}")
    lines.append(f"  adapter: {vehicle.adapter_name} ({report.adapter.kind})")

    lines.append(f"measurements ({len(report.measurements)}):")
    for measurement in report.measurements:
        lines.append(
            f"  {measurement.pid}  {measurement.name:<32} "
            f"{measurement.value:>10.2f} {measurement.unit}"
        )

    lines.append(f"trouble codes ({len(report.trouble_codes)}):")
    if not report.trouble_codes:
        lines.append("  none stored")
    for trouble_code in report.trouble_codes:
        known = "" if trouble_code.is_known else " [no description]"
        lines.append(
            f"  {trouble_code.code} [{trouble_code.scope}]{known} {trouble_code.display_text}"
        )

    if report.skipped_pids:
        lines.append(f"parameters not answered: {', '.join(report.skipped_pids)}")

    if report.knowledge_source:
        authoritative = (report.knowledge_metadata or {}).get("authoritative", False)
        note = "authoritative" if authoritative else "NOT authoritative"
        lines.append(f"knowledge database: {report.knowledge_source} ({note})")
    else:
        lines.append("knowledge database: not used (trouble codes have no descriptions)")

    lines.append(f"mode: {'read-only' if report.read_only else 'WRITE'}")
    lines.append(f"duration: {report.duration_s:.3f} s")
    return tuple(lines)


def _cmd_diagnose(args: argparse.Namespace, settings: AppSettings) -> int:
    pids: tuple[str, ...] | None = None
    if args.pids:
        pids = tuple(normalize_pid(part) for part in args.pids.split(",") if part.strip())
        if not pids:
            raise ConfigurationError("--pids was given but contains no parameter identifier")
        invalid = [pid for pid in pids if not is_valid_pid(pid)]
        if invalid:
            raise ConfigurationError(
                f"--pids contains invalid parameter identifier(s): {', '.join(invalid)} "
                "(expected request form such as 010C)"
            )

    if args.adapter:
        settings = replace(settings, adapter=args.adapter)

    application = Application.from_settings(settings)
    report = application.diagnose(pids=pids, read_vin=not args.no_vin)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        for line in _render_report(report):
            print(line)

    if args.output is not None:
        path = Path(args.output)
        if path.parent != Path():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _LOG.info("report written to %s", path)
        print(f"report written to {path}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Argument list; defaults to :data:`sys.argv` minus the program name.

    Returns:
        Process exit code (see the module docstring).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # 'version' must work even when the configuration file is broken.
    if args.command == "version":
        return _cmd_version()

    try:
        settings = load_settings(args.config)
        if args.log_level:
            settings = replace(settings, log_level=normalize_log_level(args.log_level))
        configure_logging(settings.log_level, settings.log_file, force=True)
        _LOG.debug("settings loaded from %s", settings.config_file or "defaults")

        if args.command == "adapters":
            return _cmd_adapters()
        if args.command == "config":
            return _cmd_config(settings)
        if args.command == "diagnose":
            return _cmd_diagnose(args, settings)
    except (ConfigurationError, AdapterNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except IranObdTunerError as exc:
        _LOG.error("%s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED

    parser.error(f"unknown command: {args.command}")  # pragma: no cover - argparse guards this
    return EXIT_USAGE
