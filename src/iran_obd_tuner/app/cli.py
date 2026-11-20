"""Command line interface — read-only, hardware independent by default.

Commands:

* ``version``  — version and interpreter information (works without any configuration).
* ``adapters`` — every declared adapter and whether it is implemented.
* ``config``   — resolved settings and where they came from.
* ``diagnose`` — run one read-only diagnostic session and print or save the report
  (uses the simulated adapter by default; no vehicle is required). A real adapter is used
  with ``--adapter elm327 --port COM3``.
* ``uds-ident`` — read-only UDS ECU identification over CAN (DIDs via ``0x22``, DTCs via
  ``0x19``). ``--interface virtual-sim`` (default) talks to the simulated ECU on python-can's
  virtual bus; real interfaces (``slcan``, ``pcan``, ...) need the ``[can]`` extra and
  ``--channel``. Its JSON is a separate document, not a diagnostic report.
* ``vehicles list|show|match`` - the read-only Iranian vehicle / ECU knowledge base: every
  claim with its confidence level and source; ``match`` ranks profiles against saved
  ``diagnose`` / ``uds-ident`` JSON (offline, nothing is sent to a vehicle).

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
from iran_obd_tuner.app.application import Application, uds_link_config
from iran_obd_tuner.core.config import CAN_INTERFACE_CHOICES, AppSettings, load_settings
from iran_obd_tuner.core.exceptions import (
    AdapterNotFoundError,
    ConfigurationError,
    IranObdTunerError,
    VehicleKnowledgeError,
)
from iran_obd_tuner.core.knowledge import (
    Claim,
    ConfidenceLevel,
    VehicleProfile,
    claim_rows,
    format_claim_value,
)
from iran_obd_tuner.core.logging import configure_logging, get_logger, normalize_log_level
from iran_obd_tuner.core.pids import is_valid_pid, normalize_pid
from iran_obd_tuner.database.vehicles import JsonVehicleKnowledgeBase, VehicleKnowledgeBase
from iran_obd_tuner.diagnostics.ecu_identification import (
    EcuIdentification,
    UdsIdentificationSession,
)
from iran_obd_tuner.diagnostics.models import DiagnosticReport, ReadOutcome, TroubleCode
from iran_obd_tuner.diagnostics.vehicle_matcher import MatchReport, ObservedFacts, rank_candidates
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
        help="adapter to use, overriding the configured one (e.g. mock, elm327)",
    )
    diagnose.add_argument(
        "--port",
        default=None,
        metavar="PORT",
        help="serial port of a real adapter, overriding 'serial_port' (e.g. COM3)",
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

    uds = subparsers.add_parser(
        "uds-ident",
        help="read-only UDS ECU identification over CAN (simulated ECU by default)",
    )
    uds.add_argument(
        "--interface",
        choices=CAN_INTERFACE_CHOICES,
        default=None,
        help="CAN interface, overriding 'can_interface' (virtual-sim = simulated ECU)",
    )
    uds.add_argument(
        "--channel",
        default=None,
        metavar="NAME",
        help="interface channel, e.g. COM5 (slcan), PCAN_USBBUS1 (pcan), can0 (socketcan)",
    )
    uds.add_argument("--bitrate", type=int, default=None, metavar="BPS", help="CAN bitrate")
    uds.add_argument(
        "--discover",
        action="store_true",
        help="first send one functional TesterPresent (0x7DF 3E 00) and list who answers",
    )
    uds.add_argument(
        "--extended-session",
        action="store_true",
        help="allow a read-only fallback into the extended session (0x10 0x03) for DIDs the "
        "default session refuses; the ECU is always returned to 0x10 0x01",
    )
    uds.add_argument("--json", action="store_true", help="print the result as JSON")
    uds.add_argument(
        "--output", type=Path, default=None, metavar="PATH", help="also write the JSON here"
    )

    vehicles = subparsers.add_parser(
        "vehicles",
        help="browse the read-only vehicle / ECU knowledge base (claims with confidence)",
    )
    vehicle_commands = vehicles.add_subparsers(dest="vehicles_command", required=True)
    v_list = vehicle_commands.add_parser("list", help="list vehicles and their variants")
    v_list.add_argument("--manufacturer", default=None, metavar="ID", help="e.g. ikco, saipa")
    v_list.add_argument("--json", action="store_true", help="print JSON")
    v_show = vehicle_commands.add_parser("show", help="every claim of one vehicle")
    v_show.add_argument("vehicle_id", metavar="VEHICLE_ID", help="e.g. ikco.peugeot_pars")
    v_show.add_argument("--json", action="store_true", help="print JSON")
    v_match = vehicle_commands.add_parser(
        "match",
        help="rank profiles against saved 'diagnose --output' / 'uds-ident --output' JSON",
    )
    v_match.add_argument("--report", type=Path, default=None, metavar="PATH")
    v_match.add_argument("--uds", type=Path, default=None, metavar="PATH")
    v_match.add_argument("--json", action="store_true", help="print JSON")
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


def _render_codes(codes: Sequence[TroubleCode]) -> list[str]:
    lines = []
    for trouble_code in codes:
        known = "" if trouble_code.is_known else " [no description]"
        lines.append(
            f"  {trouble_code.code} [{trouble_code.scope}]{known} {trouble_code.display_text}"
        )
    return lines


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

    monitor = report.monitor_status
    if monitor is not None:
        if monitor.status is not None:
            state = monitor.status
            mil = "ON" if state.mil_on else "off"
            incomplete = ", ".join(state.incomplete_monitors) or "none"
            lines.append(
                f"MIL (check engine): {mil}; confirmed codes: {state.confirmed_dtc_count}; "
                f"ignition: {state.ignition}; incomplete monitors: {incomplete}"
            )
            if state.ecu_count > 1:
                lines.append(f"  ({state.ecu_count} ECUs answered; readiness from the first)")
        else:
            lines.append(f"MIL (check engine): unknown ({monitor.outcome}: {monitor.detail})")

    categories = report.dtc_categories
    if not categories:
        lines.append(f"trouble codes ({len(report.trouble_codes)}):")
        if not report.trouble_codes:
            lines.append("  none stored")
        lines.extend(_render_codes(report.trouble_codes))
    for category in categories:
        title = f"{category.status} trouble codes (service {category.status.service})"
        if category.outcome is not ReadOutcome.OK:
            lines.append(f"{title}: NOT AVAILABLE - {category.outcome}: {category.detail}")
            continue
        lines.append(f"{title} ({len(category.codes)}):")
        if not category.codes:
            lines.append("  none reported")
        lines.extend(_render_codes(category.codes))

    frame = report.freeze_frame
    if frame is not None:
        if frame.outcome is not ReadOutcome.OK:
            lines.append(f"freeze frame: NOT AVAILABLE - {frame.outcome}: {frame.detail}")
        elif not frame.present:
            lines.append("freeze frame: none stored")
        else:
            trigger = frame.trigger.code if frame.trigger is not None else "unknown"
            lines.append(f"freeze frame 00 (trigger {trigger}):")
            for reading in frame.readings:
                if reading.measurement is not None:
                    m = reading.measurement
                    lines.append(f"  {m.pid}  {m.name:<32} {m.value:>10.2f} {m.unit}")
                else:
                    lines.append(f"  {reading.pid}  {'-':<32} {'n/a':>10} ({reading.status})")

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


def _render_identification(result: EcuIdentification) -> tuple[str, ...]:
    origin = "SIMULATED ECU - not vehicle data" if result.simulated else "CAN bus"
    lines = [
        f"ECU identification ({origin}): {result.interface}:{result.channel or '-'} "
        f"request {result.request_id:03X} / response {result.response_id:03X}",
    ]
    for reading in result.dids:
        label = f"  {reading.did:04X} {reading.name:<22}"
        if reading.outcome is ReadOutcome.OK:
            assert reading.raw is not None
            value = reading.text if reading.text is not None else reading.raw.hex(" ").upper()
            session = " (extended session)" if reading.session == "extended" else ""
            lines.append(f"{label} {value}{session}")
        else:
            lines.append(f"{label} NOT AVAILABLE - {reading.outcome}: {reading.detail}")
    vin = result.vin
    lines.append(f"vin (from DID F190): {vin or 'unknown'}")
    if result.extended_session_detail:
        lines.append(f"extended session refused: {result.extended_session_detail}")
    elif result.extended_session_used:
        lines.append("extended session: used for a fallback read, then returned to default")
    if result.dtc_outcome is not ReadOutcome.OK:
        lines.append(f"DTCs (0x19): NOT AVAILABLE - {result.dtc_outcome}: {result.dtc_detail}")
    else:
        lines.append(f"DTCs (0x19 0x02, format {result.dtc_format or 0:02X}): {len(result.dtcs)}")
        for dtc in result.dtcs:
            status = dtc.status
            flags = ", ".join(
                name
                for name, on in (
                    ("confirmed", status.confirmed),
                    ("pending", status.pending),
                    ("test failed", status.test_failed),
                    ("warning lamp", status.warning_indicator),
                )
                if on
            )
            code = dtc.code or f"raw {dtc.raw.hex().upper()}"
            lines.append(f"  {code}  status {status.raw:02X} ({flags or 'no flag set'})")
    lines.append("mode: read-only")
    return tuple(lines)


def _cmd_uds_ident(args: argparse.Namespace, settings: AppSettings) -> int:
    config = uds_link_config(
        settings, interface=args.interface, channel=args.channel, bitrate=args.bitrate
    )
    if not config.simulated:
        print(
            "safety: read-only requests only. Ignition ON, engine OFF, vehicle stationary.",
            file=sys.stderr,
        )
    session = UdsIdentificationSession(config, allow_extended_session=args.extended_session)
    if args.discover:
        found = session.discover()
        listed = ", ".join(f"{rid:03X}" for rid in found) or "none"
        print(f"functional TesterPresent answered by: {listed}")
    result = session.identify()
    payload = json.dumps(result.as_dict(), indent=2, ensure_ascii=False)
    if args.json:
        print(payload)
    else:
        for line in _render_identification(result):
            print(line)
    if args.output is not None:
        path = Path(args.output)
        if path.parent != Path():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(f"identification written to {path}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# vehicles
# --------------------------------------------------------------------------- #

VEHICLE_LIST_SCHEMA_VERSION = 1


def _claim_dict(field: str, claim: Claim[object] | None, kb: VehicleKnowledgeBase) -> dict:
    if claim is None:
        return {"field": field, "value": None, "confidence": "unknown", "recorded": False}
    return {
        "field": field,
        "value": None if claim.value is None else format_claim_value(field, claim.value),
        "confidence": str(claim.confidence),
        "source": claim.source,
        "notes": claim.notes,
        "verify": claim.verify,
        "verify_text": kb.guidance(claim.verify, "en"),
        "evidence": None if claim.evidence is None else claim.evidence.as_dict(),
        "recorded": True,
    }


def _vehicle_dict(vehicle: VehicleProfile, kb: VehicleKnowledgeBase) -> dict:
    return {
        "id": vehicle.id,
        "manufacturer_id": vehicle.manufacturer_id,
        "names": {"en": vehicle.names.en, "fa": vehicle.names.fa},
        "pack": vehicle.pack,
        "notes": vehicle.notes,
        "variants": [
            {
                "id": variant.id,
                "names": {"en": variant.names.en, "fa": variant.names.fa},
                "notes": variant.notes,
                "claims": [
                    {"section": row.section, **_claim_dict(row.field, row.claim, kb)}
                    for row in claim_rows(variant)
                ],
            }
            for variant in vehicle.variants
        ],
        "sources": [source.as_dict() for source in vehicle.sources.values()],
    }


def _badge(claim: Claim[object] | None) -> str:
    level = ConfidenceLevel.UNKNOWN if claim is None else claim.confidence
    return f"[{level.value.upper()}]"


def _short(field: str, claim: Claim[object] | None) -> str:
    value = None if claim is None else claim.value
    return f"{format_claim_value(field, value)} {_badge(claim)}"


def _cmd_vehicles(args: argparse.Namespace, settings: AppSettings) -> int:
    kb = JsonVehicleKnowledgeBase(settings.knowledge_dir)
    if args.vehicles_command == "list":
        return _vehicles_list(kb, args.manufacturer, as_json=args.json)
    if args.vehicles_command == "show":
        return _vehicles_show(kb, args.vehicle_id, as_json=args.json)
    return _vehicles_match(kb, args)


def _vehicles_list(kb: VehicleKnowledgeBase, manufacturer: str | None, *, as_json: bool) -> int:
    if manufacturer is not None and kb.manufacturer(manufacturer) is None:
        known = ", ".join(m.id for m in kb.manufacturers())
        print(f"error: unknown manufacturer {manufacturer!r} (known: {known})", file=sys.stderr)
        return EXIT_USAGE
    vehicles = kb.vehicles(manufacturer)
    counts = dict.fromkeys(ConfidenceLevel, 0)
    for vehicle in vehicles:
        for variant in vehicle.variants:
            for row in claim_rows(variant):
                counts[row.confidence] += 1
    if as_json:
        payload = {
            "kind": "vehicle_knowledge_list",
            "schema_version": VEHICLE_LIST_SCHEMA_VERSION,
            "knowledge_source": kb.source,
            "read_only": True,
            "manufacturers": [
                {"id": m.id, "names": {"en": m.names.en, "fa": m.names.fa}}
                for m in kb.manufacturers()
                if manufacturer is None or m.id == manufacturer
            ],
            "vehicles": [_vehicle_dict(v, kb) for v in vehicles],
            "claims_by_confidence": {str(level): n for level, n in counts.items()},
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"vehicle knowledge base (read-only): {kb.source}")
    for m in kb.manufacturers():
        if manufacturer is not None and m.id != manufacturer:
            continue
        print(f"{m.id}  {m.names.en}")
        for vehicle in kb.vehicles(m.id):
            for variant in vehicle.variants:
                ecu = variant.ecus[0] if variant.ecus else None
                family = ecu.family_candidates[0] if ecu and ecu.family_candidates else None
                key = f"{vehicle.id}/{variant.id}"
                print(f"  {key:<28} {vehicle.names.en} - {variant.names.en}")
                print(
                    f"      engine {_short('engine', variant.engine)}  "
                    f"OBD port {_short('obd_physical_layer', variant.obd_physical_layer)}  "
                    f"ECU {_short('family_candidates', family)}"
                )
    summary = ", ".join(f"{level.value} {n}" for level, n in counts.items())
    print(f"claims by confidence: {summary}")
    print("unknown = not established by a citable source; see 'vehicles show <id>'.")
    return EXIT_OK


def _vehicles_show(kb: VehicleKnowledgeBase, vehicle_id: str, *, as_json: bool) -> int:
    vehicle = kb.vehicle(vehicle_id)
    if vehicle is None:
        known = ", ".join(v.id for v in kb.vehicles())
        print(f"error: unknown vehicle {vehicle_id!r} (known: {known})", file=sys.stderr)
        return EXIT_USAGE
    if as_json:
        payload = {"kind": "vehicle_profile", "schema_version": VEHICLE_LIST_SCHEMA_VERSION}
        payload.update(_vehicle_dict(vehicle, kb))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    manufacturer = kb.manufacturer(vehicle.manufacturer_id)
    maker = manufacturer.names.en if manufacturer else vehicle.manufacturer_id
    print(f"{vehicle.id}  {vehicle.names.en}  ({maker}; pack {vehicle.pack})")
    if vehicle.notes:
        print(f"  note: {vehicle.notes}")
    for variant in vehicle.variants:
        print(f"variant {variant.id}: {variant.names.en}")
        section = None
        for row in claim_rows(variant):
            if row.section != section:
                section = row.section
                print(f"  [{section}]")
            claim = row.claim
            value = format_claim_value(row.field, None if claim is None else claim.value)
            source = "-" if claim is None or claim.source is None else claim.source
            print(f"    {row.field:<26} {value:<34} {_badge(claim):<13} {source}")
            if claim is None:
                print("      (nothing recorded)")
                continue
            if claim.notes:
                print(f"      note: {claim.notes}")
            if claim.verify:
                print(f"      verify: {kb.guidance(claim.verify, 'en') or claim.verify}")
            if claim.evidence:
                print(f"      evidence: {claim.evidence.bundle} ({claim.evidence.sha256[:12]})")
    print("sources:")
    if not vehicle.sources:
        print("  none")
    for source in vehicle.sources.values():
        where = f" {source.url} (accessed {source.accessed})" if source.url else ""
        publisher = f" - {source.publisher}" if source.publisher else ""
        print(f"  {source.id}  {source.kind}  {source.title}{publisher}{where}")
    return EXIT_OK


def _read_document(path: Path) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigurationError(f"{path} does not contain a JSON object")
    return document


def _render_match(report: MatchReport) -> tuple[str, ...]:
    facts = report.facts
    origin = "SIMULATED observations" if facts.simulated else "observations"
    observed = ", ".join(
        f"{key} {','.join(value) if isinstance(value, list) else value}"
        for key, value in facts.as_dict().items()
        if value not in (None, [], False) and key not in ("simulated", "active_protocol")
    )
    lines = [f"vehicle match ({origin}) against {report.knowledge_source}"]
    lines.append(f"observed: {observed or 'nothing'}")
    lines.append(f"candidates ({len(report.candidates)}):")
    for index, candidate in enumerate(report.candidates, start=1):
        lines.append(
            f"  {index}. {candidate.key}  {candidate.vehicle_names.en} - "
            f"{candidate.variant_names.en}  {candidate.tier.value.upper()} "
            f"score {candidate.score:.3f}"
        )
        for s in candidate.signals:
            if s.outcome.value in ("match", "conflict"):
                level = "-" if s.confidence is None else s.confidence.value.upper()
                lines.append(
                    f"       {s.signal.value:<16} {s.outcome.value:<8} observed {s.observed}"
                    f"  claimed {s.claimed} [{level} {s.source}]"
                )
    if report.excluded:
        lines.append(f"excluded ({len(report.excluded)}):")
        for candidate in report.excluded:
            lines.append(f"  {candidate.key}: {candidate.excluded_reason}")
    lines.append(f"no comparable evidence: {report.unmatched_count} variant(s)")
    lines.extend(f"note: {note}" for note in report.notes)
    return tuple(lines)


def _vehicles_match(kb: VehicleKnowledgeBase, args: argparse.Namespace) -> int:
    if args.report is None and args.uds is None:
        print("error: give --report and/or --uds", file=sys.stderr)
        return EXIT_USAGE
    report_doc = None if args.report is None else _read_document(args.report)
    uds_doc = None if args.uds is None else _read_document(args.uds)
    try:
        facts = ObservedFacts.from_documents(report_doc, uds_doc)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    report = rank_candidates(kb.vehicles(), facts, kb.manufacturers(), knowledge_source=kb.source)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        for line in _render_match(report):
            print(line)
    return EXIT_OK


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
    if args.port:
        settings = replace(settings, serial_port=args.port)

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
        if args.command == "uds-ident":
            return _cmd_uds_ident(args, settings)
        if args.command == "vehicles":
            return _cmd_vehicles(args, settings)
    except (ConfigurationError, AdapterNotFoundError, VehicleKnowledgeError) as exc:
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
