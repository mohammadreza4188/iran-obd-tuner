"""Flash plan (Milestone 7d): exactly which image goes to which bench target, validated.

:func:`build_flash_plan` turns a loaded :class:`~iran_obd_tuner.tuning.rom_loader.RomImage`
and a :class:`~iran_obd_tuner.core.bench.BenchTargetProfile` into a :class:`FlashPlan`, or
refuses with :class:`FlashPlanError`. It checks:

* the image is exactly the target's size, and every region lies inside it;
* the image bytes still have the SHA-256 recorded when they were loaded, and a file-backed
  image is unchanged on disk;
* every configured checksum block is well formed and ``valid``
  (:func:`~iran_obd_tuner.tuning.checksum.verify_all`). ``invalid`` or ``not_configured``
  refuses the plan - unless the caller explicitly allows an unverified checksum **and** the
  target is simulated (``target.simulated is True``); the override is recorded in the plan.

Nothing here talks to an ECU or writes a file.
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

from iran_obd_tuner.core.bench import BenchTargetProfile, MemoryRegion
from iran_obd_tuner.core.calibration import ChecksumResult, ChecksumStatus
from iran_obd_tuner.core.exceptions import SafetyGuardError
from iran_obd_tuner.tuning.checksum import ChecksumBlock, validate_block, verify_all
from iran_obd_tuner.tuning.rom_loader import RomImage

__all__ = ["FlashPlan", "FlashPlanError", "build_flash_plan"]


class FlashPlanError(SafetyGuardError):
    """The image may not be flashed to this target (size, integrity or checksums)."""


@dataclass(frozen=True, slots=True)
class FlashPlan:
    """A validated image for one bench target.

    Attributes:
        target: The bench target.
        image: The exact bytes to program (image order of the target's regions).
        image_sha256: SHA-256 of ``image`` (what the authorisation is bound to).
        image_name: Name of the source image, for the journal and the reports.
        image_simulated: Whether the image itself is the SIMULATED demo.
        checksums: The checksum verification results the plan was accepted with.
        unverified_checksum_override: ``True`` when it was accepted despite an ``invalid``
            or ``not_configured`` checksum (simulated targets only).
    """

    target: BenchTargetProfile
    image: bytes
    image_sha256: str
    image_name: str
    image_simulated: bool
    checksums: tuple[ChecksumResult, ...]
    unverified_checksum_override: bool

    @property
    def simulated(self) -> bool:
        """Whether the target is simulated (the only kind that exists in this build)."""
        return self.target.simulated

    @property
    def regions(self) -> tuple[MemoryRegion, ...]:
        return self.target.regions

    def region_data(self, region: MemoryRegion) -> bytes:
        """The image bytes that belong to ``region``."""
        if region not in self.target.regions:
            raise ValueError(f"region {region.describe()} is not one of the target's regions")
        return self.image[region.image_offset : region.image_end]

    def region_crc32(self, region: MemoryRegion) -> int:
        return zlib.crc32(self.region_data(region))

    @property
    def checksums_valid(self) -> bool:
        return all(result.status is ChecksumStatus.VALID for result in self.checksums)


def build_flash_plan(
    rom: RomImage,
    target: BenchTargetProfile,
    *,
    checksum_blocks: Sequence[ChecksumBlock] = (),
    allow_unverified_checksum_in_simulation: bool = False,
) -> FlashPlan:
    """Validate ``rom`` for ``target`` and return the plan.

    Raises:
        FlashPlanError: Wrong size, region outside the image, changed bytes or source file,
            malformed checksum definition, or a checksum that is not ``valid`` (without the
            simulated-only override).
    """
    data = rom.data
    if len(data) != target.image_size:
        raise FlashPlanError(
            f"image {rom.name!r} has {len(data)} bytes; target {target.target_id!r} takes "
            f"exactly {target.image_size}"
        )
    for region in target.regions:
        if region.image_end > len(data):
            raise FlashPlanError(f"region {region.describe()} lies outside the image")
    if hashlib.sha256(data).hexdigest() != rom.sha256:
        raise FlashPlanError(f"image {rom.name!r} no longer matches its recorded SHA-256")
    if not rom.verify_source_unchanged():
        raise FlashPlanError(f"the file behind {rom.name!r} changed on disk since it was loaded")
    size = len(data)
    problems = [problem for block in checksum_blocks for problem in validate_block(block, size)]
    if problems:
        raise FlashPlanError("malformed checksum definition: " + "; ".join(problems))
    results = verify_all(data, checksum_blocks)
    unverified = [result for result in results if result.status is not ChecksumStatus.VALID]
    override = False
    if unverified:
        statuses = ", ".join(f"{r.block_id or '-'}={r.status}" for r in unverified)
        if not allow_unverified_checksum_in_simulation:
            raise FlashPlanError(f"checksums are not all valid ({statuses}); refusing to flash")
        if target.simulated is not True:
            raise FlashPlanError(
                "an unverified checksum may only be overridden for a simulated target"
            )
        override = True
    return FlashPlan(
        target=target,
        image=data,
        image_sha256=rom.sha256,
        image_name=rom.name,
        image_simulated=rom.simulated,
        checksums=results,
        unverified_checksum_override=override,
    )
