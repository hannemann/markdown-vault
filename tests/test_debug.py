"""Tests for markdown_vault.core.debug — formats debug dumps and delegates the write to
StateFS.

The six dump-producing components (file/backlink/vault-tree indices, tabs, sidebar,
preview) build their own data but delegate the disk write here, and from here through
StateFS. A debug dump must never take down the app, so a write, containment or
serialization failure is logged and swallowed, not raised — that "does not raise" is the
property under test. The tests patch StateFS's roots to a temp dir so the guarded write is
allowed there; a path outside it exercises the containment-refusal swallow.
"""

import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from markdown_vault.core import debug
from markdown_vault.core import state_fs


def _state_root(root):
    """Patch StateFS so *root* is the one allowed state root and there are no vaults."""
    return (mock.patch.object(state_fs, "_state_roots", return_value=[str(root)]),
            mock.patch.object(state_fs, "_vault_roots", return_value=[]))


class _Rooted(unittest.TestCase):
    def setUp(self):
        self._dir = TemporaryDirectory()
        self.root = self._dir.name
        self._patches = _state_root(self.root)
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._dir.cleanup()


class TestDumpJson(_Rooted):
    def test_writes_indented_utf8_json(self):
        path = Path(self.root) / "dump.json"
        debug.dump_json(path, {"stem": "Ünïcode/note"}, "FileIndex")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"stem": "Ünïcode/note"})
        self.assertIn("\n", path.read_text(encoding="utf-8"))   # indent=2, not one line

    def test_overwrites_an_existing_file(self):
        path = Path(self.root) / "dump.json"
        path.write_text("old", encoding="utf-8")
        debug.dump_json(path, {"a": 1}, "X")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})

    def test_a_refused_write_is_swallowed_and_logged_not_raised(self):
        # A path outside the allowed state root: StateFS raises OutsideAllowedRoots, which a
        # debug dump must swallow, not propagate.
        with TemporaryDirectory() as outside:
            path = Path(outside) / "dump.json"
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_json(path, {"a": 1}, "X")   # must not raise
            self.assertFalse(path.exists())

    def test_a_non_serializable_value_is_swallowed_not_raised(self):
        # json.dumps raises TypeError before StateFS is called; a debug dump must not crash.
        path = Path(self.root) / "dump.json"
        with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
            debug.dump_json(path, {"bad": object()}, "X")   # must not raise
        self.assertFalse(path.exists())

    def test_a_circular_reference_is_swallowed_not_raised(self):
        path = Path(self.root) / "dump.json"
        loop = {}
        loop["self"] = loop
        with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
            debug.dump_json(path, loop, "VaultTree")   # must not raise


class TestDumpText(_Rooted):
    def test_writes_text_verbatim(self):
        path = Path(self.root) / "dump.html"
        debug.dump_text(path, "<p>Ünïcode</p>", "preview HTML")
        self.assertEqual(path.read_text(encoding="utf-8"), "<p>Ünïcode</p>")

    def test_a_refused_write_is_swallowed_and_logged_not_raised(self):
        with TemporaryDirectory() as outside:
            path = Path(outside) / "dump.html"
            with self.assertLogs("markdown_vault.core.debug", level=logging.WARNING):
                debug.dump_text(path, "<p>x</p>", "preview HTML")   # must not raise
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
