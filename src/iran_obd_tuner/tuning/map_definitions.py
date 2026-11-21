"""Map definition files: where calibration tables live in a ROM and how to read them.

A definition file (``schema: "iran-obd-tuner/map-definitions"``) is supplied by the user or
is the bundled SIMULATED demo. The project ships **no** real ECU offsets. Every file carries
provenance (M5 :class:`~iran_obd_tuner.core.knowledge.ConfidenceLevel` and
:class:`~iran_obd_tuner.core.knowledge.Source`); ``verified`` is refused because the project
holds no field evidence for any map layout.

Layout: a table of ``rows x cols`` cells starts at ``byte_offset``; cells are stored
row-major (``layout: "row_major"``, the default) or column-major. Physical value =
``raw * scale + offset``. Axes are either fixed values in the file or values stored in the
ROM; axes are read-only in this milestone and may be shared between tables.

Validation names the JSON path of every problem; :func:`check_compatibility` refuses a ROM of
the wrong size, missing identifier strings, out-of-bounds or overlapping tables and tables
overlapping a checksum field.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iran_obd_tuner.core.calibration import (
    ChecksumAlgorithm,
    CompatibilityReport,
    DataType,
    DefinitionsSummary,
    MapCategory,
)
from iran_obd_tuner.core.exceptions import MapDefinitionError
from iran_obd_tuner.core.knowledge import ConfidenceLevel, LocalizedText, Source, SourceKind
from iran_obd_tuner.tuning.checksum import ChecksumBlock, validate_block

__all__ = [
    "DEFINITIONS_SCHEMA",
    "MAX_DEFINITIONS_SIZE",
    "AxisDefinition",
    "MapDefinition",
    "MapDefinitions",
    "TargetDescription",
    "check_compatibility",
    "load_definitions",
    "parse_definitions",
]

DEFINITIONS_SCHEMA = "iran-obd-tuner/map-definitions"
SCHEMA_VERSION = 1
MAX_DEFINITIONS_SIZE = 2 * 1024 * 1024
MAX_CELLS = 64 * 64

_ID = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
_ARABIC = re.compile(r"[؀-ۿ]")
_HEX = re.compile(r"^(0x)?[0-9A-Fa-f]{1,8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    """An axis: fixed values from the file (``kind="fixed"``) or values in the ROM."""

    kind: str
    unit: str
    names: LocalizedText | None = None
    values: tuple[float, ...] = ()
    byte_offset: int = 0
    count: int = 0
    data_type: DataType = DataType.UINT8
    scale: float = 1.0
    offset: float = 0.0

    @property
    def length(self) -> int:
        return len(self.values) if self.kind == "fixed" else self.count

    @property
    def byte_range(self) -> tuple[int, int] | None:
        if self.kind != "rom":
            return None
        return self.byte_offset, self.byte_offset + self.count * self.data_type.size


@dataclass(frozen=True, slots=True)
class MapDefinition:
    """One calibration table."""

    id: str
    names: LocalizedText
    category: MapCategory
    byte_offset: int
    rows: int
    cols: int
    data_type: DataType
    scale: float
    offset: float
    unit: str
    min: float
    max: float
    x_axis: AxisDefinition | None = None
    y_axis: AxisDefinition | None = None
    layout: str = "row_major"
    notes: str | None = None

    @property
    def cell_count(self) -> int:
        return self.rows * self.cols

    @property
    def byte_length(self) -> int:
        return self.cell_count * self.data_type.size

    @property
    def byte_range(self) -> tuple[int, int]:
        return self.byte_offset, self.byte_offset + self.byte_length

    def cell_offset(self, row: int, col: int) -> int:
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise IndexError(f"cell ({row}, {col}) is outside {self.id} ({self.rows}x{self.cols})")
        index = row * self.cols + col if self.layout == "row_major" else col * self.rows + row
        return self.byte_offset + index * self.data_type.size


@dataclass(frozen=True, slots=True)
class TargetDescription:
    description: str
    rom_size: int
    identifiers: tuple[tuple[int, str], ...] = ()
    rom_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class MapDefinitions:
    """A validated definition file."""

    name: str
    sha256: str
    simulated: bool
    confidence: ConfidenceLevel
    source: Source | None
    provenance_notes: str | None
    target: TargetDescription
    maps: tuple[MapDefinition, ...]
    checksums: tuple[ChecksumBlock, ...]

    def map(self, map_id: str) -> MapDefinition:
        for definition in self.maps:
            if definition.id == map_id:
                return definition
        raise MapDefinitionError(f"no map {map_id!r} in {self.name}")

    def summary(self) -> DefinitionsSummary:
        return DefinitionsSummary(
            name=self.name,
            sha256=self.sha256,
            simulated=self.simulated,
            confidence=self.confidence,
            source=self.source,
            notes=self.provenance_notes,
            description=self.target.description,
            map_count=len(self.maps),
            checksum_count=len(self.checksums),
        )


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


class _P:
    """Parsing context: the file name for error messages."""

    def __init__(self, name: str) -> None:
        self.name = name

    def error(self, path: str, message: str) -> MapDefinitionError:
        return MapDefinitionError(f"{self.name}: {path}: {message}")

    def obj(
        self, path: str, raw: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise self.error(path, "must be a JSON object")
        missing = [k for k in required if k not in raw]
        if missing:
            raise self.error(path, f"missing key(s): {', '.join(missing)}")
        unknown = sorted(set(raw) - set(required) - set(optional))
        if unknown:
            raise self.error(path, f"unknown key(s): {', '.join(unknown)}")
        return raw

    def text(self, path: str, raw: Any, *, ascii_only: bool = False) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise self.error(path, "must be a non-empty string")
        if ascii_only and not raw.isascii():
            raise self.error(path, "must be ASCII")
        return raw.strip()

    def integer(self, path: str, raw: Any, *, minimum: int = 0, maximum: int | None = None) -> int:
        if isinstance(raw, bool):
            raise self.error(path, "must be an integer")
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str) and _HEX.match(raw.strip()):
            value = int(raw.strip(), 16)
        else:
            raise self.error(path, "must be an integer or hex text such as '0x2000'")
        if value < minimum or (maximum is not None and value > maximum):
            raise self.error(path, f"{value} is out of range")
        return value

    def number(self, path: str, raw: Any) -> float:
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise self.error(path, "must be a number")
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            raise self.error(path, "must be a finite number")
        return value

    def enum(self, path: str, raw: Any, kind: type) -> Any:
        try:
            return kind(raw)
        except ValueError as exc:
            choices = ", ".join(member.value for member in kind)
            raise self.error(path, f"{raw!r} is not one of: {choices}") from exc

    def names(self, path: str, raw: Any) -> LocalizedText:
        obj = self.obj(path, raw, ("en", "fa"))
        en = self.text(f"{path}.en", obj["en"], ascii_only=True)
        fa = self.text(f"{path}.fa", obj["fa"])
        if not _ARABIC.search(fa):
            raise self.error(f"{path}.fa", "must contain Persian text")
        return LocalizedText(en=en, fa=fa)

    def ident(self, path: str, raw: Any) -> str:
        if not isinstance(raw, str) or not _ID.match(raw):
            raise self.error(path, f"{raw!r} is not a valid id (lowercase, digits, _)")
        return raw


def _axis(p: _P, path: str, raw: Any) -> AxisDefinition:
    obj = p.obj(
        path,
        raw,
        ("kind", "unit"),
        ("names", "values", "byte_offset", "count", "data_type", "scale", "offset"),
    )
    kind = obj["kind"]
    unit = p.text(f"{path}.unit", obj["unit"], ascii_only=True)
    names = None if obj.get("names") is None else p.names(f"{path}.names", obj["names"])
    if kind == "fixed":
        values = obj.get("values")
        if not isinstance(values, list) or not values:
            raise p.error(f"{path}.values", "a fixed axis needs a non-empty list of values")
        for key in ("byte_offset", "count", "data_type", "scale", "offset"):
            if key in obj:
                raise p.error(f"{path}.{key}", "only allowed for kind 'rom'")
        numbers = tuple(p.number(f"{path}.values[{i}]", v) for i, v in enumerate(values))
        return AxisDefinition(kind="fixed", unit=unit, names=names, values=numbers)
    if kind == "rom":
        if "values" in obj:
            raise p.error(f"{path}.values", "only allowed for kind 'fixed'")
        for key in ("byte_offset", "count", "data_type"):
            if key not in obj:
                raise p.error(path, f"missing key(s): {key}")
        scale = p.number(f"{path}.scale", obj.get("scale", 1))
        if scale == 0:
            raise p.error(f"{path}.scale", "must not be zero")
        return AxisDefinition(
            kind="rom",
            unit=unit,
            names=names,
            byte_offset=p.integer(f"{path}.byte_offset", obj["byte_offset"]),
            count=p.integer(f"{path}.count", obj["count"], minimum=1, maximum=64),
            data_type=p.enum(f"{path}.data_type", obj["data_type"], DataType),
            scale=scale,
            offset=p.number(f"{path}.offset", obj.get("offset", 0)),
        )
    raise p.error(f"{path}.kind", f"{kind!r} is not 'fixed' or 'rom'")


def _physical_limits(dtype: DataType, scale: float, offset: float) -> tuple[float, float]:
    a, b = dtype.min_raw * scale + offset, dtype.max_raw * scale + offset
    return min(a, b), max(a, b)


def _map(p: _P, path: str, raw: Any) -> MapDefinition:
    obj = p.obj(
        path,
        raw,
        ("id", "names", "category", "byte_offset", "rows", "cols", "data_type", "scale", "unit"),
        ("offset", "min", "max", "x_axis", "y_axis", "layout", "notes"),
    )
    map_id = p.ident(f"{path}.id", obj["id"])
    rows = p.integer(f"{path}.rows", obj["rows"], minimum=1, maximum=64)
    cols = p.integer(f"{path}.cols", obj["cols"], minimum=1, maximum=64)
    dtype = p.enum(f"{path}.data_type", obj["data_type"], DataType)
    scale = p.number(f"{path}.scale", obj["scale"])
    if scale == 0:
        raise p.error(f"{path}.scale", "must not be zero")
    offset = p.number(f"{path}.offset", obj.get("offset", 0))
    low, high = _physical_limits(dtype, scale, offset)
    edit_min = p.number(f"{path}.min", obj["min"]) if "min" in obj else low
    edit_max = p.number(f"{path}.max", obj["max"]) if "max" in obj else high
    if not edit_min < edit_max:
        raise p.error(path, "min must be smaller than max")
    if edit_min < low - abs(scale) / 2 or edit_max > high + abs(scale) / 2:
        raise p.error(path, f"min/max exceed what {dtype} can store ({low:g}..{high:g})")
    layout = obj.get("layout", "row_major")
    if layout not in ("row_major", "column_major"):
        raise p.error(f"{path}.layout", "must be 'row_major' or 'column_major'")
    x_axis = None if obj.get("x_axis") is None else _axis(p, f"{path}.x_axis", obj["x_axis"])
    y_axis = None if obj.get("y_axis") is None else _axis(p, f"{path}.y_axis", obj["y_axis"])
    if x_axis is not None and x_axis.length != cols:
        raise p.error(f"{path}.x_axis", f"has {x_axis.length} values for {cols} column(s)")
    if y_axis is not None:
        if rows == 1:
            raise p.error(f"{path}.y_axis", "a 1-row table has no y axis")
        if y_axis.length != rows:
            raise p.error(f"{path}.y_axis", f"has {y_axis.length} values for {rows} row(s)")
    return MapDefinition(
        id=map_id,
        names=p.names(f"{path}.names", obj["names"]),
        category=p.enum(f"{path}.category", obj["category"], MapCategory),
        byte_offset=p.integer(f"{path}.byte_offset", obj["byte_offset"]),
        rows=rows,
        cols=cols,
        data_type=dtype,
        scale=scale,
        offset=offset,
        unit=p.text(f"{path}.unit", obj["unit"], ascii_only=True),
        min=edit_min,
        max=edit_max,
        x_axis=x_axis,
        y_axis=y_axis,
        layout=layout,
        notes=None if obj.get("notes") is None else p.text(f"{path}.notes", obj["notes"]),
    )


def _checksum(p: _P, path: str, raw: Any) -> ChecksumBlock:
    obj = p.obj(
        path,
        raw,
        ("id", "algorithm", "start", "end", "checksum_offset"),
        ("exclude_checksum_bytes", "store_endianness"),
    )
    exclude = obj.get("exclude_checksum_bytes", True)
    if not isinstance(exclude, bool):
        raise p.error(f"{path}.exclude_checksum_bytes", "must be true or false")
    order = obj.get("store_endianness")
    if order not in (None, "little", "big"):
        raise p.error(f"{path}.store_endianness", "must be 'little' or 'big'")
    return ChecksumBlock(
        id=p.ident(f"{path}.id", obj["id"]),
        algorithm=p.enum(f"{path}.algorithm", obj["algorithm"], ChecksumAlgorithm),
        start=p.integer(f"{path}.start", obj["start"]),
        end=p.integer(f"{path}.end", obj["end"]),
        checksum_offset=p.integer(f"{path}.checksum_offset", obj["checksum_offset"]),
        exclude_checksum_bytes=exclude,
        store_endianness=order,
    )


def _provenance(p: _P, raw: Any) -> tuple[ConfidenceLevel, Source | None, str | None]:
    obj = p.obj("provenance", raw, ("confidence",), ("source", "notes"))
    confidence = p.enum("provenance.confidence", obj["confidence"], ConfidenceLevel)
    if confidence is ConfidenceLevel.VERIFIED:
        raise p.error(
            "provenance.confidence",
            "'verified' is refused: the project holds no field evidence for any map layout",
        )
    source = None
    if obj.get("source") is not None:
        s = p.obj(
            "provenance.source",
            obj["source"],
            ("id", "kind", "title"),
            ("publisher", "url", "notes"),
        )
        url = s.get("url")
        if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
            raise p.error("provenance.source.url", "must be an https:// address")
        source = Source(
            id=p.text("provenance.source.id", s["id"], ascii_only=True),
            kind=p.enum("provenance.source.kind", s["kind"], SourceKind),
            title=p.text("provenance.source.title", s["title"]),
            publisher=s.get("publisher"),
            url=url,
            notes=s.get("notes"),
        )
    notes = None if obj.get("notes") is None else p.text("provenance.notes", obj["notes"])
    return confidence, source, notes


def parse_definitions(raw: Any, name: str, sha256: str = "") -> MapDefinitions:
    """Validate one definition document and build its models.

    Raises:
        MapDefinitionError: On the first problem, naming the JSON path.
    """
    p = _P(name)
    obj = p.obj(
        "$",
        raw,
        ("schema", "schema_version", "simulated", "provenance", "target", "maps"),
        ("checksums",),
    )
    if obj["schema"] != DEFINITIONS_SCHEMA:
        raise p.error("schema", f"must be {DEFINITIONS_SCHEMA!r}")
    if obj["schema_version"] != SCHEMA_VERSION:
        raise p.error("schema_version", f"must be {SCHEMA_VERSION}")
    if not isinstance(obj["simulated"], bool):
        raise p.error("simulated", "must be true or false")
    confidence, source, notes = _provenance(p, obj["provenance"])

    t = p.obj("target", obj["target"], ("description", "rom_size"), ("identifiers", "rom_sha256"))
    identifiers = []
    for i, item in enumerate(t.get("identifiers") or []):
        ident = p.obj(f"target.identifiers[{i}]", item, ("byte_offset", "ascii"))
        identifiers.append(
            (
                p.integer(f"target.identifiers[{i}].byte_offset", ident["byte_offset"]),
                p.text(f"target.identifiers[{i}].ascii", ident["ascii"], ascii_only=True),
            )
        )
    rom_sha = t.get("rom_sha256")
    if rom_sha is not None and (not isinstance(rom_sha, str) or not _SHA256.match(rom_sha)):
        raise p.error("target.rom_sha256", "must be 64 lowercase hex characters")
    target = TargetDescription(
        description=p.text("target.description", t["description"]),
        rom_size=p.integer("target.rom_size", t["rom_size"], minimum=1),
        identifiers=tuple(identifiers),
        rom_sha256=rom_sha,
    )

    raw_maps = obj["maps"]
    if not isinstance(raw_maps, list) or not raw_maps:
        raise p.error("maps", "must be a non-empty list")
    maps = tuple(_map(p, f"maps[{i}]", m) for i, m in enumerate(raw_maps))
    ids = [m.id for m in maps]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise p.error("maps", f"duplicate map id(s): {', '.join(duplicates)}")

    raw_checksums = obj.get("checksums", [])
    if not isinstance(raw_checksums, list):
        raise p.error("checksums", "must be a list")
    checksums = tuple(_checksum(p, f"checksums[{i}]", c) for i, c in enumerate(raw_checksums))
    cids = [c.id for c in checksums]
    if len(cids) != len(set(cids)):
        raise p.error("checksums", "duplicate checksum id")
    return MapDefinitions(
        name=name,
        sha256=sha256,
        simulated=obj["simulated"],
        confidence=confidence,
        source=source,
        provenance_notes=notes,
        target=target,
        maps=maps,
        checksums=checksums,
    )


def load_definitions(path: Path | str) -> MapDefinitions:
    """Read and validate a definition file (at most :data:`MAX_DEFINITIONS_SIZE` bytes)."""
    source = Path(path)
    try:
        if source.stat().st_size > MAX_DEFINITIONS_SIZE:
            raise MapDefinitionError(f"{source.name}: larger than {MAX_DEFINITIONS_SIZE} bytes")
        blob = source.read_bytes()
    except OSError as exc:
        raise MapDefinitionError(f"{source}: cannot be read: {exc}") from exc
    try:
        raw = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapDefinitionError(f"{source.name}: not valid UTF-8 JSON: {exc}") from exc
    return parse_definitions(raw, source.name, hashlib.sha256(blob).hexdigest())


# --------------------------------------------------------------------------- #
# compatibility with a ROM
# --------------------------------------------------------------------------- #


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def check_compatibility(
    definitions: MapDefinitions, rom_data: bytes, *, rom_sha256: str, rom_simulated: bool
) -> CompatibilityReport:
    """Whether ``definitions`` describe an image like ``rom_data``. Errors refuse it."""
    errors: list[str] = []
    warnings: list[str] = []
    size = len(rom_data)
    target = definitions.target
    if target.rom_size != size:
        errors.append(f"ROM size is {size} bytes, the definitions expect {target.rom_size}")
    for offset, text in target.identifiers:
        found = rom_data[offset : offset + len(text)]
        if found != text.encode("ascii"):
            errors.append(f"identifier {text!r} not found at 0x{offset:X}")
    if target.rom_sha256 is not None and target.rom_sha256 != rom_sha256:
        warnings.append("this is not the exact stock file the definitions were written for")
    if definitions.simulated and not rom_simulated:
        warnings.append(
            "SIMULATED definitions on a non-simulated ROM: offsets describe no real ECU"
        )
    if rom_simulated and not definitions.simulated:
        warnings.append("non-simulated definitions on the SIMULATED demo ROM")

    tables: list[tuple[str, tuple[int, int]]] = []
    for m in definitions.maps:
        lo, hi = m.byte_range
        if hi > size:
            errors.append(f"map {m.id}: 0x{lo:X}..0x{hi:X} is outside the ROM")
        tables.append((m.id, m.byte_range))
        for axis_name, axis in (("x_axis", m.x_axis), ("y_axis", m.y_axis)):
            axis_range = None if axis is None else axis.byte_range
            if axis_range is not None and axis_range[1] > size:
                errors.append(f"map {m.id}: {axis_name} is outside the ROM")
    for i, (a_id, a_range) in enumerate(tables):
        for b_id, b_range in tables[i + 1 :]:
            if _overlaps(a_range, b_range):
                errors.append(f"maps {a_id} and {b_id} overlap")
    for m in definitions.maps:
        for axis_name, axis in (("x_axis", m.x_axis), ("y_axis", m.y_axis)):
            axis_range = None if axis is None else axis.byte_range
            if axis_range is None:
                continue
            for t_id, t_range in tables:
                if _overlaps(axis_range, t_range):
                    errors.append(f"map {m.id}: {axis_name} overlaps the data of map {t_id}")
    for block in definitions.checksums:
        errors.extend(validate_block(block, size))
        for t_id, t_range in tables:
            if _overlaps(block.field_range, t_range):
                errors.append(f"checksum {block.id}: its field overlaps map {t_id}")
    return CompatibilityReport(errors=tuple(errors), warnings=tuple(warnings))
