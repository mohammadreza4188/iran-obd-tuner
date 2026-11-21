"""Error taxonomy of the project.

Every error raised deliberately by this project derives from
:class:`IranObdTunerError`, so callers (CLI, UI, future HTTP layer) can distinguish
project failures from programming errors without inspecting messages.

Sub-trees:

* configuration and knowledge data — :class:`ConfigurationError`,
  :class:`KnowledgeDatabaseError`
* hardware / communication — :class:`AdapterError` and its subclasses
* blocked or missing vehicle operations — :class:`SafetyGuardError`,
  :class:`UnsupportedOperationError`
"""

from __future__ import annotations

__all__ = [
    "AdapterConnectionError",
    "AdapterError",
    "AdapterNotFoundError",
    "AdapterNotImplementedError",
    "AdapterTimeoutError",
    "ConfigurationError",
    "EvidenceExportError",
    "IranObdTunerError",
    "KnowledgeDatabaseError",
    "MapDefinitionError",
    "ProtocolError",
    "RomError",
    "RomExportError",
    "RomLoadError",
    "SafetyGuardError",
    "TableEditError",
    "UnsupportedOperationError",
    "UnsupportedPidError",
    "VehicleKnowledgeError",
]


class IranObdTunerError(Exception):
    """Base class for every error raised deliberately by Iran OBD Tuner."""


# --------------------------------------------------------------------------- #
# configuration and knowledge data
# --------------------------------------------------------------------------- #


class ConfigurationError(IranObdTunerError):
    """Configuration is missing, unreadable, malformed or out of range."""


class KnowledgeDatabaseError(IranObdTunerError):
    """The knowledge database (ECU/DTC/map data) could not be read or is malformed."""


class EvidenceExportError(IranObdTunerError):
    """A Field Evidence Bundle could not be built, written or verified.

    Raised, for example, when VIN redaction was requested but the VIN would still appear in
    the bundle: the export fails closed instead of leaking it.
    """


class RomError(IranObdTunerError):
    """Base class of offline ROM analysis errors (Phase 4; never about an ECU connection)."""


class RomLoadError(RomError):
    """A ROM file cannot be loaded (size bounds, not a regular file, unsupported format)."""


class MapDefinitionError(RomError):
    """A map definition file is malformed or does not fit the loaded ROM."""


class TableEditError(RomError):
    """A table edit was refused (out of range, unknown map or cell). Nothing was changed."""


class RomExportError(RomError):
    """Exporting a modified ROM was refused or failed. The source file is never touched."""


class VehicleKnowledgeError(KnowledgeDatabaseError):
    """A vehicle knowledge pack is unreadable or violates the pack schema.

    The message names the pack and the JSON path of the offending value.
    """


# --------------------------------------------------------------------------- #
# hardware and communication
# --------------------------------------------------------------------------- #


class AdapterError(IranObdTunerError):
    """Base class for adapter (transport hardware) failures."""


class AdapterNotFoundError(IranObdTunerError):
    """An adapter name was requested that this project does not know at all."""


class AdapterNotImplementedError(AdapterError):
    """The adapter is declared (and documented) but not implemented in this build."""


class AdapterConnectionError(AdapterError):
    """The adapter could not be opened, was closed unexpectedly or was used unconnected."""


class AdapterTimeoutError(AdapterError):
    """The adapter did not answer in time (connection or single request)."""


class ProtocolError(AdapterError):
    """The adapter answered, but the response was malformed or could not be decoded."""


class UnsupportedPidError(AdapterError):
    """The requested parameter identifier is not supported by this adapter/vehicle."""


# --------------------------------------------------------------------------- #
# blocked or missing vehicle operations
# --------------------------------------------------------------------------- #


class UnsupportedOperationError(IranObdTunerError):
    """A well-formed request for a feature that is not implemented in this build."""


class SafetyGuardError(IranObdTunerError):
    """An operation was blocked by the safety policy (read-only by default).

    Raised by :func:`iran_obd_tuner.core.safety.require` for any operation that changes
    vehicle state or writes ECU calibration data. This is an intentional refusal, not a
    technical failure: write-capable features are not implemented and require explicit
    developer/owner approval plus controlled, vehicle-specific validation.
    """
