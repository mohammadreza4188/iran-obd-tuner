"""Application layer: startup, wiring and the command line interface.

Import rules for this package: it may import every other layer (``core``, ``hardware``,
``diagnostics``, ``database``, ``tuning``) and is the only place where concrete
implementations are chosen. Nothing else imports ``app``.
"""
