"""Layer boundaries and safety invariants, checked on the source code itself.

These rules used to exist only in docs/architecture.md; here they are enforced:

* each layer imports only the layers below it (table ``ALLOWED``);
* third-party packages are imported at module level only by the Qt layers (``ui``, the GUI
  entry point), and lazily (inside functions) only by ``hardware`` adapter modules;
* nothing in the package refers to an OBD "clear trouble codes" command, and no class
  overrides the refused ``ObdAdapter.clear_diagnostic_trouble_codes``.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import iran_obd_tuner

PACKAGE_DIR = Path(iran_obd_tuner.__file__).parent
ROOT = "iran_obd_tuner"

#: layer -> first-party layers it may import ("" is the package root, e.g. __version__).
ALLOWED: dict[str, set[str]] = {
    "core": {"core"},
    "hardware": {"core", "hardware"},
    "database": {"core", "database"},
    "tuning": {"core", "tuning"},
    "diagnostics": {"core", "hardware", "database", "diagnostics"},
    "ui": {"", "core", "diagnostics", "ui"},
    "app": {"", "core", "hardware", "database", "diagnostics", "tuning", "ui", "app"},
    "": {"", "app"},  # __init__.py and __main__.py
}

#: (layer, module-level third-party top-level package) pairs that are allowed.
MODULE_LEVEL_THIRD_PARTY: set[tuple[str, str]] = {("ui", "PySide6"), ("app", "PySide6")}
#: Qt may be imported at module level by these files only (CLI and wiring stay Qt-free).
QT_FREE_FILES = {"ui/__init__.py", "ui/i18n.py", "ui/backend.py", "app/cli.py", "app/dashboard.py"}


@dataclass(frozen=True)
class ImportUse:
    file: str
    layer: str
    module: str
    module_level: bool


def _layer_of(relative: Path) -> str:
    return relative.parts[0] if len(relative.parts) > 1 else ""


def _imports() -> list[ImportUse]:
    uses: list[ImportUse] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_level_nodes = set(map(id, tree.body))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                uses.append(
                    ImportUse(
                        file=relative.as_posix(),
                        layer=_layer_of(relative),
                        module=name,
                        module_level=id(node) in module_level_nodes,
                    )
                )
    return uses


def _first_party_layer(module: str) -> str | None:
    if module == ROOT:
        return ""
    if module.startswith(f"{ROOT}."):
        return module.split(".")[1]
    return None


def test_layers_only_import_what_they_may() -> None:
    violations = []
    for use in _imports():
        target = _first_party_layer(use.module)
        if target is not None and target not in ALLOWED[use.layer]:
            violations.append(f"{use.file}: imports {use.module}")
    assert violations == []


def test_third_party_imports_follow_the_optional_dependency_rule() -> None:
    violations = []
    for use in _imports():
        top = use.module.split(".")[0]
        if _first_party_layer(use.module) is not None or top in sys.stdlib_module_names:
            continue
        if use.module_level:
            allowed = (use.layer, top) in MODULE_LEVEL_THIRD_PARTY and use.file not in QT_FREE_FILES
        else:
            allowed = use.layer == "hardware"
        if not allowed:
            where = "module level" if use.module_level else "function level"
            violations.append(f"{use.file}: imports {use.module} at {where}")
    assert violations == []


def test_nothing_refers_to_a_clear_trouble_codes_command() -> None:
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "CLEAR_DTC" in text:  # python-OBD's service 04 command object
            offenders.append(path.relative_to(PACKAGE_DIR).as_posix())
    assert offenders == []


def test_no_adapter_overrides_the_refused_clear_method() -> None:
    """Only the base class defines it, and it always refuses (see test_mock_adapter)."""
    definitions = []
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "clear_diagnostic_trouble_codes":
                definitions.append(path.relative_to(PACKAGE_DIR).as_posix())
    assert definitions == ["hardware/base.py"]


def test_the_rules_above_actually_see_the_code() -> None:
    """Guard against a silently empty scan (wrong path, renamed package)."""
    uses = _imports()
    assert any(use.layer == "ui" and use.module == "PySide6.QtWidgets" for use in uses)
    assert any(use.file == "hardware/serial_ports.py" and not use.module_level for use in uses)


# --------------------------------------------------------------------------- #
# Milestone 4: CAN / UDS safety invariants
# --------------------------------------------------------------------------- #

#: UDS services that write, erase, reset, unlock or reprogram (see hardware/uds.py).
WRITE_SIDS = {0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37, 0x3D, 0x85, 0x87}
WRITE_SERVICE_NAMES = (
    "ECUReset",
    "ClearDiagnosticInformation",
    "SecurityAccess",
    "WriteDataByIdentifier",
    "InputOutputControlByIdentifier",
    "RoutineControl",
    "RequestDownload",
    "RequestUpload",
    "TransferData",
    "RequestTransferExit",
)
#: The only module allowed to name these services: the allowlist that refuses them.
POLICY_MODULE = "hardware/uds.py"
#: Milestone 7b: the virtual programmable ECU is the *answering* side of these services; it
#: may name them, but (scanned below like every other module) never build a request.
SIMULATOR_MODULE = "hardware/uds_sim_programmable.py"
WRITE_SERVICE_NAME_MODULES = {POLICY_MODULE, SIMULATOR_MODULE}


def _first_request_byte(node: ast.AST) -> int | None:
    """First byte of a literal request: b"..", bytes([..]) or bytes.fromhex("..")."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes) and node.value:
        return node.value[0]
    if isinstance(node, ast.Call) and node.args:
        func, arg = node.func, node.args[0]
        if (
            isinstance(func, ast.Name)
            and func.id == "bytes"
            and isinstance(arg, (ast.List, ast.Tuple))
        ):
            first = arg.elts[0] if arg.elts else None
            if isinstance(first, ast.Constant) and isinstance(first.value, int):
                return first.value
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "fromhex"
            and isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and len(arg.value.replace(" ", "")) >= 2
        ):
            return int(arg.value.replace(" ", "")[:2], 16)
    return None


def test_no_literal_request_starts_with_a_write_service() -> None:
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR).as_posix()
        if relative == POLICY_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            first = _first_request_byte(node)
            if first in WRITE_SIDS and relative.startswith(("hardware/", "diagnostics/", "app/")):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


def test_write_service_names_appear_only_in_the_allowlist() -> None:
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative not in WRITE_SERVICE_NAME_MODULES and any(
            name in text for name in WRITE_SERVICE_NAMES
        ):
            offenders.append(relative)
    assert offenders == []


def test_every_python_can_bus_names_its_interface_explicitly() -> None:
    """python-can would otherwise take the interface from ~/.canrc or the environment."""
    calls = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Bus"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "can"
            ):
                keywords = {k.arg for k in node.keywords}
                calls.append((path.name, "interface" in keywords))
    assert calls and all(explicit for _name, explicit in calls), calls


def test_can_libraries_stay_inside_hardware_functions() -> None:
    for use in _imports():
        top = use.module.split(".")[0]
        if top in {"can", "isotp", "udsoncan"}:
            assert use.layer == "hardware" and not use.module_level, use


def test_the_simulated_ecu_server_is_only_started_by_the_virtual_sim_link() -> None:
    """Only ``hardware/uds_link.py`` (for ``virtual-sim``) may create the simulator server."""
    creators = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "VirtualBusEcuServer"
            ):
                creators.append(path.relative_to(PACKAGE_DIR).as_posix())
    assert creators == ["hardware/uds_link.py"]


# --------------------------------------------------------------------------- #
# Milestone 5: vehicle knowledge base, matcher and evidence export
# --------------------------------------------------------------------------- #

#: Passive modules: they may take protocol *names* from hardware, never a transport.
PASSIVE_MODULES = ("diagnostics/vehicle_matcher.py", "diagnostics/evidence_export.py")
_WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch", "rename"}


def _hardware_names(relative: str) -> set[tuple[str, str]]:
    tree = ast.parse((PACKAGE_DIR / relative).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(f"{ROOT}.hardware"):
            names |= {(node.module or "", alias.name) for alias in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(f"{ROOT}.hardware"), (relative, alias.name)
    return names


def test_matcher_and_evidence_export_cannot_talk_to_a_vehicle() -> None:
    allowed = {(f"{ROOT}.hardware.elm327_protocol", "PROTOCOL_NAMES")}
    for relative in PASSIVE_MODULES:
        assert _hardware_names(relative) <= allowed, relative
    assert _hardware_names("diagnostics/vehicle_matcher.py") == allowed  # the scan works


def test_the_knowledge_database_never_writes_files() -> None:
    offenders = []
    for path in sorted((PACKAGE_DIR / "database").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _WRITE_CALLS:
                offenders.append(f"{path.name}:{node.lineno} {func.attr}")
            if isinstance(func, ast.Name) and func.id == "open":
                modes = list(node.args[1:2]) + [k.value for k in node.keywords if k.arg == "mode"]
                for mode in modes:
                    if not (
                        isinstance(mode, ast.Constant) and set(str(mode.value)) <= {"r", "b", "t"}
                    ):
                        offenders.append(f"{path.name}:{node.lineno} open")
    assert offenders == []


def test_milestone_5_did_not_widen_the_request_allowlists() -> None:
    """Manufacturer identifiers in the packs are knowledge only: nothing can send them."""
    from iran_obd_tuner.core.exceptions import SafetyGuardError
    from iran_obd_tuner.hardware.elm327_protocol import check_command_allowed
    from iran_obd_tuner.hardware.uds import ALLOWED_REQUESTS, check_request_allowed

    assert set(ALLOWED_REQUESTS) == {0x10, 0x3E, 0x22, 0x19}
    for request in (b"\x21\x01", b"\x2e\xf1\x90\x00", b"\x27\x01"):
        try:
            check_request_allowed(request)
        except SafetyGuardError:
            continue
        raise AssertionError(f"UDS allowlist accepts {request.hex()}")
    for command in ("2101", "22F190", "04", "1001"):
        try:
            check_command_allowed(command)
        except SafetyGuardError:
            continue
        raise AssertionError(f"ELM327 allowlist accepts {command}")


def test_vehicle_packs_are_data_only() -> None:
    """Packs ship as JSON next to the code that validates them, never as Python."""
    data = PACKAGE_DIR / "database" / "data" / "vehicles"
    names = sorted(p.name for p in data.iterdir() if p.is_file())
    assert names and all(name.endswith(".json") for name in names)


# --------------------------------------------------------------------------- #
# Milestone 6: offline ROM analysis (Phase 4); Phase 5 stays locked
# --------------------------------------------------------------------------- #

#: Standard library modules that could reach a network, a process or native code.
_TUNING_FORBIDDEN_STDLIB = {
    "socket",
    "ssl",
    "subprocess",
    "ctypes",
    "urllib",
    "http",
    "ftplib",
    "asyncio",
}
#: Calls that create, replace or delete files.
_FILE_WRITE_CALLS = {
    "write_text",
    "write_bytes",
    "replace",
    "rename",
    "unlink",
    "touch",
    "mkdir",
    "NamedTemporaryFile",
    "mkstemp",
}
FILE_WRITER = "tuning/rom_export.py"


def test_tuning_uses_only_core_and_harmless_standard_library() -> None:
    offenders = []
    for use in _imports():
        if use.layer != "tuning":
            continue
        top = use.module.split(".")[0]
        first_party = _first_party_layer(use.module)
        if first_party is not None:
            if first_party not in {"core", "tuning"}:
                offenders.append(f"{use.file}: {use.module}")
        elif top not in sys.stdlib_module_names or top in _TUNING_FORBIDDEN_STDLIB:
            offenders.append(f"{use.file}: {use.module}")
    assert offenders == []


def test_only_the_exporter_writes_files_in_tuning() -> None:
    offenders = []
    for path in sorted((PACKAGE_DIR / "tuning").rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            writes = name in _FILE_WRITE_CALLS
            if name == "open":
                modes = list(node.args[1:2]) + [k.value for k in node.keywords if k.arg == "mode"]
                writes = any(
                    not (isinstance(m, ast.Constant) and set(str(m.value)) <= {"r", "b", "t"})
                    for m in modes
                )
            if writes and relative != FILE_WRITER:
                offenders.append(f"{relative}:{node.lineno} {name}")
    assert offenders == []


def test_the_exporter_scan_sees_its_writes() -> None:
    text = (PACKAGE_DIR / FILE_WRITER).read_text(encoding="utf-8")
    assert "NamedTemporaryFile" in text and ".replace(target)" in text


def test_nothing_implements_an_ecu_calibration_writer() -> None:
    definitions = []
    for path in PACKAGE_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name == "write_calibration":
                definitions.append(path.relative_to(PACKAGE_DIR).as_posix())
    assert definitions == ["tuning/interfaces.py"]  # the Protocol only


def test_phase5_stays_locked_by_the_default_policy() -> None:
    import pytest

    from iran_obd_tuner.core.exceptions import SafetyGuardError
    from iran_obd_tuner.core.safety import DEFAULT_POLICY, OperationClass
    from iran_obd_tuner.tuning.interfaces import BenchSafetyChecklist, assert_write_allowed

    assert not DEFAULT_POLICY.allows(OperationClass.CALIBRATION_WRITE)
    assert not DEFAULT_POLICY.allows(OperationClass.BENCH_FLASH)
    everything_checked = BenchSafetyChecklist(**dict.fromkeys(BenchSafetyChecklist.__slots__, True))
    assert everything_checked.is_satisfied
    with pytest.raises(SafetyGuardError):
        assert_write_allowed(everything_checked)


def test_ui_never_imports_tuning() -> None:
    offenders = [
        f"{use.file}: {use.module}"
        for use in _imports()
        if use.layer == "ui" and _first_party_layer(use.module) == "tuning"
    ]
    assert offenders == []
    assert "tuning" not in ALLOWED["ui"]


# --------------------------------------------------------------------------- #
# Milestone 7a: bench flash contract (Phase 5) - mode, capabilities, read-only paths
# --------------------------------------------------------------------------- #

#: Modules of the existing read-only vehicle paths. Milestone 7 must not touch their
#: guarantees: none of them may know about bench programming at all.
READ_ONLY_PATH_MODULES = (
    "hardware/base.py",
    "hardware/can_bus.py",
    "hardware/elm327.py",
    "hardware/elm327_protocol.py",
    "hardware/isotp.py",
    "hardware/mock.py",
    "hardware/registry.py",
    "hardware/serial_ports.py",
    "hardware/transport.py",
    "hardware/uds.py",
    "hardware/uds_link.py",
    "hardware/uds_sim.py",
)
#: Files that may refer to ``OperationClass.BENCH_FLASH`` (planned files included; a file
#: that does not exist yet simply has nothing to check).
BENCH_FLASH_REFERENCES = {
    "core/bench.py",
    "tuning/flash_gate.py",
    "hardware/bench_programmer.py",
}
#: capability -> the only module that may construct it (and import the mint token).
CAPABILITY_MINTERS = {
    "FlashAuthorization": "tuning/flash_gate.py",
    "BackupReceipt": "tuning/backup_vault.py",
}
#: Port methods that change the ECU, and the capability parameters each must declare.
CAPABILITY_PARAMETERS = {
    "enter_programming_session": {"authorization": "FlashAuthorization"},
    "unlock": {"authorization": "FlashAuthorization"},
    "upload_region": {"authorization": "FlashAuthorization"},
    "erase_region": {"authorization": "FlashAuthorization", "backup": "BackupReceipt"},
    "download_region": {"authorization": "FlashAuthorization", "backup": "BackupReceipt"},
    "check_memory": {"authorization": "FlashAuthorization"},
    "hard_reset": {"authorization": "FlashAuthorization"},
}
#: Public surface of the read-only UDS client (Milestone 4); bench work must not widen it.
UDS_CLIENT_PUBLIC = {
    "diagnostic_session_control",
    "extended_session",
    "extended_session_allowed",
    "in_extended_session",
    "read_data_by_identifier",
    "read_dtc_count",
    "read_dtcs_by_status_mask",
    "tester_present",
}


def _package_trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(PACKAGE_DIR).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
    ]


def _bench_flash_references(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "BENCH_FLASH"
        and isinstance(node.value, ast.Name)
        and node.value.id == "OperationClass"
    ]


def test_the_read_only_uds_client_is_not_widened() -> None:
    from iran_obd_tuner.hardware.uds import (
        ALLOWED_REQUESTS,
        SID_SESSION_CONTROL,
        SID_TESTER_PRESENT,
    )

    assert set(ALLOWED_REQUESTS) == {0x10, 0x3E, 0x22, 0x19}
    sessions = ALLOWED_REQUESTS[SID_SESSION_CONTROL] or frozenset()
    assert {sub & 0x7F for sub in sessions} == {0x01, 0x03}  # never 0x02 programming
    assert ALLOWED_REQUESTS[SID_TESTER_PRESENT] == frozenset({0x00, 0x80})
    tree = ast.parse((PACKAGE_DIR / "hardware" / "uds.py").read_text(encoding="utf-8"))
    client = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UdsClient"
    )
    public = {
        item.name
        for item in client.body
        if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")
    }
    assert public == UDS_CLIENT_PUBLIC


def test_read_only_paths_know_nothing_about_bench_programming() -> None:
    offenders = []
    for relative, tree in _package_trees():
        in_read_only_path = relative in READ_ONLY_PATH_MODULES or relative.startswith(
            "diagnostics/"
        )
        if not in_read_only_path:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == f"{ROOT}.core.bench":
                offenders.append(f"{relative}: imports core.bench")
        if _bench_flash_references(tree):
            offenders.append(f"{relative}: refers to BENCH_FLASH")
    assert offenders == []


def test_bench_flash_is_referenced_only_by_the_bench_modules() -> None:
    offenders = [
        f"{relative}:{line}"
        for relative, tree in _package_trees()
        if relative not in BENCH_FLASH_REFERENCES
        for line in _bench_flash_references(tree)
    ]
    assert offenders == []
    core = ast.parse((PACKAGE_DIR / "core" / "bench.py").read_text(encoding="utf-8"))
    assert _bench_flash_references(core)  # the scan sees the real reference


def test_bench_flash_mode_can_never_come_from_settings() -> None:
    from dataclasses import fields as dataclass_fields

    from iran_obd_tuner.core.config import FIELD_CASTERS, AppSettings

    names = {item.name for item in dataclass_fields(AppSettings)} | set(FIELD_CASTERS)
    assert not any("flash" in name or "bench" in name for name in names), names
    for relative in ("core/config.py", "app/dashboard.py", "app/application.py"):
        text = (PACKAGE_DIR / relative).read_text(encoding="utf-8")
        assert "BENCH_FLASH" not in text and "bench_flash_policy" not in text, relative


def test_capabilities_are_minted_only_by_their_minting_modules() -> None:
    constructed: dict[str, list[str]] = {name: [] for name in CAPABILITY_MINTERS}
    token_users = []
    for relative, tree in _package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in CAPABILITY_MINTERS
            ):
                constructed[node.func.id].append(relative)
            if isinstance(node, ast.ImportFrom) and any(a.name == "_MINT" for a in node.names):
                token_users.append(relative)
            if isinstance(node, ast.Attribute) and node.attr == "_MINT":
                token_users.append(relative)
    for capability, minter in CAPABILITY_MINTERS.items():
        assert set(constructed[capability]) <= {minter}, (capability, constructed[capability])
    assert set(token_users) <= set(CAPABILITY_MINTERS.values()), token_users


def _capability_problems(cls: ast.ClassDef) -> list[str]:
    problems = []
    methods = {item.name: item for item in cls.body if isinstance(item, ast.FunctionDef)}
    for name, required in CAPABILITY_PARAMETERS.items():
        method = methods.get(name)
        if method is None:
            problems.append(f"{cls.name}.{name} is missing")
            continue
        keyword_only = {
            arg.arg: ast.unparse(arg.annotation) if arg.annotation else None
            for arg in method.args.kwonlyargs
        }
        for parameter, annotation in required.items():
            if keyword_only.get(parameter) != annotation:
                problems.append(f"{cls.name}.{name} needs keyword-only {parameter}: {annotation}")
    return problems


def test_port_methods_that_change_the_ecu_demand_capabilities() -> None:
    tree = ast.parse((PACKAGE_DIR / "core" / "bench.py").read_text(encoding="utf-8"))
    port = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BenchProgrammerPort"
    )
    assert _capability_problems(port) == []


def test_every_declared_bench_target_is_simulated() -> None:
    """Belt and braces next to the runtime refusal: no source line declares a real target."""
    declarations = []
    for relative, tree in _package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BenchTargetProfile"
            ):
                simulated = next((k.value for k in node.keywords if k.arg == "simulated"), None)
                literal_true = isinstance(simulated, ast.Constant) and simulated.value is True
                declarations.append((relative, literal_true))
    assert declarations and all(ok for _relative, ok in declarations), declarations


def test_ui_never_handles_bench_capabilities_or_the_policy() -> None:
    forbidden = {
        "_MINT",
        "FlashAuthorization",
        "BackupReceipt",
        "bench_flash_policy",
        "BenchProgrammerPort",
    }
    offenders = []
    for relative, tree in _package_trees():
        if not relative.startswith("ui/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == f"{ROOT}.core.bench":
                offenders += [f"{relative}: {a.name}" for a in node.names if a.name in forbidden]
    assert offenders == []


def test_the_bench_contract_is_passive() -> None:
    """core/bench.py describes bench work; it opens, writes and sends nothing."""
    tree = ast.parse((PACKAGE_DIR / "core" / "bench.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in _FILE_WRITE_CALLS | {"open", "send", "write"}, (name, node.lineno)
        assert _first_request_byte(node) is None, getattr(node, "lineno", "?")


# --------------------------------------------------------------------------- #
# Milestone 7b: the virtual programmable ECU stays a simulator on the virtual bus
# --------------------------------------------------------------------------- #

#: The only module that may create the programmable simulator's server (planned, 7c).
PROGRAMMABLE_SERVER_CREATORS = {"hardware/bench_link.py"}
#: Request service identifiers of bench work; the simulator answers them but never builds one.
_BENCH_REQUEST_SIDS = WRITE_SIDS | {0x10, 0x19, 0x22, 0x3E}


def _simulator_tree() -> ast.Module:
    return ast.parse((PACKAGE_DIR / SIMULATOR_MODULE).read_text(encoding="utf-8"))


def test_the_programmable_simulator_opens_only_the_virtual_bus() -> None:
    tree = _simulator_tree()
    opens = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "PythonCanBus"
        ):
            first = node.args[0] if node.args else None
            opens.append(isinstance(first, ast.Constant) and first.value == "virtual")
        if isinstance(node, ast.Attribute) and node.attr == "Bus":
            raise AssertionError(f"{SIMULATOR_MODULE}:{node.lineno} uses can.Bus directly")
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            names = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
            assert "interface" not in names, f"{SIMULATOR_MODULE}:{node.lineno}"
    assert opens and all(opens), opens  # at least one open, every one literally "virtual"
    for use in _imports():
        if use.file == SIMULATOR_MODULE:
            assert use.module.split(".")[0] != "can", use


def test_the_programmable_simulator_server_is_created_only_by_the_bench_link() -> None:
    creators = []
    for relative, tree in _package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "VirtualProgrammableEcuServer"
            ):
                creators.append(relative)
    assert set(creators) <= PROGRAMMABLE_SERVER_CREATORS, creators


def test_the_programmable_simulator_is_imported_only_by_the_bench_link() -> None:
    importers = {
        use.file for use in _imports() if use.module == f"{ROOT}.hardware.uds_sim_programmable"
    }
    assert importers <= PROGRAMMABLE_SERVER_CREATORS, importers


def test_the_programmable_simulator_builds_answers_never_requests() -> None:
    offenders = [
        f"{SIMULATOR_MODULE}:{node.lineno}"
        for node in ast.walk(_simulator_tree())
        if _first_request_byte(node) in _BENCH_REQUEST_SIDS
    ]
    assert offenders == []
    literals = [n for n in ast.walk(_simulator_tree()) if _first_request_byte(n) is not None]
    assert literals  # the scan sees the module's answers


def test_the_read_only_simulator_still_refuses_every_write_service() -> None:
    from iran_obd_tuner.hardware.uds_sim import SimulatedUdsEcu

    ecu = SimulatedUdsEcu()
    for sid in sorted(WRITE_SIDS):
        assert ecu.handle(bytes([sid, 0x01])) == [bytes([0x7F, sid, 0x11])], hex(sid)
