"""Open one read-only UDS link: CAN interface -> ISO-TP channel -> :class:`UdsClient`.

The interface is always named explicitly:

* ``virtual-sim`` - python-can's in-process ``virtual`` bus **plus** a
  :class:`~iran_obd_tuner.hardware.uds_sim.VirtualBusEcuServer` answering on it. Everything
  read over it is simulated and the link says so (:attr:`UdsLink.simulated`).
* ``slcan``, ``pcan``, ``kvaser`` - a real CAN interface on the channel the user gave, at
  250 or 500 kbit/s only. There is no automatic channel probing and a failed real interface
  never falls back to the simulator.

Before the first transmission on a real interface, :meth:`UdsLink.open` runs a listen-only
probe (:meth:`~iran_obd_tuner.hardware.can_bus.PythonCanBus.probe`): an error frame or a
bad controller status aborts before anything is sent. The probe result is kept
(:attr:`UdsLink.probe`) and written to the traffic log; a silent bus does not confirm the
bitrate, which the UDS client covers by aborting when its request goes unanswered on a bus
that never delivered a frame.

The extended session (``0x10 0x03``) is a code-level opt-in passed by the caller
(``allow_extended_session``); it is not a configuration key.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from iran_obd_tuner.core.exceptions import ConfigurationError
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.can_bus import (
    LISTEN_PROBE_S,
    SUPPORTED_INTERFACES,
    BusProbe,
    PythonCanBus,
    check_bitrate,
)
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig
from iran_obd_tuner.hardware.uds import (
    FUNCTIONAL_REQUEST_ID,
    OBD_RESPONSE_IDS,
    UdsClient,
    UdsTiming,
    discover_ecus,
)
from iran_obd_tuner.hardware.uds_sim import VirtualBusEcuServer

__all__ = ["LINK_INTERFACES", "SIMULATED_INTERFACE", "UdsLink", "UdsLinkConfig"]

#: Interface name of the simulated ECU on the virtual bus.
SIMULATED_INTERFACE = "virtual-sim"
#: Interfaces a user may choose ("virtual" alone would have nobody answering).
LINK_INTERFACES: tuple[str, ...] = (
    SIMULATED_INTERFACE,
    *(name for name in SUPPORTED_INTERFACES if name != "virtual"),
)
_MAX_STANDARD_ID = 0x7FF


@dataclass(frozen=True, slots=True)
class UdsLinkConfig:
    """Where and how to talk UDS.

    Attributes:
        interface: One of :data:`LINK_INTERFACES`.
        channel: Interface channel (``COM5``, ``PCAN_USBBUS1``, ``can0``, ...); required for
            real interfaces, generated for ``virtual-sim``.
        bitrate: CAN bitrate (OBD-II on CAN: 500 kbit/s or 250 kbit/s).
        request_id / response_id: 11-bit physical identifiers (default 0x7E0 / 0x7E8, the
            ISO 15765-4 engine ECU pair; not verified for any specific vehicle).
        padding: ISO-TP padding byte, ``None`` for no padding.
    """

    interface: str = SIMULATED_INTERFACE
    channel: str | None = None
    bitrate: int = 500_000
    request_id: int = 0x7E0
    response_id: int = 0x7E8
    padding: int | None = 0xCC

    def __post_init__(self) -> None:
        if self.interface not in LINK_INTERFACES:
            raise ConfigurationError(
                f"unsupported CAN interface {self.interface!r}; choose one of: "
                + ", ".join(LINK_INTERFACES)
            )
        check_bitrate("virtual" if self.simulated else self.interface, self.bitrate)
        for name in ("request_id", "response_id"):
            value = getattr(self, name)
            if not 0 <= value <= _MAX_STANDARD_ID:
                raise ConfigurationError(f"{name} must be an 11-bit identifier, got {value:#x}")
        if self.request_id == self.response_id:
            raise ConfigurationError("request_id and response_id must differ")
        if self.padding is not None and not 0 <= self.padding <= 0xFF:
            raise ConfigurationError("the ISO-TP padding must be a byte or None")

    @property
    def simulated(self) -> bool:
        return self.interface == SIMULATED_INTERFACE


class UdsLink:
    """A UDS client over a freshly opened CAN interface (use as a context manager).

    Args:
        config: See :class:`UdsLinkConfig`.
        allow_extended_session: Code-level opt-in for ``0x10 0x03`` (default off).
        traffic_log: Records every CAN frame (TX/RX) for the traffic panel.
        timing: UDS client timing.
        probe_s: Seconds of the listen-only probe on a real interface (must be positive;
            the simulated ECU is not probed).
    """

    def __init__(
        self,
        config: UdsLinkConfig,
        *,
        allow_extended_session: bool = False,
        traffic_log: TrafficLog | None = None,
        timing: UdsTiming | None = None,
        probe_s: float = LISTEN_PROBE_S,
    ) -> None:
        if probe_s <= 0:
            raise ConfigurationError("the listen-only probe cannot be skipped (probe_s > 0)")
        self._config = config
        self._allow_extended = allow_extended_session
        self._traffic = traffic_log
        self._timing = timing or UdsTiming()
        self._probe_s = probe_s
        self._probe: BusProbe | None = None
        self._bus: PythonCanBus | None = None
        self._server: VirtualBusEcuServer | None = None
        self._client: UdsClient | None = None
        self._channel = config.channel

    @property
    def config(self) -> UdsLinkConfig:
        return self._config

    @property
    def simulated(self) -> bool:
        """``True`` when the simulated ECU answers (never inferred from the data)."""
        return self._config.simulated

    @property
    def channel(self) -> str | None:
        """Channel in use (generated for ``virtual-sim``)."""
        return self._channel

    @property
    def probe(self) -> BusProbe | None:
        """Listen-only probe of the last :meth:`open` (``None`` for the simulated ECU)."""
        return self._probe

    @property
    def adapter_name(self) -> str:
        return f"can:{self._config.interface}"

    @property
    def client(self) -> UdsClient:
        if self._client is None:
            raise RuntimeError("the UDS link is not open")
        return self._client

    def open(self) -> None:
        """Open the interface (and start the simulated ECU for ``virtual-sim``).

        Raises:
            ConfigurationError: A real interface without a channel.
            AdapterConnectionError: The listen-only probe saw an error frame or a bad
                controller status (nothing was transmitted).
            AdapterConnectionError: python-can missing or the interface cannot be opened.
        """
        if self._bus is not None:
            return
        cfg = self._config
        interface = cfg.interface
        if cfg.simulated:
            interface = "virtual"
            self._channel = cfg.channel or f"iran-obd-tuner-sim-{uuid.uuid4().hex[:8]}"
            self._server = VirtualBusEcuServer(
                self._channel,
                request_id=cfg.request_id,
                response_id=cfg.response_id,
                bitrate=cfg.bitrate,
            )
            self._server.start()
        elif not cfg.channel:
            raise ConfigurationError(
                f"a CAN channel is required for interface {cfg.interface!r} "
                "(e.g. COM5 for slcan, PCAN_USBBUS1 for pcan, 0 for kvaser)"
            )
        else:
            # Listen first: a wrong bitrate is detected before anything is transmitted.
            self._probe = PythonCanBus.probe(
                interface, cfg.channel, cfg.bitrate, duration_s=self._probe_s
            )
            if self._traffic is not None:
                self._traffic.append(
                    Direction.INFO,
                    self._probe.describe(),
                    adapter=self.adapter_name,
                    simulated=False,
                )
        receive_ids = tuple(sorted({cfg.response_id, *OBD_RESPONSE_IDS}))
        try:
            assert self._channel is not None
            self._bus = PythonCanBus.open(
                interface, self._channel, cfg.bitrate, receive_ids=receive_ids
            )
        except BaseException:
            self._stop_server()
            raise
        iso = IsoTpChannel(
            self._bus,
            IsoTpConfig(cfg.request_id, cfg.response_id, padding=cfg.padding),
            traffic_log=self._traffic,
            adapter_name=self.adapter_name,
        )
        self._client = UdsClient(
            iso, timing=self._timing, allow_extended_session=self._allow_extended
        )

    def discover(self, window_s: float = 0.3) -> tuple[int, ...]:
        """Response identifiers answering a functional TesterPresent on 0x7DF."""
        if self._bus is None:
            raise RuntimeError("the UDS link is not open")
        return discover_ecus(
            self._bus,
            functional_id=FUNCTIONAL_REQUEST_ID,
            window_s=window_s,
            padding=self._config.padding,
            traffic_log=self._traffic,
            adapter_name=self.adapter_name,
        )

    def close(self) -> None:
        bus, self._bus, self._client = self._bus, None, None
        if bus is not None:
            bus.close()
        self._stop_server()

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.stop()

    def __enter__(self) -> UdsLink:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
