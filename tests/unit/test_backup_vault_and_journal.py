"""Backup vault and Flash Transaction Journal (Milestone 7d): files, atomicity, fail-closed."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iran_obd_tuner.core.bench import (
    BENCH_CONFIRMATION_PHRASE,
    SIMULATED_BENCH_TARGET,
    BackupReceipt,
    BenchAttestation,
    EcuIdentity,
    JournalState,
)
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.tuning.backup_vault import (
    MANIFEST_KIND,
    BackupError,
    BackupVault,
    receipt_record,
)
from iran_obd_tuner.tuning.flash_journal import (
    ARCHIVE_DIR,
    JOURNAL_NAME,
    LOCK_NAME,
    TEMP_NAME,
    AssessmentStatus,
    FlashJournal,
    JournalError,
    JournalInspector,
    JournalLock,
    JournalLockedError,
)

TARGET = SIMULATED_BENCH_TARGET
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
IDENTITY = EcuIdentity({0xF187: "SIM-PART-0001", 0xF191: "SIM-HW-1.0", 0xF18C: "SIM-SERIAL-42"})
DATA = bytes((i * 5 + 1) & 0xFF for i in range(TARGET.image_size))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def make_writable(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def store(vault: BackupVault, first: bytes = DATA, second: bytes = DATA) -> BackupReceipt:
    return vault.store(
        first, second, target=TARGET, identity=IDENTITY, regions=TARGET.regions, simulated=True
    )


ATTESTATION = BenchAttestation(
    ecu_on_bench_out_of_vehicle=True,
    supply_stable_and_current_limited=True,
    wiring_verified=True,
    owner_approval_recorded=True,
    operator="bench operator",
    confirmation_phrase=BENCH_CONFIRMATION_PHRASE,
    attested_at=NOW,
    simulated=True,
)


def begin(directory: Path, clock=None) -> FlashJournal:
    return FlashJournal.begin(
        directory,
        transaction_id="0" * 31 + "1",
        target_id=TARGET.target_id,
        image_sha256="a" * 64,
        image_name="image.bin",
        simulated=True,
        attestation=ATTESTATION,
        authorization_nonce="n" * 32,
        identity=IDENTITY,
        unverified_checksum_override=False,
        now=clock or Clock(),
    )


# --------------------------------------------------------------------------- vault


def test_a_backup_is_written_once_labelled_and_read_back(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path / "backups", now=Clock())

    receipt = store(vault)

    image = Path(receipt.path)
    manifest = json.loads(image.with_name(image.stem + ".manifest.json").read_text("utf-8"))
    assert image.name.startswith("SIMULATED-backup-simulated.bench_ecu_v1-")
    assert image.read_bytes() == DATA and receipt.size == len(DATA)
    assert manifest["kind"] == MANIFEST_KIND and manifest["simulated"] is True
    assert manifest["sha256"] == receipt.sha256 == __import__("hashlib").sha256(DATA).hexdigest()
    assert manifest["identity_fingerprint"] == IDENTITY.fingerprint()
    assert not os.access(image, os.W_OK)  # read-only after writing
    vault.verify(receipt)
    assert vault.read(receipt) == DATA


def test_differing_read_outs_write_nothing(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path / "backups")
    second = bytearray(DATA)
    second[100] ^= 0x01

    with pytest.raises(BackupError, match="differ"):
        store(vault, DATA, bytes(second))

    assert not (tmp_path / "backups").exists()


def test_a_backup_file_is_never_overwritten(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=lambda: NOW)  # same name twice
    store(vault)

    with pytest.raises(BackupError, match="refusing to overwrite"):
        store(vault)


def test_a_backup_changed_after_its_receipt_is_refused(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=Clock())
    receipt = store(vault)
    image = Path(receipt.path)
    make_writable(image)
    tampered = bytearray(DATA)
    tampered[0] ^= 0xFF
    image.write_bytes(bytes(tampered))

    with pytest.raises(BackupError, match="changed on disk"):
        vault.verify(receipt)


def test_a_manifest_that_disagrees_is_refused(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=Clock())
    receipt = store(vault)
    image = Path(receipt.path)
    manifest_path = image.with_name(image.stem + ".manifest.json")
    make_writable(manifest_path)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["identity_fingerprint"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError, match="identity_fingerprint"):
        vault.verify(receipt)


def test_a_missing_backup_is_refused(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=Clock())
    receipt = store(vault)
    make_writable(Path(receipt.path))
    Path(receipt.path).unlink()

    with pytest.raises(BackupError, match="cannot be read back"):
        vault.read(receipt)


def test_a_read_back_that_differs_from_what_was_written_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.read_bytes

    def flaky(self: Path) -> bytes:
        data = real(self)
        return data[:-1] + bytes([data[-1] ^ 1]) if self.suffix == ".bin" else data

    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(BackupError, match="changed on disk"):
        store(BackupVault(tmp_path, now=Clock()))


def test_a_journal_record_reissues_a_verified_receipt(tmp_path: Path) -> None:
    vault = BackupVault(tmp_path, now=Clock())
    receipt = store(vault)
    record = json.loads(json.dumps(receipt_record(receipt)))  # through the journal's JSON

    again = vault.receipt_from_record(record)

    assert again.sha256 == receipt.sha256 and again.regions == receipt.regions
    with pytest.raises(BackupError, match="incomplete"):
        vault.receipt_from_record({"path": receipt.path})
    record["sha256"] = "0" * 64
    with pytest.raises(BackupError):
        vault.receipt_from_record(record)


def test_wrong_sizes_are_refused(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="bytes, the regions"):
        store(BackupVault(tmp_path), DATA[:-1], DATA[:-1])


# --------------------------------------------------------------------------- journal


def test_begin_transition_history_and_archive(tmp_path: Path) -> None:
    journal = begin(tmp_path)
    assert journal.state is JournalState.PENDING and (tmp_path / JOURNAL_NAME).exists()

    backup = {"path": "x", "sha256": "b" * 64}
    journal.transition(JournalState.BACKUP_VERIFIED, backup=backup)
    journal.update(progress={"done_bytes": 1})
    journal.transition(JournalState.ABORTED, abort_reason="test")
    archived = journal.archive()

    document = json.loads(archived.read_text("utf-8"))
    assert archived == tmp_path / ARCHIVE_DIR / f"{journal.transaction_id}.json"
    assert [h["state"] for h in document["history"]] == ["pending", "backup_verified", "aborted"]
    assert document["progress"] == {"done_bytes": 1} and document["backup"] == backup
    assert document["attestation"]["operator"] == "bench operator"
    assert not (tmp_path / JOURNAL_NAME).exists()


def test_illegal_transitions_change_nothing(tmp_path: Path) -> None:
    journal = begin(tmp_path)
    before = (tmp_path / JOURNAL_NAME).read_bytes()

    with pytest.raises(SafetyGuardError, match="not allowed"):
        journal.transition(JournalState.ERASING)  # never erase before the backup is verified
    with pytest.raises(JournalError, match="must record its backup"):
        journal.transition(JournalState.BACKUP_VERIFIED)  # no backup record

    assert (tmp_path / JOURNAL_NAME).read_bytes() == before
    assert journal.state is JournalState.PENDING
    with pytest.raises(JournalError, match="cannot be archived"):
        journal.archive()


def test_a_crash_between_temp_write_and_replace_keeps_the_previous_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = begin(tmp_path)
    journal.transition(JournalState.BACKUP_VERIFIED, backup={"path": "x", "sha256": "b" * 64})

    def crash(self: Path, target: Path) -> None:
        raise OSError("power lost before the rename")

    monkeypatch.setattr(Path, "replace", crash)
    with pytest.raises(OSError, match="power lost"):
        journal.transition(JournalState.ERASING)
    monkeypatch.undo()

    assert journal.state is JournalState.BACKUP_VERIFIED  # memory follows the disk
    assessment = JournalInspector.inspect(tmp_path)
    assert assessment.state is JournalState.BACKUP_VERIFIED  # no false "erasing" record...
    assert assessment.leftover_temp_file  # ...and the half-done write is visible
    assert assessment.status is AssessmentStatus.STALE  # the ECU was never erased
    journal.transition(JournalState.ERASING)  # the next write simply overwrites the .tmp
    assert JournalInspector.inspect(tmp_path).status is AssessmentStatus.DIRTY


def test_a_second_begin_is_refused_while_a_journal_exists(tmp_path: Path) -> None:
    begin(tmp_path)

    with pytest.raises(JournalError, match="unfinished flash journal"):
        begin(tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"kind": "something else"}),
        json.dumps({"kind": "bench_flash_journal", "schema_version": 1, "state": "exploded"}),
    ],
)
def test_a_corrupt_journal_fails_closed(tmp_path: Path, content: str) -> None:
    (tmp_path / JOURNAL_NAME).write_text(content, encoding="utf-8")

    assessment = JournalInspector.inspect(tmp_path)

    assert assessment.status is AssessmentStatus.CORRUPT
    assert assessment.blocks_new_flash and not assessment.can_recover
    with pytest.raises(JournalError, match="only a stale journal"):
        JournalInspector.discard_stale(tmp_path)


def test_dirty_states_need_recovery_and_are_never_discarded(tmp_path: Path) -> None:
    journal = begin(tmp_path)
    journal.transition(JournalState.BACKUP_VERIFIED, backup={"path": "x", "sha256": "b" * 64})
    journal.transition(JournalState.ERASING)

    assessment = JournalInspector.inspect(tmp_path)

    assert assessment.status is AssessmentStatus.DIRTY and assessment.can_recover
    assert assessment.backup_sha256 == "b" * 64
    assert assessment.image_sha256 == "a" * 64 and assessment.simulated is True
    with pytest.raises(JournalError, match="dirty"):
        JournalInspector.discard_stale(tmp_path)


def test_stale_journals_can_be_discarded(tmp_path: Path) -> None:
    begin(tmp_path)
    assert JournalInspector.inspect(tmp_path).status is AssessmentStatus.STALE

    archived = JournalInspector.discard_stale(tmp_path)

    assert json.loads(archived.read_text("utf-8"))["state"] == "aborted"
    assert JournalInspector.inspect(tmp_path).status is AssessmentStatus.CLEAN


def test_an_empty_directory_is_clean(tmp_path: Path) -> None:
    assessment = JournalInspector.inspect(tmp_path / "nothing-here")

    assert assessment.status is AssessmentStatus.CLEAN and not assessment.blocks_new_flash


def test_the_lock_admits_one_session_and_is_never_broken(tmp_path: Path) -> None:
    with JournalLock(tmp_path):
        assert (tmp_path / LOCK_NAME).read_text("ascii") == str(os.getpid())
        with pytest.raises(JournalLockedError, match=f"PID {os.getpid()}"), JournalLock(tmp_path):
            pass
    assert not (tmp_path / LOCK_NAME).exists()  # released
    (tmp_path / LOCK_NAME).write_text("424242", encoding="ascii")  # left by a crash
    with pytest.raises(JournalLockedError, match="never broken automatically"):
        JournalLock(tmp_path).acquire()


def test_a_stale_lock_also_blocks_discarding(tmp_path: Path) -> None:
    begin(tmp_path)
    (tmp_path / LOCK_NAME).write_text("1", encoding="ascii")

    with pytest.raises(JournalLockedError):
        JournalInspector.discard_stale(tmp_path)


def test_leftover_temp_without_a_journal_is_clean_but_reported(tmp_path: Path) -> None:
    (tmp_path / TEMP_NAME).write_text("{partial", encoding="utf-8")

    assessment = JournalInspector.inspect(tmp_path)

    assert assessment.status is AssessmentStatus.CLEAN and assessment.leftover_temp_file


def test_a_transient_windows_sharing_refusal_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iran_obd_tuner.tuning import flash_journal

    journal = begin(tmp_path)
    real = Path.replace
    refusals = {"left": 3}
    pauses: list[float] = []

    def scanned_by_antivirus(self: Path, target: Path) -> Path:
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(13, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", scanned_by_antivirus)
    monkeypatch.setattr(flash_journal, "_sleep", pauses.append)

    journal.transition(JournalState.BACKUP_VERIFIED, backup={"path": "x", "sha256": "b" * 64})

    assert pauses == [flash_journal._SHARING_PAUSE_S] * 3
    assert JournalInspector.inspect(tmp_path).state is JournalState.BACKUP_VERIFIED


def test_a_persistent_sharing_refusal_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iran_obd_tuner.tuning import flash_journal

    journal = begin(tmp_path)
    attempts: list[int] = []

    def always_locked(self: Path, target: Path) -> Path:
        attempts.append(1)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", always_locked)
    monkeypatch.setattr(flash_journal, "_sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        journal.transition(JournalState.BACKUP_VERIFIED, backup={"path": "x", "sha256": "b" * 64})

    assert len(attempts) == flash_journal._SHARING_ATTEMPTS  # bounded
    assert journal.state is JournalState.PENDING  # nothing claimed that was not written
