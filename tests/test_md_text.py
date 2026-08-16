"""Tests for the shared Markdown->plain-text reduction."""

import unittest

from markdown_vault.markdown.md_text import strip_markdown, unwrap_bold_headings


class TestStripMarkdown(unittest.TestCase):
    def test_removes_bold_and_italic(self):
        self.assertEqual(strip_markdown("**Bold** and *italic*"), "Bold and italic")

    def test_removes_underscore_and_strikethrough(self):
        self.assertEqual(strip_markdown("_em_ and ~~gone~~"), "em and gone")

    def test_keeps_link_text_only(self):
        self.assertEqual(strip_markdown("see [the docs](https://x)"), "see the docs")

    def test_wikilink_keeps_display_side(self):
        self.assertEqual(strip_markdown("[[Page|Shown]]"), "Shown")

    def test_removes_inline_code_marks(self):
        self.assertEqual(strip_markdown("run `make test` now"), "run make test now")

    def test_bold_heading_text_reads_cleanly(self):
        # The outline case: a heading imported with bold markers.
        self.assertEqual(strip_markdown("**Attention Is All You Need**"),
                         "Attention Is All You Need")

    def test_line_leading_markers_dropped(self):
        self.assertEqual(strip_markdown("## Heading"), "Heading")
        self.assertEqual(strip_markdown("- item"), "item")
        self.assertEqual(strip_markdown("> quote"), "quote")

    def test_plain_text_unchanged(self):
        self.assertEqual(strip_markdown("just words"), "just words")


class TestUnwrapBoldHeadings(unittest.TestCase):
    def test_unwraps_whole_heading_bold(self):
        self.assertEqual(unwrap_bold_headings("# **Attention Is All You Need**"),
                         "# Attention Is All You Need")

    def test_unwraps_double_underscore_bold(self):
        self.assertEqual(unwrap_bold_headings("## __1 Introduction__"),
                         "## 1 Introduction")

    def test_tolerates_trailing_space(self):
        self.assertEqual(unwrap_bold_headings("# **Abstract** "), "# Abstract")

    def test_keeps_italic_heading(self):
        # Importers only ever bold headings; italic is left as authored.
        self.assertEqual(unwrap_bold_headings("# *Emphasis*"), "# *Emphasis*")
        self.assertEqual(unwrap_bold_headings("# _Emphasis_"), "# _Emphasis_")

    def test_leaves_partially_bold_heading(self):
        self.assertEqual(unwrap_bold_headings("# Intro **note**"), "# Intro **note**")

    def test_leaves_two_bold_spans(self):
        self.assertEqual(unwrap_bold_headings("# **A** and **B**"), "# **A** and **B**")

    def test_does_not_touch_non_heading_bold(self):
        self.assertEqual(unwrap_bold_headings("**A bold paragraph**"),
                         "**A bold paragraph**")

    def test_does_not_touch_bold_inside_code_fence(self):
        md = "```\n# **not a heading**\n```"
        self.assertEqual(unwrap_bold_headings(md), md)

    def test_multiple_headings(self):
        md = "# **One**\n\ntext\n\n## **Two**"
        self.assertEqual(unwrap_bold_headings(md), "# One\n\ntext\n\n## Two")


if __name__ == "__main__":
    unittest.main()
