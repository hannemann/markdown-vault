"""AST guard: no raw mutating filesystem call the guard can SEE outside the two facades
(core/vault_fs.py, core/state_fs.py) and a small set of named, test-backed exceptions
(_EXCEPTIONS). Everywhere else the write must go through VaultFS / StateFS.

The wording matters: "the guard can see". The criterion has never been "exactly two files
touch raw FS" literally — logging opens its file through a stdlib handler the scanner cannot
see, and always could. This enforces the guard's *reach*, not an unattainable absolute.

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

**What an empty baseline does NOT mean.** The scan sees the stdlib calls listed in
``_MUTATIONS`` — it cannot see a third-party library writing on our behalf. ``numpy.save``
in ``search/semantic_index.py`` is the live instance: it produces the index matrix itself,
and the facade guards only where the result LANDS (``state_fs.promote``). Logging is a second
case — it opens its own file through a stdlib handler the scanner never sees, which is why
``core/logging_setup.py`` is an exception rather than a finding. So zero means "no raw
filesystem call the scanner can see", not "no bytes are written outside the facades". Read as
the latter it is more convincing than a comment making the same overclaim, because a number
looks like a measurement.
"""

import ast
import sys
import unittest
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "markdown_vault"

#: The two guarded facades — the intended homes for raw filesystem mutation.
_FACADES = {"core/vault_fs.py", "core/state_fs.py"}

#: Named exceptions: files that legitimately hold guard-visible raw FS, each with a reason
#: that is a CHECKED FACT rather than a promise (that is the line against the allowlist AG4
#: removed — "trusted writer" cannot be tested, "runs before the facade exists" can, and is
#: pinned by a test below). Not for open decisions: the editor's still-direct save stays in
#: the baseline as remaining work (option C), so its number keeps nagging.
_EXCEPTIONS = {
    "core/logging_setup.py":
        "main.py runs logging_setup.init() before settings.yaml is read (init(None) at "
        "main.py:18; config.settings() at :22). init imports config but only does a dict "
        "lookup on the PASSED settings — it reads no file. Routing its log-dir os.makedirs "
        "through StateFS would pull the READING of settings.yaml into that first init, via "
        "StateFS's vault clause (config.load_vaults), ahead of the handlers — so a broken-"
        "settings.yaml warning there would never reach the log file, the file you open to "
        "find out why the config is broken. The property (bootstrap reads no config file) is "
        "pinned by test_logging_setup.InitTest.test_init_does_not_read_settings_yaml; "
        "TestLoggingSetupStaysEarly below is a cheap early proxy for it.",
}

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
        if rel in _FACADES or rel in _EXCEPTIONS:
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

    def test_each_exception_still_holds_raw_fs(self):
        # An exception excuses raw FS the scanner sees. If a file's raw FS is gone (migrated
        # or removed), the exemption is stale — drop it from _EXCEPTIONS so it cannot cover a
        # future raw call added there by mistake.
        for exc in _EXCEPTIONS:
            ops = _scan((_ROOT / exc).read_text(encoding="utf-8"))
            self.assertTrue(ops, f"{exc} no longer holds raw FS — remove it from _EXCEPTIONS")


class TestLoggingSetupStaysEarly(unittest.TestCase):
    """A CHEAP EARLY PROXY for the real property (pinned directly in
    test_logging_setup.InitTest.test_init_does_not_read_settings_yaml): logging_setup must
    not read settings.yaml during bootstrap. This checks the module-level imports stay
    stdlib/gi/core.paths — a load-time first-party import is the usual way that reading would
    sneak in. It is only a proxy and diverges both ways (a harmless module-level import fails
    it; a config.load_vaults() added INSIDE init passes it), so it is the early warning, not
    the guarantee. logging_setup already imports config function-locally — that fires at the
    first init() call (main.py:18), but only does a dict lookup on the passed settings and
    reads no file, so it is fine."""

    def test_module_level_imports_stay_bootstrap_light(self):
        tree = ast.parse((_ROOT / "core" / "logging_setup.py").read_text(encoding="utf-8"))
        first_party = set()
        for node in tree.body:                      # top level only — not ast.walk into functions
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top == "markdown_vault":
                    first_party.update(f"{node.module}.{a.name}" for a in node.names)
                elif top != "gi" and top not in sys.stdlib_module_names:
                    self.fail(f"logging_setup pulls in third-party {node.module!r} at load")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top == "markdown_vault":
                        first_party.add(a.name)
                    elif top != "gi" and top not in sys.stdlib_module_names:
                        self.fail(f"logging_setup pulls in third-party {a.name!r} at load")
        self.assertEqual(
            first_party, {"markdown_vault.core.paths"},
            "logging_setup must import only core.paths from the app AT MODULE LEVEL — its raw-FS "
            "exception rests on staying import-light before config exists. Reach config/state_fs "
            "via a function-local import (deferred to call time) if you must, never at load")


class TestFsChokepoint(unittest.TestCase):
    maxDiff = None   # this goes red by design on nearly every slice-5 commit; show the exact delta

    def test_raw_fs_only_in_the_facades(self):
        current = _scan_tree()
        current_plain = {f: dict(c) for f, c in current.items()}
        self.assertEqual(
            current_plain, _BASELINE,
            "Raw filesystem mutation the guard can see, outside the facades and exceptions, "
            "changed.\n"
            "  New/grown entry  -> route it through VaultFS/StateFS; only if it genuinely "
            "cannot (a facade, or a bootstrap/pre-facade case) add it to _FACADES or "
            "_EXCEPTIONS with a test-backed reason.\n"
            "  Shrunk/removed    -> a site was migrated; update _BASELINE to match "
            "(the baseline is the remaining-work list).\n"
            f"  scanned: {current_plain}")


# Raw-FS sites still to migrate (slice 5). Started at 13 modules / 46 sites; shrinks per
# redirect; empty == effort complete. Migrated: core/debug.py, core/session.py -> StateFS;
# vault/vault_tree.py, vault/backlink_index.py, vault/file_ops.py, core/attachments.py,
# editor/editor.py -> VaultFS; importers/document_import.py -> both (note+dir VaultFS,
# whisper model dir StateFS); importers/web_import.py -> VaultFS (its four image sites went
# away entirely by sharing attachments.store_image_at rather than being redirected);
# search/model_download.py -> StateFS (likewise: all four vanished into write_stream, which
# already owned the .part-then-replace dance the downloader had its own copy of);
# search/semantic_index.py -> StateFS, which needed a new op: numpy writes the matrix itself,
# so state_fs.promote guards where the temp file LANDS rather than how it is produced;
# core/config.py -> StateFS LAST, because state_fs imports it, so the import back has to stay
# function-local (pinned in test_config).
# Exempted (see _EXCEPTIONS): core/logging_setup.py — a bootstrap dir mkdir before the facade.
#
# EMPTY. The migration is complete; per the docstring above, the ratchet is now due to be
# replaced by a plain "must be empty" assertion so the apparatus does not outlive its purpose.
_BASELINE: dict[str, dict[str, int]] = {}


if __name__ == "__main__":
    unittest.main()
