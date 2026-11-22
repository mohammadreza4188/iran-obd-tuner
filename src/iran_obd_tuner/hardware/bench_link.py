"""Open a bench programming link (Milestone 7c) - **simulated only** in this build.

The single interface is :data:`BENCH_SIMULATED_INTERFACE` (``virtual-sim-programmable``):
python-can's in-process ``virtual`` bus plus a
:class:`~iran_obd_tuner.hardware.uds_sim_programmable.VirtualProgrammableEcuServer`
answering on it, with a :class:`~iran_obd_tuner.hardware.bench_programmer.BenchProgrammer`
for the simulated bench target. Any other interface - ``slcan``, ``pcan``, ``kvaser``, the
read-only ``virtual-sim``, a bare ``virtual``, anything - raises
:class:`~iran_obd_tuner.core.exceptions.BenchTargetNotQualifiedError`, and so does a channel
that does not carry the simulator prefix (so a real port name like ``COM5`` or
``PCAN_USBBUS1`` can never be typed in by mistake). There is no fallback of any kind.

This is the only module that may create the programmer and the programmable simulator's
server (architecture test). It is also the only door to the simulator for the ``app`` wiring
(Milestone 7e): :func:`simulated_bench_ecu` builds the simulated ECU (factory fresh or booted
from a saved :class:`SimulatedEcuState`, optionally with a :class:`FaultPlan`), and
:func:`simulated_seed_key` returns the simulated target's published seed-key provider.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET, BenchTargetProfile, SeedKeyProvider
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, ConfigurationError
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.hardware.bench_programmer import BenchProgrammer, BenchTiming
from iran_obd_tuner.hardware.can_bus import PythonCanBus
from iran_obd_tuner.hardware.uds_sim_programmable import (
    FaultPlan,
    ProgrammableEcuConfig,
    SimulatedEcuState,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    VirtualProgrammableEcuServer,
)

__all__ = [
    "BENCH_LINK_INTERFACES",
    "BENCH_SIMULATED_INTERFACE",
    "BENCH_SIM_CHANNEL_PREFIX",
    "BenchLink",
    "BenchLinkConfig",
    "BenchTiming",
    "FaultPlan",
    "SimulatedEcuState",
    "simulated_bench_ecu",
    "simulated_seed_key",
]

#: The only bench interface of this build: virtual bus + virtual programmable ECU.
BENCH_SIMULATED_INTERFACE = "virtual-sim-programmable"
BENCH_LINK_INTERFACES: tuple[str, ...] = (BENCH_SIMULATED_INTERFACE,)
#: Every simulated bench channel starts with this (generated when none is given).
BENCH_SIM_CHANNEL_PREFIX = "bench-sim-"
_MAX_STANDARD_ID = 0x7FF


def simulated_bench_ecu(
    *,
    state: SimulatedEcuState | None = None,
    faults: FaultPlan | None = None,
    max_block_length: int = 258,
) -> VirtualProgrammableEcu:
    """A virtual programmable ECU for :data:`SIMULATED_BENCH_TARGET` (simulated only).

    Args:
        state: Boot from this saved persistent state (a power cycle); factory fresh if ``None``.
        faults: Deterministic fault plan (each fault fires once).
        max_block_length: ``maxNumberOfBlockLength`` the simulator announces.
    """
    config = ProgrammableEcuConfig(target=SIMULATED_BENCH_TARGET, max_block_length=max_block_length)
    return VirtualProgrammableEcu(config, faults, state=state)


def simulated_seed_key() -> SeedKeyProvider:
    """The simulated target's published seed-key (protects nothing; no real algorithm)."""
    return SimulatedXorSeedKey(SIMULATED_BENCH_TARGET.security_level)


@dataclass(frozen=True, slots=True)
class BenchLinkConfig:
    """Where the bench link goes (only the simulator in this build).

    Attributes:
        interface: Must be :data:`BENCH_SIMULATED_INTERFACE`.
        channel: Virtual channel name starting with :data:`BENCH_SIM_CHANNEL_PREFIX`;
            generated when ``None``.
        request_id / response_id: 11-bit physical identifiers.
        padding: ISO-TP padding byte.
    """

    interface: str = BENCH_SIMULATED_INTERFACE
    channel: str | None = None
    request_id: int = 0x7E0
    response_id: int = 0x7E8
    padding: int | None = 0xCC

    def __post_init__(self) -> None:
        if self.interface not in BENCH_LINK_INTERFACES:
            raise BenchTargetNotQualifiedError(
                f"bench interface {self.interface!r} is not available: in this build bench "
                f"programming only exists against the simulator ({BENCH_SIMULATED_INTERFACE})"
            )
        if self.channel is not None and not self.channel.startswith(BENCH_SIM_CHANNEL_PREFIX):
            raise BenchTargetNotQualifiedError(
                f"bench channel {self.channel!r} is not a simulator channel (it must start "
                f"with {BENCH_SIM_CHANNEL_PREFIX!r}); real ports are never used for bench work"
            )
        for name in ("request_id", "response_id"):
            if not 0 <= getattr(self, name) <= _MAX_STANDARD_ID:
                raise ConfigurationError(f"{name} must be an 11-bit identifier")
        if self.request_id == self.response_id:
            raise ConfigurationError("request_id and response_id must differ")

    @property
    def simulated(self) -> bool:
        return self.interface == BENCH_SIMULATED_INTERFACE


class BenchLink:
    """A bench programmer on a freshly started simulator (use as a context manager).

    Args:
        config: See :class:`BenchLinkConfig`.
        ecu: The simulated ECU to serve (tests and the recovery demo pass one with a
            :class:`~iran_obd_tuner.hardware.uds_sim_programmable.FaultPlan`); a factory-
            fresh one when ``None``. Its state outlives the link, so a test can reconnect to
            the same (e.g. half-flashed) ECU.
        timing: Bench request timing.
        traffic_log: Request-level, header-only record of the bench exchange.
    """

    def __init__(
        self,
        config: BenchLinkConfig | None = None,
        *,
        ecu: VirtualProgrammableEcu | None = None,
        timing: BenchTiming | None = None,
        traffic_log: TrafficLog | None = None,
    ) -> None:
        self._config = config or BenchLinkConfig()
        self._ecu = ecu or VirtualProgrammableEcu()
        self._timing = timing
        self._traffic = traffic_log
        self._channel = self._config.channel or f"{BENCH_SIM_CHANNEL_PREFIX}{uuid.uuid4().hex[:8]}"
        self._server: VirtualProgrammableEcuServer | None = None
        self._programmer: BenchProgrammer | None = None

    @property
    def simulated(self) -> bool:
        return self._config.simulated

    @property
    def target(self) -> BenchTargetProfile:
        return SIMULATED_BENCH_TARGET

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def ecu(self) -> VirtualProgrammableEcu:
        """The simulated ECU behind the link (for fault injection and inspection)."""
        return self._ecu

    @property
    def programmer(self) -> BenchProgrammer:
        if self._programmer is None:
            raise RuntimeError("the bench link is not open")
        return self._programmer

    def open(self) -> None:
        """Start the simulator and the programmer on a virtual bus channel."""
        if self._programmer is not None:
            return
        cfg = self._config
        self._server = VirtualProgrammableEcuServer(
            self._channel, self._ecu, request_id=cfg.request_id, response_id=cfg.response_id
        )
        self._server.start()
        try:
            bus = PythonCanBus.open(
                "virtual", self._channel, 500_000, receive_ids=(cfg.response_id,)
            )
            self._programmer = BenchProgrammer(
                bus,
                SIMULATED_BENCH_TARGET,
                request_id=cfg.request_id,
                response_id=cfg.response_id,
                padding=cfg.padding,
                timing=self._timing,
                traffic_log=self._traffic,
                adapter_name=f"bench:{cfg.interface}",
            )
        except BaseException:
            self._stop_server()
            raise

    def close(self) -> None:
        programmer, self._programmer = self._programmer, None
        if programmer is not None:
            programmer.close()
        self._stop_server()

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.stop()

    def __enter__(self) -> BenchLink:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
