"""Bench (off-vehicle) ECU work: offline ROM file analysis and the write boundary.

**Nothing in this package talks to an ECU.** There is no ECU communication, no ROM reading
from an ECU, no security access and no flashing here.

Implemented (Phase 4, Milestone 6 — offline file analysis only):

* ``rom_loader.py`` — read-only loading of raw ``.bin`` dumps with size bounds, SHA-256 /
  CRC32 fingerprints and observed ASCII strings (no identification is claimed);
* ``map_definitions.py`` — validated map definition files with provenance (the project ships
  no real ECU offsets; only the SIMULATED demo in ``demo.py`` / ``data/demo_maps.json``);
* ``tables.py`` / ``workspace.py`` — table decoding, bounded editing on a working copy, undo /
  redo and diffs; the loaded image itself is immutable;
* ``checksum.py`` — exactly defined checksum algorithms; "valid" means "matches the configured
  algorithm", never "an ECU accepts it";
* ``rom_export.py`` — the only file writer: a modified copy goes to a *different* file with a
  manifest, never over the source and never to an ECU.

Still only a boundary (no implementation, Phase 5 stays locked):

* :class:`CalibrationReader` / :class:`CalibrationWriter` — the shape of bench ECU access;
* :class:`BenchSafetyChecklist` and :func:`assert_write_allowed` — the preconditions any
  future ECU write must satisfy; the default safety policy refuses every write.

Import rules for this package: ``core`` and the standard library only.
"""
