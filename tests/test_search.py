"""Tests for markdown_vault.search — full-text search logic."""

import shutil
import tempfile
import unittest
from pathlib import Path

# We test the search logic directly without GTK widgets.
from src.search import SearchBar


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

    def _make_searcher(self):
        """Create a SearchBar instance and return the internal search method."""
        bar = SearchBar(get_vault_paths=lambda: [str(self._vault_a), str(self._vault_b)])
        return bar

    def test_finds_match(self):
        (self._vault_a / "doc.md").write_text("Hello World", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("World")
        self.assertEqual(len(results), 1)
        self.assertIn("doc.md", results[0][0])

    def test_case_insensitive(self):
        (self._vault_a / "doc.md").write_text("MARKDOWN", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("markdown")
        self.assertEqual(len(results), 1)

    def test_no_match(self):
        (self._vault_a / "doc.md").write_text("nothing here", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("xyz")
        self.assertEqual(len(results), 0)

    def test_ignores_non_md(self):
        (self._vault_a / "doc.txt").write_text("target", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("target")
        self.assertEqual(len(results), 0)

    def test_multiple_vaults(self):
        (self._vault_a / "a.md").write_text("needle", encoding="utf-8")
        (self._vault_b / "b.md").write_text("needle", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("needle")
        self.assertEqual(len(results), 2)

    def test_line_numbers(self):
        (self._vault_a / "doc.md").write_text("line1\nneedle\nline3", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("needle")
        self.assertEqual(results[0][1], 2)

    def test_multiple_matches_in_file(self):
        (self._vault_a / "doc.md").write_text("a a a", encoding="utf-8")
        bar = self._make_searcher()
        results = bar._search_vaults("a")
        self.assertEqual(len(results), 1)  # one line, three matches still one entry


if __name__ == "__main__":
    unittest.main()
