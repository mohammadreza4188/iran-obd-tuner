"""Application configuration.

Precedence (lowest to highest):

1. built-in defaults (safe: mock adapter, read-only, INFO logging),
2. a TOML configuration file — explicitly via ``--config`` or implicitly from
   ``config/local.toml`` when that file exists,
3. environment variables named ``IRAN_OBD_TUNER_<FIELD>``.

Example configuration: ``config/example.toml``. Unknown keys and malformed values are
rejected with :class:`~iran_obd_tuner.core.exceptions.ConfigurationError` instead of being
silently ignored, so a typo cannot silently change behaviour.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.exceptions import ConfigurationError
from iran_obd_tuner.core.logging import normalize_log_level
from iran_obd_tuner.core.pids import DEFAULT_LIVE_DATA_PIDS, is_valid_pid, normalize_pid

__all__ = [
    "CAN_INTERFACE_CHOICES",
    "DEFAULT_CONFIG_FILE",
    "ENV_PREFIX",
    "AppSettings",
    "config_file_for",
    "load_settings",
]

#: Prefix of every environment variable that overrides a setting.
ENV_PREFIX = "IRAN_OBD_TUNER_"

#: Configuration file loaded automatically when it exists and no explicit file is given.
DEFAULT_CONFIG_FILE = Path("config/local.toml")

#: Field that records where the settings came from; never read from file or environment.
SOURCE_FIELD = "config_file"


def _as_text(raw: Any, field: str) -> str:
    text = str(raw).strip()
    if not text:
        raise ConfigurationError(f"setting {field!r} must not be empty")
    return text


def _as_optional_text(raw: Any, field: str) -> str | None:
    text = str(raw).strip()
    return text or None


def _as_positive_int(raw: Any, field: str) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigurationError(f"setting {field!r} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"setting {field!r} must be positive, got {value}")
    return value


def _as_positive_float(raw: Any, field: str) -> float:
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise ConfigurationError(f"setting {field!r} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"setting {field!r} must be positive, got {value}")
    return value


def _as_path(raw: Any, field: str) -> Path:
    return Path(_as_text(raw, field))


def _as_optional_path(raw: Any, field: str) -> Path | None:
    text = _as_optional_text(raw, field)
    return None if text is None else Path(text)


def _as_non_negative_float(raw: Any, field: str) -> float:
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise ConfigurationError(f"setting {field!r} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ConfigurationError(f"setting {field!r} must not be negative, got {value}")
    return value


def _as_optional_positive_float(raw: Any, field: str) -> float | None:
    text = str(raw).strip()
    return None if not text else _as_positive_float(text, field)


#: Allowed range of ``command_timeout_s`` (seconds). Below the minimum every ELM327 reply
#: would time out and the next request would interrupt the adapter (a burst of requests on
#: the vehicle bus); above the maximum a stalled adapter blocks the session for too long.
COMMAND_TIMEOUT_RANGE_S: tuple[float, float] = (0.5, 10.0)
#: Allowed range of ``connect_timeout_s`` (seconds): reset and protocol search need a few
#: seconds; more than a minute only hides a dead adapter.
CONNECT_TIMEOUT_RANGE_S: tuple[float, float] = (5.0, 60.0)


#: CAN interfaces for UDS identification. Kept in sync with
#: ``iran_obd_tuner.hardware.uds_link.LINK_INTERFACES`` by a test (core imports no hardware).
CAN_INTERFACE_CHOICES: tuple[str, ...] = (
    "virtual-sim",
    "slcan",
    "pcan",
    "kvaser",
)


def _as_can_interface(raw: Any, field: str) -> str:
    text = _as_text(raw, field).lower()
    if text not in CAN_INTERFACE_CHOICES:
        raise ConfigurationError(
            f"setting {field!r} must be one of {', '.join(CAN_INTERFACE_CHOICES)}, got {raw!r}"
        )
    return text


def _as_int_in_range(raw: Any, field: str, maximum: int) -> int:
    """An integer given as a number or as text (``"0x7E0"``, ``"2016"``)."""
    if isinstance(raw, bool):
        raise ConfigurationError(f"setting {field!r} must be an integer, got {raw!r}")
    if isinstance(raw, int):
        value = raw
    else:
        try:
            value = int(str(raw).strip(), 0)
        except ValueError as exc:
            raise ConfigurationError(
                f"setting {field!r} must be an integer such as 0x7E0, got {raw!r}"
            ) from exc
    if not 0 <= value <= maximum:
        raise ConfigurationError(
            f"setting {field!r} must be within 0..{maximum:#x}, got {value:#x}"
        )
    return value


def _as_can_id(raw: Any, field: str) -> int:
    return _as_int_in_range(raw, field, 0x7FF)


def _as_optional_byte(raw: Any, field: str) -> int | None:
    if isinstance(raw, str) and raw.strip().lower() in ("", "none"):
        return None
    return _as_int_in_range(raw, field, 0xFF)


def _as_log_level(raw: Any, field: str) -> str:
    try:
        return normalize_log_level(str(raw))
    except ConfigurationError as exc:
        raise ConfigurationError(f"setting {field!r}: {exc}") from exc


def _as_pid_list(raw: Any, field: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        items = [part for part in raw.replace(",", " ").split() if part]
    elif isinstance(raw, (list, tuple)):
        items = [str(part) for part in raw]
    else:
        raise ConfigurationError(
            f"setting {field!r} must be a list of PID identifiers or a comma separated string, "
            f"got {raw!r}"
        )
    pids = tuple(normalize_pid(part) for part in items)
    for pid in pids:
        if not is_valid_pid(pid):
            raise ConfigurationError(
                f"setting {field!r} contains an invalid PID identifier {pid!r} "
                "(expected request form such as '010C')"
            )
    return pids


#: Casters for every setting that may be provided by a file or the environment.
#: A test asserts that this mapping stays in sync with the fields of :class:`AppSettings`.
FIELD_CASTERS: Mapping[str, Callable[[Any, str], Any]] = {
    "adapter": _as_text,
    "serial_port": _as_optional_text,
    "serial_baudrate": _as_positive_int,
    "connect_timeout_s": _as_positive_float,
    "command_timeout_s": _as_positive_float,
    "live_data_pids": _as_pid_list,
    "log_level": _as_log_level,
    "log_file": _as_optional_path,
    "output_dir": _as_path,
    "knowledge_dir": _as_optional_path,
    "dyno_curb_mass_kg": _as_optional_positive_float,
    "dyno_extra_mass_kg": _as_non_negative_float,
    "dyno_drag_coefficient": _as_positive_float,
    "dyno_frontal_area_m2": _as_positive_float,
    "dyno_drivetrain_loss": _as_non_negative_float,
    "dyno_rolling_resistance": _as_non_negative_float,
    "dyno_air_density_kg_m3": _as_positive_float,
    "can_interface": _as_can_interface,
    "can_channel": _as_optional_text,
    "can_bitrate": _as_positive_int,
    "uds_request_id": _as_can_id,
    "uds_response_id": _as_can_id,
    "isotp_padding": _as_optional_byte,
}


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Resolved application settings.

    Attributes:
        adapter: Registry name of the adapter to use (default ``"mock"``).
        serial_port: Port of a serial adapter; unset for the mock adapter.
        serial_baudrate: Baud rate for serial adapters (ELM327 clones commonly use 38400).
        connect_timeout_s: Seconds to wait for a connection to be established
            (:data:`CONNECT_TIMEOUT_RANGE_S`: 5 to 60).
        command_timeout_s: Seconds to wait for a single request/response exchange
            (:data:`COMMAND_TIMEOUT_RANGE_S`: 0.5 to 10).
        live_data_pids: Service 01 parameters read by the diagnostic workflow.
        log_level: Logging level name.
        log_file: Optional log file; logs go to stderr when unset.
        output_dir: Directory for generated artefacts (reports, logs, captures).
        knowledge_dir: Optional extra knowledge data directory.
        dyno_*: Defaults of the virtual dyno's vehicle parameters (the dashboard can change
            them for the session). ``dyno_curb_mass_kg`` has no default on purpose: the
            vehicle's mass must come from the user. Ranges are checked by
            :class:`iran_obd_tuner.diagnostics.dyno.VehicleParameters`.
        can_interface: CAN interface for UDS identification; ``virtual-sim`` (default) is
            the simulated ECU on python-can's virtual bus.
        can_channel: Channel of a real CAN interface (``COM5``, ``PCAN_USBBUS1``, ``0``).
        can_bitrate: CAN bitrate in bit/s (real interfaces: 250000 or 500000 only, checked
            when the link is configured).
        uds_request_id / uds_response_id: 11-bit physical UDS identifiers (ISO 15765-4
            defaults; not verified for any specific vehicle).
        isotp_padding: ISO-TP padding byte, or ``None`` (``"none"`` in a file) for none.
            (The UDS extended session is deliberately *not* a setting: it can only be
            enabled per run, in code or in the dashboard.)
        config_file: File the settings were loaded from, or ``None`` for pure defaults.
    """

    adapter: str = "mock"
    serial_port: str | None = None
    serial_baudrate: int = 38400
    connect_timeout_s: float = 5.0
    command_timeout_s: float = 2.0
    live_data_pids: tuple[str, ...] = DEFAULT_LIVE_DATA_PIDS
    log_level: str = "INFO"
    log_file: Path | None = None
    output_dir: Path = Path("output")
    knowledge_dir: Path | None = None
    dyno_curb_mass_kg: float | None = None
    dyno_extra_mass_kg: float = 75.0
    dyno_drag_coefficient: float = 0.33
    dyno_frontal_area_m2: float = 2.0
    dyno_drivetrain_loss: float = 0.15
    dyno_rolling_resistance: float = 0.013
    dyno_air_density_kg_m3: float = 1.06
    can_interface: str = "virtual-sim"
    can_channel: str | None = None
    can_bitrate: int = 500_000
    uds_request_id: int = 0x7E0
    uds_response_id: int = 0x7E8
    isotp_padding: int | None = 0xCC
    config_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.adapter.strip():
            raise ConfigurationError("setting 'adapter' must not be empty")
        if self.serial_baudrate <= 0:
            raise ConfigurationError("setting 'serial_baudrate' must be positive")
        if self.connect_timeout_s <= 0 or self.command_timeout_s <= 0:
            raise ConfigurationError("timeouts must be positive")
        for name, (low, high) in (
            ("command_timeout_s", COMMAND_TIMEOUT_RANGE_S),
            ("connect_timeout_s", CONNECT_TIMEOUT_RANGE_S),
        ):
            value = getattr(self, name)
            if not low <= value <= high:
                raise ConfigurationError(
                    f"setting {name!r} must be within {low:g}..{high:g} s, got {value:g} "
                    "(out-of-range values are refused, not silently changed)"
                )
        normalize_log_level(self.log_level)
        if self.can_interface not in CAN_INTERFACE_CHOICES:
            raise ConfigurationError(f"unsupported can_interface {self.can_interface!r}")
        if self.can_bitrate <= 0:
            raise ConfigurationError("setting 'can_bitrate' must be positive")
        if self.uds_request_id == self.uds_response_id:
            raise ConfigurationError("uds_request_id and uds_response_id must differ")
        for pid in self.live_data_pids:
            if not is_valid_pid(pid):
                raise ConfigurationError(f"invalid PID identifier {pid!r} in 'live_data_pids'")

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the settings (paths as strings)."""
        view: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Path):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            view[field.name] = value
        return view


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"configuration file {path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"configuration file {path} could not be read: {exc}") from exc

    unknown = sorted(set(raw) - set(FIELD_CASTERS))
    if unknown:
        valid = ", ".join(sorted(FIELD_CASTERS))
        raise ConfigurationError(
            f"unknown key(s) in {path}: {', '.join(unknown)}; valid keys are: {valid}"
        )
    return {name: FIELD_CASTERS[name](value, name) for name, value in raw.items()}


def _read_environment(env: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, caster in FIELD_CASTERS.items():
        env_name = f"{ENV_PREFIX}{name.upper()}"
        if env_name in env:
            values[name] = caster(env[env_name], name)
    return values


def config_file_for(
    config_file: Path | str | None = None,
    *,
    use_default_config_file: bool = True,
) -> Path | None:
    """Return the configuration file that :func:`load_settings` would read, if any.

    Args:
        config_file: Explicit path from the caller (``--config``).
        use_default_config_file: Whether ``config/local.toml`` may be picked up implicitly.

    Raises:
        ConfigurationError: If an explicit path does not exist.
    """
    if config_file is not None:
        path = Path(config_file)
        if not path.is_file():
            raise ConfigurationError(f"configuration file not found: {path}")
        return path
    if use_default_config_file and DEFAULT_CONFIG_FILE.is_file():
        return DEFAULT_CONFIG_FILE
    return None


def load_settings(
    config_file: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    use_default_config_file: bool = True,
) -> AppSettings:
    """Load settings from defaults, an optional TOML file and the environment.

    Args:
        config_file: Explicit configuration file. When ``None`` and
            ``config/local.toml`` exists, that file is used.
        env: Environment mapping, defaults to :data:`os.environ`.
        use_default_config_file: Set to ``False`` to ignore ``config/local.toml``.

    Returns:
        The resolved, validated settings.

    Raises:
        ConfigurationError: On a missing explicit file, malformed TOML, unknown keys or
            invalid values.
    """
    environment = os.environ if env is None else env
    path = config_file_for(config_file, use_default_config_file=use_default_config_file)

    values: dict[str, Any] = {}
    if path is not None:
        values.update(_read_config_file(path))
    values.update(_read_environment(environment))
    values[SOURCE_FIELD] = path

    try:
        return AppSettings(**values)
    except TypeError as exc:  # pragma: no cover - defensive: keys are validated above
        raise ConfigurationError(f"invalid configuration: {exc}") from exc
