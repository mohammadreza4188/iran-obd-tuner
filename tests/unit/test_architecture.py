"""Layer boundaries and safety invariants, checked on the source code itself.

These rules used to exist only in docs/architecture.md; here they are enforced:

* each layer imports only the layers below it (table ``ALLOWED``);
* third-party packages are imported at module level only by the Qt layers (``ui``, the GUI
  entry point), and lazily (inside functions) only by ``hardware`` adapter modules;
* nothing in the package refers to an OBD "clear trouble codes" command, and no class
  overrides the refused ``ObdAdapter.clear_diagnostic_trouble_codes``.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

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
QT_FREE_FILES = {
    "ui/__init__.py",
    "ui/i18n.py",
    "ui/backend.py",
    "app/cli.py",
    "app/dashboard.py",
    "app/bench_flash.py",
}


@dataclass(frozen=True)
class ImportUse:
    file: str
    layer: str
    module: str
    module_level: bool


def _layer_of(relative: Path) -> str:
    return relative.parts[0] if len(relative.parts) > 1 else ""


def _is_first_party_module(name: str) -> bool:
    if name != ROOT and not name.startswith(f"{ROOT}."):
        return False
    path = PACKAGE_DIR.joinpath(*name.split(".")[1:])
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _imported_modules(node: ast.AST) -> list[str]:
    """Modules an import statement loads (relative imports are refused by their own test).

    Milestone 7e: ``from iran_obd_tuner.hardware import bench_link`` loads the submodule
    ``iran_obd_tuner.hardware.bench_link``; it used to be recorded as the package only.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        submodules = [f"{node.module}.{alias.name}" for alias in node.names]
        return [node.module] + [name for name in submodules if _is_first_party_module(name)]
    return []


def _imports() -> list[ImportUse]:
    uses: list[ImportUse] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_level_nodes = set(map(id, tree.body))
        for node in ast.walk(tree):
            names = _imported_modules(node)
            if not names:
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
#: Milestone 7c: the bench programmer is the only module that may compose programming
#: requests (behind its own default-deny allowlist and the capability checks).
PROGRAMMER_MODULE = "hardware/bench_programmer.py"
WRITE_SERVICE_NAME_MODULES = {POLICY_MODULE, SIMULATOR_MODULE, PROGRAMMER_MODULE}
#: Modules that may build a request starting with a write service identifier.
WRITE_REQUEST_MODULES = {POLICY_MODULE, PROGRAMMER_MODULE}


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
        if relative in WRITE_REQUEST_MODULES:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            first = _first_request_byte(node)
            if first in WRITE_SIDS:  # Milestone 7c: the whole package, not only three layers
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


def test_write_service_identifiers_are_named_only_where_requests_may_be_built() -> None:
    """Closes the gap of the literal scan above: ``bytes([SID_X, ...])`` with a named constant.

    A module-level ``SID_*`` constant holding a write service identifier may exist only in
    the modules that are allowed to build such requests.
    """
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(PACKAGE_DIR).as_posix()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            value = getattr(node, "value", None)
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.startswith("SID_")
                    and isinstance(value, ast.Constant)
                    and value.value in WRITE_SIDS
                    and relative not in WRITE_REQUEST_MODULES
                ):
                    offenders.append(f"{relative}:{node.lineno} {target.id}")
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
#: Milestone 7d: the backup vault and the flash journal are the other two file writers.
TUNING_FILE_WRITERS = {FILE_WRITER, "tuning/backup_vault.py", "tuning/flash_journal.py"}


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


def _is_os_attr(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


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
                # builtin open(path, mode) and os.open(path, flags) carry the mode second;
                # Path.open(mode) carries it first (Milestone 7d: that form was not seen)
                position = 1 if isinstance(func, ast.Name) or _is_os_attr(func) else 0
                modes = list(node.args[position : position + 1]) + [
                    k.value for k in node.keywords if k.arg in ("mode", "flags")
                ]
                writes = any(
                    not (isinstance(m, ast.Constant) and set(str(m.value)) <= {"r", "b", "t"})
                    for m in modes
                )
            if writes and relative not in TUNING_FILE_WRITERS:
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


# --------------------------------------------------------------------------- #
# Milestone 7c: the bench programmer is the single, gated writer (simulated only)
# --------------------------------------------------------------------------- #

LINK_MODULE = "hardware/bench_link.py"
#: Who may import the bench link (the 7d wiring) - and nobody imports the programmer but it.
BENCH_LINK_IMPORTERS = {"app/bench_flash.py"}
_CAPABILITY_CHECKS = {"_check_authorization", "_check_capabilities"}


def _class_named(relative: str, name: str) -> ast.ClassDef:
    tree = ast.parse((PACKAGE_DIR / relative).read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _first_real_statement(function: ast.FunctionDef) -> ast.stmt:
    body = function.body
    has_docstring = (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    return body[1] if has_docstring else body[0]


def test_the_programmer_implements_the_port_signatures() -> None:
    assert _capability_problems(_class_named(PROGRAMMER_MODULE, "BenchProgrammer")) == []


def test_every_state_changing_method_checks_its_capabilities_first() -> None:
    programmer = _class_named(PROGRAMMER_MODULE, "BenchProgrammer")
    methods = {f.name: f for f in programmer.body if isinstance(f, ast.FunctionDef)}
    problems = []
    for name, required in CAPABILITY_PARAMETERS.items():
        first = _first_real_statement(methods[name])
        call = first.value if isinstance(first, ast.Expr) else None
        func = getattr(call, "func", None)
        ok = (
            isinstance(call, ast.Call)
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr in _CAPABILITY_CHECKS
        )
        if ok and "backup" in required:
            ok = func.attr == "_check_capabilities" and len(call.args) == 3
        if not ok:
            problems.append(f"BenchProgrammer.{name}: first statement is not a capability check")
    assert problems == []


def test_the_programmer_sends_only_through_its_allowlist_choke_point() -> None:
    programmer = _class_named(PROGRAMMER_MODULE, "BenchProgrammer")
    senders = []
    for function in (f for f in programmer.body if isinstance(f, ast.FunctionDef)):
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send"
            ):
                senders.append(function.name)
    assert senders == ["_transmit"], senders
    transmit = next(
        f for f in programmer.body if isinstance(f, ast.FunctionDef) and f.name == "_transmit"
    )
    first = _first_real_statement(transmit)
    assert (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Name)
        and first.value.func.id == "check_bench_request"
    ), "the allowlist check must come before anything is sent"


def test_the_programmer_and_the_link_are_reachable_only_through_the_link() -> None:
    importers: dict[str, set[str]] = {"bench_programmer": set(), "bench_link": set()}
    constructed = []
    for use in _imports():
        for name in importers:
            if use.module == f"{ROOT}.hardware.{name}":
                importers[name].add(use.file)
    for relative, tree in _package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BenchProgrammer"
            ):
                constructed.append(relative)
    assert importers["bench_programmer"] <= {LINK_MODULE}, importers
    assert importers["bench_link"] <= BENCH_LINK_IMPORTERS, importers
    assert set(constructed) <= {LINK_MODULE}, constructed
    assert constructed  # the scan sees the real construction


def test_the_bench_link_opens_only_the_virtual_bus() -> None:
    tree = ast.parse((PACKAGE_DIR / LINK_MODULE).read_text(encoding="utf-8"))
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
        assert not (isinstance(node, ast.Attribute) and node.attr == "Bus"), node.lineno
    assert opens and all(opens), opens
    from iran_obd_tuner.hardware.bench_link import BENCH_LINK_INTERFACES

    assert BENCH_LINK_INTERFACES == ("virtual-sim-programmable",)


def test_the_bench_allowlist_is_exactly_what_bench_programming_needs() -> None:
    from iran_obd_tuner.hardware.bench_programmer import (
        BENCH_ALLOWED_REQUESTS,
        BENCH_FORBIDDEN_SERVICES,
    )

    assert set(BENCH_ALLOWED_REQUESTS) == {
        0x10, 0x11, 0x22, 0x27, 0x31, 0x34, 0x35, 0x36, 0x37, 0x3E
    }  # fmt: skip
    assert set(BENCH_FORBIDDEN_SERVICES) == {0x14, 0x28, 0x2E, 0x2F, 0x3D, 0x85, 0x87}
    assert not set(BENCH_ALLOWED_REQUESTS) & set(BENCH_FORBIDDEN_SERVICES)


def test_the_read_only_paths_stay_locked_after_milestone_7c() -> None:
    from iran_obd_tuner.hardware.elm327_protocol import _ALLOWED_OBD, ALLOWED_AT_COMMANDS

    assert (
        frozenset({"ATZ", "ATE0", "ATL0", "ATH0", "ATTP0", "ATDPN", "ATDP", "ATI", "ATRV", "ATPC"})
        == ALLOWED_AT_COMMANDS
    )
    assert _ALLOWED_OBD.pattern == r"01[0-9A-F]{2}|02[0-9A-F]{2}00|03|07|0A|09[0-9A-F]{2}"
    bench_modules = {
        f"{ROOT}.hardware.bench_programmer",
        f"{ROOT}.hardware.bench_link",
        f"{ROOT}.hardware.uds_sim_programmable",
    }
    offenders = [
        f"{use.file}: {use.module}"
        for use in _imports()
        if use.module in bench_modules
        and (use.file in READ_ONLY_PATH_MODULES or use.layer in {"diagnostics", "ui"})
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# Milestone 7d: orchestration in tuning - port only, ordered, never a blind reset
# --------------------------------------------------------------------------- #

ORCHESTRATOR_MODULE = "tuning/flash_orchestrator.py"
BENCH_TUNING_MODULES = (
    "tuning/flash_plan.py",
    "tuning/flash_gate.py",
    "tuning/backup_vault.py",
    "tuning/flash_journal.py",
    ORCHESTRATOR_MODULE,
)


def _function(relative: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((PACKAGE_DIR / relative).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _call_lines(node: ast.AST, attribute: str) -> list[int]:
    return sorted(
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == attribute
    )


def test_the_bench_tuning_modules_exist_and_stay_off_hardware() -> None:
    for relative in BENCH_TUNING_MODULES:
        assert (PACKAGE_DIR / relative).exists(), relative
    offenders = [
        f"{use.file}: {use.module}"
        for use in _imports()
        if use.file in BENCH_TUNING_MODULES and _first_party_layer(use.module) == "hardware"
    ]
    assert offenders == []


def test_the_backup_is_re_verified_on_disk_right_before_the_erase() -> None:
    stages = _function(ORCHESTRATOR_MODULE, "_program_and_verify")
    verify, erase = _call_lines(stages, "verify"), _call_lines(stages, "erase_region")
    assert verify and erase and verify[0] < erase[0]


def test_the_backup_is_journaled_before_stages_three_and_four() -> None:
    flash = _function(ORCHESTRATOR_MODULE, "flash")
    journaled = [
        call.lineno
        for call in ast.walk(flash)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "transition"
        and call.args
        and ast.unparse(call.args[0]) == "JournalState.BACKUP_VERIFIED"
    ]
    programmed = [
        call.lineno
        for call in ast.walk(flash)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_program_and_verify"
    ]
    assert journaled and programmed and journaled[0] < programmed[0]


def test_a_reset_happens_only_after_a_successful_verification() -> None:
    tree = ast.parse((PACKAGE_DIR / ORCHESTRATOR_MODULE).read_text(encoding="utf-8"))
    callers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and _call_lines(function, "hard_reset")
    }
    assert callers == {"_program_and_verify"}  # never in _fail_flash or a recovery handler
    stages = _function(ORCHESTRATOR_MODULE, "_program_and_verify")
    assert _call_lines(stages, "check_memory")[0] < _call_lines(stages, "hard_reset")[0]
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        assert not _call_lines(handler, "hard_reset"), handler.lineno


def test_the_orchestrator_mints_nothing_and_only_uses_the_port() -> None:
    tree = ast.parse((PACKAGE_DIR / ORCHESTRATOR_MODULE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in CAPABILITY_MINTERS, node.lineno
        if isinstance(node, ast.Attribute):
            assert not node.attr.startswith("_check_"), node.lineno  # programmer internals
    assert _first_request_byte_free(tree)


def _first_request_byte_free(tree: ast.AST) -> bool:
    return all(_first_request_byte(node) is None for node in ast.walk(tree))


def test_the_writer_scan_sees_path_open_writes() -> None:
    """Milestone 7d closed this gap: ``Path.open("wb")`` carries its mode first."""
    vault = (PACKAGE_DIR / "tuning" / "backup_vault.py").read_text(encoding="utf-8")
    journal = (PACKAGE_DIR / "tuning" / "flash_journal.py").read_text(encoding="utf-8")
    assert '.open("xb")' in vault and '.open("wb")' in journal


# --------------------------------------------------------------------------- #
# Milestone 7e: one wiring point, simulated-only CLI (D7, the exception to ADR 46)
# --------------------------------------------------------------------------- #

#: The single module where the tuning orchestration meets the hardware bench link.
BENCH_WIRING_MODULE = "app/bench_flash.py"
#: The shared, Qt-free reports of a bench session (``ui`` may read them in 7f).
BENCH_REPORTS_MODULE = "core/bench_reports.py"
_BENCH_TUNING_IMPORT_NAMES = {
    f"{ROOT}.tuning.{Path(relative).stem}" for relative in BENCH_TUNING_MODULES
}


def test_the_bench_wiring_is_the_single_meeting_point_of_tuning_and_hardware() -> None:
    assert (PACKAGE_DIR / BENCH_WIRING_MODULE).exists()
    assert {BENCH_WIRING_MODULE} == BENCH_LINK_IMPORTERS
    uses = _imports()
    tuning_importers = {
        use.file
        for use in uses
        if use.module in _BENCH_TUNING_IMPORT_NAMES and use.layer != "tuning"
    }
    link_importers = {use.file for use in uses if use.module == f"{ROOT}.hardware.bench_link"}
    assert tuning_importers == {BENCH_WIRING_MODULE}, tuning_importers
    assert link_importers == {BENCH_WIRING_MODULE}, link_importers
    # nothing reaches the wiring but the CLI (and, from 7f, the dashboard wiring in app)
    wiring_importers = {use.file for use in uses if use.module == f"{ROOT}.app.bench_flash"}
    assert wiring_importers <= {"app/cli.py", "app/dashboard.py", "app/gui_main.py"}
    assert all(not f.startswith(("ui/", "tuning/", "hardware/")) for f in wiring_importers)


def test_the_bench_wiring_only_ever_builds_the_default_simulated_link() -> None:
    tree = ast.parse((PACKAGE_DIR / BENCH_WIRING_MODULE).read_text(encoding="utf-8"))
    configs, links = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "BenchLinkConfig":
                configs.append(not node.args and not node.keywords)  # no interface, no channel
            if node.func.id == "BenchLink":
                first = node.args[0] if node.args else None
                links.append(
                    isinstance(first, ast.Call)
                    and getattr(first.func, "id", "") == "BenchLinkConfig"
                    and {k.arg for k in node.keywords} <= {"ecu", "timing"}
                )
    assert configs and all(configs), configs
    assert links and all(links), links


def test_the_bench_wiring_mints_nothing_and_composes_no_request() -> None:
    tree = ast.parse((PACKAGE_DIR / BENCH_WIRING_MODULE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in CAPABILITY_MINTERS, node.lineno
        if isinstance(node, ast.Name):
            assert node.id != "_MINT", node.lineno
    assert _first_request_byte_free(tree)
    assert not _bench_flash_references(tree)


def test_the_bench_reports_are_passive_and_ui_safe() -> None:
    tree = ast.parse((PACKAGE_DIR / BENCH_REPORTS_MODULE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in _FILE_WRITE_CALLS | {"open", "send", "write"}, (name, node.lineno)
        assert _first_request_byte(node) is None, getattr(node, "lineno", "?")
    imported = {use.module for use in _imports() if use.file == BENCH_REPORTS_MODULE}
    first_party = {m for m in imported if m.startswith(ROOT)}
    assert first_party == {f"{ROOT}.core.bench"}, first_party
    assert not _bench_flash_references(tree)


@pytest.mark.parametrize(
    "option",
    ["--interface", "--channel", "--port", "--adapter", "--bitrate", "--bus", "--target"],
)
def test_the_simulated_flash_cli_has_no_way_to_name_real_hardware(option: str) -> None:
    from iran_obd_tuner.app.cli import build_parser

    parser = build_parser()
    for argv in (
        ["bench", "flash-sim", "--output", "x", option, "COM5"],
        ["bench", "flash-sim", "--output", "x", "--recover", option, "COM5"],
    ):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(argv)
        assert raised.value.code == 2


def test_the_cli_still_offers_no_rom_edit_or_export() -> None:
    """ADR 46 stands: flash-sim is the only bench exception, and it is simulated."""
    from iran_obd_tuner.app.cli import build_parser

    def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return dict(action.choices)

    bench = subcommands(build_parser())["bench"]
    assert set(subcommands(bench)) == {"inspect", "table", "demo", "flash-sim", "journal"}


def test_the_import_scan_sees_every_first_party_import() -> None:
    """Milestone 7e closed this gap: ``from iran_obd_tuner.hardware import bench_link`` was
    recorded as ``iran_obd_tuner.hardware`` only, and relative imports were not seen at all,
    so every "only X may import Y" rule could be bypassed."""
    relative = [
        f"{path.relative_to(PACKAGE_DIR).as_posix()}:{node.lineno}"
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert relative == []  # absolute imports only, so the scan below sees all of them
    sneaky = ast.parse(
        "\n".join(
            [f"from {ROOT}.hardware import bench_link, BENCH_X", f"from {ROOT} import __version__"]
        )
    )
    found = [name for node in sneaky.body for name in _imported_modules(node)]
    assert found == [f"{ROOT}.hardware", f"{ROOT}.hardware.bench_link", ROOT]


# --------------------------------------------------------------------------- #
# Milestone 7e.1: backups are bound to the unit, never to the full identity
# --------------------------------------------------------------------------- #

#: Modules that may compute the full identity fingerprint - to *record* it, never to bind.
FULL_FINGERPRINT_RECORDERS = {"tuning/backup_vault.py", "tuning/flash_journal.py"}


def test_nothing_binds_a_backup_to_the_full_identity_fingerprint() -> None:
    """The full fingerprint includes the software DIDs, so it changes with every flash; a
    binding on it made the recovery after a verify-stage fault impossible (ADR 75)."""
    callers = []
    for relative, tree in _package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "fingerprint"
            ):
                callers.append(relative)
    assert set(callers) <= FULL_FINGERPRINT_RECORDERS, callers
    assert callers  # the scan sees the recorders
    for module in (PROGRAMMER_MODULE, ORCHESTRATOR_MODULE):
        tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
        assert _call_lines(tree, "unit_fingerprint"), module  # they bind to the unit


# --------------------------------------------------------------------------- #
# Milestone 7f: the simulated bench flash UI - session only, simulated only
# --------------------------------------------------------------------------- #

BENCH_PANEL_MODULE = "ui/bench_flash_panel.py"
#: Names that would persist the bench flash mode (decision D4: never stored).
_PERSISTENCE_NAMES = {"QSettings", "shelve", "pickle", "sqlite3"}


def test_the_bench_flash_mode_is_never_persisted_by_the_ui() -> None:
    offenders = []
    for relative, tree in _package_trees():
        if not relative.startswith("ui/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in _PERSISTENCE_NAMES:
                    offenders.append(f"{relative}:{node.lineno} {name}")
    panel = ast.parse((PACKAGE_DIR / BENCH_PANEL_MODULE).read_text(encoding="utf-8"))
    for node in ast.walk(panel):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # QDialog.open() takes no argument; a file open (builtin or Path) does
            file_open = name == "open" and (isinstance(func, ast.Name) or node.args)
            if name in _FILE_WRITE_CALLS or file_open:
                offenders.append(f"{BENCH_PANEL_MODULE}:{node.lineno} {name}")
    assert offenders == []
    opens = [c for c in ast.walk(panel) if isinstance(c, ast.Call) and _call_name(c) == "open"]
    assert opens  # the scan sees the dialogs being opened


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def test_the_ui_only_ever_builds_simulated_attestations() -> None:
    calls = []
    for relative, tree in _package_trees():
        if not relative.startswith("ui/"):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BenchAttestation"
            ):
                simulated = next((k.value for k in node.keywords if k.arg == "simulated"), None)
                calls.append(isinstance(simulated, ast.Constant) and simulated.value is True)
    assert calls and all(calls), calls


def test_the_app_service_implements_the_ui_protocol() -> None:
    import inspect

    from iran_obd_tuner.app.bench_flash import BenchFlashService as Service
    from iran_obd_tuner.ui.backend import BenchFlashService as Protocol

    methods = [
        name
        for name, value in vars(Protocol).items()
        if callable(value) and not name.startswith("_")
    ]
    assert len(methods) == 6, methods
    for name in methods:
        wanted = inspect.signature(getattr(Protocol, name))
        actual = inspect.signature(getattr(Service, name))
        assert list(wanted.parameters) == list(actual.parameters), name
        for param in wanted.parameters.values():
            assert actual.parameters[param.name].kind == param.kind, (name, param.name)
        # nothing in the contract could name a real interface, channel or port
        assert not {"interface", "channel", "port", "bitrate"} & set(wanted.parameters), name


def test_the_window_reaches_bench_flashing_only_through_the_backend() -> None:
    for relative in ("ui/main_window.py", BENCH_PANEL_MODULE):
        modules = {use.module for use in _imports() if use.file == relative}
        assert not any(m.startswith(f"{ROOT}.app") for m in modules), relative
        assert not any(_first_party_layer(m) in {"tuning", "hardware"} for m in modules), relative
