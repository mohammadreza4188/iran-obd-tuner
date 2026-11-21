"""End to end (Milestone 7d): gate -> orchestrator -> bench link -> virtual programmable ECU.

Real threads on python-can's in-process ``virtual`` bus, the real vault and journal on disk:
a normal flash, then a power loss mid-transfer and a recovery on a new link to the same,
half-written simulated ECU. Skipped without the ``[can]`` extra.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.core.bench import (  # noqa: E402
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BenchAttestation,
    JournalState,
    bench_flash_policy,
)
from iran_obd_tuner.core.traffic import TrafficLog  # noqa: E402
from iran_obd_tuner.hardware.bench_link import (  # noqa: E402
    BENCH_SIM_CHANNEL_PREFIX,
    BenchLink,
    BenchLinkConfig,
)
from iran_obd_tuner.hardware.bench_programmer import BenchTiming  # noqa: E402
from iran_obd_tuner.hardware.uds_sim_programmable import (  # noqa: E402
    FaultPlan,
    ProgrammableEcuConfig,
    SimulatedXorSeedKey,
    VirtualProgrammableEcu,
    default_simulated_image,
)
from iran_obd_tuner.tuning.backup_vault import BackupVault  # noqa: E402
from iran_obd_tuner.tuning.demo import load_demo  # noqa: E402
from iran_obd_tuner.tuning.flash_gate import (  # noqa: E402
    authorize_bench_flash,
    authorize_bench_recovery,
)
from iran_obd_tuner.tuning.flash_journal import AssessmentStatus  # noqa: E402
from iran_obd_tuner.tuning.flash_orchestrator import BenchFlashOrchestrator  # noqa: E402
from iran_obd_tuner.tuning.flash_plan import build_flash_plan  # noqa: E402

ATTESTATION = BenchAttestation(
    ecu_on_bench_out_of_vehicle=True,
    supply_stable_and_current_limited=True,
    wiring_verified=True,
    owner_approval_recorded=True,
    operator="end-to-end test",
    confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
    attested_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
    simulated=True,
)
POLICY = bench_flash_policy(ATTESTATION)


def _ecu(**faults: object) -> VirtualProgrammableEcu:
    # 1 KiB blocks keep the threaded test quick (64 blocks per image)
    return VirtualProgrammableEcu(ProgrammableEcuConfig(max_block_length=1026), FaultPlan(**faults))  # type: ignore[arg-type]


@pytest.fixture
def setup(tmp_path: Path, request: pytest.FixtureRequest):
    rom, definitions = load_demo()
    plan = build_flash_plan(rom, SIMULATED_BENCH_TARGET, checksum_blocks=definitions.checksums)
    orchestrator = BenchFlashOrchestrator(
        tmp_path / "journal", BackupVault(tmp_path / "backups"), progress_every_blocks=8
    )
    channel = f"{BENCH_SIM_CHANNEL_PREFIX}{request.node.name[:30]}"
    return plan, orchestrator, tmp_path, channel


def test_flash_the_demo_rom_end_to_end(setup) -> None:
    plan, orchestrator, root, channel = setup
    ecu = _ecu()
    log = TrafficLog()
    authorization = authorize_bench_flash(
        POLICY, ATTESTATION, plan, backup_dir=root / "backups", link_simulated=True
    )

    with BenchLink(BenchLinkConfig(channel=channel), ecu=ecu, traffic_log=log) as link:
        outcome = orchestrator.flash(link.programmer, plan, authorization, SimulatedXorSeedKey())

    assert outcome.state is JournalState.COMPLETED, outcome.error
    assert ecu.flash == plan.image and ecu.application_valid
    backups = sorted((root / "backups").glob("SIMULATED-backup-*.bin"))
    assert [b.read_bytes() for b in backups] == [default_simulated_image()]
    assert orchestrator.assess().status is AssessmentStatus.CLEAN
    assert all(entry.simulated for entry in log.snapshot())
    assert max(len(entry.text) for entry in log.snapshot()) < 60  # header-only traffic


def test_power_loss_then_recovery_on_a_new_link_end_to_end(setup) -> None:
    plan, orchestrator, root, channel = setup
    ecu = _ecu(power_loss_at_block=20)
    authorization = authorize_bench_flash(
        POLICY, ATTESTATION, plan, backup_dir=root / "backups", link_simulated=True
    )
    timing = BenchTiming(p2_s=0.3)

    with BenchLink(BenchLinkConfig(channel=channel), ecu=ecu, timing=timing) as link:
        interrupted = orchestrator.flash(
            link.programmer, plan, authorization, SimulatedXorSeedKey()
        )

    assert interrupted.state is JournalState.INTERRUPTED
    assessment = orchestrator.assess()
    assert assessment.status is AssessmentStatus.DIRTY and assessment.can_recover
    journal = json.loads(assessment.journal_path.read_text("utf-8"))
    assert journal["progress"]["done_bytes"] >= 8 * 1024  # saved every 8 blocks
    ecu.power_on()  # the operator restores the bench supply

    recovery = authorize_bench_recovery(
        POLICY,
        ATTESTATION,
        SIMULATED_BENCH_TARGET,
        assessment.backup_sha256,  # type: ignore[arg-type]
        link_simulated=True,
    )
    with BenchLink(BenchLinkConfig(channel=channel + "-r"), ecu=ecu) as link:
        recovered = orchestrator.recover(link.programmer, recovery, SimulatedXorSeedKey())

    assert recovered.state is JournalState.RECOVERED, recovered.error
    assert ecu.flash == default_simulated_image() and ecu.application_valid
    assert orchestrator.assess().status is AssessmentStatus.CLEAN
