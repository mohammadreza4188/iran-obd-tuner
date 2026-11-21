"""A deterministic **SIMULATED** demo ROM and its map definitions.

The image is generated in code (64 KiB) and describes **no real ECU**: its header says
``SIMULATED-DEMO-ROM / NOT AN ECU IMAGE``, its tables are smooth synthetic surfaces, and its
two checksum blocks (``sum16_le`` and ``crc32``) are valid as generated. It exists so that
the Bench workspace, the CLI and the tests can be exercised without any real dump. No
Iranian vehicle, ECU, part number or real offset appears here.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from iran_obd_tuner.core.calibration import ChecksumAlgorithm
from iran_obd_tuner.tuning.checksum import ChecksumBlock, encode_checksum
from iran_obd_tuner.tuning.map_definitions import MapDefinitions, parse_definitions
from iran_obd_tuner.tuning.rom_loader import RomImage, rom_from_bytes

__all__ = [
    "DEMO_DEFINITIONS_NAME",
    "DEMO_HEADER",
    "DEMO_ROM_NAME",
    "DEMO_ROM_SIZE",
    "build_demo_rom",
    "demo_definitions",
    "demo_definitions_text",
    "load_demo",
]

DEMO_ROM_SIZE = 64 * 1024
DEMO_ROM_NAME = "simulated_demo_rom.bin"
DEMO_DEFINITIONS_NAME = "simulated_demo_maps.json"
DEMO_HEADER = b"SIMULATED-DEMO-ROM / NOT AN ECU IMAGE / iran-obd-tuner"

_RPM_AXIS = (1000, 1800, 2600, 3400, 4200, 5000, 5800, 6600)
_BLOCKS = (
    ChecksumBlock("demo_low_sum", ChecksumAlgorithm.SUM16_LE, 0x0000, 0x8000, 0x7FFE),
    ChecksumBlock("demo_high_crc", ChecksumAlgorithm.CRC32, 0x8000, 0x10000, 0xFFFC),
)


def _put(image: bytearray, offset: int, data: bytes) -> None:
    image[offset : offset + len(data)] = data


def build_demo_rom() -> bytes:
    """The SIMULATED demo image (identical on every call)."""
    image = bytearray(b"\xff" * DEMO_ROM_SIZE)
    _put(image, 0x0040, DEMO_HEADER)
    _put(image, 0x0100, b"DEMO-SW-0001 (simulated)")
    for i, rpm in enumerate(_RPM_AXIS):  # shared x axis, uint16 LE
        _put(image, 0x1F00 + 2 * i, rpm.to_bytes(2, "little"))
    for r in range(8):
        for c in range(8):
            fuel_ms = 2.0 + 0.2 * c + 0.3 * r  # demo fuel, 0.01 ms per bit
            _put(image, 0x2000 + 2 * (8 * r + c), round(fuel_ms * 100).to_bytes(2, "little"))
            ignition_deg = 30.0 - 2.0 * r + 1.5 * c  # demo ignition, 0.5 deg per bit, int8
            _put(
                image,
                0x2100 + 8 * r + c,
                round(ignition_deg * 2).to_bytes(1, "little", signed=True),
            )
    for c in range(8):  # demo torque limit, 0.1 Nm per bit, uint16 BE
        _put(image, 0x2200 + 2 * c, (1200 + 50 * c).to_bytes(2, "big"))
    _put(image, 0x2300, (6500).to_bytes(2, "little"))  # demo rev limiter, rpm
    _put(image, 0x3000, bytes(0x400))  # a zero-filled region
    state = 0x1234_5678
    pattern = bytearray()
    for _ in range(1024):  # deterministic pseudo-random bytes (LCG)
        state = (1103515245 * state + 12345) & 0x7FFF_FFFF
        pattern.append(state >> 16 & 0xFF)
    _put(image, 0x9000, bytes(pattern))
    for block in _BLOCKS:
        lo, hi = block.field_range
        image[lo:hi] = encode_checksum(image, block)
    return bytes(image)


def demo_definitions_text() -> str:
    return (
        files("iran_obd_tuner.tuning")
        .joinpath("data", "demo_maps.json")
        .read_text(encoding="utf-8")
    )


def demo_definitions() -> MapDefinitions:
    text = demo_definitions_text()
    return parse_definitions(
        json.loads(text), DEMO_DEFINITIONS_NAME, hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def load_demo() -> tuple[RomImage, MapDefinitions]:
    """The SIMULATED demo image (``simulated=True``) and its definitions."""
    return rom_from_bytes(build_demo_rom(), name=DEMO_ROM_NAME, simulated=True), demo_definitions()
