<a id="top"></a>

# iran-obd-tuner

[![CI](https://github.com/mohammadreza4188/iran-obd-tuner/actions/workflows/ci.yml/badge.svg)](https://github.com/mohammadreza4188/iran-obd-tuner/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-pre--alpha-orange)

**A safety-first, bilingual (English / فارسی) desktop and command-line toolkit for OBD-II / UDS
vehicle diagnostics and bench ECU engineering, built for the Iranian automotive market.**

**[English](#english)** · **[فارسی (Persian)](#persian)**

> [!IMPORTANT]
> Everything you can run today works **without a vehicle, an adapter or an ECU**, against clearly
> labelled simulators. The real-hardware paths (ELM327, CAN) are implemented and strictly
> read-only, but **have not yet been validated on a physical adapter or vehicle**. Bench flashing
> exists **only against a virtual ECU**; flashing a real ECU is intentionally locked.

---

<a id="english"></a>

## Part 1 — English

### Contents

1. [Overview](#en-overview)
2. [Project status at a glance](#en-status)
3. [Safety model](#en-safety)
4. [Features](#en-features)
5. [Architecture](#en-architecture)
6. [What is simulated, and what is not validated](#en-honesty)
7. [Requirements and installation](#en-install)
8. [Configuration](#en-config)
9. [Quickstart](#en-quickstart)
10. [Testing and quality gates](#en-testing)
11. [Project layout](#en-layout)
12. [Documentation](#en-docs)
13. [Roadmap](#en-roadmap)
14. [Contributing, authors and license](#en-license)

<a id="en-overview"></a>

### 1. Overview

`iran-obd-tuner` is a Python platform for diagnosing vehicles common in Iran (the first target is
the Peugeot Pars with the TU5 engine) and, under explicit safety gates, for future bench ECU work.
It is developed **hardware-independently**: every workflow runs and is tested against
deterministic simulators, so the project can grow before any adapter, vehicle or ECU bench exists.

Design principles:

- **Read-only by default.** The default safety policy only allows reading. Nothing that changes a
  vehicle or an ECU can be enabled from a configuration file or an environment variable.
- **Diagnostics and tuning are separate.** Diagnostic paths never import bench or tuning code;
  architecture tests enforce it.
- **Data honesty.** Simulated data is labelled everywhere (CLI, JSON, CSV, GUI, traffic log). A value
  that could not be read is shown as missing with a reason, never as a substitute number. No ECU
  model, CAN identifier or vehicle capability is claimed without traceable evidence.
- **No hardware needed to develop or test.** The core package has no runtime dependencies at all;
  hardware and GUI support are optional extras.

The full product concept is in [`docs/idea-report.md`](docs/idea-report.md) (Persian).

<a id="en-status"></a>

### 2. Project status at a glance

| Phase | Scope | Milestones | State |
|---|---|---|---|
| 0 | Foundation: packaging, configuration, logging, safety policy, simulated adapter, CI | — | Done |
| 1 | OBD-II diagnostics: one data path, read-only ELM327, live data, DTCs, freeze frame, monitors, performance tools, bilingual dashboard | M1, M2, M3 | Implemented; **real-hardware validation pending** |
| 2 | CAN, ISO-TP and a read-only UDS client; ECU identification | M4 | Implemented; **real-interface validation pending** |
| 3 | Iranian vehicle and ECU knowledge base, passive matching, field evidence bundles | M5 | Structure implemented; **technical claims stay `unknown` until field evidence exists** |
| 4 | Offline ROM analysis: loader, map definitions, table editing, checksums, safe export | M6 | Implemented (offline files only; no real map definitions yet) |
| — | Pre-field hardening audit (timeouts, listen-only probe, circuit breaker, report v3) | — | Done |
| 5 | Bench flash and recovery: backup, four-stage flash, crash-safe journal, recovery | M7 (7a–7g, 7e.1) | **Simulation only**; real ECUs locked |

<a id="en-safety"></a>

### 3. Safety model

The project distinguishes two operation classes, defined in code (`core/safety.py`,
`core/bench.py`) and never read from settings:

| | `READ_ONLY` (default) | `BENCH_FLASH` (simulation only) |
|---|---|---|
| Who grants it | Always on | Only `bench_flash_policy()`, from a complete human `BenchAttestation` for one session |
| What it allows | Reading PIDs, DTCs, freeze frames, VIN and UDS identification DIDs | Programming the **virtual** programmable ECU |
| What it never allows | Clearing DTCs, actuator tests, security access, writes, real flashing | Any real ECU, interface, channel or port |

Key guarantees, each backed by automated tests:

- **Allowlists, not blocklists.** The ELM327 driver only sends a fixed set of AT commands and OBD
  services `01`, `02` (frame `00`), `03`, `07`, `0A` and `09`. The UDS client only sends `0x10`
  (sub-functions `01`/`03`), `0x19` (`01`/`02`), `0x22` and `0x3E`. Write, security and
  programming services (`0x11`, `0x14`, `0x27`, `0x2E`, `0x2F`, `0x31`, `0x34`–`0x37`, …) and the
  programming session are refused before anything is sent.
- **Clearing trouble codes is not implemented.** The dashboard button only shows a refusal dialog.
- **Bounded waits everywhere.** A live session that gets no answer to 3 polls in a row (for
  example, the ignition was switched off) raises `VehicleSilentError` and disconnects instead of
  polling a silent vehicle forever. Serial writes, command and connect timeouts are all bounded.
- **Listen-only first on real CAN.** A real CAN interface (`slcan`, `pcan`, `kvaser`; 250 or
  500 kbit/s only) listens for one second before its first transmission and stops on any error
  frame or controller error state.
- **No implicit hardware.** Serial ports are never auto-probed, and a failed real connection never
  silently falls back to the simulator.
- **Bench capabilities.** Changing ECU memory requires a `FlashAuthorization`; erasing and writing
  also require a `BackupReceipt` bound to the physical unit. Each can only be minted by one module.
  A real `BenchTargetProfile` is refused at construction.

<a id="en-features"></a>

### 4. Features

#### 4.1 Diagnostics core

- **One diagnostic data path** (`ObdAdapter` contract) shared by the CLI and the dashboard.
- **Live data sessions** with an explicit per-parameter status (unsupported / no answer / malformed),
  no fallback values, and the 3-poll silence circuit breaker.
- **Trouble codes:** stored, pending and permanent DTCs (services `03` / `07` / `0A`). Each category
  carries its own status, so one failed category does not hide the others. Codes are read only.
- **Freeze frame `00`** (service `02`) with the trigger code and eight parameters.
- **MIL status and readiness monitors** (PID `0101`).
- **JSON report, schema v3**, including `monitor_status`, `dtc_categories`, `freeze_frame` and
  `unanswered_pids` (kept apart from unsupported PIDs).
- **CSV recording** with UTC timestamps, units in every column header and a `simulated` column.
- **Raw traffic viewer** (TX/RX): bounded, thread-safe, with pause, clear and copy.
- **Performance tools:** a 0–100 km/h timer with stated uncertainty and a virtual dyno with
  **estimated** power and torque. Both are passive and only reuse data that is already polled.

#### 4.2 Hardware layer

- **Simulated adapter** (static and time-varying drive cycle) with injectable communication faults.
- **Read-only ELM327 driver** for USB/serial (`[obd]` extra): initialisation, protocol detection,
  supported PIDs, live data, DTCs, VIN, a command allowlist and `AT PC` on disconnect. It uses
  `AT TP 0`, never `AT SP`, so nothing is written to the adapter's EEPROM.
- **Serial port listing** without opening any port.
- **The project's own CAN and ISO-TP (ISO 15765-2) layer** on python-can (`[can]` extra): single,
  first and consecutive frames, flow control (BS, STmin, Wait), padding, bounded timeouts. Real
  interfaces pass the mandatory listen-only probe first.
- **Read-only UDS (ISO 14229-1) client:** default-deny allowlist, NRC handling (`0x78` with a
  ceiling, `0x21`, `0x22`, `0x31`, `0x33`). The extended session is opt-in for one run only and
  always returns to `0x10 0x01`.
- **UDS ECU identification:** standard DIDs `F190` (VIN), `F187`, `F189`, `F191` and `F197`, plus
  UDS DTCs, each with its own status. ECU discovery (`3E 00` on `0x7DF`) only runs on request.
- **Deterministic simulated UDS ECU** on python-can's in-process `virtual` bus (`virtual-sim`).

#### 4.3 Iranian vehicle knowledge base and field evidence

- **Provenance-tracked claims.** Every fact carries a confidence level (`verified`, `documented`,
  `reported`, `unverified` or `unknown`) and a traceable source.
- **Vehicle packs** for IKCO (Peugeot Pars, Peugeot 206, Samand, Dena), SAIPA (Pride, Tiba, Saina,
  Quick) and MVM (315, X22), checked by a strict schema validator.
- **Honest content.** Bus protocols, CAN identifiers and ECU families are deliberately `unknown`. A
  claim can only become `verified` by referencing a reviewed Field Evidence Bundle in
  [`evidence/`](evidence/README.md), which is empty today. **No claim is `verified` yet.**
- **Passive vehicle matching** against what a session observed (protocol, PIDs, VIN, UDS DIDs).
  Nothing is sent, and a result means "consistent with the observations", not an identification.
- **Field Evidence Bundle export** (CLI and the dashboard's Vehicle knowledge tab): protocol, PIDs,
  identification DIDs and raw traffic in one SHA-256-hashed JSON. The last six VIN characters are
  redacted by default, and an export that still contains a VIN is refused.

#### 4.4 ROM and calibration map workbench (offline)

- **Read-only binary loading** (`.bin`, 16 KiB–8 MiB) with SHA-256, CRC32, fill statistics and
  observed ASCII strings (no identification is claimed). The original file is never modified.
- **Map definitions** (JSON) with source and confidence, a ROM compatibility check, and 1-D/2-D
  tables with bounded decode/encode. Out-of-range values are refused, never clamped.
- **Editing** with undo/redo and original, modified, difference and percent views (heat map).
- **Checksum engine** with exactly defined algorithms: `sum8`, `sum16_le`, `sum16_be`,
  `crc16_ccitt_false`, `crc32`, `twos_complement_8` and `twos_complement_16_le`. "Valid" means it
  matches the configured algorithm, not that any ECU accepts it.
- **Safe export** to a different file only, after explicit confirmation. It is refused while a
  configured checksum is invalid and writes a manifest with `validated_for_vehicle: false`.
- **A labelled SIMULATED demo ROM** and map definitions for trying everything without real data.

#### 4.5 Safe bench flash and recovery engine (Phase 5, simulation only)

- **`VirtualProgrammableEcu`:** a deterministic simulated bootloader target. It follows NOR flash
  rules (bits only go 1→0; whole-sector erase), counts an application as valid only after a passing
  check routine over the whole flash, and falls back to its bootloader otherwise. It has a seed-key
  with an attempt counter and delay, and a deterministic, fire-once `FaultPlan`: link drop, NRC,
  power loss with a torn block, corrupted read-back and a wrong key.
- **Capability-gated `BenchProgrammer`:** the only module that composes programming requests,
  behind its own default-deny allowlist. Every state-changing method checks a `FlashAuthorization`
  first; erase and download also check a `BackupReceipt` bound to the ECU's **unit identity**
  (hardware number and serial). Only `0x36` blocks are retried, within a budget; a security key is
  never retried.
- **Backup vault:** two independent full read-outs that must match, a write-once file plus manifest,
  a read-back from disk before the receipt is issued, and a re-check right before the erase.
- **Atomic, crash-safe flash journal:** every step is recorded *before* it is taken (temporary file,
  fsync, atomic replace). A single-session lock is never broken automatically, and a dirty or
  unreadable journal blocks every new flash.
- **Four-stage pipeline:** pre-flight → mandatory full backup → programming transfer → verify
  (check routine, read-back hash, reset, identity). After the erase starts, a failure is journaled
  as `interrupted` and *nothing else* is sent: no blind reset of a half-written ECU.
- **Recovery** restores the verified backup recorded in the journal and never takes a new backup. It
  accepts the target's bootloader, or an application of the **same unit** even after its software
  changed.
- **CLI:** `bench flash-sim` (default SIMULATED demo ROM, `--rom/--maps`, `--fault`, `--recover`,
  `--json`) and `bench journal show|discard-stale`. There is no interface, channel or port option.
- **Bilingual GUI panel** in the Bench tab: an attestation modal (four human statements, live ✓/✗
  machine checks, operator name and the typed phrase `BENCH FLASH`, held in memory for the current
  window only). It has a four-stage stepper with byte and block progress, a copyable backup path and
  SHA-256, and a Cancel button that locks the moment the erase starts. A window-wide **emergency
  recovery banner** appears for dirty or corrupt journals.

Full workflow, journal states and safety invariants:
[`docs/bench-flash-protocol.md`](docs/bench-flash-protocol.md) (Persian).

#### 4.6 Desktop dashboard

A PySide6 dashboard in Persian (RTL) and English (LTR) with a Slate + Indigo theme. Tabs:
Dashboard (gauges), Live Graph, Trouble Codes, Performance, Identification (OBD and UDS), Vehicle
knowledge, Bench / Tuning (ROM workbench and simulated bench flash), Settings and Events (log and
raw traffic). Adapter I/O runs on a background thread, and simulated bench work on a separate one,
so the window never freezes.

<a id="en-architecture"></a>

### 5. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ app/   CLI, GUI entry point and wiring: the only layer that imports all                 │
└─────────────────────────────────────────────────────────────────────────────────────────┘
          │                       │                      │                      │
          ▼                       ▼                      ▼                      ▼
┌───────────────────┐   ┌───────────────────┐   ┌────────────────┐   ┌────────────────────┐
│ ui/               │──▶│ diagnostics/      │──▶│ hardware/      │   │ tuning/            │
│ PySide6 views,    │   │ sessions, reports │   │ adapters, CAN, │   │ ROM workbench,     │
│ Qt-free protocols │   │ (+ database/)     │   │ ISO-TP, UDS,   │   │ bench orchestrator │
│                   │   │                   │   │ bench link     │   │ (port only)        │
└───────────────────┘   └───────────────────┘   └────────────────┘   └────────────────────┘
          │                       │                      │                      │
          ▼                       ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ core/  models, safety policy, bench contract, reports (stdlib only)                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

Dependency rules (enforced by `tests/unit/test_architecture.py`):

- `core` imports nothing first-party but itself and uses only the standard library.
- `hardware`, `database` and `tuning` import only `core` (and themselves). `tuning` never touches
  hardware: the bench orchestrator drives a `BenchProgrammerPort` protocol defined in `core`.
- `diagnostics` may use `core`, `hardware` and `database`; it knows nothing about bench work.
- `ui` may use `core` and `diagnostics` only. It talks to the application through Qt-free
  protocols in `ui/backend.py` and never sees `tuning`, capabilities or the policy.
- `app` wires everything. `app/bench_flash.py` is the **single** place where the tuning orchestrator
  meets the hardware bench link.

Selected AST guardrails: no python-OBD or DTC-clearing path; write-service request literals only in
the two allowlist modules; each capability minted by exactly one module; `BENCH_FLASH` referenced
only by the bench modules and never by settings; the bench link opens only the `virtual` bus; the
bench wiring only builds the default simulated link; the GUI never persists bench flash mode and only
builds simulated attestations; file writes in `tuning` only in three named modules. Decisions are
recorded as 80 short ADRs in [`docs/architecture.md`](docs/architecture.md) (Persian).

<a id="en-honesty"></a>

### 6. What is simulated, and what is not validated

**Simulated, and labelled as such:**

- All live data and DTCs from the `mock` adapter: reports carry `simulated: true`, CSV files a
  `simulated` column, and the dashboard shows a permanent banner.
- The simulated UDS ECU (`virtual-sim`), whose values are obviously fake (`SIMULATEDVIN00004`,
  `SIM-…`).
- The demo ROM (header `SIMULATED-DEMO-ROM / NOT AN ECU IMAGE`) and its map definitions. They
  describe no real ECU.
- The virtual programmable ECU, its published XOR seed-key, `SIMULATED-` backups and
  `SIMULATED-bench-ecu-state.json`.
- DTC descriptions, which come from a hand-written generic subset (`authoritative: false`).

**Implemented but not yet validated on real hardware:** the ELM327 driver, the CAN/ISO-TP/UDS stack
on real interfaces, the `0101` bit table (not yet checked against SAE J1979 text), and the
performance tools (not compared with GPS or a dynamometer). Whether the Peugeot Pars TU5 answers UDS
on the OBD CAN pins at all is still unknown.

**Intentionally locked:** real-ECU bench reading and flashing, DTC clearing, actuator tests,
security access bypass, and any real seed-key algorithm. Unlocking a real ECU requires an
evidence-backed target profile, an authorised seed-key provider, a real CAN interface, a controlled
lab test and a separate owner approval.

<a id="en-install"></a>

### 7. Requirements and installation

- Python **3.11 or newer** (CI runs 3.11, 3.12 and 3.13).
- No hardware is needed for the simulated workflows.

| Extra | Installs | Needed for |
|---|---|---|
| *(none)* | nothing: the core is standard-library only | CLI diagnostics with the simulated adapter, ROM workbench, knowledge base |
| `[gui]` | PySide6, pyqtgraph | the desktop dashboard |
| `[can]` | python-can | `uds-ident`, simulated bench flashing, real CAN interfaces |
| `[obd]` | pyserial | a real ELM327 adapter |
| `[bench]` | udsoncan, crcmod | declared for future work; not imported by the current code |
| `[dev]` | pytest, ruff | development and tests |
| `[full]` | all runtime extras | the complete desktop application |

```bash
git clone https://github.com/mohammadreza4188/iran-obd-tuner.git
cd iran-obd-tuner
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev,gui,can]"
```

Without installing, tests run with `python -m pytest`, and the CLI with
`PYTHONPATH=src python -m iran_obd_tuner`.

<a id="en-config"></a>

### 8. Configuration

```bash
cp config/example.toml config/local.toml   # git-ignored
IRAN_OBD_TUNER_LOG_LEVEL=DEBUG iran-obd-tuner config
```

Precedence: code defaults → `config/local.toml` → `IRAN_OBD_TUNER_*` environment variables. Unknown
keys and invalid values are rejected, never silently ignored. Out-of-range timeouts are refused,
not clamped. No secret is ever needed. The safety policy and bench flash mode can **not** be set
here.

<a id="en-quickstart"></a>

### 9. Quickstart

#### Command line

```bash
iran-obd-tuner version                       # version and interpreter
iran-obd-tuner adapters                      # declared adapters and their status
iran-obd-tuner diagnose                      # one read-only session, simulated adapter
iran-obd-tuner diagnose --json --output output/report.json
iran-obd-tuner diagnose --adapter elm327 --port COM3 --no-live   # real adapter, least traffic
iran-obd-tuner uds-ident --discover          # UDS identification, simulated ECU ([can])
iran-obd-tuner vehicles list                 # knowledge base with confidence levels
iran-obd-tuner vehicles show ikco.peugeot_pars
iran-obd-tuner vehicles match --report output/report.json --uds output/ecu.json
iran-obd-tuner bench demo --output output/demo
iran-obd-tuner bench inspect output/demo/simulated_demo_rom.bin --maps output/demo/simulated_demo_maps.json
iran-obd-tuner bench table output/demo/simulated_demo_rom.bin --maps output/demo/simulated_demo_maps.json --map demo_fuel_base
```

Every command also works as `python -m iran_obd_tuner <command>`.

#### Simulated bench flash and recovery (needs `[can]`)

```bash
iran-obd-tuner bench flash-sim --output output/bench                      # flash the SIMULATED demo ROM
iran-obd-tuner bench flash-sim --output output/bench --fault power-loss-at-block=20   # exits 3
iran-obd-tuner bench journal show output/bench                            # dirty: recovery required
iran-obd-tuner bench flash-sim --output output/bench --recover            # restore the verified backup
iran-obd-tuner bench journal discard-stale output/bench                   # only for stale journals
```

Faults: `drop-at-block=N`, `nrc72-at-block=N`, `power-loss-at-block=N` (N = 1–64), `bad-key` and
`corrupt-readback`.

Exit codes: `0` success; `1` runtime failure (for `flash-sim`, also a run stopped before any erase);
`2` usage or configuration error (also an image the flash plan refuses); `3` a dirty journal is left,
so recovery is required; `130` interrupted.

#### Dashboard (needs `[gui]`)

```bash
iran-obd-tuner-gui                 # or: python -m iran_obd_tuner.app.gui_main
```

1. Choose the language and "Simulation" mode in **Settings**, then press **Connect** to drive the
   gauges and charts without an adapter. Press ⏺ to record CSV into `output/`.
2. **Identification** reads OBD and UDS identification. **Vehicle knowledge** matches a session
   against the knowledge base and exports a Field Evidence Bundle.
3. **Bench / Tuning** opens a ROM or the SIMULATED demo for offline analysis. Below it,
   **Enable bench flash mode…** opens the attestation modal, then **Start simulated flash**. Pick a
   training fault to see the emergency recovery banner and run **Start recovery**.

On a headless machine, set `QT_QPA_PLATFORM=offscreen`.

<a id="en-testing"></a>

### 10. Testing and quality gates

```bash
python scripts/check.py            # byte-compile, ruff check, ruff format --check, pytest
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

- `scripts/check.py` is strict: a missing tool counts as a **failure**, not a skip.
- Last local run on Python 3.13 with every extra installed: **1,311 passed, 1 skipped** (a symlink
  test that Windows does not support). Without `[gui]` or `[can]`, the tests needing them are skipped.
- Every test runs without hardware, a display or network access: scripted serial transports, a fake
  CAN bus, python-can's in-process `virtual` bus and headless Qt (`offscreen`). No test opens a
  serial port or a real CAN interface.
- CI (`.github/workflows/ci.yml`) runs lint, format and tests on Python 3.11–3.13 with `[can]`, plus
  a job without optional extras. `[gui]` is not installed in CI.

<a id="en-layout"></a>

### 11. Project layout

```
iran-obd-tuner/
├─ src/iran_obd_tuner/
│  ├─ app/          cli.py, gui_main.py, application.py, dashboard.py, bench.py, bench_flash.py
│  ├─ core/         config, logging, models, pids, dtc, monitors, safety, traffic, knowledge,
│  │                calibration, bench (contract + capabilities), bench_reports
│  ├─ hardware/     base, mock, registry, serial_ports, transport, elm327(+_protocol), can_bus,
│  │                isotp, uds, uds_link, uds_sim, uds_sim_programmable, bench_programmer, bench_link
│  ├─ diagnostics/  service, live_data, models, recorder, performance, dyno,
│  │                ecu_identification, vehicle_matcher, evidence_export
│  ├─ database/     repository, vehicle_schema, vehicles + data/ (DTCs, vehicle packs)
│  ├─ tuning/       rom_loader, map_definitions, tables, workspace, checksum, rom_export, demo,
│  │                interfaces, flash_plan, flash_gate, backup_vault, flash_journal, flash_orchestrator
│  └─ ui/           main_window, backend, worker, i18n, gauges, chart, icon, safety_modal,
│                   diagnostics_view, performance_view, traffic_view, uds_view, knowledge_view,
│                   bench_view, bench_flash_panel
├─ tests/           unit/, integration/ (CLI, GUI, virtual bus), fixtures/
├─ evidence/        reviewed Field Evidence Bundles (empty today)
├─ config/          example.toml
├─ docs/            architecture, development, field validation, bench flash protocol, idea report
├─ scripts/         check.py
└─ pyproject.toml, requirements.txt, CONTRIBUTING.md, LICENSE
```

<a id="en-docs"></a>

### 12. Documentation

| Document | Content |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Layers, dependency rules, safety boundary, ADRs 1–80 |
| [`docs/development.md`](docs/development.md) | Environment, tests without hardware, extension recipes, troubleshooting |
| [`docs/field-validation-protocol.md`](docs/field-validation-protocol.md) | Safe first connection to a real adapter and vehicle |
| [`docs/bench-flash-protocol.md`](docs/bench-flash-protocol.md) | Simulated bench flash and recovery: stages, journal, invariants |
| [`docs/idea-report.md`](docs/idea-report.md) | Product concept and roadmap |
| [`evidence/README.md`](evidence/README.md) | How a knowledge-base claim becomes `verified` |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Branches, commits, pull requests, review |
| [`docs/ai-assistant-rules.md`](docs/ai-assistant-rules.md) | Project rules for AI assistants |

The project documentation is written in Persian. This README is bilingual.

<a id="en-roadmap"></a>

### 13. Roadmap

Next, in order of value:

1. **Field validation** of the ELM327 driver and the CAN/UDS stack on a real adapter and on a
   Peugeot Pars TU5, following [`docs/field-validation-protocol.md`](docs/field-validation-protocol.md).
2. **Evidence-backed knowledge:** turn `unknown` claims into `verified` ones with reviewed Field
   Evidence Bundles.
3. Multi-ECU separation with headers, more freeze frames, PID `0141`, and checking the `0101` bit
   table against SAE J1979.
4. Documented map and checksum definitions for one Iranian ECU family (including multi-part
   checksums and Intel HEX/S-record).
5. **Real-ECU bench work stays locked** until an evidence-backed target profile, an authorised
   seed-key provider, lab testing and a separate owner approval exist. Flashing over the OBD socket is
   not planned.

<a id="en-license"></a>

### 14. Contributing, authors and license

Contributions follow [`CONTRIBUTING.md`](CONTRIBUTING.md). Authors: Mohammad Reza, Abbas.
Licensed under the [MIT License](LICENSE).

> [!WARNING]
> Changing ECU calibration can damage the engine, gearbox and exhaust system and affect vehicle
> safety, and it may void warranties or break the law. This project performs no write to any real
> ECU. Any future write capability requires vehicle-specific validation and a controlled test path.

[↑ Back to top](#top)

---

<a id="persian"></a>

<div dir="rtl">

## بخش ۲ — فارسی

### فهرست

۱. [معرفی](#fa-overview)<br>
۲. [وضعیت پروژه در یک نگاه](#fa-status)<br>
۳. [مدل ایمنی](#fa-safety)<br>
۴. [قابلیت‌ها](#fa-features)<br>
۵. [معماری](#fa-architecture)<br>
۶. [چه چیزی شبیه‌سازی‌شده است و چه چیزی هنوز اعتبارسنجی نشده](#fa-honesty)<br>
۷. [پیش‌نیازها و نصب](#fa-install)<br>
۸. [پیکربندی](#fa-config)<br>
۹. [شروع سریع](#fa-quickstart)<br>
۱۰. [تست و گیت‌های کیفیت](#fa-testing)<br>
۱۱. [ساختار پروژه](#fa-layout)<br>
۱۲. [مستندات](#fa-docs)<br>
۱۳. [نقشه راه](#fa-roadmap)<br>
۱۴. [مشارکت، نویسندگان و مجوز](#fa-license)

<a id="fa-overview"></a>

### ۱. معرفی

`iran-obd-tuner` یک بستر پایتونی برای عیب‌یابی خودروهای رایج در ایران است و در آینده، فقط پشت
مرزهای ایمنی صریح، برای کار Bench روی ECU هم به کار می‌رود. نخستین هدف آن پژو پارس با موتور TU5
است. توسعه پروژه **مستقل از سخت‌افزار** است: هر گردش کاری روی شبیه‌سازهای قطعی اجرا و تست
می‌شود، پس پروژه پیش از وجود هر آداپتور، خودرو یا میز ECU می‌تواند رشد کند.

اصول طراحی:

- **پیش‌فرض فقط‌خواندنی.** سیاست ایمنی پیش‌فرض فقط خواندن را مجاز می‌داند. هیچ چیزی که خودرو یا
  ECU را تغییر دهد از فایل تنظیمات یا متغیر محیطی فعال نمی‌شود.
- **جداسازی عیب‌یابی و تیونینگ.** مسیرهای عیب‌یابی هرگز کد Bench یا تیونینگ را import نمی‌کنند و
  تست‌های معماری این را تضمین می‌کنند.
- **صداقت داده.** داده شبیه‌سازی‌شده همه‌جا برچسب دارد: CLI، JSON، CSV، رابط گرافیکی و لاگ ترافیک.
  مقداری که خوانده نشود با دلیلش «ناموجود» نمایش داده می‌شود، هرگز با عدد جایگزین. هیچ مدل ECU،
  شناسه CAN یا قابلیت خودرویی بدون شاهد قابل ردیابی ادعا نمی‌شود.
- **توسعه و تست بدون سخت‌افزار.** هسته پروژه هیچ وابستگی اجرایی ندارد؛ پشتیبانی سخت‌افزار و رابط
  گرافیکی به‌صورت extraهای اختیاری است.

سند مفهومی کامل محصول: [`docs/idea-report.md`](docs/idea-report.md).

<a id="fa-status"></a>

### ۲. وضعیت پروژه در یک نگاه

| فاز | دامنه | Milestone | وضعیت |
|---|---|---|---|
| ۰ | پایه: بسته‌بندی، پیکربندی، لاگ، سیاست ایمنی، آداپتور شبیه‌ساز، CI | — | انجام‌شده |
| ۱ | عیب‌یابی OBD-II: یک مسیر داده، ELM327 فقط‌خواندنی، داده زنده، کدهای خطا، Freeze Frame، مانیتورها، ابزارهای عملکرد، داشبورد دوزبانه | M1، M2، M3 | پیاده‌شده؛ **اعتبارسنجی روی سخت‌افزار واقعی باقی است** |
| ۲ | CAN، ISO-TP و کلاینت UDS فقط‌خواندنی؛ شناسایی ECU | M4 | پیاده‌شده؛ **اعتبارسنجی روی رابط واقعی باقی است** |
| ۳ | پایگاه دانش خودرو و ECUهای ایرانی، تطبیق غیرفعال، بسته شواهد میدانی | M5 | ساختار پیاده‌شده؛ **ادعاهای فنی تا رسیدن شواهد میدانی «نامعلوم» می‌مانند** |
| ۴ | تحلیل آفلاین ROM: بارگذاری، تعریف نقشه، ویرایش جدول، چک‌سام، خروجی ایمن | M6 | پیاده‌شده (فقط فایل آفلاین؛ هنوز تعریف نقشه واقعی نیست) |
| — | سخت‌سازی پیش از آزمون میدانی (timeoutها، آزمون listen-only، قطع خودکار، گزارش نسخه ۳) | — | انجام‌شده |
| ۵ | فلش و بازیابی Bench: پشتیبان، فلش چهارمرحله‌ای، ژورنال مقاوم در برابر crash، بازیابی | M7 (7a تا 7g، 7e.1) | **فقط شبیه‌سازی**؛ ECU واقعی قفل |

<a id="fa-safety"></a>

### ۳. مدل ایمنی

پروژه دو کلاس عملیات دارد که در کد تعریف شده‌اند (`core/safety.py` و `core/bench.py`) و هرگز از
تنظیمات خوانده نمی‌شوند:

| | `READ_ONLY` (پیش‌فرض) | `BENCH_FLASH` (فقط شبیه‌سازی) |
|---|---|---|
| چه کسی آن را می‌دهد | همیشه فعال است | فقط `bench_flash_policy()`، از یک `BenchAttestation` انسانی کامل برای یک جلسه |
| چه چیزی را مجاز می‌کند | خواندن PID، کد خطا، Freeze Frame، VIN و DIDهای شناسایی UDS | برنامه‌ریزی ECU برنامه‌پذیر **مجازی** |
| چه چیزی را هرگز مجاز نمی‌کند | پاک کردن کد خطا، تست عملگر، Security Access، نوشتن، فلش واقعی | هیچ ECU، رابط، کانال یا درگاه واقعی |

تضمین‌های اصلی، که هر کدام با تست خودکار پشتیبانی می‌شوند:

- **فهرست سفید، نه فهرست سیاه.** درایور ELM327 فقط مجموعه‌ای ثابت از دستورهای AT و سرویس‌های OBD
  `01`، `02` (فریم `00`)، `03`، `07`، `0A` و `09` را می‌فرستد. کلاینت UDS فقط `0x10` (زیرتابع‌های
  `01`/`03`)، `0x19` (`01`/`02`)، `0x22` و `0x3E` را می‌فرستد. سرویس‌های نوشتن، امنیت و برنامه‌ریزی
  (`0x11`، `0x14`، `0x27`، `0x2E`، `0x2F`، `0x31`، `0x34` تا `0x37` و …) و جلسه برنامه‌ریزی پیش از
  هر ارسالی رد می‌شوند.
- **پاک کردن کد خطا پیاده نشده است.** دکمه آن در داشبورد فقط پنجره رد را نشان می‌دهد.
- **همه انتظارها محدودند.** نشست زنده‌ای که سه poll پیاپی هیچ پاسخی نگیرد (مثلاً سوئیچ بسته شده
  باشد) با `VehicleSilentError` قطع می‌شود، نه اینکه بی‌پایان از خودروی ساکت بپرسد. نوشتن سریال و
  timeoutهای فرمان و اتصال همه محدودند.
- **اول فقط‌شنود روی CAN واقعی.** رابط CAN واقعی (`slcan`، `pcan`، `kvaser`؛ فقط ۲۵۰ یا ۵۰۰ کیلوبیت بر
  ثانیه) پیش از نخستین ارسال یک ثانیه فقط گوش می‌دهد و با هر فریم خطا یا وضعیت خطای کنترلر متوقف
  می‌شود.
- **هیچ سخت‌افزار ضمنی‌ای در کار نیست.** درگاه سریال هرگز خودکار جستجو نمی‌شود و اتصال واقعیِ ناموفق
  هرگز بی‌صدا به شبیه‌ساز تبدیل نمی‌شود.
- **capabilityهای Bench.** تغییر حافظه ECU یک `FlashAuthorization` می‌خواهد. پاک کردن و نوشتن علاوه بر
  آن یک `BackupReceipt` می‌خواهند که به قطعه فیزیکی بسته است. هر کدام را فقط یک ماژول می‌تواند بسازد.
  `BenchTargetProfile` واقعی همان هنگام ساخت رد می‌شود.

<a id="fa-features"></a>

### ۴. قابلیت‌ها

#### ۴٫۱ هسته عیب‌یابی

- **یک مسیر داده تشخیصی** (قرارداد `ObdAdapter`) مشترک میان CLI و داشبورد.
- **نشست داده زنده** با وضعیت صریح برای هر پارامتر (پشتیبانی‌نشده / بدون پاسخ / پاسخ نامعتبر)، بدون
  مقدار جایگزین، همراه با قطع خودکار پس از سه poll بی‌پاسخ.
- **کدهای خطا:** ذخیره‌شده، در انتظار و دائمی (سرویس‌های `03` / `07` / `0A`). هر دسته وضعیت خودش را
  دارد، پس شکست یک دسته بقیه را پنهان نمی‌کند. کدها فقط خوانده می‌شوند.
- **Freeze Frame شماره `00`** (سرویس `02`) با کد عامل و هشت پارامتر.
- **وضعیت چراغ چک (MIL) و آمادگی مانیتورها** (PID `0101`).
- **گزارش JSON نسخه ۳** شامل `monitor_status`، `dtc_categories`، `freeze_frame` و `unanswered_pids`
  (جدا از PIDهای پشتیبانی‌نشده).
- **ضبط CSV** با زمان UTC، واحد در عنوان هر ستون و ستون `simulated`.
- **نمایشگر ترافیک خام** (TX/RX): محدود، ایمن برای چند thread، با توقف، پاک کردن و کپی.
- **ابزارهای عملکرد:** زمان‌سنج ۰ تا ۱۰۰ با عدم‌قطعیت اعلام‌شده و دینوی مجازی با توان و گشتاور
  **تخمینی**. هر دو غیرفعال‌اند و فقط از داده‌ای که از قبل poll شده استفاده می‌کنند.

#### ۴٫۲ لایه سخت‌افزار

- **آداپتور شبیه‌ساز** (ایستا و چرخه رانندگی متغیر با زمان) با تزریق خطای ارتباطی.
- **درایور ELM327 فقط‌خواندنی** برای USB/سریال (extra `[obd]`): راه‌اندازی، تشخیص پروتکل، PIDهای
  پشتیبانی‌شده، داده زنده، کدهای خطا، VIN، فهرست سفید دستورها و `AT PC` هنگام قطع اتصال. از
  `AT TP 0` استفاده می‌کند، نه `AT SP`، پس چیزی در EEPROM آداپتور نوشته نمی‌شود.
- **فهرست درگاه‌های سریال** بدون باز کردن هیچ درگاهی.
- **لایه CAN و ISO-TP (ISO 15765-2) خود پروژه** روی python-can (extra `[can]`): فریم‌های Single،
  First و Consecutive، Flow Control (BS، STmin، Wait)، padding و timeoutهای محدود. رابط‌های واقعی
  اول از آزمون اجباری فقط‌شنود عبور می‌کنند.
- **کلاینت UDS (ISO 14229-1) فقط‌خواندنی:** فهرست سفید پیش‌فرض‌-رد و مدیریت NRC (`0x78` با سقف،
  `0x21`، `0x22`، `0x31`، `0x33`). جلسه Extended فقط با انتخاب صریح و برای همان اجرا فعال می‌شود و
  همیشه به `0x10 0x01` برمی‌گردد.
- **شناسایی ECU با UDS:** DIDهای استاندارد `F190` (VIN)، `F187`، `F189`، `F191` و `F197` به‌علاوه
  کدهای خطای UDS، هر کدام با وضعیت مستقل. جستجوی ECU (`3E 00` روی `0x7DF`) فقط با درخواست کاربر
  اجرا می‌شود.
- **ECU شبیه‌سازی‌شده قطعی UDS** روی باس `virtual` داخل پروسه python-can (`virtual-sim`).

#### ۴٫۳ پایگاه دانش خودروهای ایرانی و شواهد میدانی

- **ادعاهای دارای منبع.** هر ادعا سطح اطمینان (`verified`، `documented`، `reported`، `unverified` یا
  `unknown`) و منبع قابل ردیابی دارد.
- **بسته‌های خودرو** برای ایران‌خودرو (پژو پارس، پژو ۲۰۶، سمند، دنا)، سایپا (پراید، تیبا، ساینا،
  کوییک) و ام‌وی‌ام (۳۱۵، X22)، با اعتبارسنج سخت‌گیر.
- **محتوای صادقانه.** پروتکل باس، شناسه‌های CAN و خانواده ECU عمداً `unknown` هستند. یک ادعا فقط با
  ارجاع به یک بسته شواهد میدانی بازبینی‌شده در [`evidence/`](evidence/README.md) می‌تواند `verified`
  شود و این پوشه امروز خالی است. **هنوز هیچ ادعایی `verified` نیست.**
- **تطبیق غیرفعال خودرو** با آنچه یک نشست مشاهده کرده است (پروتکل، PIDها، VIN، DIDهای UDS). چیزی
  ارسال نمی‌شود و نتیجه یعنی «سازگار با مشاهدات»، نه شناسایی قطعی.
- **خروجی بسته شواهد میدانی** (از CLI و تب «پایگاه دانش خودرو» در داشبورد): پروتکل، PIDها، DIDهای
  شناسایی و ترافیک خام در یک JSON با هش SHA-256. شش نویسه آخر VIN به‌طور پیش‌فرض پنهان می‌شود و
  خروجی‌ای که هنوز VIN داشته باشد رد می‌شود.

#### ۴٫۴ میز کار ROM و نقشه‌های کالیبراسیون (آفلاین)

- **بارگذاری فقط‌خواندنی فایل باینری** (`.bin`، ۱۶ کیلوبایت تا ۸ مگابایت) با SHA-256، CRC32، آمار
  پرشدگی و رشته‌های ASCII مشاهده‌شده (بدون ادعای شناسایی). فایل اصلی هرگز تغییر نمی‌کند.
- **تعریف نقشه** (JSON) با منبع و سطح اطمینان، بررسی سازگاری با ROM، و جدول‌های یک‌بعدی و دوبعدی با
  decode/encode محدود. مقدار بیرون از بازه رد می‌شود، هرگز clamp نمی‌شود.
- **ویرایش** با Undo/Redo و نماهای اصلی، اصلاح‌شده، تفاوت و درصد (نقشه حرارتی).
- **موتور چک‌سام** با الگوریتم‌های دقیقاً تعریف‌شده: `sum8`، `sum16_le`، `sum16_be`،
  `crc16_ccitt_false`، `crc32`، `twos_complement_8` و `twos_complement_16_le`. «معتبر» یعنی مطابق
  الگوریتم پیکربندی‌شده، نه اینکه ECUای آن را بپذیرد.
- **خروجی ایمن** فقط به فایلی دیگر و پس از تأیید صریح. تا وقتی چک‌سام پیکربندی‌شده‌ای نامعتبر باشد
  رد می‌شود و یک مانیفست با `validated_for_vehicle: false` می‌نویسد.
- **ROM نمایشی برچسب‌دار SIMULATED** و تعریف نقشه‌هایش، برای امتحان همه چیز بدون داده واقعی.

#### ۴٫۵ موتور ایمن فلش و بازیابی Bench (فاز ۵، فقط شبیه‌سازی)

- **`VirtualProgrammableEcu`:** یک هدف bootloader شبیه‌سازی‌شده و قطعی. از قواعد فلش NOR پیروی می‌کند
  (بیت‌ها فقط از ۱ به ۰ می‌روند و پاک‌سازی سکتور کامل است). application را فقط پس از عبور روتین
  بررسی روی کل فلش معتبر می‌داند و در غیر این صورت به bootloader برمی‌گردد. seed-key با شمارنده تلاش
  و تأخیر دارد، و یک `FaultPlan` قطعی که هر خطا را فقط یک بار اجرا می‌کند: قطع لینک، NRC، قطع برق با
  بلوک نیمه‌نوشته، بازخوانی خراب و کلید نادرست.
- **`BenchProgrammer` با capability:** تنها ماژولی که درخواست برنامه‌ریزی می‌سازد، پشت فهرست سفید
  پیش‌فرض‌-رد خودش. هر متد تغییردهنده اول `FlashAuthorization` را بررسی می‌کند. erase و download
  علاوه بر آن `BackupReceipt` را بررسی می‌کنند که به **هویت قطعه** ECU (شماره سخت‌افزار و سریال)
  بسته است. فقط بلوک‌های `0x36` در یک بودجه محدود تکرار می‌شوند و کلید امنیتی هرگز دوباره امتحان
  نمی‌شود.
- **انبار پشتیبان:** دو خواندن کامل و مستقل که باید برابر باشند، فایل یک‌بارنویس همراه مانیفست،
  بازخوانی از دیسک پیش از صدور رسید، و بررسی دوباره درست پیش از erase.
- **ژورنال اتمیک فلش، مقاوم در برابر crash:** هر مرحله *پیش* از انجامش ثبت می‌شود (فایل موقت، fsync،
  جایگزینی اتمیک). قفل تک‌جلسه هرگز خودکار شکسته نمی‌شود و ژورنال ناتمام یا ناخوانا هر فلش جدید را
  مسدود می‌کند.
- **فرایند چهارمرحله‌ای:** پیش‌پرواز ← پشتیبان کامل اجباری ← انتقال برنامه ← بازبینی (روتین بررسی، هش
  بازخوانی، reset، هویت). پس از شروع erase، شکست با حالت `interrupted` ثبت می‌شود و *دیگر هیچ چیز*
  ارسال نمی‌شود؛ ECU نیمه‌نوشته کورکورانه reset نمی‌شود.
- **بازیابی** پشتیبان تأییدشده ثبت‌شده در ژورنال را برمی‌گرداند و هرگز پشتیبان تازه نمی‌گیرد. هم
  bootloader هدف را می‌پذیرد و هم application **همان قطعه** را، حتی اگر نرم‌افزارش عوض شده باشد.
- **CLI:** دستور `bench flash-sim` (پیش‌فرض ROM نمایشی SIMULATED، و گزینه‌های `--rom/--maps`،
  `--fault`، `--recover` و `--json`) و دستور `bench journal show|discard-stale`. هیچ گزینه‌ای برای
  رابط، کانال یا درگاه وجود ندارد.
- **پنل دوزبانه در تب Bench:** modal تأیید شامل چهار جمله انسانی، بررسی‌های ماشینی زنده ✓/✗، نام
  اپراتور و تایپ عبارت `BENCH FLASH`؛ این تأیید فقط در حافظه همان پنجره نگه داشته می‌شود. نمایشگر
  چهارمرحله‌ای با پیشرفت بایت و بلوک، مسیر و SHA-256 پشتیبان قابل کپی، و دکمه لغو که درست از لحظه
  شروع erase قفل می‌شود. برای ژورنال ناتمام یا خراب، یک **بنر بازیابی اضطراری** در کل پنجره دیده
  می‌شود.

فرایند کامل، حالت‌های ژورنال و ناوردهای ایمنی: [`docs/bench-flash-protocol.md`](docs/bench-flash-protocol.md).

#### ۴٫۶ داشبورد دسکتاپ

داشبورد PySide6 به فارسی (راست‌به‌چپ) و انگلیسی (چپ‌به‌راست) با تم Slate + Indigo. تب‌ها: داشبورد
(گیج‌ها)، نمودار زنده، کدهای خطا، عملکرد، شناسایی خودرو (OBD و UDS)، پایگاه دانش خودرو، تیونینگ
(Bench) شامل میز کار ROM و فلش شبیه‌سازی‌شده، تنظیمات و رویدادها (لاگ و ترافیک خام). ارتباط با
آداپتور روی یک thread پس‌زمینه و کار Bench شبیه‌سازی‌شده روی thread جداگانه‌ای اجرا می‌شود، پس پنجره
هرگز قفل نمی‌شود.

<a id="fa-architecture"></a>

### ۵. معماری

</div>

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ app/   CLI, GUI entry point and wiring: the only layer that imports all                 │
└─────────────────────────────────────────────────────────────────────────────────────────┘
          │                       │                      │                      │
          ▼                       ▼                      ▼                      ▼
┌───────────────────┐   ┌───────────────────┐   ┌────────────────┐   ┌────────────────────┐
│ ui/               │──▶│ diagnostics/      │──▶│ hardware/      │   │ tuning/            │
│ PySide6 views,    │   │ sessions, reports │   │ adapters, CAN, │   │ ROM workbench,     │
│ Qt-free protocols │   │ (+ database/)     │   │ ISO-TP, UDS,   │   │ bench orchestrator │
│                   │   │                   │   │ bench link     │   │ (port only)        │
└───────────────────┘   └───────────────────┘   └────────────────┘   └────────────────────┘
          │                       │                      │                      │
          ▼                       ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ core/  models, safety policy, bench contract, reports (stdlib only)                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

<div dir="rtl">

قواعد وابستگی (با `tests/unit/test_architecture.py` اجرا می‌شوند):

- `core` جز خودش هیچ ماژول داخلی دیگری را import نمی‌کند و فقط از کتابخانه استاندارد استفاده می‌کند.
- `hardware`، `database` و `tuning` فقط `core` (و خودشان) را import می‌کنند. `tuning` هرگز به
  سخت‌افزار دست نمی‌زند: هماهنگ‌کننده Bench یک Protocol به نام `BenchProgrammerPort` را که در `core`
  تعریف شده به کار می‌گیرد.
- `diagnostics` می‌تواند از `core`، `hardware` و `database` استفاده کند و هیچ چیز درباره کار Bench
  نمی‌داند.
- `ui` فقط از `core` و `diagnostics` استفاده می‌کند. با برنامه از طریق Protocolهای بدون Qt در
  `ui/backend.py` حرف می‌زند و هرگز `tuning`، capabilityها یا سیاست ایمنی را نمی‌بیند.
- `app` همه چیز را به هم وصل می‌کند. `app/bench_flash.py` **تنها** جایی است که هماهنگ‌کننده تیونینگ
  به لینک Bench سخت‌افزار می‌رسد.

نمونه‌ای از گاردهای AST: هیچ مسیر python-OBD یا پاک کردن کد خطا وجود ندارد؛ literal درخواست‌های
نوشتنی فقط در دو ماژول فهرست سفید مجازند؛ هر capability را دقیقاً یک ماژول می‌سازد؛ `BENCH_FLASH` فقط
در ماژول‌های Bench و هرگز در تنظیمات دیده می‌شود؛ لینک Bench فقط باس `virtual` را باز می‌کند؛
سیم‌کشی Bench فقط لینک شبیه‌سازی‌شده پیش‌فرض را می‌سازد؛ رابط گرافیکی حالت فلش Bench را هرگز ذخیره
نمی‌کند و فقط attestation شبیه‌سازی‌شده می‌سازد؛ نوشتن فایل در `tuning` فقط در سه ماژول نام‌برده مجاز
است. تصمیم‌ها در قالب ۸۰ ADR کوتاه در [`docs/architecture.md`](docs/architecture.md) ثبت شده‌اند.

<a id="fa-honesty"></a>

### ۶. چه چیزی شبیه‌سازی‌شده است و چه چیزی هنوز اعتبارسنجی نشده

**شبیه‌سازی‌شده، و با همین برچسب:**

- همه داده‌های زنده و کدهای خطای آداپتور `mock`: گزارش‌ها `simulated: true` دارند، فایل‌های CSV ستون
  `simulated`، و داشبورد یک نوار هشدار دائمی نشان می‌دهد.
- ECU شبیه‌سازی‌شده UDS (`virtual-sim`) که مقادیرش آشکارا ساختگی‌اند (`SIMULATEDVIN00004`، `SIM-…`).
- ROM نمایشی (سرآیند `SIMULATED-DEMO-ROM / NOT AN ECU IMAGE`) و تعریف نقشه‌هایش، که هیچ ECU واقعی را
  توصیف نمی‌کنند.
- ECU برنامه‌پذیر مجازی، seed-key منتشرشده XOR آن، پشتیبان‌های `SIMULATED-` و فایل
  `SIMULATED-bench-ecu-state.json`.
- توضیح کدهای خطا، که از یک زیرمجموعه عمومی دست‌نویس می‌آید (`authoritative: false`).

**پیاده‌شده ولی هنوز روی سخت‌افزار واقعی اعتبارسنجی‌نشده:** درایور ELM327، پشته CAN/ISO-TP/UDS روی
رابط‌های واقعی، جدول بیت‌های `0101` (هنوز با متن SAE J1979 مقایسه نشده) و ابزارهای عملکرد (با GPS یا
دینامومتر مقایسه نشده‌اند). اینکه پژو پارس TU5 اصلاً روی پایه‌های CAN سوکت OBD به UDS پاسخ می‌دهد یا
نه، هنوز معلوم نیست.

**عمداً قفل:** خواندن و فلش ECU واقعی روی Bench، پاک کردن کد خطا، تست عملگر، دور زدن Security Access و
هر الگوریتم seed-key واقعی. باز شدن مسیر ECU واقعی نیازمند profile مستند هدف، `SeedKeyProvider` مجاز،
رابط CAN واقعی، آزمون کنترل‌شده آزمایشگاهی و تأیید جداگانه صاحب پروژه است.

<a id="fa-install"></a>

### ۷. پیش‌نیازها و نصب

- پایتون **۳٫۱۱ یا جدیدتر** (CI روی ۳٫۱۱، ۳٫۱۲ و ۳٫۱۳ اجرا می‌شود).
- برای گردش‌کارهای شبیه‌سازی‌شده هیچ سخت‌افزاری لازم نیست.

| extra | نصب می‌کند | لازم برای |
|---|---|---|
| *(هیچ)* | هیچ چیز؛ هسته فقط از کتابخانه استاندارد استفاده می‌کند | عیب‌یابی CLI با آداپتور شبیه‌ساز، میز کار ROM، پایگاه دانش |
| `[gui]` | PySide6، pyqtgraph | داشبورد دسکتاپ |
| `[can]` | python-can | `uds-ident`، فلش Bench شبیه‌سازی‌شده، رابط‌های CAN واقعی |
| `[obd]` | pyserial | آداپتور ELM327 واقعی |
| `[bench]` | udsoncan، crcmod | برای کارهای آینده تعریف شده؛ کد فعلی آن را import نمی‌کند |
| `[dev]` | pytest، ruff | توسعه و تست |
| `[full]` | همه extraهای اجرایی | برنامه کامل دسکتاپ |

</div>

```bash
git clone https://github.com/mohammadreza4188/iran-obd-tuner.git
cd iran-obd-tuner
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev,gui,can]"
```

<div dir="rtl">

بدون نصب هم می‌توان تست‌ها را با `python -m pytest` و CLI را با `PYTHONPATH=src python -m iran_obd_tuner`
اجرا کرد.

<a id="fa-config"></a>

### ۸. پیکربندی

</div>

```bash
cp config/example.toml config/local.toml   # git-ignored
IRAN_OBD_TUNER_LOG_LEVEL=DEBUG iran-obd-tuner config
```

<div dir="rtl">

ترتیب اولویت: پیش‌فرض‌های کد ← `config/local.toml` ← متغیرهای محیطی `IRAN_OBD_TUNER_*`. کلید ناشناخته
و مقدار نامعتبر رد می‌شوند و بی‌صدا نادیده گرفته نمی‌شوند. timeoutهای بیرون از بازه رد می‌شوند، نه
clamp. هیچ رمز یا کلید محرمانه‌ای لازم نیست. سیاست ایمنی و حالت فلش Bench را **نمی‌توان** از اینجا
تنظیم کرد.

<a id="fa-quickstart"></a>

### ۹. شروع سریع

#### خط فرمان

</div>

```bash
iran-obd-tuner version                       # version and interpreter
iran-obd-tuner adapters                      # declared adapters and their status
iran-obd-tuner diagnose                      # one read-only session, simulated adapter
iran-obd-tuner diagnose --json --output output/report.json
iran-obd-tuner diagnose --adapter elm327 --port COM3 --no-live   # real adapter, least traffic
iran-obd-tuner uds-ident --discover          # UDS identification, simulated ECU ([can])
iran-obd-tuner vehicles list                 # knowledge base with confidence levels
iran-obd-tuner vehicles show ikco.peugeot_pars
iran-obd-tuner vehicles match --report output/report.json --uds output/ecu.json
iran-obd-tuner bench demo --output output/demo
iran-obd-tuner bench inspect output/demo/simulated_demo_rom.bin --maps output/demo/simulated_demo_maps.json
iran-obd-tuner bench table output/demo/simulated_demo_rom.bin --maps output/demo/simulated_demo_maps.json --map demo_fuel_base
```

<div dir="rtl">

همه دستورها به شکل `python -m iran_obd_tuner <command>` هم کار می‌کنند.

#### فلش و بازیابی Bench شبیه‌سازی‌شده (نیازمند `[can]`)

</div>

```bash
iran-obd-tuner bench flash-sim --output output/bench                      # flash the SIMULATED demo ROM
iran-obd-tuner bench flash-sim --output output/bench --fault power-loss-at-block=20   # exits 3
iran-obd-tuner bench journal show output/bench                            # dirty: recovery required
iran-obd-tuner bench flash-sim --output output/bench --recover            # restore the verified backup
iran-obd-tuner bench journal discard-stale output/bench                   # only for stale journals
```

<div dir="rtl">

خطاهای شبیه‌ساز: `drop-at-block=N`، `nrc72-at-block=N`، `power-loss-at-block=N` (N از ۱ تا ۶۴)،
`bad-key` و `corrupt-readback`.

کدهای خروج: `0` موفق؛ `1` خطای اجرا (برای `flash-sim` همچنین اجرایی که پیش از هر erase متوقف شده)؛ `2`
خطای کاربرد یا پیکربندی (همچنین تصویری که flash plan رد می‌کند)؛ `3` ژورنال ناتمام مانده و بازیابی لازم
است؛ `130` قطع با Ctrl+C.

#### داشبورد (نیازمند `[gui]`)

</div>

```bash
iran-obd-tuner-gui                 # or: python -m iran_obd_tuner.app.gui_main
```

<div dir="rtl">

۱. در تب **تنظیمات** زبان و حالت «شبیه‌سازی» را انتخاب کنید و **اتصال** را بزنید تا گیج‌ها و نمودارها
بدون آداپتور کار کنند. با ⏺ داده را در قالب CSV در پوشه `output/` ضبط کنید.<br>
۲. تب **شناسایی خودرو** شناسایی OBD و UDS را می‌خواند. تب **پایگاه دانش خودرو** یک نشست را با پایگاه
دانش تطبیق می‌دهد و بسته شواهد میدانی را خروجی می‌گیرد.<br>
۳. در تب **تیونینگ (Bench)** یک ROM یا نمونه SIMULATED را برای تحلیل آفلاین باز کنید. پایین همان تب،
**فعال‌سازی حالت فلش Bench…** modal تأیید را باز می‌کند و سپس **شروع فلش شبیه‌سازی‌شده** را بزنید. با
انتخاب یک خطای آموزشی، بنر بازیابی اضطراری را می‌بینید و می‌توانید **شروع بازیابی** را اجرا کنید.

روی ماشین بدون نمایشگر، `QT_QPA_PLATFORM=offscreen` را تنظیم کنید.

<a id="fa-testing"></a>

### ۱۰. تست و گیت‌های کیفیت

</div>

```bash
python scripts/check.py            # byte-compile, ruff check, ruff format --check, pytest
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

<div dir="rtl">

- `scripts/check.py` سخت‌گیر است: ابزار غایب **شکست** حساب می‌شود، نه skip.
- آخرین اجرای محلی روی پایتون ۳٫۱۳ با همه extraها: **۱٬۳۱۱ تست پاس و ۱ skip** (تست symlink که ویندوز
  پشتیبانی نمی‌کند). بدون `[gui]` یا `[can]`، تست‌هایی که به آن‌ها نیاز دارند skip می‌شوند.
- همه تست‌ها بدون سخت‌افزار، نمایشگر و شبکه اجرا می‌شوند: transport سریال اسکریپتی، باس CAN جعلی، باس
  `virtual` داخل پروسه python-can و Qt بدون نمایشگر (`offscreen`). هیچ تستی درگاه سریال یا رابط CAN
  واقعی باز نمی‌کند.
- CI (`.github/workflows/ci.yml`) لینت، قالب‌بندی و تست‌ها را روی پایتون ۳٫۱۱ تا ۳٫۱۳ با `[can]` اجرا
  می‌کند، به‌علاوه یک job بدون extraهای اختیاری. `[gui]` در CI نصب نمی‌شود.

<a id="fa-layout"></a>

### ۱۱. ساختار پروژه

</div>

```
iran-obd-tuner/
├─ src/iran_obd_tuner/
│  ├─ app/          cli.py, gui_main.py, application.py, dashboard.py, bench.py, bench_flash.py
│  ├─ core/         config, logging, models, pids, dtc, monitors, safety, traffic, knowledge,
│  │                calibration, bench (contract + capabilities), bench_reports
│  ├─ hardware/     base, mock, registry, serial_ports, transport, elm327(+_protocol), can_bus,
│  │                isotp, uds, uds_link, uds_sim, uds_sim_programmable, bench_programmer, bench_link
│  ├─ diagnostics/  service, live_data, models, recorder, performance, dyno,
│  │                ecu_identification, vehicle_matcher, evidence_export
│  ├─ database/     repository, vehicle_schema, vehicles + data/ (DTCs, vehicle packs)
│  ├─ tuning/       rom_loader, map_definitions, tables, workspace, checksum, rom_export, demo,
│  │                interfaces, flash_plan, flash_gate, backup_vault, flash_journal, flash_orchestrator
│  └─ ui/           main_window, backend, worker, i18n, gauges, chart, icon, safety_modal,
│                   diagnostics_view, performance_view, traffic_view, uds_view, knowledge_view,
│                   bench_view, bench_flash_panel
├─ tests/           unit/, integration/ (CLI, GUI, virtual bus), fixtures/
├─ evidence/        reviewed Field Evidence Bundles (empty today)
├─ config/          example.toml
├─ docs/            architecture, development, field validation, bench flash protocol, idea report
├─ scripts/         check.py
└─ pyproject.toml, requirements.txt, CONTRIBUTING.md, LICENSE
```

<div dir="rtl">

<a id="fa-docs"></a>

### ۱۲. مستندات

| سند | محتوا |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | لایه‌ها، قواعد وابستگی، مرز ایمنی، ADRهای ۱ تا ۸۰ |
| [`docs/development.md`](docs/development.md) | محیط توسعه، تست بدون سخت‌افزار، الگوهای افزودن قابلیت، عیب‌یابی |
| [`docs/field-validation-protocol.md`](docs/field-validation-protocol.md) | روش ایمن نخستین اتصال به آداپتور و خودروی واقعی |
| [`docs/bench-flash-protocol.md`](docs/bench-flash-protocol.md) | فلش و بازیابی Bench شبیه‌سازی‌شده: مراحل، ژورنال، ناوردها |
| [`docs/idea-report.md`](docs/idea-report.md) | سند مفهومی و نقشه راه محصول |
| [`evidence/README.md`](evidence/README.md) | چگونه یک ادعای پایگاه دانش `verified` می‌شود |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | شاخه، کامیت، Pull Request، بازبینی |
| [`docs/ai-assistant-rules.md`](docs/ai-assistant-rules.md) | قواعد پروژه برای دستیارهای هوش مصنوعی |

مستندات پروژه به فارسی نوشته شده‌اند و این README دوزبانه است.

<a id="fa-roadmap"></a>

### ۱۳. نقشه راه

گام‌های بعدی، به ترتیب ارزش:

۱. **اعتبارسنجی میدانی** درایور ELM327 و پشته CAN/UDS روی آداپتور واقعی و یک پژو پارس TU5، طبق
[`docs/field-validation-protocol.md`](docs/field-validation-protocol.md).<br>
۲. **دانش مبتنی بر شاهد:** تبدیل ادعاهای `unknown` به `verified` با بسته‌های شواهد میدانی بازبینی‌شده.<br>
۳. تفکیک چند ECU با headerها، Freeze Frameهای بیشتر، PID `0141` و مقایسه جدول بیت‌های `0101` با SAE J1979.<br>
۴. تعریف مستند نقشه و چک‌سام برای یک خانواده ECU ایرانی (شامل چک‌سام‌های چندبخشی و Intel HEX/S-record).<br>
۵. **کار Bench روی ECU واقعی قفل می‌ماند** تا profile مستند هدف، `SeedKeyProvider` مجاز، آزمون
آزمایشگاهی و تأیید جداگانه صاحب پروژه فراهم شود. فلش از طریق سوکت OBD برنامه‌ریزی نشده است.

<a id="fa-license"></a>

### ۱۴. مشارکت، نویسندگان و مجوز

مشارکت طبق [`CONTRIBUTING.md`](CONTRIBUTING.md) انجام می‌شود. نویسندگان: محمد رضا، عباس. مجوز:
[MIT](LICENSE).

> **هشدار:** تغییر کالیبراسیون ECU می‌تواند به موتور، جعبه‌دنده و سیستم اگزوز آسیب بزند و بر ایمنی
> خودرو اثر بگذارد، و ممکن است گارانتی یا قانون را نقض کند. این پروژه هیچ نوشتنی روی ECU واقعی انجام
> نمی‌دهد. هر قابلیت نوشتنی در آینده نیازمند اعتبارسنجی مخصوص خودرو و مسیر تست کنترل‌شده است.

[↑ بازگشت به بالا](#top)

</div>
