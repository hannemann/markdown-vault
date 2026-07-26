#!/usr/bin/env python3
"""Generate an ASCII dependency map of the markdown_vault package.

Parses internal imports (from . import X / from .module import Y) across all
.py files in the markdown_vault package and outputs a tree diagram grouped by
layer. Designed to replace the <!-- DEPENDENCY_MAP_START --> ... <!--
DEPENDENCY_MAP_END --> block in AGENTS.md.

Run from project root:
    python3 scripts/dep_map.py
"""

import ast
import os
import re
import sys
from pathlib import Path

PACKAGE_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "lib" / "python3.13" / "site-packages" / "markdown_vault"
)

# Skip non-module files
SKIP = {"__init__.py", "__main__.py"}

# Manual layer assignment (overrides auto-detection)
LAYERS = {
    "Entry": {"main", "__main__"},
    "UI": {
        "app_window", "editor", "preview", "tabs", "sidebar",
        "vault_tree", "vault_monitor", "search", "preferences",
        "markdown_help", "banners", "dialogs",
    },
    "Service": {
        "monitor_handler", "git_integration", "history",
        "autosave", "file_ops", "search_logic",
    },
    "Data": {
        "config", "session", "backlink_index", "file_index",
    },
    "Utils": {
        "tags", "validation", "path_utils", "latex_mathml", "mru",
    },
}


def _collect_modules():
    """Return dict of module_name -> list of imported module names."""
    modules = {}
    for pyfile in sorted(PACKAGE_DIR.glob("*.py")):
        name = pyfile.stem
        if name in SKIP:
            continue
        modules[name] = _get_imports(pyfile)
    return modules


def _get_imports(pyfile):
    """Extract internal markdown_vault imports from a Python file."""
    try:
        source = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(pyfile))
    except (SyntaxError, OSError):
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # from .module import X  ->  module
            if node.level == 1 and node.module:
                imports.append(node.module)
            # from . import X  ->  X
            if node.level == 1 and not node.module:
                for alias in node.names or []:
                    imports.append(alias.name)
            # from markdown_vault.module import X  ->  module
            if node.level == 0 and node.module and node.module.startswith("markdown_vault"):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    imports.append(parts[1])
    return sorted(set(imports))


def _group_by_layer(modules):
    """Group modules into layers."""
    result = {}
    for layer, members in sorted(LAYERS.items()):
        layer_mods = {m for m in members if m in modules}
        if layer_mods:
            result[layer] = sorted(layer_mods)
    # Catch any modules not in a layer
    all_labeled = {m for members in LAYERS.values() for m in members}
    unassigned = sorted(m for m in modules if m not in all_labeled)
    if unassigned:
        result["Other"] = unassigned
    return result


def _render(modules):
    """Render ASCII dependency tree per module, grouped by layer."""
    layers = _group_by_layer(modules)
    lines = []
    for layer_name, members in layers.items():
        if layer_name == "Other" and members == ["__init__"]:
            continue
        lines.append(f"### {layer_name}")
        for mod in members:
            deps = modules[mod]
            # Filter to only internal deps that exist
            internal = [d for d in deps if d in modules]
            if not internal:
                lines.append(f"- `{mod}`")
            else:
                lines.append(f"- `{mod}`")
                for dep in internal:
                    lines.append(f"  - `→ {dep}`")
        lines.append("")
    return "\n".join(lines)


def main():
    modules = _collect_modules()
    output = _render(modules)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
