"""Guard against stale hardcoded *filesystem* paths to package source files.

A module's on-disk location gets written down in two shapes. ``test_string_module_refs``
already guards the **dotted** shape (``"markdown_vault.editor.mru"``). This guards the
**filesystem** shape, which contains no ``markdown_vault.<name>`` and so is invisible to
that test — and to the import machinery and the rewrite step:

* single literal — ``"…/markdown_vault/editor/mru.py"``
* split form — the package and the file name sit in *separate* string constants of one
  expression: ``os.path.join(dirname(__file__), "..", "src", "markdown_vault", "mru.py")``
  or ``Path(__file__).parent.parent / "src" / "markdown_vault" / "mru.py"``.

Both are collected here and asserted to resolve to an existing file. When a module moves
into a subpackage, any path left pointing at the old location fails immediately instead of
surfacing as a downstream ``FileNotFoundError`` in whichever test happens to read it (that
is how the editor move produced 22 opaque errors — see ticket 04b / finding B1).
"""

import ast
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "src" / "markdown_vault"
_SCAN_DIRS = ("src", "tests", "scripts", "tests-e2e")

# Single literal: "…markdown_vault/<sub>/<stem>.py". The negative lookahead keeps the
# generated-at-build ``_version.py.in`` template (a real path that stays at the root, so
# ``markdown_vault/_version.py`` never exists as a source file) from matching.
_SINGLE_RE = re.compile(r"markdown_vault/([\w/]+\.py)(?![\w.])")
_SEGMENT_RE = re.compile(r"[\w.\-]+")


def _split_form_ref(node: ast.AST) -> str | None:
    """For a Call/BinOp whose string constants spell ``…/markdown_vault/…/<stem>.py``,
    return the ``markdown_vault/…/<stem>.py`` relative path; else ``None``."""
    consts = sorted(
        (n.lineno, n.col_offset, n.value)
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    values = [v for _, _, v in consts]
    if "markdown_vault" not in values:
        return None
    tail = values[values.index("markdown_vault"):]
    segs: list[str] = []
    for seg in tail:
        if not _SEGMENT_RE.fullmatch(seg):
            break
        segs.append(seg)
        if seg.endswith(".py"):
            return "/".join(segs)
    return None


def _claimed_paths(pyfile: Path):
    """Yield ``(relpath_under_src, lineno)`` for every source path the file names."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), str(pyfile))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = _SINGLE_RE.search(node.value)
            if m:
                yield f"markdown_vault/{m.group(1)}", node.lineno
        elif isinstance(node, (ast.Call, ast.BinOp)):
            rel = _split_form_ref(node)
            if rel:
                yield rel, node.lineno


class TestSourcePathRefs(unittest.TestCase):
    def test_hardcoded_source_paths_resolve(self):
        files = [
            p
            for d in _SCAN_DIRS
            if (_ROOT / d).exists()
            for p in (_ROOT / d).rglob("*.py")
        ]
        self.assertGreater(len(files), 50, "scanned too few files — wrong path? (L2)")
        stale = []
        for pyfile in files:
            for rel, lineno in _claimed_paths(pyfile):
                if not (_ROOT / "src" / rel).exists():
                    stale.append(f"{pyfile.relative_to(_ROOT)}:{lineno} -> src/{rel}")
        self.assertEqual(
            sorted(set(stale)), [], f"stale hardcoded source-file path(s): {stale}"
        )


if __name__ == "__main__":
    unittest.main()
