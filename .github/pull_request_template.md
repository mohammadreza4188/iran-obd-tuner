<!-- Iran OBD Tuner — pull request template.
     Keep it short. Delete the sections that do not apply. -->

## Summary

<!-- What does this change do, and why? One short paragraph, not a commit log. -->

## Area

<!-- Tick the parts of the tree this PR touches. -->

- [ ] `app/` — entry points, wiring, CLI
- [ ] `core/` — config, logging, models, safety, catalogues
- [ ] `hardware/` — adapter interfaces/implementations (interfaces only today)
- [ ] `diagnostics/` — diagnostic workflow and models
- [ ] `tuning/` — bench/calibration interfaces
- [ ] `database/` — knowledge database
- [ ] `ui/` — desktop user interface (not implemented yet)
- [ ] tests
- [ ] docs / tooling / project metadata

## Safety checklist

- [ ] No real ECU write, flash, calibration-write, security-access or destructive command was added.
- [ ] No code path sends anything to a real vehicle in tests, CI or on import.
- [ ] New operations that change vehicle state are gated by `core.safety.require(...)`.
- [ ] Read-only behaviour remains the default (mock adapter, no hardware needed).
- [ ] Any claim about a real adapter, protocol or vehicle is backed by evidence (spec/datasheet/log),
      otherwise it is documented as unverified.

## Validation

<!-- Paste the actual commands and their results; do not write "tested locally". -->

```
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

## Documentation and dependencies

- [ ] README / docs updated for behaviour, configuration or capability changes.
- [ ] No new runtime dependency (or: justified in the description and added to the right extra in `pyproject.toml`).
- [ ] New PIDs, DTCs, adapters or vehicles are referenced in the capability tables in `README.md` / `docs/architecture.md`.

## Notes for the reviewer

<!-- Trade-offs, open questions, decisions that need approval, things you could not verify. -->
