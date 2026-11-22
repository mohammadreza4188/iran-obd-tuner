"""Bench link (Milestone 7c): simulated only; real threads on python-can's virtual bus.

Every bench link here runs against :class:`VirtualProgrammableEcu` on the in-process
``virtual`` interface - the only bench interface the build has. The configuration tests
need no python-can at all.
"""

from __future__ import annotations

import hashlib
import uuid
import zlib
from datetime import UTC, datetime

import pytest

from iran_obd_tuner.core import bench
from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BackupReceipt,
    BenchAttestation,
    EcuIdentity,
    FlashAuthorization,
    bench_flash_policy,
)
from iran_obd_tuner.core.exceptions import BenchTargetNotQualifiedError, ConfigurationError
from iran_obd_tuner.core.traffic import TrafficLog
from iran_obd_tuner.hardware.bench_link import (
    BENCH_SIM_CHANNEL_PREFIX,
    BENCH_SIMULATED_INTERFACE,
    BenchLink,
    BenchLinkConfig,
)
from iran_obd_tuner.hardware.bench_programmer import BenchTiming, BenchTransferError
from iran_obd_tuner.hardware.uds_sim_programmable import (
    FaultPlan,
    ProgrammableEcuConfig,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    default_simulated_image,
)

REGION = SIMULATED_BENCH_TARGET.regions[0]
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
DIDS = (0xF187, 0xF189, 0xF18C, 0xF191, 0xFD00)


# --------------------------------------------------------------------------- configuration


@pytest.mark.parametrize(
    "interface",
    ["slcan", "pcan", "kvaser", "socketcan", "gs_usb", "virtual", "virtual-sim", "VIRTUAL", ""],
)
def test_every_other_interface_is_refused(interface: str) -> None:
    with pytest.raises(BenchTargetNotQualifiedError, match="only exists against the simulator"):
        BenchLinkConfig(interface=interface)


@pytest.mark.parametrize("channel", ["COM5", "PCAN_USBBUS1", "can0", "0", "/dev/ttyACM0", ""])
def test_real_looking_channels_are_refused(channel: str) -> None:
    with pytest.raises(BenchTargetNotQualifiedError, match="not a simulator channel"):
        BenchLinkConfig(channel=channel)


def test_identifier_validation_and_defaults() -> None:
    for bad in ({"request_id": 0x800}, {"response_id": -1}, {"request_id": 0x7E8}):
        with pytest.raises(ConfigurationError):
            BenchLinkConfig(**bad)  # type: ignore[arg-type]
    config = BenchLinkConfig()
    assert config.interface == BENCH_SIMULATED_INTERFACE and config.simulated
    link = BenchLink()
    assert link.channel.startswith(BENCH_SIM_CHANNEL_PREFIX) and link.simulated
    with pytest.raises(RuntimeError, match="not open"):
        _ = link.programmer


# --------------------------------------------------------------------------- over the virtual bus


def _authorization(image: bytes) -> FlashAuthorization:
    attest = BenchAttestation(
        ecu_on_bench_out_of_vehicle=True,
        supply_stable_and_current_limited=True,
        wiring_verified=True,
        owner_approval_recorded=True,
        operator="test",
        confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
        attested_at=NOW,
        simulated=True,
    )
    return FlashAuthorization(
        policy=bench_flash_policy(attest),
        attestation=attest,
        target_id=SIMULATED_BENCH_TARGET.target_id,
        image_sha256=hashlib.sha256(image).hexdigest(),
        simulated=True,
        nonce=uuid.uuid4().hex,
        issued_at=NOW,
        _token=bench._MINT,
    )


def _receipt(backup: bytes, identity: EcuIdentity) -> BackupReceipt:
    return BackupReceipt(
        path="SIMULATED-backup.bin",
        sha256=hashlib.sha256(backup).hexdigest(),
        size=len(backup),
        regions=SIMULATED_BENCH_TARGET.regions,
        target_id=SIMULATED_BENCH_TARGET.target_id,
        identity_fingerprint=identity.fingerprint(),
        unit_fingerprint=SIMULATED_BENCH_TARGET.unit_fingerprint(identity) or "e" * 64,
        simulated=True,
        created_at=NOW,
        _token=bench._MINT,
    )


def _image(seed: int = 9) -> bytes:
    return bytes((i * 17 + seed) & 0xFF for i in range(SIMULATED_BENCH_TARGET.image_size))


def _ecu(**faults: object) -> VirtualProgrammableEcu:
    # 1 KiB blocks keep the threaded bus test quick (64 blocks for the whole image)
    return VirtualProgrammableEcu(
        ProgrammableEcuConfig(max_block_length=1026),
        FaultPlan(**faults),  # type: ignore[arg-type]
    )


@pytest.fixture
def channel(request: pytest.FixtureRequest) -> str:
    """A simulator channel; skips the bus tests (only) without the ``[can]`` extra."""
    pytest.importorskip("can", reason="needs the optional [can] extra")
    return f"{BENCH_SIM_CHANNEL_PREFIX}{request.node.name[:40]}"


def test_a_full_flash_over_the_bench_link(channel: str) -> None:
    image = _image()
    log = TrafficLog()
    ecu = _ecu()
    with BenchLink(BenchLinkConfig(channel=channel), ecu=ecu, traffic_log=log) as link:
        programmer = link.programmer
        auth = _authorization(image)
        programmer.check_link(2)
        identity = programmer.read_identity(DIDS)
        programmer.enter_programming_session(authorization=auth)
        programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
        backup = programmer.upload_region(REGION, authorization=auth)
        receipt = _receipt(backup, identity)
        programmer.erase_region(REGION, authorization=auth, backup=receipt)
        programmer.download_region(REGION, image, authorization=auth, backup=receipt)
        assert programmer.check_memory(REGION, zlib.crc32(image), authorization=auth)
        programmer.hard_reset(authorization=auth)
        after = programmer.read_identity(DIDS)

    assert backup == default_simulated_image()
    assert after.dids[0xF189] == f"SIM-SW-{zlib.crc32(image):08X}"
    assert ecu.flash == image and ecu.application_valid
    assert all(entry.simulated for entry in log.snapshot())
    assert all(entry.adapter == f"bench:{BENCH_SIMULATED_INTERFACE}" for entry in log.snapshot())


def test_power_loss_then_recovery_from_the_backup_on_a_new_link(channel: str) -> None:
    """Hardware-level recovery: the half-written ECU only runs its bootloader; a new link
    restores the backup image (the orchestrator of 7d adds the journal around this)."""
    image = _image()
    ecu = _ecu(power_loss_at_block=10)
    timing = BenchTiming(p2_s=0.3)
    with BenchLink(BenchLinkConfig(channel=channel), ecu=ecu, timing=timing) as link:
        programmer = link.programmer
        auth = _authorization(image)
        identity = programmer.read_identity(DIDS)
        programmer.enter_programming_session(authorization=auth)
        programmer.unlock(authorization=auth, provider=SimulatedXorSeedKey())
        backup = programmer.upload_region(REGION, authorization=auth)
        receipt = _receipt(backup, identity)
        programmer.erase_region(REGION, authorization=auth, backup=receipt)
        with pytest.raises(BenchTransferError):
            programmer.download_region(REGION, image, authorization=auth, backup=receipt)
    assert not ecu.powered

    ecu.power_on()
    assert ecu.in_bootloader
    with BenchLink(BenchLinkConfig(channel=channel + "-2"), ecu=ecu) as link:
        programmer = link.programmer
        recovery = _authorization(backup)  # recovery is authorised for the backup image
        assert programmer.read_identity(DIDS).dids[0xFD00] == "BOOTLOADER"
        programmer.enter_programming_session(authorization=recovery)
        programmer.unlock(authorization=recovery, provider=SimulatedXorSeedKey())
        programmer.erase_region(REGION, authorization=recovery, backup=receipt)
        programmer.download_region(REGION, backup, authorization=recovery, backup=receipt)
        assert programmer.check_memory(REGION, zlib.crc32(backup), authorization=recovery)
        programmer.hard_reset(authorization=recovery)
        restored = programmer.read_identity(DIDS)

    assert ecu.flash == default_simulated_image() and ecu.application_valid
    assert restored.dids[0xFD00] == "APPLICATION"


def test_closing_the_link_stops_the_simulator(channel: str) -> None:
    link = BenchLink(BenchLinkConfig(channel=channel))
    link.open()
    link.open()  # idempotent
    server = link._server  # noqa: SLF001 - checking the cleanup
    assert server is not None
    link.close()
    link.close()  # idempotent

    assert link._server is None  # noqa: SLF001
    assert not server._thread.is_alive()  # noqa: SLF001
    with pytest.raises(RuntimeError, match="not open"):
        _ = link.programmer
