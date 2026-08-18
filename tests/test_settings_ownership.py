"""Ownership guard: the app has exactly one settings state.

The lost-update defect this closes was never a bug in one call site — it was the
*shape* of the code: three components each loaded their own snapshot, and
``save_settings`` wrote the whole block back, so whichever saved last reset every
key the others had changed meanwhile. Reads went stale in between, which is why
three ad-hoc reloads had grown at the seams.

`config.settings()` owns that state now. This test keeps it owned: a second
`load_settings()` call in application code would hand out an independent copy and
recreate the whole class of failure, quietly and without any test going red — the
copy behaves perfectly until two writes overlap.

`load_settings()` itself stays legitimate: it is what `settings()` is built on, and
tests use it to read back what was written. So the rule is scoped, not absolute —
`core/config.py` may call it, application code may not.
"""

import ast
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "src" / "markdown_vault"

#: The owner itself — it defines and uses the loader.
_ALLOWED = {_PKG / "core" / "config.py"}


def _calls_load_settings(path: Path):
    """Line numbers of ``load_settings(...)`` calls in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if name == "load_settings":
            lines.append(node.lineno)
    return lines


class TestSettingsHaveOneOwner(unittest.TestCase):

    def test_application_code_does_not_load_its_own_snapshot(self):
        offenders = []
        for path in sorted(_PKG.rglob("*.py")):
            if path in _ALLOWED:
                continue
            for line in _calls_load_settings(path):
                offenders.append(f"{path.relative_to(_REPO)}:{line}")
        self.assertEqual(
            offenders, [],
            "these call load_settings() and would hold a private copy of the "
            "settings; use config.settings() instead:\n  " + "\n  ".join(offenders))

    def test_the_guard_can_actually_see_a_violation(self):
        # A guard that cannot fail is decoration. Prove the AST sweep finds both
        # spellings before trusting the assertion above.
        src = "import x\ncfg = x.load_settings()\ns = load_settings()\n"
        tmp = _REPO / "tmp" / "_guard_probe.py"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(src, encoding="utf-8")
        try:
            self.assertEqual(_calls_load_settings(tmp), [2, 3])
        finally:
            tmp.unlink()


if __name__ == "__main__":
    unittest.main()
