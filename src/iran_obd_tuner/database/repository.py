"""Read access to the knowledge database.

The contract is deliberately small: describe a trouble code, and state where the data came
from. Everything the rest of the application needs from the knowledge database goes through
:class:`KnowledgeRepository`, so the JSON implementation shipped today can be replaced by a
real database later without touching callers.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.dtc import parse_dtc_code
from iran_obd_tuner.core.exceptions import KnowledgeDatabaseError
from iran_obd_tuner.core.logging import get_logger

__all__ = [
    "DTC_CATALOG_FILENAME",
    "DtcDefinition",
    "JsonKnowledgeRepository",
    "KnowledgeRepository",
]

#: File name expected inside the bundled data directory and inside an override directory.
DTC_CATALOG_FILENAME = "dtc_catalog.json"

_LOG = get_logger("database.repository")


@dataclass(frozen=True, slots=True)
class DtcDefinition:
    """Knowledge database entry for one trouble code.

    Attributes:
        code: Normalised code, e.g. ``"P0301"``.
        description: What the code means.
        source: Where this description came from, e.g. ``"bundled:dtc_catalog.json"``.
    """

    code: str
    description: str
    source: str


class KnowledgeRepository(ABC):
    """Read-only access to knowledge data."""

    @property
    @abstractmethod
    def source(self) -> str:
        """Human readable description of where the loaded data comes from."""

    @abstractmethod
    def describe_dtc(self, code: str) -> DtcDefinition | None:
        """Return the definition for ``code``, or ``None`` when it is unknown.

        A malformed code is not an error here: an adapter may return a code string the
        standard does not define, and the report must still be produced with the raw code.
        """

    @abstractmethod
    def metadata(self) -> Mapping[str, Any]:
        """Provenance metadata of the loaded catalogue (source notes, review status)."""

    def known_codes(self) -> tuple[str, ...]:
        """Return every trouble code present in the catalogue, sorted."""
        return tuple(sorted(self._codes()))

    @abstractmethod
    def _codes(self) -> Mapping[str, str]:
        """Return the raw code to description mapping."""


@dataclass(frozen=True, slots=True)
class _Catalog:
    metadata: Mapping[str, Any]
    codes: Mapping[str, str]
    source: str


def _load_catalog(reader: Any, source: str) -> _Catalog:
    """Read and validate a catalogue document from a path or package resource."""
    try:
        raw = json.loads(reader.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeDatabaseError(f"knowledge catalogue not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeDatabaseError(
            f"knowledge catalogue {source} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise KnowledgeDatabaseError(
            f"knowledge catalogue {source} could not be read: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise KnowledgeDatabaseError(f"knowledge catalogue {source} must contain a JSON object")

    codes = raw.get("codes", {})
    if not isinstance(codes, dict) or not codes:
        raise KnowledgeDatabaseError(
            f"knowledge catalogue {source} must contain a non-empty 'codes' object"
        )

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise KnowledgeDatabaseError(f"'metadata' in {source} must be a JSON object")

    normalized: dict[str, str] = {}
    for code, description in codes.items():
        try:
            normalized_code = parse_dtc_code(str(code)).code
        except ValueError as exc:
            raise KnowledgeDatabaseError(
                f"invalid trouble code {code!r} in {source}: {exc}"
            ) from exc
        if not str(description).strip():
            raise KnowledgeDatabaseError(f"empty description for {normalized_code} in {source}")
        normalized[normalized_code] = str(description).strip()

    return _Catalog(metadata=dict(metadata), codes=normalized, source=source)


class JsonKnowledgeRepository(KnowledgeRepository):
    """Knowledge repository backed by JSON documents.

    The catalogue bundled with the package is always loaded. Passing ``extra_dir`` loads a
    second ``dtc_catalog.json`` from that directory whose entries override the bundled ones —
    the extension point for researched data that is not ready to ship in the package (see
    the ``knowledge_dir`` setting in ``config/example.toml``).

    Args:
        extra_dir: Optional directory with an overriding catalogue.

    Raises:
        KnowledgeDatabaseError: If a catalogue is missing, unreadable, malformed or contains
            invalid trouble codes.
    """

    def __init__(self, extra_dir: Path | str | None = None) -> None:
        bundled_reader = files("iran_obd_tuner.database").joinpath("data", DTC_CATALOG_FILENAME)
        bundled = _load_catalog(bundled_reader, source=f"bundled:{DTC_CATALOG_FILENAME}")

        codes: dict[str, str] = dict(bundled.codes)
        metadata: dict[str, Any] = dict(bundled.metadata)
        sources = [bundled.source]

        if extra_dir is not None:
            override_path = Path(extra_dir) / DTC_CATALOG_FILENAME
            if override_path.is_file():
                override = _load_catalog(override_path, source=str(override_path))
                codes.update(override.codes)
                metadata.update(override.metadata)
                sources.append(override.source)
                _LOG.info(
                    "loaded %d overriding trouble code definition(s) from %s",
                    len(override.codes),
                    override_path,
                )
            else:
                _LOG.warning(
                    "knowledge directory %s contains no %s; using the bundled catalogue only",
                    extra_dir,
                    DTC_CATALOG_FILENAME,
                )

        self._codes_by_code = codes
        self._metadata: Mapping[str, Any] = metadata
        self._source = " + ".join(sources)

    @property
    def source(self) -> str:
        return self._source

    def metadata(self) -> Mapping[str, Any]:
        return self._metadata

    def _codes(self) -> Mapping[str, str]:
        return self._codes_by_code

    def describe_dtc(self, code: str) -> DtcDefinition | None:
        try:
            normalized = parse_dtc_code(code).code
        except ValueError:
            _LOG.warning("knowledge lookup skipped for malformed trouble code %r", code)
            return None
        description = self._codes_by_code.get(normalized)
        if description is None:
            return None
        return DtcDefinition(code=normalized, description=description, source=self._source)
