"""String-reference guard: every ``markdown_vault.<module>`` written as a *string*
must resolve to a real module.

Module paths appear in string literals that no import machinery sees — ``mock.patch``
targets, ``sys.modules`` comparisons, ``argparse(prog=…)``, docstrings — 296 of them
today. When a module moves, these silently rot: the suite may or may not exercise the
line, so ``make test`` is an unreliable backstop. An AST sweep for *every* string
literal naming a module is closed by construction (it does not care why the string
names a module) and turns the whole class into something the suite enforces.

The rule needs three ingredients or it is red on arrival / blind (review H8.4):

1. a negative lookbehind, so a longer dotted name (the D-Bus interface
   ``de.hannemann.markdown_vault.Debug``) is not matched;
2. the first component must be a known module stem or package name, so a logger
   namespace (``markdown_vault.llama``) or embedded Python source (``markdown_vault.
   __path__``) is ignored;
3. the resolving prefix must be a real ``.py`` module, not a package directory, so a
   stale ``markdown_vault.search`` is not excused by the *package* ``search/``.
"""

import ast
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "src" / "markdown_vault"
_SCAN = ["src", "tests", "scripts", "tests-e2e"]

# (1) negative lookbehind: do not match inside a longer dotted name.
_RE = re.compile(r"(?<![\w.])markdown_vault((?:\.\w+)+)")


def _known_first_components() -> set:
    known = set()
    for p in _PKG.iterdir():
        if p.suffix == ".py":
            known.add(p.stem)                 # module stem
        elif p.is_dir() and p.name != "__pycache__":
            known.add(p.name)                 # package directory
    return known


def _resolves_to_module(dotted: str) -> bool:
    """(3) True if the longest prefix of *dotted* is a real ``.py`` module."""
    parts = dotted.split(".")
    for n in range(len(parts), 0, -1):
        if _PKG.joinpath(*parts[:n]).with_suffix(".py").exists():
            return True
    return False


def _stale_string_module_refs():
    known = _known_first_components()
    stale = []
    n_files = 0
    for sub in _SCAN:
        base = _REPO / sub
        if not base.exists():
            continue
        for pyfile in base.rglob("*.py"):
            if "__pycache__" in pyfile.parts:
                continue
            try:
                tree = ast.parse(pyfile.read_text(encoding="utf-8"), str(pyfile))
            except (SyntaxError, UnicodeDecodeError):
                continue
            n_files += 1
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                for m in _RE.finditer(node.value):
                    dotted = m.group(1).lstrip(".")
                    if dotted.split(".")[0] not in known:     # (2)
                        continue
                    if not _resolves_to_module(dotted):
                        rel = pyfile.relative_to(_REPO)
                        stale.append(f"{rel}:{node.lineno}  markdown_vault.{dotted}")
    return stale, n_files


class TestStringModuleRefs(unittest.TestCase):
    def test_all_string_module_paths_resolve(self):
        stale, n_files = _stale_string_module_refs()
        self.assertGreater(n_files, 50, "scanned too few files — wrong path? (L2)")
        self.assertEqual(stale, [], "stale string module paths:\n" + "\n".join(stale))


if __name__ == "__main__":
    unittest.main()
