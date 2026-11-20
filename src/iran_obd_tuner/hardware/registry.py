"""Adapter registry: which adapters exist, and how honest the tool is about them.

Every adapter this project knows about is declared here with an explicit implementation
status. Requesting a declared-but-unimplemented adapter raises
:class:`~iran_obd_tuner.core.exceptions.AdapterNotImplementedError` with the missing
dependency and the planned phase, instead of pretending to connect to hardware.

Adding a real adapter means: implement :class:`~iran_obd_tuner.hardware.base.ObdAdapter` in
``hardware/<name>.py``, import the third-party driver lazily inside that module, flip
``implemented`` to ``True`` here and update the capability tables in README.md and
docs/architecture.md.
"""

from __future__ import annotations

from iran_obd_tuner.core.exceptions import (
    AdapterNotFoundError,
    AdapterNotImplementedError,
    ConfigurationError,
)
from iran_obd_tuner.core.safety import DEFAULT_POLICY, SafetyPolicy
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter
from iran_obd_tuner.hardware.elm327 import ELM327_INFO, ELM327_NAME, Elm327Adapter
from iran_obd_tuner.hardware.mock import MockObdAdapter, MockScenario

__all__ = [
    "ELM327_ADAPTER_NAME",
    "MOCK_ADAPTER_NAME",
    "create_adapter",
    "declared_adapters",
    "implemented_adapters",
]

MOCK_ADAPTER_NAME = "mock"

#: Registry name of the ELM327 serial/USB adapter.
ELM327_ADAPTER_NAME = ELM327_NAME

_DECLARED: tuple[AdapterInfo, ...] = (
    AdapterInfo(
        name=MOCK_ADAPTER_NAME,
        kind=AdapterKind.MOCK,
        description="In-memory simulated vehicle; needs no hardware.",
        implemented=True,
        protocols=("ISO 15765-4 CAN (11-bit ID, 500 kbit/s) [simulated]",),
        hardware_required=False,
        notes="Implemented. Values are simulated and must never be presented as vehicle data.",
        simulated=True,
    ),
    ELM327_INFO,
    AdapterInfo(
        name="can-bench",
        kind=AdapterKind.CAN,
        description="Direct CAN interface for off-vehicle (bench) ECU work (planned, phase 4+).",
        implemented=False,
        protocols=("ISO 15765-2 (ISO-TP)", "ISO 14229 (UDS)"),
        hardware_required=True,
        notes=(
            "Not implemented, and its write services are blocked by the safety policy. "
            "Requires python-can plus a SocketCAN/PCAN/Kvaser interface."
        ),
    ),
)


def declared_adapters() -> tuple[AdapterInfo, ...]:
    """Return every adapter this build knows about, implemented or not."""
    return _DECLARED


def implemented_adapters() -> tuple[AdapterInfo, ...]:
    """Return the adapters that can actually be instantiated today."""
    return tuple(info for info in _DECLARED if info.implemented)


def create_adapter(
    name: str,
    *,
    mock_scenario: MockScenario | None = None,
    policy: SafetyPolicy = DEFAULT_POLICY,
    serial_port: str | None = None,
    serial_baudrate: int = 38400,
    command_timeout_s: float = 2.0,
    traffic_log: TrafficLog | None = None,
) -> ObdAdapter:
    """Instantiate the adapter called ``name``.

    Args:
        name: Registry name, e.g. ``"mock"`` (case insensitive).
        mock_scenario: Scenario for the mock adapter; ignored by other adapters.
        policy: Safety policy handed to the adapter.
        serial_port: Port of a serial adapter. Required for ``elm327``: there is no
            automatic probing, because probing would send commands to whatever device is
            connected to every port.
        serial_baudrate: Baud rate of a serial adapter.
        command_timeout_s: Seconds a serial adapter waits for each reply.
        traffic_log: Where the adapter records its TX/RX lines (optional).

    Returns:
        A ready-to-connect adapter (not yet connected).

    Raises:
        AdapterNotFoundError: If the name is not declared at all.
        AdapterNotImplementedError: If the adapter is declared but not implemented.
        ConfigurationError: If a serial adapter is requested without a port.
    """
    normalized = name.strip().lower()
    declared = {info.name: info for info in _DECLARED}

    info = declared.get(normalized)
    if info is None:
        known = ", ".join(sorted(declared))
        raise AdapterNotFoundError(f"unknown adapter {name!r}; known adapters are: {known}")

    if normalized == MOCK_ADAPTER_NAME:
        return MockObdAdapter(mock_scenario, policy=policy, traffic_log=traffic_log)

    if normalized == ELM327_ADAPTER_NAME:
        if serial_port is None or not serial_port.strip():
            raise ConfigurationError(
                "the elm327 adapter needs an explicit serial port (setting 'serial_port', "
                "CLI '--port' or the dashboard port selector); automatic port probing is "
                "disabled because it would send commands to unknown devices"
            )
        return Elm327Adapter(
            serial_port,
            baudrate=serial_baudrate,
            command_timeout_s=command_timeout_s,
            policy=policy,
            traffic_log=traffic_log,
        )

    raise AdapterNotImplementedError(
        f"adapter {normalized!r} is declared but not implemented in this build: {info.notes}"
    )
