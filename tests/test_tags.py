"""Tests for markdown_vault.tags — wikilink parsing and backlink discovery."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.tags import parse_wikilinks, resolve_link, find_backlinks


class TestParseWikilinks(unittest.TestCase):
    """Unit tests for ``parse_wikilinks``."""

    def test_single_link(self):
        result = parse_wikilinks("See [[MyPage]] for details.")
        self.assertEqual(result, [("MyPage", None)])

    def test_link_with_alias(self):
        result = parse_wikilinks("Click [[Target|here]].")
        self.assertEqual(result, [("Target", "here")])

    def test_multiple_links(self):
        result = parse_wikilinks("[[A]] and [[B|label]] and [[C]]")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], ("A", None))
        self.assertEqual(result[1], ("B", "label"))
        self.assertEqual(result[2], ("C", None))

    def test_no_links(self):
        result = parse_wikilinks("Plain text with no links.")
        self.assertEqual(result, [])

    def test_empty_string(self):
        result = parse_wikilinks("")
        self.assertEqual(result, [])

    def test_nested_brackets_not_matched(self):
        result = parse_wikilinks("[[outer [[inner]]]]")
        # Only the inner [[inner]] should match.
        self.assertTrue(len(result) <= 1)

    def test_link_with_special_chars(self):
        result = parse_wikilinks("[[Page-Name_123]]")
        self.assertEqual(result, [("Page-Name_123", None)])


class TestResolveLink(unittest.TestCase):
    """Tests for ``resolve_link``."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._vault = Path(self._tmpdir) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_resolves_in_same_directory(self):
        target = self._vault / "Page.md"
        target.write_text("# Page", encoding="utf-8")
        current = self._vault / "Current.md"
        result = resolve_link("Page", current, [str(self._vault)])
        self.assertEqual(result, target)

    def test_resolves_in_vault_root(self):
        target = self._vault / "Remote.md"
        target.write_text("# Remote", encoding="utf-8")
        sub = self._vault / "sub"
        sub.mkdir()
        current = sub / "Current.md"
        result = resolve_link("Remote", current, [str(self._vault)])
        self.assertEqual(result, target)

    def test_returns_none_when_not_found(self):
        current = self._vault / "Current.md"
        current.write_text("", encoding="utf-8")
        result = resolve_link("Nonexistent", current, [str(self._vault)])
        self.assertIsNone(result)

    def test_prefers_same_dir_over_vault(self):
        local = self._vault / "Page.md"
        local.write_text("local", encoding="utf-8")
        sub = self._vault / "sub"
        sub.mkdir()
        other_vault = Path(self._tmpdir) / "other"
        other_vault.mkdir()
        remote = other_vault / "Page.md"
        remote.write_text("remote", encoding="utf-8")
        current = sub / "Current.md"
        current.write_text("", encoding="utf-8")
        result = resolve_link("Page", current, [str(self._vault), str(other_vault)])
        self.assertEqual(result, local)


class TestFindBacklinks(unittest.TestCase):
    """Tests for ``find_backlinks``."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._vault = Path(self._tmpdir) / "vault"
        self._vault.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_finds_single_backlink(self):
        (self._vault / "A.md").write_text("See [[B]] here.", encoding="utf-8")
        (self._vault / "B.md").write_text("# B", encoding="utf-8")
        result = find_backlinks(self._vault / "B.md", [str(self._vault)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "A.md")

    def test_finds_multiple_backlinks(self):
        (self._vault / "A.md").write_text("[[Target]]", encoding="utf-8")
        (self._vault / "B.md").write_text("[[Target]]", encoding="utf-8")
        (self._vault / "Target.md").write_text("# Target", encoding="utf-8")
        result = find_backlinks(self._vault / "Target.md", [str(self._vault)])
        self.assertEqual(len(result), 2)

    def test_excludes_self(self):
        (self._vault / "Self.md").write_text("[[Self]]", encoding="utf-8")
        result = find_backlinks(self._vault / "Self.md", [str(self._vault)])
        self.assertEqual(len(result), 0)

    def test_ignores_non_md_files(self):
        (self._vault / "notes.txt").write_text("[[Target]]", encoding="utf-8")
        (self._vault / "Target.md").write_text("# T", encoding="utf-8")
        result = find_backlinks(self._vault / "Target.md", [str(self._vault)])
        self.assertEqual(len(result), 0)

    def test_returns_empty_for_no_matches(self):
        (self._vault / "A.md").write_text("No links here.", encoding="utf-8")
        (self._vault / "B.md").write_text("# B", encoding="utf-8")
        result = find_backlinks(self._vault / "B.md", [str(self._vault)])
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
