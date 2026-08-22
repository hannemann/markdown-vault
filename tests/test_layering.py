"""Layering guard for the package structure.

Derives each module's package from its **directory** (walk the tree, package =
relative parent dir, root = ``ROOT``) — not from a hardcoded table that could
drift — and asserts the package import graph has **no bidirectional pairs** (a
clean DAG). While the package is still flat every module is ``ROOT``, so there
are no cross-package edges and the test is trivially green; it gains teeth as
files move into subpackages, and stays green at every intermediate state.
"""

import ast
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


if __name__ == "__main__":
    unittest.main()
