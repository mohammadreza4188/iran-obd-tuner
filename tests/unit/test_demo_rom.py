"""The SIMULATED demo ROM: deterministic, labelled, self-consistent, no real ECU data."""

from __future__ import annotations

import hashlib
import json
import re

from iran_obd_tuner.core.calibration import ChecksumStatus
from iran_obd_tuner.tuning.demo import (
    DEMO_HEADER,
    DEMO_ROM_SIZE,
    build_demo_rom,
    demo_definitions,
    demo_definitions_text,
    load_demo,
)
from iran_obd_tuner.tuning.rom_loader import extract_ascii_strings
from iran_obd_tuner.tuning.workspace import RomWorkspace

DEMO_SHA = "017c76f637fdaab12da54c6ab8ec4b8e012e6b55e123bc9d12b2137bb3da4dbb"


def test_the_demo_is_deterministic_and_pinned() -> None:
    first = build_demo_rom()
    assert first == build_demo_rom()
    assert len(first) == DEMO_ROM_SIZE
    assert hashlib.sha256(first).hexdigest() == DEMO_SHA
    assert demo_definitions().target.rom_sha256 == DEMO_SHA


def test_the_demo_says_it_is_simulated() -> None:
    rom, defs = load_demo()
    assert rom.simulated and defs.simulated
    assert rom.data[0x40 : 0x40 + len(DEMO_HEADER)] == DEMO_HEADER
    assert "NOT AN ECU IMAGE" in extract_ascii_strings(rom.data).strings[0].text
    for m in defs.maps:
        assert "(simulated)" in m.names.en and "شبیه‌سازی" in (m.names.fa or "")
    assert "SIMULATED" in (defs.provenance_notes or "")


def test_the_demo_is_compatible_and_its_checksums_are_valid() -> None:
    rom, defs = load_demo()
    ws = RomWorkspace(rom, defs)
    assert ws.compatibility.ok and ws.compatibility.warnings == ()
    assert [c.status for c in ws.checksums()] == [ChecksumStatus.VALID, ChecksumStatus.VALID]


def test_no_real_vehicle_or_ecu_names_are_used() -> None:
    text = demo_definitions_text().lower() + build_demo_rom().decode("latin-1").lower()
    for word in ("peugeot", "pars", "saipa", "ikco", "bosch", "siemens", "sagem", "me7", "tu5"):
        assert not re.search(rf"\b{word}\b", text), word
    assert json.loads(demo_definitions_text())["provenance"]["confidence"] == "unverified"
