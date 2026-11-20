"""Knowledge database layer.

The knowledge database is the long-term asset of this project: definitions of ECUs, maps,
checksum algorithms, security access schemes and trouble code descriptions for the vehicle
families in scope. This package currently implements the smallest useful slice of it — a
JSON backed trouble code catalogue — behind the abstract
:class:`~iran_obd_tuner.database.repository.KnowledgeRepository`, so a real database
(SQLite, or files parsed from A2L/DAMOS descriptions) can replace it later without touching
the diagnostic layer.

The vehicle and ECU knowledge base (Milestone 5) lives next to it: provenance-backed JSON
packs in ``data/vehicles/`` (one per manufacturer), validated by ``vehicle_schema.py`` and
served read-only by :class:`~iran_obd_tuner.database.vehicles.JsonVehicleKnowledgeBase`.
Every claim carries a confidence level and a source; nothing here writes a file.

Import rules for this package:

* may import ``core`` and the standard library,
* must not import ``hardware``, ``diagnostics``, ``tuning``, ``ui`` or ``app``,
* no third-party dependency is used yet; adding one (e.g. SQLAlchemy) requires an explicit
  decision and an entry in ``pyproject.toml``.
"""
