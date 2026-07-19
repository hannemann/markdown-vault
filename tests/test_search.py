"""Tests for markdown_vault.search — search integration.

These tests exercise ``search_logic.search_vaults`` directly (the
function that ``SearchBar`` delegates to).  Widget-level tests live
in a separate file.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault import search_logic


class TestSearchVaults(unittest.TestCase):
    """Test the vault search implementation."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._vault_a = Path(self._tmpdir) / "vault_a"
        self._vault_b = Path(self._tmpdir) / "vault_b"
        self._vault_a.mkdir()
        self._vault_b.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _search(self, query, max_results=50):
        return search_logic.search_vaults(
            query, [str(self._vault_a), str(self._vault_b)], max_results,
        )

    def test_finds_match(self):
        (self._vault_a / "doc.md").write_text("Hello World", encoding="utf-8")
        results = self._search("World")
        self.assertEqual(len(results), 1)
        self.assertIn("doc.md", results[0][0])

    def test_case_insensitive(self):
        (self._vault_a / "doc.md").write_text("MARKDOWN", encoding="utf-8")
        results = self._search("markdown")
        self.assertEqual(len(results), 1)

    def test_no_match(self):
        (self._vault_a / "doc.md").write_text("nothing here", encoding="utf-8")
        results = self._search("xyz")
        self.assertEqual(len(results), 0)

    def test_ignores_non_md(self):
        (self._vault_a / "doc.txt").write_text("target", encoding="utf-8")
        results = self._search("target")
        self.assertEqual(len(results), 0)

    def test_multiple_vaults(self):
        (self._vault_a / "a.md").write_text("needle", encoding="utf-8")
        (self._vault_b / "b.md").write_text("needle", encoding="utf-8")
        results = self._search("needle")
        self.assertEqual(len(results), 2)

    def test_line_numbers(self):
        (self._vault_a / "doc.md").write_text("line1\nneedle\nline3", encoding="utf-8")
        results = self._search("needle")
        self.assertEqual(results[0][1], 2)

    def test_multiple_matches_in_file(self):
        (self._vault_a / "doc.md").write_text("a a a", encoding="utf-8")
        results = self._search("a")
        self.assertEqual(len(results), 1)  # one line, three matches still one entry

    def test_non_utf8_file_not_crash(self):
        (self._vault_a / "latin.md").write_bytes(b"\xff\xfe\x00binary needle")
        results = self._search("needle")
        self.assertEqual(len(results), 1)

    def test_early_exit_respects_max_results(self):
        for i in range(100):
            (self._vault_a / f"f{i}.md").write_text("needle", encoding="utf-8")
        results = self._search("needle", max_results=5)
        self.assertEqual(len(results), 5)


if __name__ == "__main__":
    unittest.main()
