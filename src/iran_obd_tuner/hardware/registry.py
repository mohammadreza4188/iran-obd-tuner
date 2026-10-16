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

from iran_obd_tuner.core.exceptions import AdapterNotFoundError, AdapterNotImplementedError
from iran_obd_tuner.core.safety import DEFAULT_POLICY, SafetyPolicy
from iran_obd_tuner.hardware.base import AdapterInfo, AdapterKind, ObdAdapter
from iran_obd_tuner.hardware.mock import MockObdAdapter, MockScenario

__all__ = ["MOCK_ADAPTER_NAME", "create_adapter", "declared_adapters", "implemented_adapters"]

MOCK_ADAPTER_NAME = "mock"

_DECLARED: tuple[AdapterInfo, ...] = (
    AdapterInfo(
        name=MOCK_ADAPTER_NAME,
        kind=AdapterKind.MOCK,
        description="In-memory simulated vehicle; needs no hardware.",
        implemented=True,
        protocols=("ISO 15765-4 CAN (11-bit ID, 500 kbit/s) [simulated]",),
        hardware_required=False,
        notes="Implemented. Values are simulated and must never be presented as vehicle data.",
    ),
    AdapterInfo(
        name="elm327",
        kind=AdapterKind.SERIAL,
        description="ELM327 compatible serial/USB adapter over pyserial (planned, phase 1).",
        implemented=False,
        protocols=(
            "ISO 15765-4 CAN",
            "ISO 9141-2",
            "ISO 14230-4 (KWP2000)",
            "SAE J1850 PWM",
            "SAE J1850 VPW",
        ),
        hardware_required=True,
        notes=(
            "Not implemented. Requires the [obd] extra (python-obd, pyserial) and a real "
            "adapter. Protocol coverage depends on the individual clone."
        ),
    ),
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
) -> ObdAdapter:
    """Instantiate the adapter called ``name``.

    Args:
        name: Registry name, e.g. ``"mock"`` (case insensitive).
        mock_scenario: Scenario for the mock adapter; ignored by other adapters.
        policy: Safety policy handed to the adapter.

    Returns:
        A ready-to-connect adapter (not yet connected).

    Raises:
        AdapterNotFoundError: If the name is not declared at all.
        AdapterNotImplementedError: If the adapter is declared but not implemented.
    """
    normalized = name.strip().lower()
    declared = {info.name: info for info in _DECLARED}

    info = declared.get(normalized)
    if info is None:
        known = ", ".join(sorted(declared))
        raise AdapterNotFoundError(f"unknown adapter {name!r}; known adapters are: {known}")

    if normalized == MOCK_ADAPTER_NAME:
        return MockObdAdapter(mock_scenario, policy=policy)

    raise AdapterNotImplementedError(
        f"adapter {normalized!r} is declared but not implemented in this build: {info.notes}"
    )
