"""Deterministic simulated UDS ECU, and a server that runs it on the virtual CAN bus.

:class:`SimulatedUdsEcu` is a pure function of its configuration and the requests it gets:
``handle(request) -> list[responses]``. It answers only the read-only services the client
uses (0x10 01/03, 0x3E, 0x22, 0x19 01/02); **every other service, including every write,
security or programming service, is answered "serviceNotSupported"** - the simulator has no
write capability either.

Every value is clearly simulated (``SIMULATEDVIN00004``, ``SIM-SW-1.0``, ...). No real ECU
name, part number or Iranian vehicle data is imitated.

:class:`VirtualBusEcuServer` runs the simulator in a background thread on python-can's
in-process ``virtual`` bus. It cannot be pointed at any other interface.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field

from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.hardware.can_bus import PythonCanBus
from iran_obd_tuner.hardware.isotp import IsoTpChannel, IsoTpConfig

__all__ = ["SIMULATED_DIDS", "SimulatedEcuConfig", "SimulatedUdsEcu", "VirtualBusEcuServer"]

_LOG = get_logger("hardware.uds_sim")

#: Identification values of the simulated ECU (ASCII, obviously not real).
SIMULATED_DIDS: Mapping[int, bytes] = {
    0xF190: b"SIMULATEDVIN00004",
    0xF187: b"SIM-PART-0001",
    0xF189: b"SIM-SW-1.0",
    0xF191: b"SIM-HW-1.0",
    0xF197: b"SIMULATED ECU",
    0xF18C: b"SIM-SERIAL-42",
}

_NOT_SUPPORTED, _SUB_NOT_SUPPORTED, _LENGTH = 0x11, 0x12, 0x13
_CONDITIONS, _OUT_OF_RANGE, _SECURITY, _PENDING = 0x22, 0x31, 0x33, 0x78


@dataclass(frozen=True)
class SimulatedEcuConfig:
    """Behaviour of the simulated ECU.

    Attributes:
        dids: DID -> value.
        extended_only_dids: Answered only in the extended session (``0x31`` otherwise).
        protected_dids: Answered ``0x33`` securityAccessDenied (never unlocked).
        conditions_not_correct_dids: Answered ``0x22`` conditionsNotCorrect.
        pending_dids: DID -> number of ``0x78`` responsePending answers before the value.
        dtcs: (3 DTC bytes, status byte) records for 0x19.
        dtc_format: DTCFormatIdentifier reported by 0x19 0x01 (0x00 = SAE J2012-DA).
        availability_mask: Status bits this ECU supports.
    """

    dids: Mapping[int, bytes] = field(default_factory=lambda: dict(SIMULATED_DIDS))
    extended_only_dids: frozenset[int] = frozenset({0xF197})
    protected_dids: frozenset[int] = frozenset({0xF18C})
    conditions_not_correct_dids: frozenset[int] = frozenset()
    pending_dids: Mapping[int, int] = field(default_factory=lambda: {0xF190: 2})
    dtcs: tuple[tuple[bytes, int], ...] = (
        (bytes.fromhex("030100"), 0x2F),  # P0301-00: confirmed, failed, warning bit clear
        (bytes.fromhex("042000"), 0x24),  # P0420-00: pending, failed since clear
    )
    dtc_format: int = 0x00
    availability_mask: int = 0xFF


class SimulatedUdsEcu:
    """Pure request -> responses simulator (no bus, no thread, no time)."""

    def __init__(self, config: SimulatedEcuConfig | None = None) -> None:
        self.config = config or SimulatedEcuConfig()
        self.session = 0x01
        self.requests: list[bytes] = []

    def handle(self, request: bytes) -> list[bytes]:
        self.requests.append(request)
        if not request:
            return []
        sid = request[0]
        if sid == 0x10:
            return self._session_control(request)
        if sid == 0x3E:
            return self._tester_present(request)
        if sid == 0x22:
            return self._read_did(request)
        if sid == 0x19:
            return self._read_dtc(request)
        return [bytes([0x7F, sid, _NOT_SUPPORTED])]  # everything else, writes included

    def _session_control(self, request: bytes) -> list[bytes]:
        if len(request) != 2:
            return [bytes([0x7F, 0x10, _LENGTH])]
        sub, suppress = request[1] & 0x7F, request[1] & 0x80
        if sub not in (0x01, 0x03):
            return [bytes([0x7F, 0x10, _SUB_NOT_SUPPORTED])]
        self.session = sub
        # P2 = 50 ms, P2* = 500 x 10 ms = 5 s
        return [] if suppress else [bytes([0x50, sub, 0x00, 0x32, 0x01, 0xF4])]

    def _tester_present(self, request: bytes) -> list[bytes]:
        if len(request) != 2 or request[1] & 0x7F:
            return [bytes([0x7F, 0x3E, _SUB_NOT_SUPPORTED])]
        return [] if request[1] & 0x80 else [b"\x7e\x00"]

    def _read_did(self, request: bytes) -> list[bytes]:
        if len(request) != 3:
            return [bytes([0x7F, 0x22, _LENGTH])]
        did = int.from_bytes(request[1:3], "big")
        cfg = self.config
        if did in cfg.protected_dids:
            return [bytes([0x7F, 0x22, _SECURITY])]
        if did in cfg.conditions_not_correct_dids:
            return [bytes([0x7F, 0x22, _CONDITIONS])]
        if did not in cfg.dids or (did in cfg.extended_only_dids and self.session != 0x03):
            return [bytes([0x7F, 0x22, _OUT_OF_RANGE])]
        pending = [bytes([0x7F, 0x22, _PENDING])] * cfg.pending_dids.get(did, 0)
        return [*pending, bytes([0x62, did >> 8, did & 0xFF]) + cfg.dids[did]]

    def _read_dtc(self, request: bytes) -> list[bytes]:
        if len(request) != 3:
            return [bytes([0x7F, 0x19, _LENGTH])]
        sub, mask = request[1], request[2]
        matching = [(dtc, status) for dtc, status in self.config.dtcs if status & mask]
        if sub == 0x01:
            count = len(matching).to_bytes(2, "big")
            return [
                bytes([0x59, 0x01, self.config.availability_mask, self.config.dtc_format]) + count
            ]
        if sub == 0x02:
            body = b"".join(dtc + bytes([status]) for dtc, status in matching)
            return [bytes([0x59, 0x02, self.config.availability_mask]) + body]
        return [bytes([0x7F, 0x19, _SUB_NOT_SUPPORTED])]


class VirtualBusEcuServer:
    """Serve a :class:`SimulatedUdsEcu` on python-can's ``virtual`` bus (only).

    Args:
        channel: Name of the virtual bus channel shared with the client.
        ecu: The simulator to answer with.
        request_id / response_id: Physical addressing (default 0x7E0 / 0x7E8).
        functional_id: Functional request identifier (0x7DF); single frames only.
    """

    def __init__(
        self,
        channel: str,
        ecu: SimulatedUdsEcu | None = None,
        *,
        request_id: int = 0x7E0,
        response_id: int = 0x7E8,
        functional_id: int = 0x7DF,
        bitrate: int = 500_000,
        pending_delay_s: float = 0.05,
    ) -> None:
        self.ecu = ecu or SimulatedUdsEcu()
        self._ids = (request_id, response_id, functional_id)
        self._pending_delay_s = pending_delay_s
        self._bus = PythonCanBus.open(
            "virtual", channel, bitrate, receive_ids=(request_id, functional_id)
        )
        self._iso = IsoTpChannel(self._bus, IsoTpConfig(response_id, request_id))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="uds-sim", daemon=True)
        self.errors: list[BaseException] = []

    def __enter__(self) -> VirtualBusEcuServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(5)
        self._bus.close()

    def _serve(self) -> None:
        request_id, _response_id, functional_id = self._ids
        while not self._stop.is_set():
            try:
                frame = self._bus.recv(0.05)
                if frame is None or not frame.data:
                    continue
                kind, length = frame.data[0] >> 4, frame.data[0] & 0x0F
                if kind == 0 and 1 <= length <= len(frame.data) - 1:
                    request = frame.data[1 : 1 + length]
                elif kind == 1 and frame.arbitration_id == request_id:
                    result = self._iso.receive_segmented(frame)
                    if not isinstance(result, bytes):
                        continue
                    request = result
                else:
                    continue
                if frame.arbitration_id == functional_id and request[:1] != b"\x3e":
                    continue  # functionally, the simulator answers TesterPresent only
                for index, response in enumerate(self.ecu.handle(request)):
                    if index:
                        self._stop.wait(self._pending_delay_s)
                    self._iso.send(response)
            except Exception as exc:  # kept for the test that runs the server
                _LOG.warning("simulated ECU error: %s", exc)
                self.errors.append(exc)
