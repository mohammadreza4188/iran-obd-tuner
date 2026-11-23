"""What the dashboard needs from the rest of the application.

The UI declares this small contract and the application layer
(:mod:`iran_obd_tuner.app.dashboard`) implements it, so the UI never chooses an adapter,
never imports ``hardware`` and never touches a serial port itself. Tests hand the window a
backend that builds sessions over scripted simulated adapters.

This module imports no Qt, so it is importable (and type-checkable) without the ``[gui]``
extra.

Milestone 7f adds :class:`BenchFlashService`: the simulated bench flash as the Bench tab sees
it. It speaks only :mod:`iran_obd_tuner.core.bench_reports` value objects and the human
:class:`~iran_obd_tuner.core.bench.BenchAttestation`; capabilities, the policy and every
``tuning`` type stay behind it (architecture tests).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from iran_obd_tuner.core.bench import BenchAttestation, ProgressSink
from iran_obd_tuner.core.bench_reports import (
    BenchFlashReport,
    BenchJournalReport,
    BenchPreflightReport,
    SimulatedFault,
)
from iran_obd_tuner.core.calibration import (
    ChecksumResult,
    CompatibilityReport,
    DefinitionsSummary,
    EditOutcome,
    ExportResult,
    MapSummary,
    RomSummary,
    StringScan,
    TableSnapshot,
)
from iran_obd_tuner.core.knowledge import LocalizedText, Manufacturer, VehicleProfile
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.diagnostics.dyno import VehicleParameters
from iran_obd_tuner.diagnostics.ecu_identification import UdsIdentificationSession
from iran_obd_tuner.diagnostics.live_data import LiveDataSession

__all__ = ["BenchFlashService", "BenchWorkspace", "CanDefaults", "DashboardBackend"]


@dataclass(frozen=True, slots=True)
class CanDefaults:
    """What the UDS identification panel starts with (from the settings).

    Attributes:
        interfaces: Selectable interfaces; ``simulated_interface`` is the simulated ECU.
        interface / channel / bitrate: Initial selection.
    """

    interfaces: tuple[str, ...]
    simulated_interface: str
    interface: str
    channel: str | None
    bitrate: int


class BenchWorkspace(Protocol):
    """Offline ROM analysis session used by the Bench tab (implemented in ``app/bench.py``).

    Everything is file based: nothing is read from or written to an ECU. Errors are
    :class:`~iran_obd_tuner.core.exceptions.RomError` subclasses; a refused edit or export
    changes nothing.
    """

    @property
    def loaded(self) -> bool: ...
    @property
    def is_modified(self) -> bool: ...
    @property
    def can_undo(self) -> bool: ...
    @property
    def can_redo(self) -> bool: ...

    def open_rom(self, path: Path | str) -> RomSummary: ...
    def open_demo(self) -> RomSummary: ...
    def load_definitions(self, path: Path | str) -> CompatibilityReport: ...
    def summary(self) -> RomSummary | None: ...
    def strings(self) -> StringScan | None: ...
    def definitions(self) -> DefinitionsSummary | None: ...
    def compatibility(self) -> CompatibilityReport | None: ...
    def maps(self) -> tuple[MapSummary, ...]: ...
    def table(self, map_id: str) -> TableSnapshot: ...
    def checksums(self) -> tuple[ChecksumResult, ...]: ...
    def set_cell(self, map_id: str, row: int, col: int, value: float) -> EditOutcome: ...
    def set_cells(
        self, map_id: str, cells: Sequence[tuple[int, int]], value: float
    ) -> EditOutcome: ...
    def scale_cells(
        self, map_id: str, cells: Sequence[tuple[int, int]], percent: float
    ) -> EditOutcome: ...
    def undo(self) -> str | None: ...
    def redo(self) -> str | None: ...
    def recalculate_checksums(self) -> int: ...
    def default_export_path(self) -> Path: ...
    def export(
        self, path: Path | str, *, acknowledged: bool, overwrite: bool = False
    ) -> ExportResult: ...


class BenchFlashService(Protocol):
    """Simulated bench flashing (implemented by ``app/bench_flash.py``; Milestone 7f).

    Only the virtual programmable ECU exists behind it. Every method is blocking (the Bench
    tab runs them on a background thread); ``progress`` and ``cancel`` are called on that
    thread. Refusals before anything was sent raise
    :class:`~iran_obd_tuner.core.exceptions.IranObdTunerError` subclasses; once the journal
    started, failures are returned as the report's state.
    """

    def assess_journal(self, output_dir: Path | str) -> BenchJournalReport: ...

    def preflight_flash(
        self,
        output_dir: Path | str,
        *,
        rom_path: Path | str | None = None,
        maps_path: Path | str | None = None,
        allow_unverified_checksum_in_simulation: bool = False,
    ) -> BenchPreflightReport: ...

    def preflight_recovery(self, output_dir: Path | str) -> BenchPreflightReport: ...

    def run_simulated_flash(
        self,
        output_dir: Path | str,
        *,
        rom_path: Path | str | None = None,
        maps_path: Path | str | None = None,
        fault: SimulatedFault | None = None,
        allow_unverified_checksum_in_simulation: bool = False,
        attestation: BenchAttestation | None = None,
        progress: ProgressSink | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> BenchFlashReport: ...

    def run_simulated_recovery(
        self,
        output_dir: Path | str,
        *,
        attestation: BenchAttestation | None = None,
        progress: ProgressSink | None = None,
    ) -> BenchFlashReport: ...

    def discard_stale_journal(self, output_dir: Path | str) -> str: ...


class DashboardBackend(Protocol):
    """Services the dashboard window uses."""

    @property
    def bench_flash_service(self) -> BenchFlashService:
        """Simulated bench flashing (virtual programmable ECU only)."""
        ...

    @property
    def bench_flash_dir(self) -> Path:
        """The simulated bench directory (journal, backups, simulated ECU state)."""
        ...

    def create_bench_workspace(self) -> BenchWorkspace:
        """A fresh offline Bench session (no file is opened yet)."""
        ...

    @property
    def dyno_parameters(self) -> VehicleParameters:
        """Default vehicle parameters of the virtual dyno (from the settings)."""
        ...

    @property
    def traffic_log(self) -> TrafficLog:
        """Shared record of adapter TX/RX lines shown in the traffic panel."""
        ...

    @property
    def output_dir(self) -> Path:
        """Default directory for live data recordings."""
        ...

    def list_serial_ports(self) -> tuple[str, ...]:
        """Serial ports the operating system reports (nothing is opened)."""
        ...

    @property
    def can_defaults(self) -> CanDefaults:
        """Initial choices of the UDS identification panel."""
        ...

    @property
    def vehicle_profiles(self) -> tuple[VehicleProfile, ...]:
        """Every vehicle of the (read-only) vehicle knowledge base."""
        ...

    @property
    def vehicle_manufacturers(self) -> tuple[Manufacturer, ...]:
        """Manufacturers of the vehicle knowledge base (with their WMI claims)."""
        ...

    @property
    def vehicle_guidance(self) -> Mapping[str, LocalizedText]:
        """Field-verification instructions (``verify.<name>``) in both languages."""
        ...

    @property
    def vehicle_knowledge_source(self) -> str:
        """Where the vehicle packs were loaded from."""
        ...

    def create_uds_session(
        self,
        *,
        interface: str,
        channel: str | None,
        bitrate: int,
        allow_extended_session: bool,
    ) -> UdsIdentificationSession:
        """Build a one-shot UDS identification session (nothing is opened yet).

        Raises:
            ConfigurationError: Unsupported interface or invalid parameters. A real
                interface never falls back to the simulated ECU.
        """
        ...

    def create_session(self, *, simulation: bool, port: str | None) -> LiveDataSession:
        """Build a not-yet-connected session.

        Args:
            simulation: ``True`` for the simulated vehicle, ``False`` for a real adapter.
            port: Serial port chosen by the user, ``None`` for automatic selection.

        Raises:
            IranObdTunerError: If the requested adapter cannot be created, e.g. because it
                is declared but not implemented in this build. A real-adapter request never
                falls back to the simulator.
        """
        ...
