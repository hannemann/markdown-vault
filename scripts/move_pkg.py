#!/usr/bin/env python3
"""Rewrite every reference to a set of modules that move into a subpackage.

Used by the package-structure refactoring (``tmp/Tickets/PackageStructure/``) to
perform step 3 of the per-package procedure — the import rewrite — mechanically
instead of by hand. Run it *after* ``git mv``-ing the modules into their new
subpackage directory:

    git mv src/markdown_vault/<mod>.py src/markdown_vault/<pkg>/
    python3 scripts/move_pkg.py <pkg> <mod1> <mod2> ...

It rewrites ``src/``, ``tests/``, ``scripts/`` and ``tests-e2e/`` in place, from the
repository root (paths are resolved relative to the current directory).

What it rewrites — the complete, grammar-derived set of ways a submodule is named,
all redirected to ``markdown_vault.<pkg>.<mod>``:

  1. ``from . import X``                    (Pass B)
  2. ``from .X import Y``                    (Pass B)
  3. ``from markdown_vault import X``        (Pass B)
  4. ``from markdown_vault.X import Y``      (Pass A)
  5. ``import markdown_vault.X [as Y]``      (Pass A)

Pass A additionally covers every *string* module path — ``mock.patch`` targets,
``sys.modules`` comparisons, ``argparse prog=``, docstrings — since they share the
``markdown_vault.<mod>`` spelling. Function-local imports are covered too (the
passes are textual, not import-time).

Not touched: **filesystem** paths like ``.../markdown_vault/<mod>.py`` — a different
spelling with no ``markdown_vault.<mod>`` token. Those are guarded separately by
``tests/test_source_path_refs.py``; fix them by hand when a move breaks them.

The rewrite is verified by result, not by trusting this tool: the per-package
gauntlet (import-grep post-condition, ``test_string_module_refs``, ``test_layering``,
the full suite, the installed-copy smoke test) must be green after each run.
"""
import re
import sys
from pathlib import Path

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(2)

pkg = sys.argv[1]
mods = sys.argv[2:]
MODS = set(mods)
alt = "|".join(re.escape(m) for m in mods)
REPO = Path(".").resolve()
DIRS = ["src", "tests", "scripts", "tests-e2e"]

# Pass A: absolute dotted forms + every string literal path:
#   markdown_vault.<mod>[.x]  ->  markdown_vault.<pkg>.<mod>[.x]
#   (covers `from markdown_vault.<mod> import`, `import markdown_vault.<mod>`,
#    "markdown_vault.<mod>..." in mock.patch/sys.modules/argparse/docstrings)
# The trailing lookahead makes it idempotent for the collision packages (editor,
# graph, preview, search): after one run `markdown_vault.preview` becomes
# `markdown_vault.preview.preview`, and a second run must NOT match its own output.
# Refuse to match when the next component is itself one of the moving modules.
reA = re.compile(rf"(?<![\w.])markdown_vault\.({alt})\b(?!\.({alt})\b)")


def _split(names_str):
    """Split a comma name list into (moving_line_names, staying_line_names)."""
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    moving = [n for n in names if n.split(" as ")[0].strip() in MODS]
    staying = [n for n in names if n.split(" as ")[0].strip() not in MODS]
    return moving, staying


reF2 = re.compile(rf"^(\s*)from \.({alt}) import (.+)$")        # from .<mod> import X
reF1 = re.compile(r"^(\s*)from \. import (.+?)(\s*#.*)?$")       # from . import a, b
reF3 = re.compile(r"^(\s*)from markdown_vault import (.+?)(\s*#.*)?$")  # from markdown_vault import a, b


def rewrite_line(line):
    m = reF2.match(line)
    if m:
        return [f"{m.group(1)}from markdown_vault.{pkg}.{m.group(2)} import {m.group(3)}"]
    for rgx, rel_head in ((reF1, "from . import"), (reF3, "from markdown_vault import")):
        m = rgx.match(line)
        if m:
            indent, names_str, comment = m.group(1), m.group(2), (m.group(3) or "")
            moving, staying = _split(names_str)
            if not moving:
                return [line.rstrip("\n")]
            out = []
            if staying:
                out.append(f"{indent}{rel_head} {', '.join(staying)}")
            out.append(f"{indent}from markdown_vault.{pkg} import {', '.join(moving)}{comment}")
            return out
    return [line.rstrip("\n")]


def _target_files():
    for d in DIRS:
        base = REPO / d
        if not base.exists():
            continue
        for f in base.rglob("*.py"):
            if "__pycache__" not in f.parts:
                yield f


# Pass B is line-based, so the parenthesised multi-line form
# `from . import (\n  config,\n)` would be silently skipped (the module names sit on
# continuation lines). None exist in the tree today; refuse rather than mis-handle one
# if a reformat ever introduces it. Pre-flight so nothing is written on a partial run.
_PAREN_RE = re.compile(r"from (?:\.|markdown_vault) import \(([^)]*)\)", re.DOTALL)
unsupported = []
for f in _target_files():
    for m in _PAREN_RE.finditer(f.read_text(encoding="utf-8")):
        names = re.split(r"[,\s]+", m.group(1))
        if any(n.split(" as ")[0] in MODS for n in names if n):
            line = f.read_text(encoding="utf-8")[: m.start()].count("\n") + 1
            unsupported.append(f"{f.relative_to(REPO)}:{line}")
if unsupported:
    sys.stderr.write(
        "refusing to run: multi-line `from . import (…)` of a moving module is not "
        "supported — rewrite these by hand:\n  " + "\n  ".join(unsupported) + "\n"
    )
    sys.exit(3)

changed = 0
for f in _target_files():
    text = f.read_text(encoding="utf-8")
    orig = text
    # Pass A
    text = reA.sub(rf"markdown_vault.{pkg}.\1", text)
    # Pass B (line based)
    new_lines = []
    for line in text.split("\n"):
        new_lines.extend(rewrite_line(line))
    text = "\n".join(new_lines)
    if text != orig:
        f.write_text(text, encoding="utf-8")
        changed += 1
print(f"rewrote {changed} files for package '{pkg}' (modules: {', '.join(mods)})")
