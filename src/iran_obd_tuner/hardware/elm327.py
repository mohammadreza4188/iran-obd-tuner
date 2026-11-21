"""Read-only ELM327 adapter (serial/USB), implemented directly on a byte transport.

Status: implemented and tested against scripted replies taken from the ELM327 data sheet;
**not yet validated against a physical adapter or vehicle**. Behaviour of cheap clones may
differ from the data sheet.

Session set-up (``connect``):

1. open the serial port (:class:`~iran_obd_tuner.hardware.transport.PySerialTransport`);
2. ``AT Z`` - reset; the reply must identify an ELM327, otherwise the port is closed again
   (protects other devices that might sit on the selected port);
3. ``AT E0``, ``AT L0``, ``AT H0`` - echo, linefeeds and headers off;
4. ``AT TP 0`` - automatic protocol search *without* writing the adapter's EEPROM (``AT SP``
   would store the protocol permanently);
5. ``01 00`` (and ``01 20``, ``01 40`` ... while announced) - triggers the search and reads
   which service 01 parameters the vehicle supports;
6. ``AT DPN`` - which protocol was found (reported as :attr:`active_protocol`).

Session end (``disconnect``): ``AT PC`` (Protocol Close) is sent first, so the adapter stops
the periodic ISO 9141/14230 keep-alive messages it would otherwise keep sending to the ECU
while it stays powered from the OBD socket; then the port is closed. ``AT PC`` is sent only
to a device that identified as an ELM327, is bounded by :data:`CLOSE_TIMEOUT_S`, and a
failure (e.g. the adapter was unplugged) never prevents the port from being closed.

Safety: every command passes :func:`~iran_obd_tuner.hardware.elm327_protocol.
check_command_allowed` before it is written, so only AT set-up commands and the read-only OBD
services 01, 02 (frame 00), 03, 07, 0A and 09 can ever be sent. Clearing trouble codes
(service 04) is refused by the base class safety gate and, independently, could not pass
the whitelist.

Not implemented: automatic port probing (the user must choose the port), header parsing
to tell several ECUs apart, freeze frames other than frame 00.

Reads after set-up: 01 (current data, incl. PID 0101 monitor status), 02 (freeze frame 00:
supported PIDs, trigger DTC, values), 03/07/0A (stored/pending/permanent trouble codes) and
09 (VIN).
"""

from __future__ import annotations

from collections.abc import Callable

from iran_obd_tuner.core.dtc import DtcStatus
from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    AdapterError,
    ProtocolError,
    SafetyGuardError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import Measurement
from iran_obd_tuner.core.monitors import MonitorStatus
from iran_obd_tuner.core.pids import describe_pid, normalize_pid
from iran_obd_tuner.core.safety import DEFAULT_POLICY, SafetyPolicy
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware import elm327_protocol as proto
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter
from iran_obd_tuner.hardware.transport import PySerialTransport, Transport

__all__ = ["ELM327_INFO", "ELM327_NAME", "Elm327Adapter"]

ELM327_NAME = "elm327"

ELM327_INFO = AdapterInfo(
    name=ELM327_NAME,
    kind=AdapterKind.SERIAL,
    description=(
        "ELM327 compatible serial/USB adapter (read-only: services 01, 02, 03, 07, 0A, 09)."
    ),
    implemented=True,
    protocols=tuple(proto.PROTOCOL_NAMES[n] for n in "123456789"),
    hardware_required=True,
    notes=(
        "Implemented against the ELM327 data sheet and tested with scripted replies; NOT yet "
        "validated on a physical adapter or vehicle. Needs pyserial ([obd] extra) and an "
        "explicitly selected serial port. Clone protocol coverage varies."
    ),
    simulated=False,
)

#: Seconds an ``AT Z`` reset may take (the data sheet gives no figure; generous estimate).
RESET_TIMEOUT_S = 5.0
#: Seconds the first request may take while the adapter searches all protocols. Slow ISO
#: 9141/14230 initiations take 2-3 s each (data sheet); this is a generous, unverified bound.
SEARCH_TIMEOUT_S = 30.0
#: Seconds the ``AT PC`` on disconnect may take; the port is closed afterwards regardless.
CLOSE_TIMEOUT_S = 1.0

TransportFactory = Callable[[str, int], Transport]


def _visible(raw: bytes) -> str:
    """Raw reply as one readable line: CR shown as ``⏎``, undecodable bytes as ``\\xNN``."""
    text = raw.decode("ascii", errors="backslashreplace")
    return text.replace("\r", "⏎").replace("\n", "")


class Elm327Adapter(ObdAdapter):
    """Read-only ELM327 adapter.

    Args:
        port: Serial port name, e.g. ``"COM3"`` or ``"/dev/ttyUSB0"``.
        baudrate: Serial baud rate (ELM327 parts use 9600 or 38400 by default).
        command_timeout_s: Seconds to wait for the prompt after a normal request.
        policy: Safety policy for state-changing operations (none is implemented).
        transport_factory: Opens the byte transport; replaced by a scripted fake in tests.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 38400,
        command_timeout_s: float = 2.0,
        policy: SafetyPolicy = DEFAULT_POLICY,
        transport_factory: TransportFactory = PySerialTransport.open,
        traffic_log: TrafficLog | None = None,
    ) -> None:
        super().__init__(policy=policy, traffic_log=traffic_log)
        if not port.strip():
            raise ValueError("an explicit serial port is required")
        self._port = port.strip()
        self._baudrate = baudrate
        self._command_timeout_s = command_timeout_s
        self._transport_factory = transport_factory
        self._transport: Transport | None = None
        self._protocol: proto.ProtocolInfo | None = None
        self._supported: frozenset[str] = frozenset()
        self._freeze_frame_supported: frozenset[str] | None = None
        self._version: str | None = None
        self._identified = False  # the device on the port answered AT Z as an ELM327
        self._log = get_logger("hardware.elm327")

    # ------------------------------------------------------------------ identity
    @property
    def info(self) -> AdapterInfo:
        return ELM327_INFO

    @property
    def port(self) -> str:
        """Serial port this adapter uses."""
        return self._port

    @property
    def version(self) -> str | None:
        """Identification line printed by ``AT Z`` (e.g. ``"ELM327 v1.5"``), once known."""
        return self._version

    @property
    def active_protocol(self) -> str | None:
        if self._protocol is None:
            return None
        return self._protocol.name

    # ------------------------------------------------------------------ lifecycle
    def _open(self, *, timeout_s: float | None) -> None:
        self._transport = self._transport_factory(self._port, self._baudrate)
        try:
            self._initialise(timeout_s)
        except BaseException:
            self._close()
            raise

    def _initialise(self, timeout_s: float | None) -> None:
        reset_timeout = max(RESET_TIMEOUT_S, timeout_s or 0.0)
        reply = self._command("ATZ", timeout_s=reset_timeout, check_status=False)
        identity = next((line for line in reply if "ELM327" in line.upper()), None)
        if identity is None:
            raise AdapterConnectionError(
                f"the device on {self._port} did not identify as an ELM327 "
                f"(reply to AT Z: {reply!r}); no further commands were sent"
            )
        self._version = identity
        self._identified = True
        self._log.info("adapter on %s: %s", self._port, identity)

        for command in ("ATE0", "ATL0", "ATH0", "ATTP0"):
            lines = self._command(command)
            if not any(line.upper() == "OK" for line in lines):
                raise ProtocolError(f"{command} was not acknowledged: {lines!r}")

        search_timeout = max(SEARCH_TIMEOUT_S, timeout_s or 0.0)
        self._supported = self._read_supported_bitmasks(search_timeout)
        self._protocol = proto.parse_dpn(self._command("ATDPN"))
        self._log.info(
            "vehicle protocol: %s (%s); %d service 01 parameter(s) supported",
            self._protocol.name,
            "automatic" if self._protocol.automatic else "fixed",
            len(self._supported),
        )

    def _read_supported_bitmasks(self, search_timeout_s: float) -> frozenset[str]:
        supported: set[int] = set()
        base = 0x00
        while True:
            timeout = search_timeout_s if base == 0x00 else self._command_timeout_s
            try:
                lines = self._command(f"01{base:02X}", timeout_s=timeout)
            except proto.NoDataError:
                if base == 0x00:
                    raise AdapterConnectionError(
                        "the vehicle did not answer 01 00 (supported PIDs); is the ignition on?"
                    ) from None
                break  # announced but not answered: treat the rest as unsupported
            supported |= proto.parse_supported_pids(proto.parse_messages(lines), base)
            next_base = base + 0x20
            if next_base not in supported or next_base > 0xE0:
                break
            base = next_base
        return frozenset(f"01{pid:02X}" for pid in supported)

    def _close(self) -> None:
        if self._transport is not None and self._identified:
            try:
                self._command("ATPC", timeout_s=CLOSE_TIMEOUT_S, check_status=False)
            except (AdapterError, SafetyGuardError) as exc:  # unplugged, hung: just close
                self._log.debug("AT PC on disconnect failed: %s", exc)
        self._identified = False
        transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()

    # ------------------------------------------------------------------ transport
    def _command(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        check_status: bool = True,
    ) -> list[str]:
        """Send one whitelisted command and return the cleaned reply lines."""
        normalized = proto.check_command_allowed(command)
        if self._transport is None:
            raise AdapterConnectionError(f"{ELM327_NAME}: adapter is not connected")
        self._transport.discard_input()
        self._record(Direction.TX, normalized)  # only allowlisted commands get this far
        self._transport.write(normalized.encode("ascii") + b"\r")
        wait_s = self._command_timeout_s if timeout_s is None else timeout_s
        try:
            raw = self._transport.read_until(proto.PROMPT, wait_s)
        except AdapterError as exc:
            self._record(Direction.INFO, f"{type(exc).__name__}: {exc}")
            raise
        self._record(Direction.RX, _visible(raw))
        lines = proto.clean_reply(raw, normalized)
        self._log.debug("%s -> %r", normalized, lines)
        if check_status:
            proto.raise_for_status(lines, normalized)
        return lines

    # ------------------------------------------------------------------ reads
    def read_vin(self) -> str | None:
        self._require_connected()
        try:
            lines = self._command("0902")
        except proto.NoDataError:
            return None  # service 09 PID 02 is optional; "not available" is a valid answer
        return proto.parse_vin(proto.parse_messages(lines))

    def read_supported_pids(self) -> tuple[str, ...]:
        self._require_connected()
        return tuple(sorted(self._supported))

    def read_measurement(self, pid: str) -> Measurement:
        self._require_connected()
        normalized = normalize_pid(pid)
        spec = describe_pid(normalized)
        if spec is None or spec.formula is None:
            raise UnsupportedPidError(f"PID {normalized} has no decoding definition")
        if normalized not in self._supported:
            raise UnsupportedPidError(f"PID {normalized} is not supported by the vehicle")
        messages = proto.parse_messages(self._command(normalized))
        proto.check_negative_response(messages, 0x01)
        data = proto.parse_mode01_data(messages, int(normalized[2:], 16), spec.data_bytes)
        return Measurement(pid=spec.pid, name=spec.name, value=spec.decode(data), unit=spec.unit)

    def read_diagnostic_trouble_codes(self) -> tuple[str, ...]:
        return self.read_trouble_codes(DtcStatus.STORED)

    def read_trouble_codes(self, status: DtcStatus) -> tuple[str, ...]:
        self._require_connected()
        messages = proto.parse_messages(self._command(status.service))
        return proto.parse_dtc_reply(messages, int(status.service, 16), can=self._is_can)

    def read_monitor_status(self) -> MonitorStatus:
        self._require_connected()
        if "0101" not in self._supported:
            raise UnsupportedPidError("PID 0101 (monitor status) is not supported by the vehicle")
        messages = proto.parse_messages(self._command("0101"))
        proto.check_negative_response(messages, 0x01)
        return proto.parse_monitor_status(messages)

    # ------------------------------------------------------------------ freeze frame 00
    def read_freeze_frame_supported_pids(self) -> tuple[str, ...]:
        self._require_connected()
        if self._freeze_frame_supported is None:
            supported: set[int] = set()
            base = 0x00
            while True:
                try:
                    lines = self._command(f"02{base:02X}00")
                except proto.NoDataError:
                    if base == 0x00:
                        raise
                    break
                supported |= proto.parse_freeze_frame_supported(proto.parse_messages(lines), base)
                next_base = base + 0x20
                if next_base not in supported or next_base > 0xE0:
                    break
                base = next_base
            self._freeze_frame_supported = frozenset(f"01{pid:02X}" for pid in supported)
        return tuple(sorted(self._freeze_frame_supported))

    def read_freeze_frame_trigger(self) -> str | None:
        self._require_connected()
        return proto.parse_freeze_frame_trigger(proto.parse_messages(self._command("020200")))

    def read_freeze_frame_value(self, pid: str) -> Measurement:
        self._require_connected()
        normalized = normalize_pid(pid)
        spec = describe_pid(normalized)
        if spec is None or spec.formula is None:
            raise UnsupportedPidError(f"PID {normalized} has no decoding definition")
        if normalized not in self.read_freeze_frame_supported_pids():
            raise UnsupportedPidError(f"PID {normalized} is not stored in the freeze frame")
        number = int(normalized[2:], 16)
        messages = proto.parse_messages(self._command(f"02{number:02X}00"))
        data = proto.parse_freeze_frame_data(messages, number, spec.data_bytes)
        return Measurement(pid=spec.pid, name=spec.name, value=spec.decode(data), unit=spec.unit)

    @property
    def _is_can(self) -> bool:
        return self._protocol.is_can if self._protocol is not None else False
