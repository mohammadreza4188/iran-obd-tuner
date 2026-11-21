"""Pluggable checksum verification and recalculation over byte ranges (offline only).

Every algorithm has one exact definition:

=========================  ==============================================================
``sum8``                   sum of the bytes mod 2^8, stored in 1 byte
``sum16_le`` / ``_be``     sum of 16-bit little / big endian *words* mod 2^16, stored in the
                           same byte order (the covered length must be even)
``crc16_ccitt_false``      CRC-16/IBM-3740 a.k.a. CCITT-FALSE: poly 0x1021, init 0xFFFF, no
                           reflection, xorout 0 (check value of "123456789" = 0x29B1),
                           stored big endian
``crc32``                  CRC-32/ISO-HDLC (``zlib.crc32``; check value 0xCBF43926), stored
                           little endian
``twos_complement_8``      ``(-sum8) & 0xFF``: the bytes plus the checksum sum to 0 mod 2^8
``twos_complement_16_le``  ``(-sum of 16-bit LE words) & 0xFFFF``, stored little endian
=========================  ==============================================================

A block covers ``[start, end)``. When the stored checksum lies inside that range and
``exclude_checksum_bytes`` is set (the default), its own bytes are left out of the
calculation. ``store_endianness`` may override the default storage order.

``valid`` only means "matches the configured algorithm". Most ECUs use proprietary or
multi-region schemes this project does not know; nothing here proves that an ECU accepts an
image, and nothing here sends anything to an ECU.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from iran_obd_tuner.core.calibration import ChecksumAlgorithm, ChecksumResult, ChecksumStatus

__all__ = [
    "ALGORITHM_WIDTH",
    "ChecksumBlock",
    "compute",
    "covered_bytes",
    "encode_checksum",
    "stored_value",
    "validate_block",
    "verify",
    "verify_all",
]

A = ChecksumAlgorithm


def _crc16_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        table.append(crc & 0xFFFF)
    return tuple(table)


_CRC16 = _crc16_table()


def _crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16[((crc >> 8) ^ byte) & 0xFF]
    return crc


def _word_sum(data: bytes, byteorder: str) -> int:
    if len(data) % 2:
        raise ValueError("16-bit word checksums need an even number of bytes")
    total = 0
    for i in range(0, len(data), 2):
        total += int.from_bytes(data[i : i + 2], byteorder)  # type: ignore[arg-type]
    return total & 0xFFFF


_COMPUTE: Mapping[ChecksumAlgorithm, Callable[[bytes], int]] = {
    A.SUM8: lambda d: sum(d) & 0xFF,
    A.SUM16_LE: lambda d: _word_sum(d, "little"),
    A.SUM16_BE: lambda d: _word_sum(d, "big"),
    A.CRC16_CCITT_FALSE: _crc16_ccitt_false,
    A.CRC32: lambda d: zlib.crc32(d) & 0xFFFFFFFF,
    A.TWOS_COMPLEMENT_8: lambda d: (-sum(d)) & 0xFF,
    A.TWOS_COMPLEMENT_16_LE: lambda d: (-_word_sum(d, "little")) & 0xFFFF,
}

#: Stored width in bytes.
ALGORITHM_WIDTH: Mapping[ChecksumAlgorithm, int] = {
    A.SUM8: 1,
    A.SUM16_LE: 2,
    A.SUM16_BE: 2,
    A.CRC16_CCITT_FALSE: 2,
    A.CRC32: 4,
    A.TWOS_COMPLEMENT_8: 1,
    A.TWOS_COMPLEMENT_16_LE: 2,
}
_DEFAULT_ORDER: Mapping[ChecksumAlgorithm, str] = {
    A.SUM8: "little",
    A.SUM16_LE: "little",
    A.SUM16_BE: "big",
    A.CRC16_CCITT_FALSE: "big",
    A.CRC32: "little",
    A.TWOS_COMPLEMENT_8: "little",
    A.TWOS_COMPLEMENT_16_LE: "little",
}
_WORD_ALGORITHMS = frozenset({A.SUM16_LE, A.SUM16_BE, A.TWOS_COMPLEMENT_16_LE})


@dataclass(frozen=True, slots=True)
class ChecksumBlock:
    """One configured checksum: algorithm, covered range ``[start, end)``, stored location."""

    id: str
    algorithm: ChecksumAlgorithm
    start: int
    end: int
    checksum_offset: int
    exclude_checksum_bytes: bool = True
    store_endianness: str | None = None

    @property
    def width(self) -> int:
        return ALGORITHM_WIDTH[self.algorithm]

    @property
    def byteorder(self) -> str:
        return self.store_endianness or _DEFAULT_ORDER[self.algorithm]

    @property
    def field_range(self) -> tuple[int, int]:
        return self.checksum_offset, self.checksum_offset + self.width

    @property
    def field_inside_range(self) -> bool:
        lo, hi = self.field_range
        return lo < self.end and hi > self.start


def validate_block(block: ChecksumBlock, rom_size: int) -> list[str]:
    """Problems of ``block`` for an image of ``rom_size`` bytes (empty when it is usable)."""
    problems: list[str] = []
    if block.store_endianness not in (None, "little", "big"):
        problems.append(f"{block.id}: store_endianness must be 'little' or 'big'")
    if not 0 <= block.start < block.end <= rom_size:
        problems.append(f"{block.id}: range 0x{block.start:X}..0x{block.end:X} is outside the ROM")
    lo, hi = block.field_range
    if lo < 0 or hi > rom_size:
        problems.append(f"{block.id}: checksum field 0x{lo:X} is outside the ROM")
    if block.field_inside_range and not block.exclude_checksum_bytes:
        problems.append(
            f"{block.id}: the checksum field lies inside its own range; set exclude_checksum_bytes"
        )
    if block.field_inside_range and (lo < block.start or hi > block.end):
        problems.append(f"{block.id}: the checksum field straddles the range boundary")
    if block.algorithm in _WORD_ALGORITHMS and not problems:
        covered = (block.end - block.start) - (block.width if block.field_inside_range else 0)
        if covered % 2 or (block.field_inside_range and (lo - block.start) % 2):
            problems.append(f"{block.id}: 16-bit word sums need even, word-aligned ranges")
    return problems


def compute(algorithm: ChecksumAlgorithm, data: bytes) -> int:
    """The checksum of ``data`` (see the module docstring for the exact definitions)."""
    return _COMPUTE[algorithm](bytes(data))


def covered_bytes(rom: bytes | bytearray, block: ChecksumBlock) -> bytes:
    """The bytes the checksum is calculated over (its own field excluded when inside)."""
    if block.field_inside_range and block.exclude_checksum_bytes:
        lo, hi = block.field_range
        return bytes(rom[block.start : lo]) + bytes(rom[hi : block.end])
    return bytes(rom[block.start : block.end])


def stored_value(rom: bytes | bytearray, block: ChecksumBlock) -> int:
    lo, hi = block.field_range
    return int.from_bytes(bytes(rom[lo:hi]), block.byteorder)  # type: ignore[arg-type]


def encode_checksum(rom: bytes | bytearray, block: ChecksumBlock) -> bytes:
    """The bytes that make ``block`` valid for the current content of ``rom``."""
    value = compute(block.algorithm, covered_bytes(rom, block))
    return value.to_bytes(block.width, block.byteorder)  # type: ignore[arg-type]


def verify(rom: bytes | bytearray, block: ChecksumBlock) -> ChecksumResult:
    stored = stored_value(rom, block)
    computed = compute(block.algorithm, covered_bytes(rom, block))
    return ChecksumResult(
        status=ChecksumStatus.VALID if stored == computed else ChecksumStatus.INVALID,
        block_id=block.id,
        algorithm=block.algorithm,
        start=block.start,
        end=block.end,
        checksum_offset=block.checksum_offset,
        width=block.width,
        stored=stored,
        computed=computed,
    )


def verify_all(
    rom: bytes | bytearray, blocks: Sequence[ChecksumBlock]
) -> tuple[ChecksumResult, ...]:
    """One result per block, or a single ``not_configured`` result when there is none."""
    if not blocks:
        return (ChecksumResult(status=ChecksumStatus.NOT_CONFIGURED),)
    return tuple(verify(rom, block) for block in blocks)
