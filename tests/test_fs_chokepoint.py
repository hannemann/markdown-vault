"""AST guard: raw mutating filesystem calls live ONLY in the two facades (core/vault_fs.py,
core/state_fs.py). Everywhere else they must go through VaultFS / StateFS.

Fail-loud at test time with file:line, so a stray direct write is caught when it is written,
not when it corrupts something. The scan is a static approximation — it catches the
idiomatic, literal cases (practically all real ones); dynamic dispatch, subprocesses and C
extensions are out of reach and named in the ticket.

Two disambiguations keep the false-alarm rate at zero (see the ticket's AG1/AG5):

- **Arity** separates the ``pathlib`` method from its string namesake: ``p.replace(x)`` (1
  arg) is ``Path.replace``, ``s.replace(a, b)`` is ``str.replace``; likewise ``rename``.
- **Receiver-is-module**: ``os.replace`` is a module call (flagged), ``dataclasses.replace``
  is a module call that is NOT a filesystem mutation (skipped) — a bare receiver name that
  matches a module imported in the file is judged by the module table, not the method table.

Migration is a ratchet: the current findings are frozen in ``_BASELINE`` and the test asserts
the scan equals it exactly. A NEW raw call turns it red immediately; redirecting a site onto a
facade turns it red too, forcing a deliberate baseline update in the same commit — so the
baseline is always the accurate remaining-work list. "Done" for the whole effort is
``_BASELINE == {}`` — at which point this ratchet should be REPLACED by a plain "must be empty"
assertion, so the apparatus does not outlive its purpose.

The baseline is keyed ``file -> per-op count``, deliberately without line numbers (they shift
on every unrelated edit and would make the baseline a merge-conflict magnet). The cost: a swap
WITHIN one file is invisible — drop one ``write_text`` and add another and the count is
unchanged. Acceptable for the question it answers ("which file still holds raw FS?").
"""

import ast
import unittest
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "markdown_vault"

#: The only files allowed to touch raw filesystem mutation.
_FACADES = {"core/vault_fs.py", "core/state_fs.py"}

#: Module-level functions that mutate the filesystem, keyed by the module's top name.
_MUTATING_MODULE_FUNCS = {
    "os": {"remove", "unlink", "rename", "renames", "replace", "mkdir", "makedirs",
           "rmdir", "removedirs", "fdopen", "symlink", "link"},
    "shutil": {"move", "rmtree", "copy", "copy2", "copyfile", "copytree", "copystat"},
}

#: pathlib.Path methods that mutate. ``replace``/``rename`` are arity-disambiguated below;
#: ``open`` is handled specially (flagged only in a write mode, like the builtin).
_PATH_METHODS = {
    "write_text", "write_bytes", "unlink", "rename", "replace",
    "mkdir", "rmdir", "touch", "symlink_to", "hardlink_to",
}


def _imported(tree: ast.AST) -> tuple:
    """Return ``(modules, bare_muts)``.

    *modules* maps each name bound to a module to its top-level name:
    ``import os`` -> {"os": "os"}; ``import shutil as sh`` -> {"sh": "shutil"};
    ``from markdown_vault.core.paths import STATE_DIR`` -> {"STATE_DIR": "markdown_vault"}.

    *bare_muts* maps a name imported directly from a mutating module to its op label:
    ``from os import replace`` -> {"replace": "os.replace"}, so a bare ``replace(a, b)`` is
    caught. This spelling is not the house style (measured zero today) but is statically
    visible, so it belongs in the guard rather than a caveat.
    """
    modules, bare_muts = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                modules[bound] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                modules[bound] = node.module.split(".")[0]
                if alias.name in _MUTATING_MODULE_FUNCS.get(node.module, ()):
                    bare_muts[bound] = f"{node.module}.{alias.name}"
    return modules, bare_muts


def _write_mode(node: ast.Call, mode_pos: int) -> bool:
    """Whether an open call opens for writing (mode contains w/a/x/+). *mode_pos* is where
    the mode argument sits positionally — 1 for the builtin ``open(file, mode)``, 0 for the
    method ``Path.open(mode)``."""
    mode = None
    if len(node.args) > mode_pos and isinstance(node.args[mode_pos], ast.Constant):
        mode = node.args[mode_pos].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def _mutation_op(node: ast.Call, modules: dict, bare_muts: dict) -> str | None:
    """The mutation label for *node*, or ``None`` if it is not a raw FS mutation."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "open" and _write_mode(node, 1):    # builtin open(file, mode)
            return "open(w)"
        if func.id in bare_muts:                          # from os import replace; replace(...)
            return bare_muts[func.id]
        return None
    if not isinstance(func, ast.Attribute):
        return None
    recv, attr = func.value, func.attr
    if isinstance(recv, ast.Name) and recv.id in modules:
        top = modules[recv.id]
        if attr in _MUTATING_MODULE_FUNCS.get(top, ()):   # os.replace yes, dataclasses.replace no
            return f"{top}.{attr}"
        return None
    if attr == "open" and _write_mode(node, 0):           # Path.open("w") — mode is arg 0
        return "open(w)"
    if attr in _PATH_METHODS:
        nargs = len(node.args)
        if attr == "replace" and nargs != 1:      # str.replace(a, b) / DOM replace(el, marker)
            return None
        if attr == "rename" and nargs != 1:       # a 2-arg rename is not Path.rename
            return None
        return f".{attr}"
    return None


def _scan(src: str) -> list:
    """Return sorted ``(lineno, op)`` for every raw FS mutation in *src*."""
    tree = ast.parse(src)
    modules, bare_muts = _imported(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            op = _mutation_op(node, modules, bare_muts)
            if op:
                out.append((node.lineno, op))
    return sorted(out)


def _scan_tree() -> dict:
    """``{relpath: Counter(op -> n)}`` for every source file outside the facades."""
    found = {}
    for py in sorted(_ROOT.rglob("*.py")):
        rel = py.relative_to(_ROOT).as_posix()
        if rel in _FACADES:
            continue
        ops = Counter(op for _, op in _scan(py.read_text(encoding="utf-8")))
        if ops:
            found[rel] = ops
    return found


class TestScanner(unittest.TestCase):
    """The disambiguation rules, pinned on synthetic snippets so a rule change is visible."""

    def _ops(self, src):
        return [op for _, op in _scan(src)]

    def test_path_replace_one_arg_is_flagged(self):
        self.assertEqual(self._ops("p.replace(target)"), [".replace"])

    def test_str_replace_two_args_is_not(self):
        self.assertEqual(self._ops("s.replace('a', 'b')"), [])

    def test_dataclasses_replace_is_not(self):
        self.assertEqual(self._ops("import dataclasses\ndataclasses.replace(o, x=1)"), [])

    def test_os_replace_is_flagged(self):
        self.assertEqual(self._ops("import os\nos.replace(a, b)"), ["os.replace"])

    def test_path_write_text_is_flagged(self):
        self.assertEqual(self._ops("Path(p).write_text(x)"), [".write_text"])

    def test_open_write_mode_is_flagged(self):
        self.assertEqual(self._ops("open(p, 'wb')"), ["open(w)"])

    def test_open_read_mode_is_not(self):
        self.assertEqual(self._ops("open(p)"), [])
        self.assertEqual(self._ops("open(p, 'r')"), [])

    def test_shutil_move_and_rmtree_are_flagged(self):
        self.assertEqual(self._ops("import shutil\nshutil.move(a, b)\nshutil.rmtree(d)"),
                         ["shutil.move", "shutil.rmtree"])

    def test_shutil_alias_is_flagged(self):
        self.assertEqual(self._ops("import shutil as sh\nsh.rmtree(d)"), ["shutil.rmtree"])

    def test_os_path_join_is_not(self):
        self.assertEqual(self._ops("import os\nos.path.join(a, b)"), [])

    def test_mkdir_and_unlink_and_touch(self):
        self.assertEqual(self._ops("Path(p).mkdir()\nq.unlink()\nr.touch()"),
                         [".mkdir", ".unlink", ".touch"])

    def test_path_open_write_mode_is_flagged(self):
        # AR1: the builtin open is covered, so must the Path method be — the likely spelling.
        self.assertEqual(self._ops("Path(p).open('w')"), ["open(w)"])
        self.assertEqual(self._ops("f.open('rb')"), [])          # a read open is not a mutation

    def test_os_symlink_and_link_are_flagged(self):
        # AR1: .symlink_to is on the method list, so os.symlink (same op) must be too; and
        # os.link creates a directory entry.
        self.assertEqual(self._ops("import os\nos.symlink(a, b)\nos.link(a, b)"),
                         ["os.symlink", "os.link"])

    def test_path_hardlink_to_is_flagged(self):
        self.assertEqual(self._ops("Path(a).hardlink_to(b)"), [".hardlink_to"])

    def test_bare_from_os_import_is_flagged(self):
        # AR1/(b): statically visible, so it belongs in the guard, not a caveat.
        self.assertEqual(self._ops("from os import replace\nreplace(a, b)"), ["os.replace"])
        self.assertEqual(self._ops("from shutil import rmtree as rm\nrm(d)"), ["shutil.rmtree"])


class TestFacadesAreScannedOut(unittest.TestCase):
    def test_the_facades_do_contain_raw_fs(self):
        # Sanity: the facades are the place raw FS lives, so the scanner must SEE it there —
        # otherwise the exclusion is hiding nothing and the guard proves little.
        for facade in _FACADES:
            ops = _scan((_ROOT / facade).read_text(encoding="utf-8"))
            self.assertTrue(ops, f"{facade} should contain raw FS the scanner detects")


class TestFsChokepoint(unittest.TestCase):
    maxDiff = None   # this goes red by design on nearly every slice-5 commit; show the exact delta

    def test_raw_fs_only_in_the_facades(self):
        current = _scan_tree()
        current_plain = {f: dict(c) for f, c in current.items()}
        self.assertEqual(
            current_plain, _BASELINE,
            "Raw filesystem mutation outside the two facades changed.\n"
            "  New/grown entry  -> route it through VaultFS/StateFS, or (if it IS a facade) "
            "add the file to _FACADES.\n"
            "  Shrunk/removed    -> a site was migrated; update _BASELINE to match "
            "(the baseline is the remaining-work list).\n"
            f"  scanned: {current_plain}")


# Frozen snapshot of the raw-FS sites still to migrate (slice 5). Empty == effort complete.
# Raw-FS sites still to migrate (slice 5). Started at 13 modules / 46 sites; shrinks per redirect.
# Migrated: core/debug.py, core/session.py -> StateFS.
_BASELINE = {
    "core/attachments.py": {
        ".write_text": 1, ".write_bytes": 1, ".mkdir": 2, ".rmdir": 1,
        "shutil.move": 1, "shutil.rmtree": 1,
    },
    "core/config.py": {".mkdir": 1, "os.fdopen": 1, "os.replace": 1, "os.unlink": 1},
    "core/logging_setup.py": {"os.makedirs": 2},
    "editor/editor.py": {".write_text": 1},
    "importers/document_import.py": {".mkdir": 2, ".write_text": 1},
    "importers/web_import.py": {".mkdir": 3, ".write_bytes": 2, ".write_text": 1},
    "search/model_download.py": {".mkdir": 1, "open(w)": 1, ".replace": 1, ".unlink": 1},
    "search/semantic_index.py": {
        ".mkdir": 1, ".unlink": 1, ".write_text": 1, "os.replace": 2,
    },
    "vault/backlink_index.py": {".write_text": 3},
    "vault/file_ops.py": {
        ".touch": 1, "os.mkdir": 1, "os.makedirs": 1, "os.remove": 1, "shutil.rmtree": 1,
    },
    "vault/vault_tree.py": {"os.rename": 1, "shutil.move": 1},
}


if __name__ == "__main__":
    unittest.main()
