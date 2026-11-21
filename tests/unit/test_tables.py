"""Table decoding / bounded encoding, undoable edits, diffs and the editing workspace."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.calibration import ChecksumStatus, DataType, TableViewMode
from iran_obd_tuner.core.exceptions import MapDefinitionError, TableEditError
from iran_obd_tuner.tuning import workspace as workspace_module
from iran_obd_tuner.tuning.demo import demo_definitions, load_demo
from iran_obd_tuner.tuning.rom_loader import rom_from_bytes
from iran_obd_tuner.tuning.tables import decode_raw, encode_raw, to_raw
from iran_obd_tuner.tuning.workspace import RomWorkspace

FUEL = "demo_fuel_base"
IGN = "demo_ignition_base"


@pytest.fixture
def ws() -> RomWorkspace:
    rom, defs = load_demo()
    return RomWorkspace(rom, defs)


@pytest.mark.parametrize(
    ("dtype", "raw", "value"),
    [
        (DataType.UINT8, b"\xfe", 254),
        (DataType.INT8, b"\xfe", -2),
        (DataType.UINT16_LE, b"\x34\x12", 0x1234),
        (DataType.UINT16_BE, b"\x12\x34", 0x1234),
        (DataType.INT16_LE, b"\xff\xff", -1),
        (DataType.INT16_BE, b"\x80\x00", -32768),
    ],
)
def test_decode_and_encode_every_data_type(dtype: DataType, raw: bytes, value: int) -> None:
    assert decode_raw(b"\x00" + raw, 1, dtype) == value
    assert encode_raw(value, dtype) == raw


def test_encode_refuses_values_that_do_not_fit() -> None:
    with pytest.raises(TableEditError):
        encode_raw(256, DataType.UINT8)
    with pytest.raises(TableEditError):
        encode_raw(-129, DataType.INT8)
    with pytest.raises(TableEditError, match="not enough bytes"):
        decode_raw(b"\x00", 0, DataType.UINT16_LE)


def test_physical_values_round_to_the_nearest_step_and_are_never_clamped() -> None:
    fuel = demo_definitions().map(FUEL)  # 0.01 ms, 0..20
    assert to_raw(2.555, fuel) == 256  # round half to even on the raw value
    assert to_raw(20.0, fuel) == 2000
    for bad in (20.01, -0.5, float("nan"), float("inf")):
        with pytest.raises(TableEditError):
            to_raw(bad, fuel)


def test_reading_tables_with_axes(ws: RomWorkspace) -> None:
    fuel = ws.table(FUEL)
    assert fuel.current[0][:3] == (2.0, 2.2, 2.4)
    assert fuel.x_axis is not None and fuel.x_axis.values[0] == 1000.0
    assert fuel.y_axis is not None and fuel.y_axis.values == (20, 30, 40, 50, 60, 70, 80, 90)
    torque = ws.table("demo_torque_limit")  # uint16 big endian, 0.1 Nm
    assert torque.current[0] == tuple(120.0 + 5 * c for c in range(8))
    assert ws.table(IGN).current[0][0] == 30.0  # int8 * 0.5
    assert ws.table("demo_rev_limiter").current == ((6500.0,),)
    assert [m.id for m in ws.maps()] == [FUEL, IGN, "demo_torque_limit", "demo_rev_limiter"]


def test_edit_undo_redo(ws: RomWorkspace) -> None:
    assert not ws.can_undo and not ws.is_modified
    outcome = ws.set_cell(FUEL, 0, 0, 3.0)
    assert outcome.changed_cells == 1 and ws.is_modified
    table = ws.table(FUEL)
    assert table.current[0][0] == 3.0 and table.original[0][0] == 2.0
    assert table.changed(0, 0) and table.changed_count == 1
    assert table.delta(0, 0) == pytest.approx(1.0)
    assert table.delta_percent(0, 0) == pytest.approx(50.0)
    assert table.value(0, 0, TableViewMode.ORIGINAL) == 2.0
    assert ws.undo() is not None and not ws.is_modified and ws.can_redo
    assert ws.redo() is not None and ws.table(FUEL).current[0][0] == 3.0
    assert ws.undo() and ws.undo() is None


def test_a_new_edit_clears_redo(ws: RomWorkspace) -> None:
    ws.set_cell(FUEL, 0, 0, 3.0)
    ws.undo()
    ws.set_cell(FUEL, 1, 1, 3.0)
    assert not ws.can_redo and ws.redo() is None


def test_batch_edits_are_one_undo_step(ws: RomWorkspace) -> None:
    cells = [(r, c) for r in range(2) for c in range(3)]
    outcome = ws.scale_cells(FUEL, cells, 10.0)
    assert outcome.changed_cells == 6
    assert ws.table(FUEL).current[0][1] == pytest.approx(2.42)
    ws.undo()
    assert not ws.is_modified
    assert ws.set_cells(IGN, [(0, 0), (0, 1)], 20.0).changed_cells == 2
    assert ws.history()[-1]["kind"] == "set_cells"


def test_refused_edits_change_nothing(ws: RomWorkspace) -> None:
    with pytest.raises(TableEditError, match="outside demo_fuel_base"):
        ws.set_cells(FUEL, [(0, 0), (0, 1)], 99.0)
    with pytest.raises(TableEditError, match="is outside"):
        ws.set_cell(FUEL, 8, 0, 3.0)
    with pytest.raises(TableEditError, match="given twice"):
        ws.set_cells(FUEL, [(0, 0), (0, 0)], 3.0)
    with pytest.raises(TableEditError, match="no map"):
        ws.set_cell("nope", 0, 0, 1.0)
    with pytest.raises(TableEditError):
        ws.scale_cells(FUEL, [(0, 0)], 10_000)
    assert not ws.is_modified and not ws.can_undo


def test_no_op_edit_adds_no_history(ws: RomWorkspace) -> None:
    outcome = ws.set_cell(FUEL, 0, 0, 2.001)  # rounds to the stored 2.00
    assert outcome.changed_cells == 0 and outcome.max_quantisation_error == pytest.approx(0.001)
    assert not ws.can_undo


def test_zero_original_has_no_percentage() -> None:
    rom, defs = load_demo()
    data = bytearray(rom.data)
    data[0x2000:0x2002] = b"\x00\x00"
    ws = RomWorkspace(rom_from_bytes(data, name="z.bin", simulated=True), defs)
    ws.set_cell(FUEL, 0, 0, 1.0)
    assert ws.table(FUEL).delta_percent(0, 0) is None
    assert ws.table(FUEL).value(0, 0, TableViewMode.DELTA_PERCENT) is None


def test_checksums_follow_edits_and_recalculation_is_undoable(ws: RomWorkspace) -> None:
    assert {c.status for c in ws.checksums()} == {ChecksumStatus.VALID}
    assert ws.recalculate_checksums() == 0 and not ws.can_undo
    ws.set_cell(FUEL, 0, 0, 3.0)
    assert [c.status for c in ws.checksums()][0] is ChecksumStatus.INVALID
    assert ws.recalculate_checksums() == 1
    assert {c.status for c in ws.checksums()} == {ChecksumStatus.VALID}
    assert ws.history()[-1]["kind"] == "patch_bytes"
    assert ws.undo() == "recalculate checksums"
    assert ws.checksums()[0].status is ChecksumStatus.INVALID
    assert {c.status for c in ws.original_checksums()} == {ChecksumStatus.VALID}


def test_changed_ranges_and_confinement(ws: RomWorkspace) -> None:
    assert ws.changed_ranges() == ()
    ws.set_cell(FUEL, 0, 0, 3.0)
    ws.set_cell(FUEL, 0, 2, 3.0)
    assert ws.changed_ranges() == ((0x2000, 0x2002), (0x2004, 0x2006))  # both bytes of uint16
    ws.assert_edits_are_confined()
    ws._working[0x5000] ^= 0xFF  # simulate a bug writing outside every table
    with pytest.raises(TableEditError, match="0x5000"):
        ws.assert_edits_are_confined()


def test_history_is_bounded(ws: RomWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_module, "HISTORY_LIMIT", 3)
    for value in (3.0, 3.1, 3.2, 3.3, 3.4):
        ws.set_cell(FUEL, 0, 0, value)
    assert len(ws.history()) == 3 and ws.dropped_history == 2


def test_definitions_must_fit_and_cannot_change_mid_edit(ws: RomWorkspace) -> None:
    rom, defs = load_demo()
    other = rom_from_bytes(b"\xff" * len(rom.data), name="blank.bin")
    with pytest.raises(MapDefinitionError, match="does not fit"):
        RomWorkspace(other, defs)
    ws.set_cell(FUEL, 0, 0, 3.0)
    with pytest.raises(MapDefinitionError, match="undo all edits"):
        ws.attach_definitions(defs)
    bare = RomWorkspace(rom)
    assert bare.maps() == () and bare.checksums()[0].status is ChecksumStatus.NOT_CONFIGURED
    assert bare.recalculate_checksums() == 0
    with pytest.raises(TableEditError, match="no map definitions"):
        bare.table(FUEL)
