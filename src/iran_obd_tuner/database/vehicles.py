"""Read-only vehicle and ECU knowledge base built from JSON packs.

Bundled packs live in ``data/vehicles/<manufacturer>.json`` next to ``guidance.json`` (the
bilingual field-verification instructions the packs refer to). Every pack is validated by
:mod:`iran_obd_tuner.database.vehicle_schema` when the knowledge base is built; afterwards
the knowledge base is immutable. New vehicles are added by adding or editing a pack - no
Python code changes.

``extra_dir`` (the ``knowledge_dir`` setting) may contain a ``vehicles/`` folder with local
packs: a local vehicle replaces the bundled one with the same id, a local manufacturer
record replaces the bundled one, and ``vehicles/guidance.json`` adds or replaces guidance
entries. Every replacement is logged. Nothing is ever written.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.exceptions import VehicleKnowledgeError
from iran_obd_tuner.core.knowledge import (
    LocalizedText,
    Manufacturer,
    VehicleProfile,
    VehicleVariant,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.database.vehicle_schema import ParsedPack, parse_guidance, parse_pack

__all__ = [
    "GUIDANCE_FILENAME",
    "VEHICLES_DIRNAME",
    "JsonVehicleKnowledgeBase",
    "VehicleKnowledgeBase",
]

VEHICLES_DIRNAME = "vehicles"
GUIDANCE_FILENAME = "guidance.json"

_LOG = get_logger("database.vehicles")


class VehicleKnowledgeBase(ABC):
    """Read-only access to vehicle and ECU profiles."""

    @property
    @abstractmethod
    def source(self) -> str:
        """Where the loaded packs come from (``bundled:ikco.json + ...``)."""

    @abstractmethod
    def manufacturers(self) -> tuple[Manufacturer, ...]:
        """All manufacturers, sorted by id."""

    @abstractmethod
    def vehicles(self, manufacturer: str | None = None) -> tuple[VehicleProfile, ...]:
        """All vehicles (of one manufacturer), sorted by id."""

    @abstractmethod
    def guidance_catalog(self) -> Mapping[str, LocalizedText]:
        """``verify.<name>`` -> field-verification instructions."""

    def manufacturer(self, manufacturer_id: str) -> Manufacturer | None:
        return next((m for m in self.manufacturers() if m.id == manufacturer_id), None)

    def vehicle(self, vehicle_id: str) -> VehicleProfile | None:
        return next((v for v in self.vehicles() if v.id == vehicle_id), None)

    def variant(self, vehicle_id: str, variant_id: str) -> VehicleVariant | None:
        vehicle = self.vehicle(vehicle_id)
        return None if vehicle is None else vehicle.variant(variant_id)

    def guidance(self, key: str | None, lang: str = "en") -> str | None:
        if key is None:
            return None
        text = self.guidance_catalog().get(key)
        return None if text is None else text.get(lang)


def _read_json(reader: Traversable | Path, label: str) -> Any:
    try:
        return json.loads(reader.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VehicleKnowledgeError(f"{label}: not found") from exc
    except json.JSONDecodeError as exc:
        raise VehicleKnowledgeError(f"{label}: not valid JSON: {exc}") from exc
    except OSError as exc:
        raise VehicleKnowledgeError(f"{label}: could not be read: {exc}") from exc


def _pack_files(directory: Traversable | Path) -> list[Traversable | Path]:
    return sorted(
        (
            entry
            for entry in directory.iterdir()
            if entry.is_file() and entry.name.endswith(".json") and entry.name != GUIDANCE_FILENAME
        ),
        key=lambda entry: entry.name,
    )


class JsonVehicleKnowledgeBase(VehicleKnowledgeBase):
    """Vehicle knowledge base backed by the bundled packs (and optional local packs).

    Args:
        extra_dir: Optional knowledge directory; packs are read from ``extra_dir/vehicles``.
        include_bundled: ``False`` loads only the local packs (bundled guidance is still
            loaded). Used by tests with fixture packs.

    Raises:
        VehicleKnowledgeError: A pack is missing, unreadable or violates the schema.
    """

    def __init__(
        self, extra_dir: Path | str | None = None, *, include_bundled: bool = True
    ) -> None:
        bundled_dir = files("iran_obd_tuner.database").joinpath("data", VEHICLES_DIRNAME)
        guidance = parse_guidance(
            _read_json(bundled_dir.joinpath(GUIDANCE_FILENAME), f"bundled:{GUIDANCE_FILENAME}"),
            f"bundled:{GUIDANCE_FILENAME}",
        )

        local_dir = None if extra_dir is None else Path(extra_dir) / VEHICLES_DIRNAME
        if local_dir is not None and not local_dir.is_dir():
            if include_bundled:
                _LOG.info("knowledge directory %s has no %s/ folder", extra_dir, VEHICLES_DIRNAME)
            local_dir = None
        if local_dir is not None and (local_dir / GUIDANCE_FILENAME).is_file():
            extra = parse_guidance(
                _read_json(local_dir / GUIDANCE_FILENAME, str(local_dir / GUIDANCE_FILENAME)),
                str(local_dir / GUIDANCE_FILENAME),
            )
            for key in sorted(set(extra) & set(guidance)):
                _LOG.info("local guidance replaces %s", key)
            guidance.update(extra)
        keys = frozenset(guidance)

        manufacturers: dict[str, Manufacturer] = {}
        vehicles: dict[str, VehicleProfile] = {}
        sources: list[str] = []

        if include_bundled:
            for entry in _pack_files(bundled_dir):
                label = f"bundled:{entry.name}"
                pack = parse_pack(_read_json(entry, label), label, keys)
                if pack.manufacturer.id in manufacturers:
                    raise VehicleKnowledgeError(
                        f"{label}: manufacturer {pack.manufacturer.id!r} is defined twice"
                    )
                self._add(pack, manufacturers, vehicles, replace=False)
                sources.append(label)

        if local_dir is not None:
            for entry in _pack_files(local_dir):
                label = str(entry)
                pack = parse_pack(_read_json(entry, label), label, keys)
                self._add(pack, manufacturers, vehicles, replace=True)
                sources.append(label)

        self._guidance: Mapping[str, LocalizedText] = guidance
        self._manufacturers = tuple(manufacturers[k] for k in sorted(manufacturers))
        self._vehicles = tuple(vehicles[k] for k in sorted(vehicles))
        self._source = " + ".join(sources) if sources else "no vehicle packs"

    @staticmethod
    def _add(
        pack: ParsedPack,
        manufacturers: dict[str, Manufacturer],
        vehicles: dict[str, VehicleProfile],
        *,
        replace: bool,
    ) -> None:
        if pack.manufacturer.id in manufacturers and replace:
            _LOG.info("%s replaces manufacturer %s", pack.name, pack.manufacturer.id)
        manufacturers[pack.manufacturer.id] = pack.manufacturer
        for vehicle in pack.vehicles:
            if vehicle.id in vehicles:
                if not replace:
                    raise VehicleKnowledgeError(
                        f"{pack.name}: vehicle {vehicle.id!r} is defined twice"
                    )
                _LOG.info("%s replaces vehicle %s", pack.name, vehicle.id)
            vehicles[vehicle.id] = vehicle

    @property
    def source(self) -> str:
        return self._source

    def manufacturers(self) -> tuple[Manufacturer, ...]:
        return self._manufacturers

    def vehicles(self, manufacturer: str | None = None) -> tuple[VehicleProfile, ...]:
        if manufacturer is None:
            return self._vehicles
        return tuple(v for v in self._vehicles if v.manufacturer_id == manufacturer)

    def guidance_catalog(self) -> Mapping[str, LocalizedText]:
        return self._guidance


def iter_variants(
    vehicles: Iterable[VehicleProfile],
) -> Iterable[tuple[VehicleProfile, VehicleVariant]]:
    """Every (vehicle, variant) pair, in order."""
    for vehicle in vehicles:
        for variant in vehicle.variants:
            yield vehicle, variant
