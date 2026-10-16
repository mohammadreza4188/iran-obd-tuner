"""Bench (off-vehicle) ECU tuning — interfaces and validation boundaries only.

**Nothing in this package is implemented.** There is no ECU communication, no ROM reading,
no checksum correction and no flashing here, and this scaffolding deliberately does not add
any. What the package does provide is the boundary that future write-capable work must pass
through:

* :class:`CalibrationReader` — the read-only bench interface (reading a calibration image
  from an ECU on the bench is allowed by the default safety policy);
* :class:`BenchSafetyChecklist` and :func:`assert_write_allowed` — the preconditions a write
  must satisfy, checked *before* anything is sent to an ECU;
* :class:`CalibrationWriter` — the shape of the future writer, whose every implementation
  must call :func:`assert_write_allowed` first.

Any future write-capable feature requires, in addition: documented evidence for the target
ECU family (checksum algorithm, security access scheme, memory layout), controlled testing
on a bench ECU, an explicit owner/developer approval, and an update of the capability tables
in README.md. See docs/architecture.md, section "Safety boundary".

Import rules for this package: ``core`` and the standard library only.
"""
