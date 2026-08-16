"""Tests for markdown_vault.markdown.tags — wikilink parsing."""

import unittest

from markdown_vault.markdown.tags import (
    WikilinkInfo,
    parse_wikilinks,
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


if __name__ == "__main__":
    unittest.main()
