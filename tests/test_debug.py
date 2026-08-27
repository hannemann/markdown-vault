"""Tests for markdown_vault.core.debug — the single owner of the raw FS write for
debug dumps.

The six dump-producing components (file/backlink/vault-tree indices, tabs, sidebar,
preview) build their own data but delegate the disk write here. A debug dump must never
take down the app, so a write or serialization failure is logged and swallowed, not
raised — that "does not raise" is the property under test.
"""

import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from markdown_vault.core import debug


class TestDumpJson(unittest.TestCase):
    def test_writes_indented_utf8_json(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "dump.json"
            debug.dump_json(path, {"stem": "Ünïcode/note"}, "FileIndex")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"stem": "Ünïcode/note"})
            self.assertIn("\n", path.read_text(encoding="utf-8"))   # indent=2, not one line

    def test_overwrites_an_existing_file(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "dump.json"
            path.write_text("old", encoding="utf-8")
            debug.dump_json(path, {"a": 1}, "X")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})

    def test_a_write_failure_is_swallowed_and_logged_not_raised(self):
        # Target in a non-existent directory: write_text raises FileNotFoundError (OSError).
        with TemporaryDirectory() as d:
            path = Path(d) / "gone" / "dump.json"
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_json(path, {"a": 1}, "X")   # must not raise
            self.assertFalse(path.exists())

    def test_a_non_serializable_value_is_swallowed_not_raised(self):
        # json.dumps raises TypeError before any write; a debug dump must not crash.
        with TemporaryDirectory() as d:
            path = Path(d) / "dump.json"
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_json(path, {"bad": object()}, "X")   # must not raise
            self.assertFalse(path.exists())

    def test_a_circular_reference_is_swallowed_not_raised(self):
        # This is vault_tree's ValueError case, now caught in the shared helper.
        with TemporaryDirectory() as d:
            path = Path(d) / "dump.json"
            loop = {}
            loop["self"] = loop
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_json(path, loop, "VaultTree")   # must not raise


class TestDumpText(unittest.TestCase):
    def test_writes_text_verbatim(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "dump.html"
            debug.dump_text(path, "<p>Ünïcode</p>", "preview HTML")
            self.assertEqual(path.read_text(encoding="utf-8"), "<p>Ünïcode</p>")

    def test_a_write_failure_is_swallowed_and_logged_not_raised(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "gone" / "dump.html"
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_text(path, "<p>x</p>", "preview HTML")   # must not raise
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
