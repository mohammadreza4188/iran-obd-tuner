"""Bench (off-vehicle) ECU work: offline ROM file analysis and simulated bench flashing.

**Nothing in this package opens a bus or composes an ECU request.** Offline analysis works
on files; bench flashing (Milestone 7, **simulated ECU only**) drives the
:class:`~iran_obd_tuner.core.bench.BenchProgrammerPort` that ``hardware`` implements and
``app`` wires in.

Implemented (Phase 4, Milestone 6 — offline file analysis only):

* ``rom_loader.py`` — read-only loading of raw ``.bin`` dumps with size bounds, SHA-256 /
  CRC32 fingerprints and observed ASCII strings (no identification is claimed);
* ``map_definitions.py`` — validated map definition files with provenance (the project ships
  no real ECU offsets; only the SIMULATED demo in ``demo.py`` / ``data/demo_maps.json``);
* ``tables.py`` / ``workspace.py`` — table decoding, bounded editing on a working copy, undo /
  redo and diffs; the loaded image itself is immutable;
* ``checksum.py`` — exactly defined checksum algorithms; "valid" means "matches the configured
  algorithm", never "an ECU accepts it";
* ``rom_export.py`` — a modified copy goes to a *different* file with a manifest, never over
  the source and never to an ECU.

Implemented (Phase 5, Milestone 7d — against the simulated bench ECU only):

* ``flash_plan.py`` — the validated image for one bench target;
* ``flash_gate.py`` — the only issuer of a ``FlashAuthorization``;
* ``backup_vault.py`` — write-once verified backups, the only issuer of a ``BackupReceipt``;
* ``flash_journal.py`` — the crash-safe Flash Transaction Journal and its start-up check;
* ``flash_orchestrator.py`` — the four-stage pipeline and the recovery.

Boundaries that stay: :class:`CalibrationWriter` has no implementation, and
:func:`assert_write_allowed` (``CALIBRATION_WRITE``) refuses under the default policy and
under the bench flash policy (which grants ``BENCH_FLASH`` only).

Import rules for this package: ``core`` and the standard library only. Files are written by
``rom_export.py``, ``backup_vault.py`` and ``flash_journal.py`` only (architecture test).
"""
