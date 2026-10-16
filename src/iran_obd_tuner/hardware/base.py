"""Transport contract for OBD adapters.

:class:`ObdAdapter` is the only way the rest of the application talks to a vehicle. It
separates three concerns:

* **connection lifecycle** — :meth:`ObdAdapter.connect`, :meth:`ObdAdapter.disconnect` and
  the :meth:`ObdAdapter.session` context manager, which guarantees a disconnect;
* **read-only diagnostic primitives** — vehicle identification, supported parameters,
  decoded measurements and trouble codes, all of which raise explicit errors on timeout,
  malformed response or unsupported request;
* **state-changing operations** — declared but refused: they pass through
  :func:`iran_obd_tuner.core.safety.require` and are *not implemented* in this build, so a
  caller gets a clear refusal instead of a silent no-op (see docs/architecture.md).

Adapters return *raw* trouble code strings and decoded :class:`Measurement` objects; naming
and interpretation of codes is the diagnostic layer's job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from iran_obd_tuner.core.exceptions import (
    AdapterConnectionError,
    UnsupportedOperationError,
    UnsupportedPidError,
)
from iran_obd_tuner.core.logging import get_logger
from iran_obd_tuner.core.models import Measurement
from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass, SafetyPolicy, require

__all__ = ["AdapterInfo", "AdapterKind", "MeasurementBatch", "ObdAdapter"]


class AdapterKind(StrEnum):
    """Physical class of an adapter."""

    MOCK = "mock"
    SERIAL = "serial"
    CAN = "can"


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """What an adapter is and what it claims to support.

    Attributes:
        name: Registry name used in configuration (``adapter = "<name>"``).
        kind: Physical class of the adapter.
        description: One sentence describing the adapter.
        implemented: ``False`` for adapters that are declared and documented but not built.
        protocols: Protocols this adapter can speak, as reported/claimed by the vendor.
            Empty means "unknown", never "all".
        hardware_required: Whether an external device is needed.
        notes: Caveats, e.g. required dependency or planned phase.
    """

    name: str
    kind: AdapterKind
    description: str
    implemented: bool
    protocols: tuple[str, ...] = ()
    hardware_required: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementBatch:
    """Result of reading several parameters in one go.

    Attributes:
        measurements: Successfully read measurements, in request order.
        skipped_pids: Requested parameters the adapter/vehicle did not answer (unsupported).
    """

    measurements: tuple[Measurement, ...]
    skipped_pids: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[Measurement]:
        return iter(self.measurements)

    def __len__(self) -> int:
        return len(self.measurements)


class ObdAdapter(ABC):
    """Abstract, read-only-by-default vehicle transport.

    Subclasses implement :meth:`_open`, :meth:`_close` and the raw read primitives. They
    must never send a state-changing or calibration-write command: those are refused in
    this base class through the safety policy.
    """

    def __init__(self, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy
        self._connected = False
        self._log = get_logger(f"hardware.{type(self).__name__}")

    # ------------------------------------------------------------------ #
    # identity and state
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def info(self) -> AdapterInfo:
        """Static description of this adapter."""

    @property
    def is_connected(self) -> bool:
        """Whether a connection is currently open."""
        return self._connected

    @property
    def policy(self) -> SafetyPolicy:
        """Safety policy this adapter enforces for state-changing operations."""
        return self._policy

    # ------------------------------------------------------------------ #
    # connection lifecycle
    # ------------------------------------------------------------------ #

    def connect(self, *, timeout_s: float | None = None) -> None:
        """Open the connection.

        Args:
            timeout_s: Seconds to wait for the adapter to answer.

        Raises:
            AdapterConnectionError: If the adapter is already connected.
            AdapterTimeoutError: If the adapter does not answer in time.
        """
        if self._connected:
            raise AdapterConnectionError(f"{self.info.name}: already connected")
        self._log.debug("connecting to %s adapter (timeout=%s)", self.info.name, timeout_s)
        self._open(timeout_s=timeout_s)
        self._connected = True
        self._log.info("connected to %s adapter", self.info.name)

    def disconnect(self) -> None:
        """Close the connection. Safe to call when not connected."""
        if not self._connected:
            return
        try:
            self._close()
        finally:
            self._connected = False
            self._log.info("disconnected from %s adapter", self.info.name)

    @contextmanager
    def session(self, *, timeout_s: float | None = None) -> Iterator[Self]:
        """Open a connection for the duration of the ``with`` block.

        The connection is closed even when the body raises, which is what makes the
        diagnostic workflow safe to interrupt.
        """
        self.connect(timeout_s=timeout_s)
        try:
            yield self
        finally:
            self.disconnect()

    @abstractmethod
    def _open(self, *, timeout_s: float | None) -> None:
        """Adapter specific connection setup (called by :meth:`connect`)."""

    @abstractmethod
    def _close(self) -> None:
        """Adapter specific connection teardown (called by :meth:`disconnect`)."""

    # ------------------------------------------------------------------ #
    # read-only diagnostic primitives
    # ------------------------------------------------------------------ #

    @abstractmethod
    def read_vin(self) -> str | None:
        """Return the vehicle identification number, or ``None`` when unavailable.

        Raises:
            AdapterConnectionError: If called while not connected.
            AdapterTimeoutError: If the vehicle does not answer.
        """

    @abstractmethod
    def read_supported_pids(self) -> tuple[str, ...]:
        """Return the service 01 parameters this vehicle reports as supported.

        Real adapters derive this from the PID ``0100``/``0120``/``0140`` bitmasks; nothing
        is assumed about parameters that are not reported here.
        """

    @abstractmethod
    def read_measurement(self, pid: str) -> Measurement:
        """Read and decode a single service 01 parameter.

        Args:
            pid: Parameter identifier in request form, e.g. ``"010C"``.

        Raises:
            UnsupportedPidError: If the parameter is not supported.
            ProtocolError: If the response cannot be decoded.
            AdapterTimeoutError: If no response arrives in time.
        """

    @abstractmethod
    def read_diagnostic_trouble_codes(self) -> tuple[str, ...]:
        """Return stored trouble codes as raw code strings (service 03).

        Interpretation belongs to the diagnostic layer
        (:mod:`iran_obd_tuner.diagnostics.service`), not to the transport.
        """

    def read_measurements(
        self,
        pids: Sequence[str],
        *,
        ignore_unsupported: bool = True,
    ) -> MeasurementBatch:
        """Read several parameters, reporting the ones that were skipped.

        Args:
            pids: Parameter identifiers in request form.
            ignore_unsupported: When ``True`` (default), parameters the vehicle does not
                support are collected in :attr:`MeasurementBatch.skipped_pids`. When
                ``False`` the first unsupported parameter raises instead.

        Returns:
            The batch of measurements and the skipped identifiers.
        """
        measurements: list[Measurement] = []
        skipped: list[str] = []
        for pid in pids:
            try:
                measurements.append(self.read_measurement(pid))
            except (UnsupportedPidError, UnsupportedOperationError):
                if not ignore_unsupported:
                    raise
                self._log.debug("PID %s is not supported by %s", pid, self.info.name)
                skipped.append(pid)
        return MeasurementBatch(measurements=tuple(measurements), skipped_pids=tuple(skipped))

    # ------------------------------------------------------------------ #
    # state-changing operations: declared, refused, not implemented
    # ------------------------------------------------------------------ #

    def clear_diagnostic_trouble_codes(self) -> None:
        """Clear stored trouble codes (OBD service 04). **Not implemented.**

        The call is gated by the safety policy, so with the default policy it raises
        :class:`~iran_obd_tuner.core.exceptions.SafetyGuardError`. Because no adapter
        implements the service yet, a policy that allows state-changing operations leads to
        :class:`~iran_obd_tuner.core.exceptions.UnsupportedOperationError` — the refusal is
        explicit in both cases, never a silent success.
        """
        require(
            OperationClass.STATE_CHANGING,
            policy=self._policy,
            detail="clear diagnostic trouble codes (OBD service 04)",
        )
        raise UnsupportedOperationError(
            f"{self.info.name}: clearing trouble codes is not implemented in this build"
        )

    # ------------------------------------------------------------------ #
    # helpers for subclasses
    # ------------------------------------------------------------------ #

    def _require_connected(self) -> None:
        """Raise :class:`AdapterConnectionError` when the adapter is not connected."""
        if not self._connected:
            raise AdapterConnectionError(
                f"{self.info.name}: adapter is not connected (call connect() or use session())"
            )
