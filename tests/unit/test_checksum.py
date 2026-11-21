"""Checksum algorithms: catalogue check values, exact sums, ranges, storage, verification."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.calibration import ChecksumAlgorithm as A
from iran_obd_tuner.core.calibration import ChecksumStatus
from iran_obd_tuner.tuning.checksum import (
    ALGORITHM_WIDTH,
    ChecksumBlock,
    compute,
    covered_bytes,
    encode_checksum,
    stored_value,
    validate_block,
    verify,
    verify_all,
)

CHECK = b"123456789"  # the standard input of the CRC catalogue


def test_crc_catalogue_check_values() -> None:
    assert compute(A.CRC16_CCITT_FALSE, CHECK) == 0x29B1  # CRC-16/IBM-3740
    assert compute(A.CRC32, CHECK) == 0xCBF43926  # CRC-32/ISO-HDLC
    assert compute(A.CRC16_CCITT_FALSE, b"") == 0xFFFF


@pytest.mark.parametrize(
    ("algorithm", "data", "expected"),
    [
        (A.SUM8, b"\xff\x02", 0x01),
        (A.SUM8, CHECK, sum(CHECK) & 0xFF),
        (A.SUM16_LE, b"\x01\x02\x03\x04", 0x0201 + 0x0403),
        (A.SUM16_BE, b"\x01\x02\x03\x04", 0x0102 + 0x0304),
        (A.SUM16_LE, b"\xff\xff\x02\x00", 0x0001),  # wraps mod 2^16
        (A.TWOS_COMPLEMENT_8, b"\x01\x02", 0xFD),
        (A.TWOS_COMPLEMENT_16_LE, b"\x01\x00", 0xFFFF),
    ],
)
def test_exact_definitions(algorithm: A, data: bytes, expected: int) -> None:
    assert compute(algorithm, data) == expected


def test_twos_complement_makes_the_total_zero() -> None:
    data = bytes(range(200))
    assert (sum(data) + compute(A.TWOS_COMPLEMENT_8, data)) & 0xFF == 0
    words = sum(int.from_bytes(data[i : i + 2], "little") for i in range(0, 200, 2))
    assert (words + compute(A.TWOS_COMPLEMENT_16_LE, data)) & 0xFFFF == 0


def test_word_sums_refuse_odd_lengths() -> None:
    with pytest.raises(ValueError, match="even"):
        compute(A.SUM16_LE, b"\x01\x02\x03")


def test_every_algorithm_has_a_width() -> None:
    assert set(ALGORITHM_WIDTH) == set(A)


@pytest.mark.parametrize("algorithm", list(A))
def test_recalculated_checksum_verifies(algorithm: A) -> None:
    rom = bytearray(bytes(range(256)) * 16)
    block = ChecksumBlock("b", algorithm, 0, 1024, 1000)
    assert validate_block(block, len(rom)) == []
    lo, hi = block.field_range
    rom[lo:hi] = encode_checksum(rom, block)
    result = verify(rom, block)
    assert result.status is ChecksumStatus.VALID and result.stored == result.computed
    rom[10] ^= 0xFF
    assert verify(rom, block).status is ChecksumStatus.INVALID


def test_the_checksum_field_is_excluded_from_its_own_range() -> None:
    rom = bytearray(range(16))
    block = ChecksumBlock("b", A.SUM8, 0, 16, 4)
    assert covered_bytes(rom, block) == bytes(range(4)) + bytes(range(5, 16))
    outside = ChecksumBlock("o", A.SUM8, 0, 8, 12)
    assert covered_bytes(rom, outside) == bytes(range(8))


def test_storage_byte_order() -> None:
    rom = bytearray(16)
    rom[0:2] = b"\x12\x34"
    assert stored_value(rom, ChecksumBlock("le", A.SUM16_LE, 2, 16, 0)) == 0x3412
    assert stored_value(rom, ChecksumBlock("be", A.SUM16_BE, 2, 16, 0)) == 0x1234
    assert stored_value(rom, ChecksumBlock("crc", A.CRC16_CCITT_FALSE, 2, 16, 0)) == 0x1234
    override = ChecksumBlock("x", A.CRC32, 4, 16, 0, store_endianness="big")
    assert override.byteorder == "big"


@pytest.mark.parametrize(
    ("block", "message"),
    [
        (ChecksumBlock("r", A.SUM8, 0, 2000, 0), "outside the ROM"),
        (ChecksumBlock("r", A.SUM8, 10, 5, 0), "outside the ROM"),
        (ChecksumBlock("f", A.CRC32, 0, 100, 1022), "field 0x3FE is outside"),
        (ChecksumBlock("i", A.SUM8, 0, 100, 50, exclude_checksum_bytes=False), "exclude_checksum"),
        (ChecksumBlock("s", A.CRC32, 0, 100, 98), "straddles"),
        (ChecksumBlock("w", A.SUM16_LE, 0, 101, 200), "even, word-aligned"),
        (ChecksumBlock("a", A.SUM16_LE, 0, 100, 51), "even, word-aligned"),
        (ChecksumBlock("e", A.SUM8, 0, 100, 200, store_endianness="middle"), "store_endianness"),
    ],
)
def test_invalid_blocks_are_reported(block: ChecksumBlock, message: str) -> None:
    problems = validate_block(block, 1024)
    assert any(message in p for p in problems), problems


def test_no_blocks_means_not_configured() -> None:
    (result,) = verify_all(b"\x00" * 16, ())
    assert result.status is ChecksumStatus.NOT_CONFIGURED
    assert result.as_dict()["block_id"] is None
    assert "not proof" in result.as_dict()["note"]
