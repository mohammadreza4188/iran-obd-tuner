"""Virtual programmable ECU (Milestone 7b), driven request by request without any bus.

The tests play the tester side with raw requests (tests are outside the package, whose
architecture rules forbid building programming requests anywhere but the bench
programmer of Milestone 7c). Time is a fake clock, so every security delay is exact.
"""

from __future__ import annotations

import zlib

import pytest

from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET, EcuIdentity, SeedKeyProvider
from iran_obd_tuner.hardware.uds_sim_programmable import (
    DID_BOOT_STATUS,
    FaultPlan,
    ProgrammableEcuConfig,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    default_simulated_image,
)

TARGET = SIMULATED_BENCH_TARGET
START = TARGET.regions[0].start_address
SIZE = TARGET.image_size
SECTOR = TARGET.sector_size
BLOCK = 256  # data bytes per TransferData block with the default max_block_length 258


# --------------------------------------------------------------------------- tester helpers


def alfid(address: int, size: int) -> bytes:
    return b"\x44" + address.to_bytes(4, "big") + size.to_bytes(4, "big")


def one(ecu: VirtualProgrammableEcu, request: bytes) -> bytes:
    answers = ecu.handle(request)
    assert len(answers) == 1, answers
    return answers[0]


def nrc(answer: bytes) -> int | None:
    return answer[2] if answer[:1] == b"\x7f" else None


def did(ecu: VirtualProgrammableEcu, identifier: int) -> bytes:
    return one(ecu, b"\x22" + identifier.to_bytes(2, "big"))


def did_text(ecu: VirtualProgrammableEcu, identifier: int) -> str:
    answer = did(ecu, identifier)
    assert answer[:1] == b"\x62", answer
    return answer[3:].decode("ascii")


def unlock(ecu: VirtualProgrammableEcu) -> None:
    assert one(ecu, b"\x10\x02")[:2] == b"\x50\x02"
    seed = one(ecu, b"\x27\x01")[2:]
    key = SimulatedXorSeedKey().compute_key(0x01, seed)
    assert one(ecu, b"\x27\x02" + key) == b"\x67\x02"


def erase(ecu: VirtualProgrammableEcu, address: int = START, size: int = SIZE) -> list[bytes]:
    return ecu.handle(b"\x31\x01\xff\x00" + alfid(address, size))


def check_memory(ecu: VirtualProgrammableEcu, crc: int, address: int = START, size: int = SIZE):
    return one(ecu, b"\x31\x01\xff\x01" + alfid(address, size) + crc.to_bytes(4, "big"))


def start_download(ecu: VirtualProgrammableEcu, address: int = START, size: int = SIZE) -> int:
    answer = one(ecu, b"\x34\x00" + alfid(address, size))
    assert answer[:2] == b"\x74\x20", answer
    return int.from_bytes(answer[2:4], "big") - 2


def download(ecu: VirtualProgrammableEcu, data: bytes, address: int = START) -> list[int]:
    """Download ``data``; returns the block sequence counters used."""
    chunk = start_download(ecu, address, len(data))
    sequences = []
    for index, offset in enumerate(range(0, len(data), chunk)):
        sequence = (index + 1) & 0xFF
        answer = one(ecu, bytes([0x36, sequence]) + data[offset : offset + chunk])
        assert answer == bytes([0x76, sequence]), (index, answer)
        sequences.append(sequence)
    assert one(ecu, b"\x37") == b"\x77"
    return sequences


def upload(ecu: VirtualProgrammableEcu, address: int = START, size: int = SIZE) -> bytes:
    answer = one(ecu, b"\x35\x00" + alfid(address, size))
    assert answer[:2] == b"\x75\x20", answer
    data = b""
    sequence = 0
    while len(data) < size:
        sequence = (sequence + 1) & 0xFF
        answer = one(ecu, bytes([0x36, sequence]))
        assert answer[:2] == bytes([0x76, sequence]), answer
        data += answer[2:]
    assert one(ecu, b"\x37") == b"\x77"
    return data


def new_image(seed: int = 1) -> bytes:
    return bytes((index * 13 + seed) & 0xFF for index in range(SIZE))


@pytest.fixture
def ecu(fake_clock) -> VirtualProgrammableEcu:
    return VirtualProgrammableEcu(clock=fake_clock)


# --------------------------------------------------------------------------- identity, sessions


def test_the_factory_state_is_a_valid_labelled_application(ecu) -> None:
    image = default_simulated_image()

    assert ecu.flash == image and image.startswith(b"SIMULATED-APPLICATION-IMAGE")
    assert ecu.application_valid and not ecu.in_bootloader
    assert did_text(ecu, DID_BOOT_STATUS) == "APPLICATION"
    assert did_text(ecu, 0xF189) == f"SIM-SW-{zlib.crc32(image):08X}"
    identity = EcuIdentity({d: did_text(ecu, d) for d in (0xF187, 0xF191)})
    assert identity.mismatches(TARGET.identity_patterns) == ()
    assert did(ecu, 0xF186) == b"\x62\xf1\x86\x01"  # active session: default


def test_unknown_and_write_services_are_not_supported(ecu) -> None:
    for sid in (0x14, 0x19, 0x23, 0x28, 0x2E, 0x2F, 0x3D, 0x85, 0x87, 0xBA):
        assert one(ecu, bytes([sid, 0x01])) == bytes([0x7F, sid, 0x11]), hex(sid)


def test_programming_services_need_the_programming_session_and_the_unlock(ecu) -> None:
    for sid in (0x27, 0x31, 0x34, 0x35, 0x36, 0x37):
        assert nrc(one(ecu, bytes([sid, 0x01]))) == 0x7F, hex(sid)  # not in this session
    one(ecu, b"\x10\x02")
    for request in (
        b"\x31\x01\xff\x00" + alfid(START, SECTOR),
        b"\x34\x00" + alfid(START, SIZE),
        b"\x35\x00" + alfid(START, SIZE),
        b"\x36\x01\x00",
        b"\x37",
    ):
        assert nrc(one(ecu, request)) == 0x33, request.hex()  # securityAccessDenied


def test_session_control_timing_suppression_and_bootloader_identity(ecu) -> None:
    assert one(ecu, b"\x10\x03") == b"\x50\x03\x00\x32\x01\xf4"  # P2 50 ms, P2* 5 s
    assert ecu.handle(b"\x10\x83") == []  # suppressPosRspMsgIndicationBit
    assert nrc(one(ecu, b"\x10\x04")) == 0x12
    assert nrc(one(ecu, b"\x10")) == 0x13

    one(ecu, b"\x10\x02")
    assert ecu.in_bootloader and did_text(ecu, DID_BOOT_STATUS) == "BOOTLOADER"
    assert did_text(ecu, 0xF180) == "SIM-BOOT-1.0"
    assert nrc(did(ecu, 0xF187)) == 0x31  # application DIDs are not there in the bootloader
    assert (
        EcuIdentity({0xF191: did_text(ecu, 0xF191)}).mismatches(TARGET.bootloader_identity_patterns)
        == ()
    )

    one(ecu, b"\x10\x01")
    assert not ecu.in_bootloader  # the application is still valid


def test_tester_present_and_reset(ecu) -> None:
    assert one(ecu, b"\x3e\x00") == b"\x7e\x00"
    assert ecu.handle(b"\x3e\x80") == []
    unlock(ecu)

    assert one(ecu, b"\x11\x01") == b"\x51\x01"

    assert ecu.resets == 1 and ecu.session == 0x01 and not ecu.unlocked
    assert ecu.handle(b"\x11\x81") == [] and ecu.resets == 2
    assert nrc(one(ecu, b"\x11\x03")) == 0x12


# --------------------------------------------------------------------------- security access


def test_seed_key_unlock_and_already_unlocked_seed(ecu) -> None:
    one(ecu, b"\x10\x02")
    seed = one(ecu, b"\x27\x01")[2:]
    assert len(seed) == 4 and seed != b"\x00" * 4

    key = SimulatedXorSeedKey().compute_key(0x01, seed)
    assert one(ecu, b"\x27\x02" + key) == b"\x67\x02" and ecu.unlocked
    assert one(ecu, b"\x27\x01") == b"\x67\x01\x00\x00\x00\x00"  # already unlocked


def test_a_key_needs_a_fresh_seed(ecu) -> None:
    one(ecu, b"\x10\x02")
    assert nrc(one(ecu, b"\x27\x02\x00\x00\x00\x00")) == 0x24  # no seed requested
    seed = one(ecu, b"\x27\x01")[2:]
    assert nrc(one(ecu, b"\x27\x02\x00\x00\x00\x00")) == 0x35  # wrong key consumes the seed
    key = SimulatedXorSeedKey().compute_key(0x01, seed)
    assert nrc(one(ecu, b"\x27\x02" + key)) == 0x24  # that seed is gone
    assert nrc(one(ecu, b"\x27\x05")) == 0x12  # another level does not exist


def test_wrong_keys_lock_out_with_a_delay(ecu, fake_clock) -> None:
    one(ecu, b"\x10\x02")
    answers = []
    for _attempt in range(3):
        one(ecu, b"\x27\x01")
        answers.append(nrc(one(ecu, b"\x27\x02\xde\xad\xbe\xef")))
    assert answers == [0x35, 0x35, 0x36]  # invalidKey twice, then exceededNumberOfAttempts

    assert nrc(one(ecu, b"\x27\x01")) == 0x37  # requiredTimeDelayNotExpired
    fake_clock.advance(9.9)
    assert nrc(one(ecu, b"\x27\x01")) == 0x37
    fake_clock.advance(0.2)
    seed = one(ecu, b"\x27\x01")[2:]
    assert one(ecu, b"\x27\x02" + SimulatedXorSeedKey().compute_key(1, seed)) == b"\x67\x02"
    assert ecu.failed_key_attempts == 0  # a correct key resets the counter


def test_the_attempt_counter_survives_a_power_cycle(ecu, fake_clock) -> None:
    one(ecu, b"\x10\x02")
    for _attempt in range(3):
        one(ecu, b"\x27\x01")
        one(ecu, b"\x27\x02\x00\x00\x00\x00")
    fake_clock.advance(60)
    ecu.power_cycle()  # the counter is persistent: the delay starts again at boot

    one(ecu, b"\x10\x02")
    assert nrc(one(ecu, b"\x27\x01")) == 0x37
    assert ecu.failed_key_attempts == 3


@pytest.mark.parametrize("relock", [b"\x10\x02", b"\x10\x01", b"\x10\x03", b"\x11\x01"])
def test_every_session_change_and_reset_relocks(ecu, relock: bytes) -> None:
    unlock(ecu)

    ecu.handle(relock)

    assert not ecu.unlocked


# --------------------------------------------------------------------------- erase and NOR rules


def test_erase_needs_whole_sectors_inside_the_flash(ecu) -> None:
    unlock(ecu)
    for address, size in (
        (START + 1, SECTOR),  # not sector aligned
        (START, SECTOR + 1),
        (START - SECTOR, SECTOR),  # outside the flash
        (START, SIZE + SECTOR),
        (START, 0),
    ):
        assert nrc(one(ecu, b"\x31\x01\xff\x00" + alfid(address, size))) == 0x31
    assert nrc(one(ecu, b"\x31\x01\xff\x00\x44")) == 0x13
    assert nrc(one(ecu, b"\x31\x01\x12\x34" + alfid(START, SECTOR))) == 0x31  # unknown routine
    assert nrc(one(ecu, b"\x31\x02\xff\x00")) == 0x12  # stop routine: not supported


def test_erasing_sets_ff_and_invalidates_the_application(ecu) -> None:
    unlock(ecu)

    assert erase(ecu, START + SECTOR, SECTOR) == [b"\x71\x01\xff\x00\x00"]

    flash = ecu.flash
    assert flash[SECTOR : 2 * SECTOR] == b"\xff" * SECTOR
    assert flash[:SECTOR] == default_simulated_image()[:SECTOR]  # other sectors untouched
    assert not ecu.application_valid
    one(ecu, b"\x11\x01")
    assert ecu.in_bootloader  # after a reset the bootloader keeps control


def test_programming_unerased_memory_is_a_programming_failure(ecu) -> None:
    unlock(ecu)
    start_download(ecu, START, BLOCK)

    answer = one(ecu, b"\x36\x01" + b"\xff" * BLOCK)  # needs 0 -> 1 on factory content

    assert nrc(answer) == 0x72
    assert nrc(one(ecu, b"\x36\x02" + b"\x00" * BLOCK)) == 0x24  # the transfer was aborted


def test_nor_flash_only_clears_bits(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    download(ecu, b"\x0f" * BLOCK)

    start_download(ecu, START, BLOCK)  # without an erase: 0x0F -> 0x0E is fine (1 -> 0)
    assert one(ecu, b"\x36\x01" + b"\x0e" * BLOCK) == b"\x76\x01"
    assert one(ecu, b"\x37") == b"\x77"
    assert ecu.flash[:BLOCK] == b"\x0e" * BLOCK

    start_download(ecu, START, BLOCK)  # 0x0E -> 0xF0 needs 0 -> 1: refused
    assert nrc(one(ecu, b"\x36\x01" + b"\xf0" * BLOCK)) == 0x72
    assert ecu.flash[:BLOCK] == b"\x0e" * BLOCK  # a refused block writes nothing


def test_slow_erase_answers_response_pending_first(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(slow_erase_pending=3), clock=fake_clock)
    unlock(ecu)

    answers = erase(ecu)

    assert answers == [b"\x7f\x31\x78"] * 3 + [b"\x71\x01\xff\x00\x00"]
    assert erase(ecu) == [b"\x71\x01\xff\x00\x00"]  # one-shot


# --------------------------------------------------------------------------- transfers


def test_request_download_and_upload_validation(ecu) -> None:
    unlock(ecu)
    assert nrc(one(ecu, b"\x34\x11" + alfid(START, SIZE))) == 0x31  # compressed/encrypted
    assert nrc(one(ecu, b"\x34\x00\x24" + bytes(8))) == 0x31  # other address/length format
    assert nrc(one(ecu, b"\x34\x00\x44")) == 0x13
    assert nrc(one(ecu, b"\x34\x00" + alfid(START - 1, 16))) == 0x31  # outside the flash
    assert nrc(one(ecu, b"\x35\x00" + alfid(START, SIZE + 1))) == 0x31

    assert start_download(ecu) == BLOCK
    assert nrc(one(ecu, b"\x34\x00" + alfid(START, SIZE))) == 0x22  # one transfer at a time
    assert nrc(one(ecu, b"\x31\x01\xff\x00" + alfid(START, SIZE))) == 0x22  # no erase meanwhile


def test_a_full_download_wraps_the_block_sequence_counter(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    image = new_image()

    sequences = download(ecu, image)

    assert len(sequences) == SIZE // BLOCK == 256
    assert sequences[:2] == [0x01, 0x02] and sequences[254:] == [0xFF, 0x00]  # wraps to 0x00
    assert ecu.flash == image


def test_block_sequence_errors_and_repeated_blocks(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    start_download(ecu, START, 3 * BLOCK)
    data = new_image()[: 3 * BLOCK]

    assert nrc(one(ecu, b"\x36\x02" + data[:BLOCK])) == 0x73  # the first block is 0x01
    assert one(ecu, b"\x36\x01" + data[:BLOCK]) == b"\x76\x01"
    assert one(ecu, b"\x36\x01" + data[:BLOCK]) == b"\x76\x01"  # repeat: confirmed again
    assert nrc(one(ecu, b"\x36\x03" + data[BLOCK : 2 * BLOCK])) == 0x73  # skipped 0x02
    assert one(ecu, b"\x36\x02" + data[BLOCK : 2 * BLOCK]) == b"\x76\x02"
    assert nrc(one(ecu, b"\x37")) == 0x24  # not complete yet
    assert nrc(one(ecu, b"\x36\x03" + b"\x00" * (BLOCK + 1))) == 0x13  # block too long
    assert one(ecu, b"\x36\x03" + data[2 * BLOCK :]) == b"\x76\x03"
    assert one(ecu, b"\x37") == b"\x77"
    assert ecu.flash[: 3 * BLOCK] == data


def test_writing_past_the_requested_size_suspends_the_transfer(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    start_download(ecu, START, 10)

    assert nrc(one(ecu, b"\x36\x01" + b"\x00" * 11)) == 0x71
    assert nrc(one(ecu, b"\x37")) == 0x24


def test_full_cycle_erase_download_check_reset_and_read_back(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    image = new_image(seed=7)
    download(ecu, image)

    assert check_memory(ecu, zlib.crc32(image)) == b"\x71\x01\xff\x01\x00"  # passed
    assert one(ecu, b"\x11\x01") == b"\x51\x01"

    assert ecu.application_valid and did_text(ecu, DID_BOOT_STATUS) == "APPLICATION"
    assert did_text(ecu, 0xF189) == f"SIM-SW-{zlib.crc32(image):08X}"  # the new software
    unlock(ecu)
    assert upload(ecu) == image


def test_a_failed_check_leaves_the_ecu_in_its_bootloader(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    image = new_image()
    download(ecu, image)

    assert check_memory(ecu, zlib.crc32(image) ^ 1) == b"\x71\x01\xff\x01\x01"  # failed
    one(ecu, b"\x11\x01")

    assert ecu.in_bootloader and not ecu.application_valid


def test_a_partial_check_does_not_validate_the_application(ecu) -> None:
    unlock(ecu)
    erase(ecu)
    image = new_image()
    download(ecu, image)

    assert check_memory(ecu, zlib.crc32(image[:SECTOR]), START, SECTOR)[-1] == 0x00
    one(ecu, b"\x11\x01")

    assert ecu.in_bootloader  # only a check over the whole flash validates


def test_an_upload_block_can_be_repeated(ecu) -> None:
    unlock(ecu)
    assert one(ecu, b"\x35\x00" + alfid(START, 300))[:2] == b"\x75\x20"
    first = one(ecu, b"\x36\x01")
    assert one(ecu, b"\x36\x01") == first  # answer lost: the same data again
    last = one(ecu, b"\x36\x02")
    assert one(ecu, b"\x36\x02") == last and len(last) == 2 + 300 - BLOCK
    assert nrc(one(ecu, b"\x36\x03")) == 0x24  # nothing left
    assert first[2:] + last[2:] == default_simulated_image()[:300]


# --------------------------------------------------------------------------- fault plan


def test_drop_link_at_block_loses_that_block_until_the_link_returns(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(drop_link_at_block=3), clock=fake_clock)
    unlock(ecu)
    erase(ecu)
    data = new_image()[: 4 * BLOCK]
    start_download(ecu, START, len(data))
    for sequence in (1, 2):
        one(ecu, bytes([0x36, sequence]) + data[(sequence - 1) * BLOCK : sequence * BLOCK])

    assert ecu.handle(b"\x36\x03" + data[2 * BLOCK : 3 * BLOCK]) == []
    assert ecu.handle(b"\x3e\x00") == [] and not ecu.link_up  # nothing answers any more

    ecu.restore_link()  # the ECU kept running: the transfer continues where it stopped
    assert one(ecu, b"\x36\x03" + data[2 * BLOCK : 3 * BLOCK]) == b"\x76\x03"
    assert one(ecu, b"\x36\x04" + data[3 * BLOCK :]) == b"\x76\x04"
    assert one(ecu, b"\x37") == b"\x77" and ecu.flash[: len(data)] == data


def test_nrc_at_block_refuses_and_aborts(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(nrc_at_block=(2, 0x72)), clock=fake_clock)
    unlock(ecu)
    erase(ecu)
    start_download(ecu, START, 3 * BLOCK)
    one(ecu, b"\x36\x01" + b"\x00" * BLOCK)

    assert one(ecu, b"\x36\x02" + b"\x00" * BLOCK) == b"\x7f\x36\x72"
    assert nrc(one(ecu, b"\x36\x02" + b"\x00" * BLOCK)) == 0x24  # aborted
    assert ecu.flash[BLOCK : 2 * BLOCK] == b"\xff" * BLOCK  # not written


def test_power_loss_at_block_keeps_the_torn_flash_and_loses_ram(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(power_loss_at_block=5), clock=fake_clock)
    unlock(ecu)
    erase(ecu)
    image = new_image()
    start_download(ecu, START, SIZE)
    for sequence in range(1, 5):
        one(ecu, bytes([0x36, sequence]) + image[(sequence - 1) * BLOCK : sequence * BLOCK])

    assert ecu.handle(b"\x36\x05" + image[4 * BLOCK : 5 * BLOCK]) == []
    assert not ecu.powered and ecu.handle(b"\x3e\x00") == []

    flash = ecu.flash
    assert flash[: 4 * BLOCK] == image[: 4 * BLOCK]  # blocks 1-4 programmed
    half = BLOCK // 2
    assert flash[4 * BLOCK : 4 * BLOCK + half] == image[4 * BLOCK : 4 * BLOCK + half]  # torn
    assert flash[4 * BLOCK + half :] == b"\xff" * (SIZE - 4 * BLOCK - half)

    ecu.power_on()
    assert ecu.session == 0x01 and not ecu.unlocked  # RAM state is gone
    assert ecu.in_bootloader and did_text(ecu, DID_BOOT_STATUS) == "BOOTLOADER"
    assert ecu.flash == flash  # flash survived
    unlock(ecu)  # recovery can start again (the fault fired once)
    erase(ecu)
    download(ecu, image)
    assert check_memory(ecu, zlib.crc32(image))[-1] == 0x00


def test_corrupt_readback_flips_one_byte_once(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(corrupt_readback_at_block=2), clock=fake_clock)
    unlock(ecu)

    first = upload(ecu)
    second = upload(ecu)

    expected = default_simulated_image()
    assert second == expected
    differences = [i for i, (a, b) in enumerate(zip(first, expected, strict=True)) if a != b]
    assert differences == [BLOCK]  # the first byte of block 2


def test_bad_key_refuses_a_correct_key_once(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(bad_key=True), clock=fake_clock)
    one(ecu, b"\x10\x02")
    seed = one(ecu, b"\x27\x01")[2:]

    assert nrc(one(ecu, b"\x27\x02" + SimulatedXorSeedKey().compute_key(1, seed))) == 0x35
    assert ecu.failed_key_attempts == 1
    seed = one(ecu, b"\x27\x01")[2:]
    assert one(ecu, b"\x27\x02" + SimulatedXorSeedKey().compute_key(1, seed)) == b"\x67\x02"


def test_refuse_upload_refuses_once(fake_clock) -> None:
    ecu = VirtualProgrammableEcu(faults=FaultPlan(refuse_upload=True), clock=fake_clock)
    unlock(ecu)

    assert one(ecu, b"\x35\x00" + alfid(START, SIZE)) == b"\x7f\x35\x70"
    assert upload(ecu) == default_simulated_image()


@pytest.mark.parametrize(
    "plan",
    [
        {"drop_link_at_block": 0},
        {"power_loss_at_block": -1},
        {"corrupt_readback_at_block": 0},
        {"nrc_at_block": (0, 0x72)},
        {"nrc_at_block": (1, 0x78)},  # responsePending is not a final answer
        {"slow_erase_pending": -1},
    ],
)
def test_invalid_fault_plans_are_refused(plan: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FaultPlan(**plan)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- seed-key, config


def test_the_simulated_seed_key_is_the_target_provider() -> None:
    provider = SimulatedXorSeedKey()

    assert isinstance(provider, SeedKeyProvider)
    assert provider.provider_id == TARGET.seed_key_provider_id and provider.simulated is True
    assert provider.compute_key(0x01, b"\x00\x00\x00\x00") == b"SIM!"
    with pytest.raises(ValueError):
        provider.compute_key(0x03, b"\x01\x02\x03\x04")
    with pytest.raises(ValueError):
        provider.compute_key(0x01, b"")


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="image size"):
        ProgrammableEcuConfig(initial_image=b"\x00" * 10)
    for length in (2, 4096):
        with pytest.raises(ValueError, match="max_block_length"):
            ProgrammableEcuConfig(max_block_length=length)
    with pytest.raises(ValueError):
        ProgrammableEcuConfig(security_max_attempts=0)
    custom = VirtualProgrammableEcu(ProgrammableEcuConfig(initial_image=new_image()))
    assert custom.flash == new_image() and custom.application_valid
