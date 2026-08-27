"""Layering guard for the package structure.

Derives each module's package from its **directory** (walk the tree, package =
relative parent dir, root = ``ROOT``) — not from a hardcoded table that could
drift — and asserts the package import graph has **no bidirectional pairs** (a
clean DAG). While the package is still flat every module is ``ROOT``, so there
are no cross-package edges and the test is trivially green; it gains teeth as
files move into subpackages, and stays green at every intermediate state.
"""

import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "markdown_vault"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = (_REPO_ROOT / "src", _REPO_ROOT / "tests", _REPO_ROOT / "scripts")


def _ruff_path():
    """The ruff binary next to the running interpreter (the dev venv, per
    requirements-dev.txt) or on PATH; ``None`` when ruff is not installed."""
    candidate = Path(sys.executable).with_name("ruff")
    if candidate.exists():
        return str(candidate)
    return shutil.which("ruff")


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
    """`search/ask.py` must have no module-level markdown_vault import, save for the
    dependency-free i18n binding.

    That is what keeps it safe as an import target for `semantic_search` (which
    imports `openai_base` from it) and for `ask_models` — deferred imports inside
    functions are fine and are how the existing `ask <-> ask_models` cycle is
    defused. The package-level guard above cannot see this: all three modules
    share the `search` package, so an import between them is not a package edge.

    `markdown_vault.core.i18n` is exempt: it has no `_CORE_ALLOW` entry, so
    `TestCoreLayerImportWeight` already holds it to stdlib-only — it imports no
    markdown_vault code and so cannot form the cycle this guard prevents. If that
    ever changes, the core guard turns red first, not this one.
    """

    #: Dependency-free bindings ask.py may import at module scope. A module only
    #: belongs here while it stays stdlib-only (enforced for core/ by _CORE_ALLOW).
    _EXEMPT = {"markdown_vault.core.i18n"}

    def test_ask_has_no_module_level_package_import(self):
        tree = ast.parse((_ROOT / "search" / "ask.py").read_text())
        offenders = []
        for node in tree.body:                       # module level only
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "markdown_vault"):
                if node.module in self._EXEMPT:
                    continue
                offenders.append(f"line {node.lineno}: from {node.module} import …")
            elif isinstance(node, ast.Import):
                offenders += [f"line {node.lineno}: import {a.name}"
                              for a in node.names
                              if a.name.startswith("markdown_vault")
                              and a.name not in self._EXEMPT]
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
    "state_fs":      {"core"},
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


_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
# print() to stdout/stderr is legitimate only in the CLI driver (web_import's main()).
_PRINT_ALLOWED = {"importers/web_import.py"}


def _is_log_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_METHODS)


def _comment_linenos(src: str) -> set:
    """Line numbers carrying a real comment token (not a ``#`` inside a string)."""
    out = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    return out


def _handler_is_justified(handler: ast.ExceptHandler, comment_linenos: set) -> bool:
    """A handler is fine if it logs, re-raises, or carries a justification comment.

    The three legitimate outcomes of catching an exception (the AGENTS.md rule): make it
    visible (log), let it propagate (raise), or state why swallowing is correct — a comment,
    **including an inline one on a body statement**. Surfacing to the user is covered too:
    those handlers carry a comment saying so, rather than the guard learning every surface
    function (a guard that goes red for the wrong reason gets switched off). The guard checks
    a comment *exists* anywhere in the handler; its quality is a human slop-test."""
    for n in ast.walk(handler):
        if _is_log_call(n) or isinstance(n, ast.Raise):
            return True
    start = handler.lineno
    end = max((n.end_lineno for n in ast.walk(handler)
               if getattr(n, "end_lineno", None) is not None), default=start)
    return any(start <= ln <= end for ln in comment_linenos)


def _swallow_offenders() -> dict:
    offenders = {}
    for py in sorted(_ROOT.rglob("*.py")):
        rel = py.relative_to(_ROOT).as_posix()
        src = py.read_text(encoding="utf-8")
        comments = _comment_linenos(src)
        tree = ast.parse(src, str(py))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and not _handler_is_justified(node, comments):
                bad.append(f"line {node.lineno}: except swallows without log/justification")
        if rel not in _PRINT_ALLOWED:
            bad += [f"line {n.lineno}: stray print()" for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "print"]
        if bad:
            offenders[rel] = bad
    return offenders


class TestNoSilentSwallows(unittest.TestCase):
    """No exception is swallowed without a trace, and no stray print().

    Every ``except`` handler must log, re-raise, or carry a one-line justification
    (surfacing handlers say so in a comment); ``print()`` is allowed only in the CLI
    driver. This is the tooth for the silent-swallow sweep: a new blind ``except`` or a
    debug ``print`` left in code turns it red instead of shipping unnoticed.
    """

    def test_no_handler_swallows_without_log_or_justification(self):
        offenders = _swallow_offenders()
        self.assertEqual(
            offenders, {},
            "silent swallow(s) / stray print() — add a log, a re-raise, or a one-line "
            "justification (or, for print, keep it in the CLI driver):\n"
            + "\n".join(f"  {f}: {b}" for f, b in sorted(offenders.items())))

    def test_the_check_flags_a_smuggled_silent_except_and_passes_a_justified_one(self):
        # Effectiveness, both directions: a bare swallow is caught; a logged one, a
        # re-raise and a commented one are not. Built inline so the guard is pinned
        # independent of the swept tree.
        def handlers(src):
            tree = ast.parse(src)
            return [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]

        silent = "try:\n    x()\nexcept OSError:\n    pass\n"
        logged = "try:\n    x()\nexcept OSError:\n    logger.warning('x', exc_info=True)\n"
        reraise = "try:\n    x()\nexcept OSError:\n    raise\n"
        commented = ("try:\n    x()\nexcept OSError:\n"
                     "    # expected: caller handles None\n    return None\n")
        noqa = "try:\n    x()\nexcept Exception:  # noqa: BLE001 - why\n    pass\n"
        inline = "try:\n    x()\nexcept OSError:\n    n = 0  # inline reason on a body line\n"
        self.assertFalse(_handler_is_justified(handlers(silent)[0], _comment_linenos(silent)))
        self.assertTrue(_handler_is_justified(handlers(logged)[0], _comment_linenos(logged)))
        self.assertTrue(_handler_is_justified(handlers(reraise)[0], _comment_linenos(reraise)))
        self.assertTrue(_handler_is_justified(handlers(commented)[0], _comment_linenos(commented)))
        self.assertTrue(_handler_is_justified(handlers(noqa)[0], _comment_linenos(noqa)))
        self.assertTrue(_handler_is_justified(handlers(inline)[0], _comment_linenos(inline)))


class TestRuffClean(unittest.TestCase):
    """`ruff check` reports nothing over src/tests/scripts — the lint gate with
    teeth. There is no CI, so `make lint` alone is a command someone must remember;
    this runs it on every `make test`, and a lint regression turns the suite red.

    skipUnless-guarded on ruff's presence: ruff is a dev-only tool
    (requirements-dev.txt), so a base install without it SKIPS rather than errors,
    keeping that suite green (AGENTS.md base-install rule).
    """

    def _run_ruff(self, *args):
        # Absolute paths and an explicit --config, both derived from THIS test's
        # tree — never the caller's cwd. That is exactly the config-context trap the
        # linter work fell into (a --select that dropped the config, a run that
        # inherited a foreign ruff.toml): a guard that measures the wrong tree stays
        # green and guards nothing.
        return subprocess.run(
            [_ruff_path(), "check", "--config", str(_REPO_ROOT / "ruff.toml"), *args],
            capture_output=True, text=True,
        )

    @unittest.skipUnless(_ruff_path() is not None, "ruff not installed (dev-only tool)")
    def test_tree_is_lint_clean(self):
        # Pin the roots by name: a size floor over the SUM catches a narrowed root but not
        # a *dropped* one — (src, tests) alone is still 187 > 150 while scripts/
        # (count_callbacks.py et al.) silently goes unchecked. Assert the exact set.
        self.assertEqual(
            {r.name for r in _SCAN_ROOTS}, {"src", "tests", "scripts"},
            "scan roots changed — a dropped root would lint less than the whole tree")
        # Then a file-count floor, like TestLayering does: a wrong _REPO_ROOT keeps the
        # names but finds nothing. Measured ~192 .py files; 150 leaves margin as the tree
        # grows and still trips on an empty/wrong path.
        n_files = sum(len(list(r.rglob("*.py"))) for r in _SCAN_ROOTS)
        self.assertGreater(
            n_files, 150, f"scanned only {n_files} files — narrowed roots? the guard is blind")
        result = self._run_ruff(*(str(r) for r in _SCAN_ROOTS))
        self.assertEqual(
            result.returncode, 0,
            f"ruff reported violations:\n{result.stdout}\n{result.stderr}")

    @unittest.skipUnless(_ruff_path() is not None, "ruff not installed (dev-only tool)")
    def test_the_check_can_go_red(self):
        # A guard that can only pass is worthless. Prove a violation goes red THROUGH the
        # production path (_run_ruff, real --config) — not a bespoke invocation — so
        # narrowing the real check above cannot leave this one falsely green.
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.py"
            bad.write_text("x = '" + "a" * 200 + "'\n")
            result = self._run_ruff(str(bad))
        self.assertNotEqual(
            result.returncode, 0,
            "ruff did not flag a 200-char line — the guard's own mechanism is broken")


class TestNoFStringInGettext(unittest.TestCase):
    """`_(f"…")` / `ngettext(f"…")` is an i18n bug: the f-string is interpolated
    *before* the call, so the msgid is the finished text, not the template — xgettext
    extracts a one-off string and no catalog entry ever matches. Use
    `_("… {x} …").format(...)` with named placeholders instead.

    Catches two shapes: the direct `_(f"…")`, and the two-line form the E501 line-length
    rule nudges toward — a local assigned once from an f-string in a function body and then
    passed to `_`/`ngettext` (`msg = f"…"; _(msg)`). The two guards would otherwise pull
    against each other: E501 punishes the long one-liner the direct check alone sees.
    NOT caught (documented limitation — would need real dataflow analysis): a template
    reaching `_` across functions or through more than one assignment hop.
    """

    @staticmethod
    def _msgid_args(call):
        """The msgid-position args of a `_`/`ngettext` call, or None if it is neither."""
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            return None
        if call.func.id == "_":
            return call.args[:1]                     # _(msgid)
        if call.func.id == "ngettext":
            return call.args[:2]                     # ngettext(singular, plural, n)
        return None

    @classmethod
    def _scope_nodes(cls, scope):
        """Nodes in *scope*'s own body, without descending into nested function scopes."""
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from cls._scope_nodes(child)

    @classmethod
    def _offending_lines(cls, tree):
        # (a) an f-string passed directly into a gettext call
        for node in ast.walk(tree):
            args = cls._msgid_args(node)
            if args and any(isinstance(a, ast.JoinedStr) for a in args):
                yield node.lineno
        # (b) a local assigned once from an f-string, then passed to gettext in the same scope
        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:
            fstr = {t.id for node in cls._scope_nodes(scope)
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr)
                    for t in node.targets if isinstance(t, ast.Name)}
            if not fstr:
                continue
            for node in cls._scope_nodes(scope):
                args = cls._msgid_args(node)
                if args and any(isinstance(a, ast.Name) and a.id in fstr for a in args):
                    yield node.lineno

    def test_no_fstring_in_gettext(self):
        offenders = []
        for pyfile in _ROOT.rglob("*.py"):
            tree = ast.parse(pyfile.read_text(encoding="utf-8"), str(pyfile))
            offenders += [f"{pyfile.relative_to(_ROOT)}:{ln}"
                          for ln in sorted(set(self._offending_lines(tree)))]
        self.assertEqual(
            offenders, [],
            f'_(f"…") / ngettext(f"…") found — the msgid is interpolated, untranslatable: '
            f'{offenders}')

    def test_the_check_can_go_red(self):
        # Effectiveness, both shapes and the false-positive boundary.
        direct = ast.parse('_(f"hi {n}")\nngettext(f"{n} x", "{n} xs", n)\n')
        self.assertEqual(len(set(self._offending_lines(direct))), 2)
        via_var = ast.parse('def f(n):\n    msg = f"hi {n}"\n    return _(msg)\n')
        self.assertEqual(len(set(self._offending_lines(via_var))), 1)
        # a non-f-string local passed to _ must NOT be flagged (no false red)
        ok = ast.parse('def f():\n    msg = "plain"\n    return _(msg)\n')
        self.assertEqual(list(self._offending_lines(ok)), [])


class TestTempdirPinned(unittest.TestCase):
    """The Makefile pins TMPDIR into the repo's ./tmp (TEST_ENV), so the ~44 test
    files using tempfile do not scatter into the system /tmp — the "never use /tmp"
    convention held as a property of the run, not per-test discipline. This guards BOTH
    failure modes: TMPDIR dropped from a pinned run (→ files back in /tmp), and TMPDIR
    set but inert (tempfile silently falls back to /tmp when the pinned directory does
    not exist). Keying the skip on TMPDIR alone would only catch the second and SKIP the
    first — so key "is this a pinned run?" on a different TEST_ENV var, then assert
    TMPDIR is both present and effective."""

    def test_tempfile_resolves_inside_the_repo(self):
        pinned_run = os.environ.get("MDV_CONFIG_DIR")   # any TEST_ENV var: the run is pinned
        tmpdir = os.environ.get("TMPDIR")
        if not pinned_run and not tmpdir:
            self.skipTest("not a pinned run (bare `python -m unittest`)")
        self.assertTrue(
            tmpdir, "TEST_ENV pins this run but TMPDIR is missing — temp files go to /tmp")
        self.assertEqual(
            tempfile.gettempdir(), tmpdir,
            "TMPDIR is set but tempfile fell back — the pinned directory is missing")


if __name__ == "__main__":
    unittest.main()
