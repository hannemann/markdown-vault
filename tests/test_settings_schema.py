"""The settings JSON Schema is the documentation source; this keeps it honest.

``_DEFAULT_SETTINGS`` is the runtime source of truth, the schema the docs source
(``docs/settings.md`` generates from it). They are two artifacts, so a coverage
test keeps them equal: every runtime leaf has a schema leaf and vice versa, each
schema leaf carries a description, and its declared type matches the default's.
Completeness, not correctness — a wrong *description* is caught only by a human
reading it against the code (the ZF1 rule), which is why the reference exists.
"""

import ast
import json
import re
import unittest
from pathlib import Path

import markdown_vault.core.config as _cfg

SCHEMA_PATH = Path(_cfg.__file__).parent / "settings.schema.json"

_ROOT = Path(__file__).resolve().parents[1]     # repo root
_PKG = _ROOT / "src" / "markdown_vault"
# A snake_case dotted path (at least two segments): ask.server.url, tabs.keybinding.next.
_DOTTED = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_0-9]*)+$")
# File extensions that collide with the branch filter — a filename like ``log.md``
# (branch ``log`` + ``md``) is not a settings path. A settings leaf never ends in
# one of these, so excluding them loses nothing real while dropping the filenames.
_EXT = frozenset({
    "md", "py", "json", "yaml", "yml", "txt", "csv", "png", "jpg", "jpeg", "gif",
    "svg", "webp", "gguf", "onnx", "css", "html", "htm", "gz", "zip", "log", "sh",
    "xml", "in", "desktop", "metainfo", "gresource", "pdf", "docx", "pptx", "xlsx",
    "odt", "ods", "odp", "mp3", "wav", "m4a", "flac",
})

def _schema_leaves(node, prefix=""):
    """Map ``{dotted-path: leaf-node}`` for a JSON Schema. A node without
    ``properties`` is a leaf (including the opaque object leaves)."""
    props = node.get("properties")
    if not props:
        return {prefix: node}
    leaves = {}
    for key, child in props.items():
        path = f"{prefix}.{key}" if prefix else key
        leaves.update(_schema_leaves(child, path))
    return leaves


class TestSettingsSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.leaves = _schema_leaves(cls.schema)
        cls.defaults = _cfg._flatten(_cfg._DEFAULT_SETTINGS)

    def test_leaf_sets_match(self):
        # Every runtime leaf documented, and nothing documented that is not a
        # runtime leaf — the exact ZH1 gap, but for docs. Branches marked
        # ``x-runtime: false`` (developer/debug flags) are documented on purpose
        # but deliberately absent from _DEFAULT_SETTINGS, so exclude them here.
        excluded = {k for k, v in self.schema["properties"].items()
                    if v.get("x-runtime") is False}
        documented = {p for p in self.leaves if p.split(".")[0] not in excluded}
        self.assertEqual(documented, set(self.defaults))

    def test_opaque_leaves_carry_no_additionalProperties(self):
        # _flatten stops at _OPAQUE_LEAVES, so the validator never sees their
        # contents. A schema that promised additionalProperties there would be
        # silently ineffective — two sources for one "don't descend" decision.
        for path in _cfg._OPAQUE_LEAVES:
            self.assertNotIn("additionalProperties", self.leaves[path], path)

    def test_every_leaf_has_a_description(self):
        missing = [p for p, n in self.leaves.items()
                   if not n.get("description", "").strip()]
        self.assertEqual(missing, [], f"schema leaves without a description: {missing}")

    def test_declared_type_matches_the_default(self):
        for path, default in self.defaults.items():
            with self.subTest(path=path):
                self.assertEqual(
                    self.leaves[path].get("type"), _cfg._JSON_TYPE[type(default)])

    def test_default_is_within_its_enum(self):
        for path, default in self.defaults.items():
            enum = self.leaves[path].get("enum")
            if enum is not None:
                with self.subTest(path=path):
                    self.assertIn(default, enum)

    def test_schema_default_matches_the_runtime_default(self):
        # The load-time validator resets an invalid value to the default. Schema and
        # runtime defaults must agree, or a reset would install a value the code
        # never uses — a silent drift between the two artifacts.
        for path, default in self.defaults.items():
            with self.subTest(path=path):
                self.assertEqual(self.leaves[path].get("default"), default)

    def test_schema_is_installed_via_meson(self):
        # settings.schema.json must ship as data, or the runtime validator finds no
        # schema and silently skips validation — the py_sources / ModuleNotFoundError
        # gotcha in new clothes.
        meson = (Path(_cfg.__file__).parent / "meson.build").read_text(encoding="utf-8")
        self.assertIn("settings.schema.json", meson)

    def test_debug_dump_additionalProperties_has_a_default(self):
        # The validator resets a wrong-typed debug.dump.<x> to this default; without
        # it, a reset would install None instead of False. The runtime-default test
        # above cannot catch this — debug.dump.* is not a runtime leaf (ZP1).
        self.assertIn("default", self.leaves["debug.dump"]["additionalProperties"])

    def test_generated_docs_are_in_step_with_the_schema(self):
        # docs/settings.md is a *committed generated* file. Editing the schema
        # without re-running `make docs-settings` leaves a published doc that
        # contradicts the schema — the same failure as the switch_mode enum, one
        # layer further out and with more authority. This closes the second seam
        # (schema <-> docs); the others close defaults <-> schema.
        import sys
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "scripts"))
        import gen_settings_docs as gen
        expected = gen.render(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
        actual = (root / "docs" / "settings.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected,
                         "docs/settings.md is stale — run `make docs-settings`")


def _path_form_literals(pkg):
    """Yield ``(path, file, lineno)`` for every snake_case dotted string literal
    in *pkg* whose first segment is a real settings branch.

    Scans **all** string constants, not just call arguments, so the "keys as
    data" category is covered — tuples and module constants (``_APPLIED_KEYS``,
    ``_WEBKIT_ENV_KEYS``, the keybinding/wikilink tuples) where a mis-nested key
    would otherwise hide, the exact shape that broke silently twice in this work.
    """
    branches = {p.split(".")[0] for p in _cfg._flatten(_cfg._DEFAULT_SETTINGS)}
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _DOTTED.match(node.value)
                    and node.value.split(".")[0] in branches
                    and node.value.rsplit(".", 1)[-1] not in _EXT):
                yield node.value, py, node.lineno


class TestSettingsCallSites(unittest.TestCase):
    """Every settings path in the source is a real setting — the last seam.

    The schema chain guards the three artifacts against each other; this guards
    the code that uses them. A dotted literal whose first segment is a settings
    branch (``ask.…``, ``tabs.…``) must resolve to a real leaf, so a typo or a
    forgotten default fails CI instead of silently falling back forever.

    KNOWN GAP (stated, not hidden): a typo in the *first* segment (``asks.top_k``)
    is invisible — ``asks`` is not a known branch, so the literal is not even a
    candidate. And non-literal paths (f-strings like ``f"debug.dump.{comp}"``,
    module constants resolved at runtime) cannot be seen statically.
    """

    def test_every_path_form_literal_is_a_real_setting(self):
        valid = set(_cfg._flatten(_cfg._DEFAULT_SETTINGS))
        offenders = [f"{p}  ({py.relative_to(_ROOT)}:{ln})"
                     for p, py, ln in _path_form_literals(_PKG) if p not in valid]
        self.assertEqual(
            offenders, [],
            "settings path(s) with no _DEFAULT_SETTINGS counterpart — add each to "
            "_DEFAULT_SETTINGS (and the schema):\n  " + "\n  ".join(offenders))

    def test_the_matcher_sees_data_carried_keys_and_skips_look_alikes(self):
        # Effectiveness: a branch-prefixed dotted literal inside a *tuple* (not a
        # call argument) is caught; a look-alike whose first segment is not a
        # branch (session.json, model.gguf) is not — the branch filter is what
        # keeps this from drowning in false positives.
        import tempfile
        src = ('KEYS = ("ask.server.url", "ask.bogus_leaf")\n'
               'paths = ["session.json", "model.gguf"]\n')
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.py").write_text(src, encoding="utf-8")
            found = {p for p, _, _ in _path_form_literals(Path(d))}
        self.assertEqual(found, {"ask.server.url", "ask.bogus_leaf"})

    def test_no_leaf_is_swallowed_by_the_extension_filter(self):
        # _EXT drops filename-shaped literals (log.md). That is only safe while no
        # real settings leaf ends in one of those segments — pin it, or a future
        # debug.log / import.in would silently fall out of the guard unchecked.
        for path in _cfg._flatten(_cfg._DEFAULT_SETTINGS):
            self.assertNotIn(path.rsplit(".", 1)[-1], _EXT, path)


if __name__ == "__main__":
    unittest.main()
