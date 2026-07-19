"""Tests for search_logic module (src/search_logic.py)."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault.search_logic import search_vaults


class TestSearchVaults(unittest.TestCase):
    """Tests for search_vaults()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        (self._vault / "Page.md").write_text("# Page\n\nContent here\nMore lines\n")
        (self._vault / "Sub").mkdir()
        (self._vault / "Sub" / "Deep.md").write_text("Deep content\nAnother line\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_basic_match(self):
        results = search_vaults("Content", [str(self._vault)])
        self.assertEqual(len(results), 2)
        files = {r[0] for r in results}
        self.assertIn("Page.md", next(f for f in files if "Page.md" in f))

    def test_case_insensitive(self):
        results = search_vaults("content", [str(self._vault)])
        self.assertEqual(len(results), 2)
        results = search_vaults("CONTENT", [str(self._vault)])
        self.assertEqual(len(results), 2)

    def test_no_match(self):
        results = search_vaults("nonexistent", [str(self._vault)])
        self.assertEqual(results, [])

    def test_multiple_matches_in_file(self):
        (self._vault / "Dup.md").write_text("foo\nbar\nfoo\n")
        results = search_vaults("foo", [str(self._vault)])
        self.assertEqual(len(results), 2)

    def test_subdirectory(self):
        results = search_vaults("Deep", [str(self._vault)])
        self.assertEqual(len(results), 1)
        self.assertIn("Deep.md", results[0][0])

    def test_multiple_vaults(self):
        other = Path(self._tmp) / "other"
        other.mkdir()
        (other / "Note.md").write_text("Special content\n")
        results = search_vaults("content", [str(self._vault), str(other)])
        self.assertEqual(len(results), 3)

    def test_max_results_limit(self):
        for i in range(10):
            (self._vault / f"f{i}.md").write_text("word\n")
        results = search_vaults("word", [str(self._vault)], max_results=3)
        self.assertEqual(len(results), 3)

    def test_empty_query(self):
        results = search_vaults("", [str(self._vault)])
        self.assertEqual(results, [])

    def test_non_md_ignored(self):
        (self._vault / "readme.txt").write_text("Content here\n")
        results = search_vaults("Content", [str(self._vault)])
        self.assertEqual(len(results), 2)
        files = {r[0] for r in results}
        self.assertIn("Page.md", next(f for f in files if "Page.md" in f))


if __name__ == "__main__":
    unittest.main()