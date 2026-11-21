"""Map definition files: schema rejections (with JSON paths), provenance and compatibility."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from iran_obd_tuner.core.calibration import DataType, MapCategory
from iran_obd_tuner.core.exceptions import MapDefinitionError
from iran_obd_tuner.core.knowledge import ConfidenceLevel
from iran_obd_tuner.tuning.demo import build_demo_rom, demo_definitions_text
from iran_obd_tuner.tuning.map_definitions import (
    MAX_DEFINITIONS_SIZE,
    check_compatibility,
    load_definitions,
    parse_definitions,
)

DEMO_SHA = "017c76f637fdaab12da54c6ab8ec4b8e012e6b55e123bc9d12b2137bb3da4dbb"


def _demo() -> dict[str, Any]:
    return json.loads(demo_definitions_text())


def _fuel(d: dict[str, Any]) -> dict[str, Any]:
    return d["maps"][0]


Mutation = Callable[[dict[str, Any]], None]

REJECTIONS: list[tuple[str, Mutation, str]] = [
    ("schema", lambda d: d.update(schema="x"), "schema: must be"),
    ("version", lambda d: d.update(schema_version=2), "schema_version"),
    ("typo_top", lambda d: d.update(mapz=[]), "unknown key(s): mapz"),
    ("simulated_type", lambda d: d.update(simulated="yes"), "simulated: must be true or false"),
    ("verified", lambda d: d["provenance"].update(confidence="verified"), "'verified' is refused"),
    ("bad_confidence", lambda d: d["provenance"].update(confidence="sure"), "is not one of"),
    ("http_source", lambda d: d["provenance"]["source"].update(url="http://x"), "https://"),
    ("bad_kind", lambda d: d["provenance"]["source"].update(kind="blog"), "is not one of"),
    ("no_maps", lambda d: d.update(maps=[]), "maps: must be a non-empty list"),
    ("dup_map", lambda d: d["maps"].append(copy.deepcopy(_fuel(d))), "duplicate map id"),
    ("bad_id", lambda d: _fuel(d).update(id="Fuel Map"), "is not a valid id"),
    ("category", lambda d: _fuel(d).update(category="boost"), "is not one of"),
    ("dtype", lambda d: _fuel(d).update(data_type="float32"), "is not one of"),
    ("scale_zero", lambda d: _fuel(d).update(scale=0), "must not be zero"),
    ("scale_text", lambda d: _fuel(d).update(scale="1"), "must be a number"),
    ("rows_zero", lambda d: _fuel(d).update(rows=0), "out of range"),
    ("rows_big", lambda d: _fuel(d).update(rows=65), "out of range"),
    ("offset_text", lambda d: _fuel(d).update(byte_offset="zz"), "hex text"),
    ("min_max", lambda d: _fuel(d).update(min=10, max=5), "min must be smaller"),
    ("max_too_big", lambda d: _fuel(d).update(max=1000), "exceed what uint16_le"),
    ("layout", lambda d: _fuel(d).update(layout="zigzag"), "row_major"),
    ("missing_unit", lambda d: _fuel(d).pop("unit"), "missing key(s): unit"),
    ("non_ascii_unit", lambda d: _fuel(d).update(unit="میلی‌ثانیه"), "must be ASCII"),
    ("latin_fa", lambda d: _fuel(d)["names"].update(fa="fuel"), "must contain Persian"),
    ("x_len", lambda d: _fuel(d)["x_axis"].update(count=7), "has 7 values for 8 column"),
    ("y_len", lambda d: _fuel(d)["y_axis"].update(values=[1, 2]), "has 2 values for 8 row"),
    ("axis_kind", lambda d: _fuel(d)["x_axis"].update(kind="table"), "'fixed' or 'rom'"),
    (
        "fixed_with_offset",
        lambda d: _fuel(d)["y_axis"].update(byte_offset=0),
        "only allowed for kind 'rom'",
    ),
    (
        "rom_axis_missing",
        lambda d: _fuel(d)["x_axis"].pop("data_type"),
        "missing key(s): data_type",
    ),
    (
        "y_on_1d",
        lambda d: d["maps"][2].update(y_axis=_fuel(d)["y_axis"]),
        "a 1-row table has no y axis",
    ),
    ("nan", lambda d: _fuel(d)["y_axis"].update(values=[float("nan")] * 8), "finite"),
    ("cs_algo", lambda d: d["checksums"][0].update(algorithm="md5"), "is not one of"),
    (
        "cs_exclude",
        lambda d: d["checksums"][0].update(exclude_checksum_bytes="no"),
        "true or false",
    ),
    ("cs_order", lambda d: d["checksums"][0].update(store_endianness="mixed"), "'little' or 'big'"),
    (
        "cs_dup",
        lambda d: d["checksums"].append(copy.deepcopy(d["checksums"][0])),
        "duplicate checksum",
    ),
    ("sha", lambda d: d["target"].update(rom_sha256="ABC"), "64 lowercase hex"),
]


@pytest.mark.parametrize(
    ("mutate", "message"), [pytest.param(m, s, id=n) for n, m, s in REJECTIONS]
)
def test_schema_rejects(mutate: Mutation, message: str) -> None:
    doc = _demo()
    mutate(doc)
    with pytest.raises(MapDefinitionError) as info:
        parse_definitions(doc, "defs.json")
    assert message in str(info.value)
    assert str(info.value).startswith("defs.json: ")


def test_error_names_the_json_path() -> None:
    doc = _demo()
    doc["maps"][1]["x_axis"]["count"] = 9
    with pytest.raises(MapDefinitionError, match=r"maps\[1\]\.x_axis"):
        parse_definitions(doc, "d.json")


def test_demo_definitions_parse() -> None:
    defs = parse_definitions(_demo(), "demo.json", "a" * 64)
    assert defs.simulated and defs.confidence is ConfidenceLevel.UNVERIFIED
    fuel = defs.map("demo_fuel_base")
    assert fuel.category is MapCategory.FUEL and fuel.data_type is DataType.UINT16_LE
    assert fuel.byte_range == (0x2000, 0x2080)
    assert fuel.cell_offset(1, 2) == 0x2000 + 2 * (8 + 2)
    assert defs.map("demo_rev_limiter").x_axis is None
    summary = defs.summary()
    assert summary.map_count == 4 and summary.checksum_count == 2
    with pytest.raises(MapDefinitionError, match="no map"):
        defs.map("nope")


def test_default_limits_follow_the_data_type() -> None:
    doc = _demo()
    for key in ("min", "max"):
        doc["maps"][1].pop(key)
    ignition = parse_definitions(doc, "d.json").map("demo_ignition_base")
    assert (ignition.min, ignition.max) == (-64.0, 63.5)  # int8 * 0.5


def test_column_major_layout() -> None:
    doc = _demo()
    _fuel(doc)["layout"] = "column_major"
    fuel = parse_definitions(doc, "d.json").map("demo_fuel_base")
    assert fuel.cell_offset(1, 2) == 0x2000 + 2 * (2 * 8 + 1)
    with pytest.raises(IndexError):
        fuel.cell_offset(8, 0)


def test_load_definitions_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "defs.json"
    path.write_text(demo_definitions_text(), encoding="utf-8")
    defs = load_definitions(path)
    assert defs.name == "defs.json" and len(defs.sha256) == 64
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(MapDefinitionError, match="not valid UTF-8 JSON"):
        load_definitions(path)
    path.write_bytes(b" " * (MAX_DEFINITIONS_SIZE + 1))
    with pytest.raises(MapDefinitionError, match="larger than"):
        load_definitions(path)
    with pytest.raises(MapDefinitionError, match="cannot be read"):
        load_definitions(tmp_path / "missing.json")


# --------------------------------------------------------------------------- #
# compatibility
# --------------------------------------------------------------------------- #


def _check(doc: dict[str, Any], data: bytes | None = None, *, simulated: bool = True):
    defs = parse_definitions(doc, "d.json")
    data = build_demo_rom() if data is None else data
    return check_compatibility(defs, data, rom_sha256=DEMO_SHA, rom_simulated=simulated)


def test_demo_is_compatible_with_its_rom() -> None:
    report = _check(_demo())
    assert report.ok and report.warnings == ()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["target"].update(rom_size=32768), "the definitions expect 32768"),
        (lambda d: d["target"]["identifiers"][0].update(ascii="OTHER-ROM"), "not found at 0x40"),
        (lambda d: _fuel(d).update(byte_offset="0xFFF0"), "is outside the ROM"),
        (lambda d: _fuel(d).update(byte_offset="0x2100"), "overlap"),
        (lambda d: _fuel(d)["x_axis"].update(byte_offset="0x2010"), "x_axis overlaps the data"),
        (lambda d: _fuel(d)["x_axis"].update(byte_offset="0xFFFC"), "x_axis is outside"),
        (lambda d: d["checksums"][0].update(checksum_offset="0x2002"), "its field overlaps map"),
        (lambda d: d["checksums"][0].update(end="0x20000"), "outside the ROM"),
    ],
)
def test_incompatible_definitions_are_refused(mutate: Mutation, message: str) -> None:
    doc = _demo()
    mutate(doc)
    report = _check(doc)
    assert not report.ok
    assert any(message in e for e in report.errors), report.errors


def test_shared_axes_are_allowed() -> None:
    doc = _demo()
    assert doc["maps"][0]["x_axis"]["byte_offset"] == doc["maps"][1]["x_axis"]["byte_offset"]
    assert _check(doc).ok


def test_warnings_do_not_refuse() -> None:
    doc = _demo()
    doc["target"]["rom_sha256"] = "0" * 64
    report = _check(doc, simulated=False)
    assert report.ok
    assert any("not the exact stock file" in w for w in report.warnings)
    assert any("SIMULATED definitions on a non-simulated ROM" in w for w in report.warnings)
    doc["simulated"] = False
    assert any("non-simulated definitions" in w for w in _check(doc).warnings)
