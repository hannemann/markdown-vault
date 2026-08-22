"""The settings JSON Schema is the documentation source; this keeps it honest.

``_DEFAULT_SETTINGS`` is the runtime source of truth, the schema the docs source
(``docs/settings.md`` generates from it). They are two artifacts, so a coverage
test keeps them equal: every runtime leaf has a schema leaf and vice versa, each
schema leaf carries a description, and its declared type matches the default's.
Completeness, not correctness — a wrong *description* is caught only by a human
reading it against the code (the ZF1 rule), which is why the reference exists.
"""

import json
import unittest
from pathlib import Path

import markdown_vault.core.config as _cfg

SCHEMA_PATH = Path(_cfg.__file__).parent / "settings.schema.json"

# JSON Schema type for a Python default. ``bool`` is checked by exact type (it is
# a subclass of int), so ``type(value)`` — not isinstance — is the right key.
_JSON_TYPE = {bool: "boolean", int: "integer", float: "number",
              str: "string", dict: "object"}


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
        # runtime leaf — the exact ZH1 gap, but for docs.
        self.assertEqual(set(self.leaves), set(self.defaults))

    def test_every_leaf_has_a_description(self):
        missing = [p for p, n in self.leaves.items()
                   if not n.get("description", "").strip()]
        self.assertEqual(missing, [], f"schema leaves without a description: {missing}")

    def test_declared_type_matches_the_default(self):
        for path, default in self.defaults.items():
            with self.subTest(path=path):
                self.assertEqual(
                    self.leaves[path].get("type"), _JSON_TYPE[type(default)])

    def test_default_is_within_its_enum(self):
        for path, default in self.defaults.items():
            enum = self.leaves[path].get("enum")
            if enum is not None:
                with self.subTest(path=path):
                    self.assertIn(default, enum)

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


if __name__ == "__main__":
    unittest.main()
