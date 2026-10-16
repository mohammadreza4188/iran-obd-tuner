"""Hardware abstraction: adapter interfaces and their implementations.

Import rules for this package:

* may import ``core`` and the standard library,
* must not import ``diagnostics``, ``database``, ``tuning``, ``ui`` or ``app`` (transport
  code knows nothing about diagnostic interpretation or presentation),
* third-party packages (``python-obd``, ``pyserial``, ``python-can``) may only be imported
  inside the concrete adapter module that needs them, never at package import time — an
  adapter implementation must be importable, and its absence reportable, on a machine
  without that dependency (see :mod:`iran_obd_tuner.hardware.registry`).

Only one adapter is implemented today: :mod:`iran_obd_tuner.hardware.mock`. Real hardware
adapters are *declared* in the registry with ``implemented=False`` so that the tool can say
honestly what it does not support yet.
"""
