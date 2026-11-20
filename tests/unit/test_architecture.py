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
        if relative != POLICY_MODULE and any(name in text for name in WRITE_SERVICE_NAMES):
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
