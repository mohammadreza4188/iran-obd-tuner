"""``iran-obd-tuner bench flash-sim`` / ``bench journal`` (Milestone 7e), end to end.

Every run goes through ``app/bench_flash.py`` to the real gate, vault, journal and
orchestrator, the bench programmer and the virtual programmable ECU on python-can's
in-process ``virtual`` bus. Each ``main()`` call is a separate "process": the simulated ECU
survives between calls only through its saved state in the bench directory. Skipped without
the ``[can]`` extra.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("can", reason="needs the optional [can] extra")

from iran_obd_tuner.app.bench_flash import (  # noqa: E402
    ECU_STATE_NAME,
    SimulatedEcuStore,
    simulated_attestation,
)
from iran_obd_tuner.app.cli import EXIT_RECOVERY_REQUIRED, main  # noqa: E402
from iran_obd_tuner.core.bench import SIMULATED_BENCH_TARGET, EcuIdentity  # noqa: E402
from iran_obd_tuner.hardware.uds_sim_programmable import default_simulated_image  # noqa: E402
from iran_obd_tuner.tuning.demo import DEMO_ROM_NAME, build_demo_rom  # noqa: E402
from iran_obd_tuner.tuning.flash_journal import ARCHIVE_DIR, JOURNAL_NAME, FlashJournal  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SHA = r"[0-9a-f]{64}"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    code, out, _err = run(capsys, *argv, "--json")
    return code, json.loads(out)


def ecu_flash(bench: Path) -> bytes:
    state = SimulatedEcuStore(bench).load()
    assert state is not None
    return state.flash


# --------------------------------------------------------------------------- normal flash


def test_flash_the_default_demo_rom(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bench = tmp_path / "bench"

    code, out, _ = run(capsys, "bench", "flash-sim", "--output", str(bench))

    assert code == 0, out
    assert out.startswith("SIMULATED bench flash")
    for stage in ("[pre_flight]", "[backup]", "[programming]", "[verify]"):
        assert stage in out
    assert out.index("[backup]") < out.index("[programming]") < out.index("[verify]")
    assert "image (SIMULATED demo): simulated_demo_rom.bin" in out
    assert (
        "journal states: pending -> backup_verified -> erasing -> transferring -> verifying "
        "-> completed" in out
    )
    assert "result: COMPLETED" in out and "journal now: CLEAN" in out
    backups = sorted((bench / "backups").glob("SIMULATED-backup-*.bin"))
    assert len(backups) == 1
    sha = hashlib.sha256(backups[0].read_bytes()).hexdigest()
    assert f"backup: {backups[0]} (65536 bytes)" in out
    assert f"backup sha256: {sha}" in out
    assert backups[0].read_bytes() == default_simulated_image()  # the factory content
    assert ecu_flash(bench) == build_demo_rom()  # the simulated ECU now holds the demo
    assert out.isascii()


def test_flash_json_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc = run_json(capsys, "bench", "flash-sim", "--output", str(tmp_path))

    assert code == 0
    assert doc["kind"] == "bench_flash_simulation" and doc["simulated"] is True
    result = doc["result"]
    assert result["state"] == "completed" and result["ok"] is True
    assert result["image"]["simulated"] is True
    assert result["backup"]["size"] == 65536 and len(result["backup"]["sha256"]) == 64
    assert result["identity_after"]["F187"] == "SIM-PART-0001"
    assert Path(result["archive_path"]).parent.name == ARCHIVE_DIR
    assert doc["journal"]["status"] == "clean" and doc["next_step"] is None


def test_two_flashes_in_a_row_keep_both_backups(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "bench", "flash-sim", "--output", str(tmp_path))[0] == 0
    assert run(capsys, "bench", "flash-sim", "--output", str(tmp_path))[0] == 0

    backups = sorted((tmp_path / "backups").glob("SIMULATED-backup-*.bin"))
    contents = {b.read_bytes() for b in backups}
    assert len(backups) == 2 and contents == {default_simulated_image(), build_demo_rom()}
    assert len(list((tmp_path / ARCHIVE_DIR).glob("*.json"))) == 2


# --------------------------------------------------------------------------- fault -> recovery


def test_power_loss_then_journal_show_then_recover(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bench = tmp_path / "bench dir"  # a space: the printed command must be quoted

    code, out, _ = run(
        capsys, "bench", "flash-sim", "--output", str(bench), "--fault", "power-loss-at-block=20"
    )

    assert code == EXIT_RECOVERY_REQUIRED == 3
    assert "injected simulator fault: power-loss-at-block=20" in out
    assert "result: INTERRUPTED - the simulated ECU memory may be incomplete" in out
    assert "journal now: DIRTY" in out and "RECOVERY REQUIRED" in out
    recover = f'iran-obd-tuner bench flash-sim --output "{bench}" --recover'
    assert recover in out
    torn = ecu_flash(bench)
    assert torn not in (default_simulated_image(), build_demo_rom())  # half written

    # the dirty journal, as text and as JSON
    code, out, _ = run(capsys, "bench", "journal", "show", str(bench))
    assert code == 0
    assert "flash journal: DIRTY" in out and "state:     interrupted" in out
    assert "progress:  programming" in out and f"next step: {recover}" in out
    code, doc = run_json(capsys, "bench", "journal", "show", str(bench))
    journal = doc["journal"]
    assert journal["status"] == "dirty" and journal["can_recover"] is True
    assert journal["progress"]["done_bytes"] >= 8 * 1024  # saved every 8 blocks
    assert Path(journal["backup"]["path"]).read_bytes() == default_simulated_image()
    assert doc["next_step"] == recover

    # a new flash is refused while the journal is dirty, and nothing is sent
    code, out, err = run(capsys, "bench", "flash-sim", "--output", str(bench))
    assert code == 1 and "the flash journal is dirty" in err and recover in err
    assert ecu_flash(bench) == torn

    # the stale-discard path never accepts a dirty journal
    code, _, err = run(capsys, "bench", "journal", "discard-stale", str(bench))
    assert code == 1 and "only a stale journal can be discarded" in err

    # recovery restores the verified backup
    code, out, _ = run(capsys, "bench", "flash-sim", "--output", str(bench), "--recover")
    assert code == 0, out
    assert "[recovery]" in out and "[verify]" in out
    assert "journal states: interrupted -> recovering -> recovered" in out
    assert "result: RECOVERED" in out and "journal now: CLEAN" in out
    assert ecu_flash(bench) == default_simulated_image()

    code, doc = run_json(capsys, "bench", "journal", "show", str(bench))
    assert doc["journal"]["status"] == "clean"
    archived = [json.loads(p.read_text("utf-8")) for p in (bench / ARCHIVE_DIR).glob("*.json")]
    assert [a["state"] for a in archived] == ["recovered"]

    # and the bench is usable again
    assert run(capsys, "bench", "flash-sim", "--output", str(bench))[0] == 0


@pytest.mark.parametrize("fault", ["drop-at-block=3", "nrc72-at-block=2"])
def test_other_transfer_faults_are_recoverable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    code, doc = run_json(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--fault", fault)
    assert code == 3
    assert doc["result"]["state"] == "interrupted" and doc["result"]["fault_fired"] is True
    assert doc["journal"]["status"] == "dirty" and doc["next_step"].endswith("--recover")

    code, doc = run_json(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--recover")
    assert code == 0 and doc["result"]["state"] == "recovered"
    assert ecu_flash(tmp_path) == default_simulated_image()


def test_a_bad_key_stops_before_the_backup_and_leaves_the_ecu_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "bench", "flash-sim", "--output", str(tmp_path), "--fault", "bad-key"
    )

    assert code == 1
    assert "result: ABORTED before any erase - the simulated ECU memory is untouched" in out
    assert "journal now: CLEAN" in out and "RECOVERY REQUIRED" not in out
    assert not list((tmp_path / "backups").glob("*.bin"))
    assert ecu_flash(tmp_path) == default_simulated_image()
    assert SimulatedEcuStore(tmp_path).load().failed_key_attempts == 1  # type: ignore[union-attr]


def test_a_corrupt_read_back_is_interrupted_and_recovered_on_the_same_unit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a fault in ``verifying`` the simulated ECU runs the new, checked image (its
    software DID changed). Milestone 7e.1 binds the backup to the unit (hardware number and
    serial), so the recovery restores the backup instead of failing for ever."""
    code, doc = run_json(
        capsys, "bench", "flash-sim", "--output", str(tmp_path), "--fault", "corrupt-readback"
    )
    assert code == 3 and doc["result"]["state"] == "interrupted"
    assert "read-back" in doc["result"]["error"] and doc["result"]["fault_fired"] is True
    after_flash = ecu_flash(tmp_path)
    assert after_flash == build_demo_rom()  # written and checked; only the read-back lied

    code, doc = run_json(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--recover")

    assert code == 0 and doc["result"]["state"] == "recovered", doc["result"]["error"]
    assert doc["journal"]["status"] == "clean"
    assert ecu_flash(tmp_path) == default_simulated_image()  # the backup is back


# --------------------------------------------------------------------------- stale journal


def _leave_a_stale_journal(bench: Path) -> None:
    """A journal a crashed process left in ``pending`` (nothing was erased)."""
    FlashJournal.begin(
        bench,
        transaction_id=uuid.uuid4().hex,
        target_id=SIMULATED_BENCH_TARGET.target_id,
        image_sha256=hashlib.sha256(build_demo_rom()).hexdigest(),
        image_name=DEMO_ROM_NAME,
        simulated=True,
        attestation=simulated_attestation(),
        authorization_nonce=uuid.uuid4().hex,
        identity=EcuIdentity({0xF191: "SIM-HW-1.0"}),
        unverified_checksum_override=False,
    )


def test_a_stale_journal_blocks_until_it_is_discarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _leave_a_stale_journal(tmp_path)

    code, out, _ = run(capsys, "bench", "journal", "show", str(tmp_path))
    assert code == 0 and "flash journal: STALE" in out
    assert f'next step: iran-obd-tuner bench journal discard-stale "{tmp_path}"' in out

    code, _, err = run(capsys, "bench", "flash-sim", "--output", str(tmp_path))
    assert code == 1 and "the flash journal is stale" in err
    code, _, err = run(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--recover")
    assert code == 1 and "nothing to recover" in err

    code, out, _ = run(capsys, "bench", "journal", "discard-stale", str(tmp_path))
    assert code == 0 and "stale flash journal archived to" in out
    archived = [json.loads(p.read_text("utf-8")) for p in (tmp_path / ARCHIVE_DIR).glob("*.json")]
    assert [a["state"] for a in archived] == ["aborted"]
    assert not (tmp_path / JOURNAL_NAME).exists()

    assert run(capsys, "bench", "flash-sim", "--output", str(tmp_path))[0] == 0


def test_discarding_a_clean_journal_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "bench", "journal", "discard-stale", str(tmp_path))
    assert code == 1 and "this one is clean" in err


def test_a_corrupt_journal_blocks_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / JOURNAL_NAME).write_text("{broken", encoding="utf-8")

    code, out, _ = run(capsys, "bench", "journal", "show", str(tmp_path))
    assert code == 0 and "flash journal: CORRUPT" in out and "by hand" in out
    assert run(capsys, "bench", "flash-sim", "--output", str(tmp_path))[0] == 1
    assert run(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--recover")[0] == 1
    assert run(capsys, "bench", "journal", "discard-stale", str(tmp_path))[0] == 1
    assert (tmp_path / JOURNAL_NAME).read_text("utf-8") == "{broken"  # never touched


# --------------------------------------------------------------------------- checksums


@pytest.fixture
def demo_rom_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    assert main(["bench", "demo", "--output", str(tmp_path / "demo")]) == 0
    capsys.readouterr()
    return tmp_path / "demo" / DEMO_ROM_NAME


def test_unverified_checksums_are_refused_without_the_simulation_override(
    tmp_path: Path, demo_rom_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bench = tmp_path / "bench"
    code, _, err = run(
        capsys, "bench", "flash-sim", "--output", str(bench), "--rom", str(demo_rom_file)
    )

    assert code == 2
    assert "the flash plan refuses this image" in err and "not_configured" in err
    assert not bench.exists()  # nothing was started, written or sent


def test_the_simulation_override_is_explicit_and_recorded(
    tmp_path: Path, demo_rom_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bench = tmp_path / "bench"
    code, out, _ = run(
        capsys,
        "bench",
        "flash-sim",
        "--output",
        str(bench),
        "--rom",
        str(demo_rom_file),
        "--allow-unverified-checksum-in-simulation",
    )

    assert code == 0, out
    assert "image (file): simulated_demo_rom.bin" in out
    assert "WARNING: flashed with an unverified checksum" in out
    (archived,) = (bench / ARCHIVE_DIR).glob("*.json")
    assert json.loads(archived.read_text("utf-8"))["unverified_checksum_override"] is True


def test_a_rom_with_valid_checksum_definitions_needs_no_override(
    tmp_path: Path, demo_rom_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    maps = demo_rom_file.parent / "simulated_demo_maps.json"
    code, doc = run_json(
        capsys,
        "bench",
        "flash-sim",
        "--output",
        str(tmp_path / "bench"),
        "--rom",
        str(demo_rom_file),
        "--maps",
        str(maps),
    )
    assert code == 0 and doc["result"]["image"]["unverified_checksum_override"] is False


def test_a_damaged_checksum_is_refused(
    tmp_path: Path, demo_rom_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    damaged = bytearray(demo_rom_file.read_bytes())
    damaged[0x2000] ^= 0xFF
    rom = tmp_path / "damaged.bin"
    rom.write_bytes(bytes(damaged))
    maps = demo_rom_file.parent / "simulated_demo_maps.json"

    code, _, err = run(
        capsys,
        "bench",
        "flash-sim",
        "--output",
        str(tmp_path / "b"),
        "--rom",
        str(rom),
        "--maps",
        str(maps),
    )
    assert code == 2 and "invalid" in err


# --------------------------------------------------------------------------- usage errors


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--fault", "explode"], "unknown fault"),
        (["--fault", "power-loss-at-block=999"], "N = 1..64"),
        (["--fault", "bad-key=3"], "takes no block number"),
        (["--recover", "--fault", "bad-key"], "--recover restores"),
        (["--recover", "--rom", "x.bin"], "--recover restores"),
        (["--maps", "x.json"], "--maps needs --rom"),
        (["--rom", "missing.bin"], "file not found"),
    ],
)
def test_usage_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    code, _, err = run(capsys, "bench", "flash-sim", "--output", str(tmp_path / "b"), *argv)
    assert code == 2 and message in err
    assert not (tmp_path / "b").exists()


def test_recover_on_a_clean_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "bench", "flash-sim", "--output", str(tmp_path), "--recover")
    assert code == 1 and "nothing to recover" in err


def test_a_damaged_simulated_ecu_state_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ECU_STATE_NAME).write_text("{}", encoding="utf-8")
    code, _, err = run(capsys, "bench", "flash-sim", "--output", str(tmp_path))
    assert code == 1 and "simulated bench ECU state" in err
    assert not (tmp_path / JOURNAL_NAME).exists()
