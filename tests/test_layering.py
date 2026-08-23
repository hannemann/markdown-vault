"""Layering guard for the package structure.

Derives each module's package from its **directory** (walk the tree, package =
relative parent dir, root = ``ROOT``) — not from a hardcoded table that could
drift — and asserts the package import graph has **no bidirectional pairs** (a
clean DAG). While the package is still flat every module is ``ROOT``, so there
are no cross-package edges and the test is trivially green; it gains teeth as
files move into subpackages, and stays green at every intermediate state.
"""

import ast
import sys
import unittest
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "markdown_vault"


def _package_of_file(pyfile: Path) -> str:
    rel = pyfile.relative_to(_ROOT).parent
    return str(rel).replace("/", ".") if str(rel) != "." else "ROOT"


def _package_of_dotted(dotted: str) -> str | None:
    """Package of the module a dotted path (under ``markdown_vault``, e.g.
    ``core.config`` or ``config``) resolves to — the longest prefix that is a
    real ``.py`` module, mapped to its parent directory. ``None`` if none is."""
    parts = dotted.split(".")
    for n in range(len(parts), 0, -1):
        cand = _ROOT.joinpath(*parts[:n]).with_suffix(".py")
        if cand.exists():
            return _package_of_file(cand)
    return None


def _dotted_targets(importer_pkg: str, node: ast.AST):
    """Candidate dotted module paths a node references, ``markdown_vault.``
    stripped. Handles absolute forms and relative imports of every level: a
    ``level==1`` import (``from .`` / ``from .x``) resolves within the importer's
    own package (no cross edge), a ``level>=2`` import (``from ..vault import``)
    climbs ``level-1`` packages up and is a genuine cross-package edge (L1)."""
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            if not node.module:
                return
            if node.module == "markdown_vault":
                for a in node.names:                   # from markdown_vault import X
                    yield a.name
            elif node.module.startswith("markdown_vault."):
                rest = node.module[len("markdown_vault."):]
                yield rest                             # from markdown_vault.core.x import Y
                for a in node.names:                   # from markdown_vault.core import x
                    yield f"{rest}.{a.name}"
            return
        # relative: climb (level-1) packages up from the importer's own package
        base = [] if importer_pkg == "ROOT" else importer_pkg.split(".")
        up = node.level - 1
        if up:
            base = base[:max(0, len(base) - up)]
        prefix = base + (node.module.split(".") if node.module else [])
        if node.module:
            yield ".".join(prefix)                     # from ..pkg.x import Y -> pkg.x
        for a in node.names:                           # + each imported name
            yield ".".join(prefix + [a.name])
    elif isinstance(node, ast.Import):
        for a in node.names:                           # import markdown_vault.core.x [as Y]
            if a.name.startswith("markdown_vault."):
                yield a.name[len("markdown_vault."):]


def _package_edges():
    edges = defaultdict(set)
    files = list(_ROOT.rglob("*.py"))
    for pyfile in files:
        src_pkg = _package_of_file(pyfile)
        tree = ast.parse(pyfile.read_text(encoding="utf-8"), str(pyfile))
        for node in ast.walk(tree):
            for dotted in _dotted_targets(src_pkg, node):
                dst_pkg = _package_of_dotted(dotted)
                if dst_pkg is not None and dst_pkg != src_pkg:
                    edges[src_pkg].add(dst_pkg)
    return edges, len(files)


class TestLayering(unittest.TestCase):
    def test_no_bidirectional_package_pairs(self):
        edges, n_files = _package_edges()
        self.assertGreater(n_files, 50, "scanned too few files — wrong path? (L2)")
        bidir = sorted({tuple(sorted((a, b)))
                        for a in edges for b in edges[a] if a in edges.get(b, ())})
        self.assertEqual(bidir, [], f"package import cycle(s): {bidir}")


class TestAskIsImportLight(unittest.TestCase):
    """`search/ask.py` must have no module-level markdown_vault import.

    That is what keeps it safe as an import target for `semantic_search` (which
    imports `openai_base` from it) and for `ask_models` — deferred imports inside
    functions are fine and are how the existing `ask <-> ask_models` cycle is
    defused. The package-level guard above cannot see this: all three modules
    share the `search` package, so an import between them is not a package edge.
    """

    def test_ask_has_no_module_level_package_import(self):
        tree = ast.parse((_ROOT / "search" / "ask.py").read_text())
        offenders = []
        for node in tree.body:                       # module level only
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "markdown_vault"):
                offenders.append(f"line {node.lineno}: from {node.module} import …")
            elif isinstance(node, ast.Import):
                offenders += [f"line {node.lineno}: import {a.name}"
                              for a in node.names
                              if a.name.startswith("markdown_vault")]
        self.assertEqual(offenders, [], "ask.py must stay import-light")


_CORE = _ROOT / "core"

# Beyond the standard library, each ``core/`` module may import only these extras.
# The lowest layer runs before GTK is up (``logging_setup`` runs first in ``main.py``
# with only stdlib + GLib) and is imported by pure unit tests, so a heavy import here
# breaks both. The ``"core"`` token means "may import its own layer"; ``yaml`` / ``gi``
# are the two deliberate weights. A module with **no entry is stdlib-only** — so
# ``paths.py``'s stdlib purity (the comment-only promise ``logging_setup`` leans on)
# is a structural *consequence*, and a NEW ``core/`` module falls under the strict
# default automatically.
_CORE_ALLOW = {
    "config":        {"yaml", "core"},
    "debug_control": {"gi"},
    "logging_setup": {"gi", "core"},
    "path_utils":    {"core"},
    "session":       {"core"},
}


def _module_level_imports(pyfile: Path):
    """Yield ``(lineno, imported-module-string)`` for each absolute module-level
    import — deferred imports inside functions are the escape hatch and not scanned."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), str(pyfile))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def _import_offenders(imports, allow):
    """Imports that are neither stdlib nor in *allow*, as ``"line N: module"``."""
    offenders = []
    for lineno, mod in imports:
        top = mod.split(".")[0]
        if top in sys.stdlib_module_names or top == "__future__":
            continue
        if mod.startswith("markdown_vault."):
            # same layer only (the core package); any other same-package import (the
            # search package pulls the whole stack) is never allowed here.
            ok = mod[len("markdown_vault."):].split(".")[0] == "core" and "core" in allow
        else:
            ok = top in allow                            # yaml, gi, any third party
        if not ok:
            offenders.append(f"line {lineno}: {mod}")
    return offenders


class TestCoreLayerImportWeight(unittest.TestCase):
    """Every ``core/`` module stays import-light: stdlib + a small per-module allow-list.

    Guards **weight**, whereas :class:`TestAskIsImportLight` guards **cycles** — same
    AST shape, different reason, do not merge them. ``paths.py`` has no allow-list
    entry, so its stdlib purity is a structural consequence rather than a comment; a
    new ``core/`` module falls under the strict default; and the boundary is the
    ``core`` package only — any other same-package import (the ``search`` package pulls
    the whole AI stack) is refused. The scan is **recursive** (``rglob``) and keys on
    the path relative to ``core/``, so a one-level subpackage (the ``ui/preferences``
    shape ``AGENTS.md`` allows) is covered too, without same-named modules across
    directories sharing one permission.
    """

    def test_each_core_module_imports_only_its_allowance(self):
        offenders = {}
        for pyfile in sorted(_CORE.rglob("*.py")):     # recursive: a core/ subpackage too
            key = pyfile.relative_to(_CORE).with_suffix("").as_posix()  # not stem — no collision
            bad = _import_offenders(_module_level_imports(pyfile),
                                    _CORE_ALLOW.get(key, frozenset()))
            if bad:
                offenders[key] = bad
        self.assertEqual(
            offenders, {},
            "core/ module(s) importing beyond stdlib + their allow-list — add a "
            "deliberate _CORE_ALLOW entry or defer the import inside a function:\n"
            + "\n".join(f"  {m}: {b}" for m, b in sorted(offenders.items())))

    def test_the_check_flags_heavy_imports_and_the_package_boundary(self):
        # Effectiveness: numpy, gi and a non-core same-package import are caught;
        # stdlib and a granted same-layer import are not. Built from a variable so this
        # file carries no bare-package string literal (the string-ref guard).
        import tempfile
        mv = "markdown_vault"
        src = ("import os\nimport numpy\nfrom gi.repository import Gtk\n"
               f"from {mv}.search import ask\nfrom {mv}.core import paths\n")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.py"
            f.write_text(src, encoding="utf-8")
            imports = list(_module_level_imports(f))
        strict = [o.split(": ", 1)[1] for o in _import_offenders(imports, frozenset())]
        granted = [o.split(": ", 1)[1] for o in _import_offenders(imports, {"core"})]
        self.assertEqual(strict,
                         ["numpy", "gi.repository", f"{mv}.search", f"{mv}.core"])
        self.assertEqual(granted, ["numpy", "gi.repository", f"{mv}.search"])


if __name__ == "__main__":
    unittest.main()
