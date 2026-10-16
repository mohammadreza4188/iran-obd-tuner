"""Shared domain layer: configuration, logging, models, catalogues and safety guards.

Import rules for this package:

* may import the standard library only,
* must not import ``hardware``, ``diagnostics``, ``database``, ``tuning``, ``ui`` or ``app``,
* must not import third-party packages (no ``python-obd``, no ``PySide6``, ...).

Keeping ``core`` dependency-free is what makes the diagnostic workflow testable without a
vehicle, an adapter, a GUI toolkit or a network connection.
"""
