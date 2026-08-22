#!/usr/bin/env python3
"""Generate docs/settings.md from the settings JSON Schema.

The schema (src/markdown_vault/core/settings.schema.json) is the single docs
source; this build-time script renders it to Markdown. Standard library only —
no runtime dependency and nothing to install. Run via ``make docs-settings``.
"""

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = _ROOT / "src" / "markdown_vault" / "core" / "settings.schema.json"
_OUT = _ROOT / "docs" / "settings.md"


def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell (pipes break the table)."""
    return str(text).replace("|", "\\|")


def _leaves(node, prefix=""):
    """Yield ``(dotted-path, leaf-node)`` in document order."""
    props = node.get("properties")
    if not props:
        yield prefix, node
        return
    for key, child in props.items():
        path = f"{prefix}.{key}" if prefix else key
        yield from _leaves(child, path)


def _row(path, leaf):
    default = json.dumps(leaf.get("default"))
    desc = _cell(leaf.get("description", ""))
    if leaf.get("enum"):
        values = ", ".join(f"`{v}`" for v in leaf["enum"])
        desc = f"{desc} Values: {values}."
    return f"| `{path}` | {leaf.get('type', '')} | `{_cell(default)}` | {desc} |"


def render(schema: dict) -> str:
    lines = [
        "# Settings reference",
        "",
        "Generated from `src/markdown_vault/core/settings.schema.json` — **do not "
        "edit by hand**; run `make docs-settings`. In `settings.yaml` these nest one "
        "branch per domain (`ask:`, `semantic:`, …); `_DEFAULT_SETTINGS` in "
        "`core/config.py` is the runtime source of truth, kept in step with the "
        "schema by `tests/test_settings_schema.py`.",
        "",
    ]
    for domain, node in schema["properties"].items():
        lines.append(f"## {domain}")
        lines.append("")
        lines.append("| Setting | Type | Default | Description |")
        lines.append("| --- | --- | --- | --- |")
        for path, leaf in _leaves(node, domain):
            lines.append(_row(path, leaf))
        lines.append("")
    deps = schema.get("x-dependencies")
    if deps:
        lines.append("## Keys that depend on others")
        lines.append("")
        lines.append("A per-key schema cannot express when one setting only takes "
                     "effect under another, so those couplings are listed here:")
        lines.append("")
        for dep in deps:
            lines.append(f"- **`{dep['key']}`** (depends on `{dep['depends_on']}`) — "
                         f"{dep['note']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(render(schema), encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_ROOT)} ({len(list(_leaves_all(schema)))} settings)")


def _leaves_all(schema):
    for _, node in schema["properties"].items():
        yield from _leaves(node)


if __name__ == "__main__":
    main()
