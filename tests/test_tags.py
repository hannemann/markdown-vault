"""Tests for markdown_vault.tags — wikilink parsing and backlink discovery."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.tags import (
    WikilinkInfo,
    parse_wikilinks,
    find_backlinks,
)


class TestParseWikilinks(unittest.TestCase):
    """Unit tests for ``parse_wikilinks``."""

    def test_single_link(self):
        result = parse_wikilinks("See [[MyPage]] for details.")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stem, "MyPage")
        self.assertIsNone(result[0].vault)
        self.assertIsNone(result[0].alias)

    def test_link_with_alias(self):
        result = parse_wikilinks("Click [[Target|here]].")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stem, "Target")
        self.assertEqual(result[0].alias, "here")

    def test_multiple_links(self):
        result = parse_wikilinks("[[A]] and [[B|label]] and [[C]]")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].stem, "A")
        self.assertEqual(result[1].stem, "B")
        self.assertEqual(result[1].alias, "label")
        self.assertEqual(result[2].stem, "C")

    def test_no_links(self):
        result = parse_wikilinks("Plain text with no links.")
        self.assertEqual(result, [])

    def test_nested_brackets_not_matched(self):
        result = parse_wikilinks("[[outer [[inner]]]]")
        # Only the inner [[inner]] should match.
        self.assertTrue(len(result) <= 1)

    def test_link_with_special_chars(self):
        result = parse_wikilinks("[[Page-Name_123]]")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stem, "Page-Name_123")

    def test_simple_link_no_vault(self):
        result = parse_wikilinks("See [[MyPage]] for details.")
        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertIsInstance(info, WikilinkInfo)
        self.assertEqual(info.raw, "MyPage")
        self.assertEqual(info.stem, "MyPage")
        self.assertIsNone(info.vault)
        self.assertIsNone(info.alias)
        self.assertEqual(info.display, "MyPage")

    def test_link_with_alias_no_vault(self):
        result = parse_wikilinks("Click [[Target|here]].")
        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertEqual(info.raw, "Target|here")
        self.assertEqual(info.stem, "Target")
        self.assertIsNone(info.vault)
        self.assertEqual(info.alias, "here")
        self.assertEqual(info.display, "Target|here")

    def test_link_with_vault_prefix(self):
        result = parse_wikilinks("[[VaultA>note]]")
        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertEqual(info.raw, "VaultA>note")
        self.assertEqual(info.stem, "note")
        self.assertEqual(info.vault, "VaultA")
        self.assertIsNone(info.alias)
        self.assertEqual(info.display, "note")

    def test_link_with_vault_prefix_and_alias(self):
        result = parse_wikilinks("[[VaultA>sub/note|Alias]]")
        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertEqual(info.raw, "VaultA>sub/note|Alias")
        self.assertEqual(info.stem, "sub/note")
        self.assertEqual(info.vault, "VaultA")
        self.assertEqual(info.alias, "Alias")
        self.assertEqual(info.display, "sub/note|Alias")

    def test_link_with_path_no_vault(self):
        result = parse_wikilinks("[[sub/note]]")
        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertEqual(info.raw, "sub/note")
        self.assertEqual(info.stem, "sub/note")
        self.assertIsNone(info.vault)
        self.assertIsNone(info.alias)
        self.assertEqual(info.display, "sub/note")

    def test_multiple_mixed_links(self):
        result = parse_wikilinks("[[A]] [[VaultB>C|label]] [[D|alias]]")
        self.assertEqual(len(result), 3)
        a, b, d = result
        self.assertEqual(a.stem, "A")
        self.assertIsNone(a.vault)
        self.assertEqual(b.stem, "C")
        self.assertEqual(b.vault, "VaultB")
        self.assertEqual(b.alias, "label")
        self.assertEqual(d.stem, "D")
        self.assertIsNone(d.vault)
        self.assertEqual(d.alias, "alias")

    def test_empty_string(self):
        result = parse_wikilinks("")
        self.assertEqual(result, [])

    def test_double_colon_at_start_is_vault_prefix(self):
        result = parse_wikilinks("[[Vault>A::B]]")
        self.assertEqual(len(result), 1)
        info = result[0]
        # "::" at start is recognized as vault prefix
        self.assertEqual(info.vault, "Vault")
        self.assertEqual(info.stem, "A::B")


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

    def test_finds_backlink_with_vault_prefix_stem(self):
        # find_backlinks matches on stem, so [[VaultB>Target]] has stem="Target"
        (self._vault / "A.md").write_text("[[VaultB>Target]]", encoding="utf-8")
        (self._vault / "Target.md").write_text("# Target", encoding="utf-8")
        result = find_backlinks(self._vault / "Target.md", [str(self._vault)])
        self.assertEqual(len(result), 1)

    def test_backlink_exact_stem_no_underscore_mapping(self):
        (self._vault / "A.md").write_text("[[My_Note]]", encoding="utf-8")
        (self._vault / "My Note.md").write_text("# My Note", encoding="utf-8")
        result = find_backlinks(self._vault / "My Note.md", [str(self._vault)])
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
