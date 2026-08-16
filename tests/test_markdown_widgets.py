"""Tests for markdown_widgets: inline Pango conversion and block parsing/render."""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

Adw.init()

from markdown_vault import markdown_widgets as M


class TestInline(unittest.TestCase):
    def test_bold_italic_code(self):
        self.assertEqual(M.inline_to_pango("**b**"), "<b>b</b>")
        self.assertEqual(M.inline_to_pango("__b__"), "<b>b</b>")
        self.assertEqual(M.inline_to_pango("*i*"), "<i>i</i>")
        self.assertEqual(M.inline_to_pango("_i_"), "<i>i</i>")
        self.assertEqual(M.inline_to_pango("`c`"), "<tt>c</tt>")

    def test_link(self):
        self.assertEqual(M.inline_to_pango("[t](http://x)"),
                         '<a href="http://x">t</a>')
        self.assertEqual(M.inline_to_pango("[t](https://x)"),
                         '<a href="https://x">t</a>')
        self.assertEqual(M.inline_to_pango("[m](mailto:a@b)"),
                         '<a href="mailto:a@b">m</a>')

    def test_disallowed_link_scheme_renders_as_plain_text(self):
        # R46.1 — a note can be imported from anywhere; a file:/javascript: link
        # must not become a clickable launcher, only its text survives.
        # not clickable, but text AND url stay visible (R47.2: don't vanish)
        out = M.inline_to_pango("[here](file:///etc/passwd)")
        self.assertNotIn("<a", out)
        self.assertIn("here", out)
        self.assertIn("file:///etc/passwd", out)
        # any disallowed scheme, case-insensitive, is non-clickable
        self.assertNotIn("<a", M.inline_to_pango("[x](javascript:alert(1))"))
        self.assertNotIn("<a", M.inline_to_pango("[x](FILE:///etc/passwd)"))

    def test_escapes_pango_special_chars(self):
        # < & > must be escaped so set_markup never sees stray markup
        self.assertEqual(M.inline_to_pango("a < b & c"), "a &lt; b &amp; c")

    def test_code_span_content_is_escaped_not_interpreted(self):
        # markup inside a code span is literal, and its specials are escaped
        self.assertEqual(M.inline_to_pango("`a<b & **x**`"),
                         "<tt>a&lt;b &amp; **x**</tt>")

    def test_underscore_in_word_is_not_italic(self):
        self.assertEqual(M.inline_to_pango("snake_case_name"), "snake_case_name")

    def test_emphasis_does_not_rewrite_a_url(self):
        # R49.1 — underscores/asterisks in a URL must not become <i>/<b>
        self.assertEqual(M.inline_to_pango("[x](https://e/a/_foo_/b)"),
                         '<a href="https://e/a/_foo_/b">x</a>')
        # the disallowed-scheme path keeps the URL intact too
        self.assertEqual(M.inline_to_pango("[x](file:///a/_foo_/b)"),
                         "x (file:///a/_foo_/b)")

    def test_emphasis_inside_link_text_still_applies(self):
        self.assertEqual(M.inline_to_pango("[**b**](https://e)"),
                         '<a href="https://e"><b>b</b></a>')

    def test_link_inside_bold(self):
        self.assertEqual(M.inline_to_pango("**[t](https://e)**"),
                         '<b><a href="https://e">t</a></b>')

    def test_bold_wins_over_italic(self):
        self.assertEqual(M.inline_to_pango("**strong**"), "<b>strong</b>")

    def test_sentinel_codepoints_in_input_are_harmless(self):
        # R50.1 — a stray U+E000/U+E001 pair around digits in the answer must not
        # forge a stash sentinel (IndexError or a mis-substitution). It is
        # stripped, so surrounding text renders normally.
        forged = "0"           # would collide with stash index 0
        # the delimiters are stripped; the inner digit survives as plain text,
        # so no sentinel is forged and nothing is mis-substituted or crashes.
        self.assertEqual(M.inline_to_pango(forged + "ok"), "0ok")
        self.assertEqual(M.inline_to_pango("a" + forged + "b"), "a0b")
        # and it still resolves a real code span correctly alongside it
        self.assertEqual(M.inline_to_pango("`c` " + forged), "<tt>c</tt> 0")


class TestBlocks(unittest.TestCase):
    def test_heading_and_paragraph(self):
        blocks = M.parse_blocks("# Title\n\nSome text here.")
        self.assertEqual(blocks[0], ("heading", (1, "Title")))
        self.assertEqual(blocks[1], ("paragraph", "Some text here."))

    def test_paragraph_joins_wrapped_lines(self):
        blocks = M.parse_blocks("one\ntwo\nthree")
        self.assertEqual(blocks, [("paragraph", "one two three")])

    def test_unordered_and_ordered_lists(self):
        u = M.parse_blocks("- a\n- b")
        self.assertEqual(u, [("ulist", [(None, "a"), (None, "b")])])
        o = M.parse_blocks("1. a\n2. b")
        self.assertEqual(o, [("olist", [("1", "a"), ("2", "b")])])

    def test_fenced_code_block(self):
        blocks = M.parse_blocks("```\nx = 1\ny = 2\n```")
        self.assertEqual(blocks, [("code", "x = 1\ny = 2")])

    def test_tilde_fenced_code_block(self):
        # Shared FenceTracker now recognises ~~~ fences (was: backticks only).
        blocks = M.parse_blocks("~~~\nx = 1\n~~~")
        self.assertEqual(blocks, [("code", "x = 1")])

    def test_inner_fence_stays_inside_a_longer_block(self):
        # A ``` inside a ```` block is code content, not a premature close.
        blocks = M.parse_blocks("````\n```\ninner\n```\n````")
        self.assertEqual(blocks, [("code", "```\ninner\n```")])

    def test_pipe_table(self):
        blocks = M.parse_blocks("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
        self.assertEqual(blocks, [("table",
                                   (["A", "B"], [["1", "2"], ["3", "4"]]))])

    def test_table_pipes_are_not_a_paragraph(self):
        # the reported bug: a table must not fall through to raw-pipe text
        kinds = [k for k, _ in M.parse_blocks("| A | B |\n|---|---|\n| 1 | 2 |")]
        self.assertEqual(kinds, ["table"])

    def test_blockquote_and_rule(self):
        self.assertEqual(M.parse_blocks("> quoted"), [("quote", "quoted")])
        self.assertEqual(M.parse_blocks("---"), [("rule", None)])


class TestRender(unittest.TestCase):
    def _children(self, widget):
        out, c = [], widget.get_first_child()
        while c is not None:
            out.append(c)
            c = c.get_next_sibling()
        return out

    def test_render_returns_a_box_of_blocks(self):
        box = M.render_markdown("# H\n\npara\n\n- x\n- y")
        self.assertIsInstance(box, Gtk.Box)
        self.assertEqual(len(self._children(box)), 3)   # heading, paragraph, list

    def test_table_renders_as_a_grid(self):
        box = M.render_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        grid = self._children(box)[0]
        self.assertIsInstance(grid, Gtk.Grid)
        # header at row 0, data at row 1
        self.assertEqual(grid.get_child_at(0, 0).get_text(), "A")
        self.assertEqual(grid.get_child_at(1, 1).get_text(), "2")

    def test_code_block_is_styled_as_a_box(self):
        label = self._children(M.render_markdown("```\nx = 1\n```"))[0]
        self.assertEqual(label.get_text(), "x = 1")
        self.assertTrue(label.has_css_class("monospace"))
        self.assertTrue(label.has_css_class("mv-answer-code"))   # bg/padding box

    def test_rendered_labels_are_selectable_but_not_focusable(self):
        # selectable so a mouse selection + context menu copies the visible text,
        # but not focusable so the answer adds no Tab stops (R47.1)
        label = self._children(M.render_markdown("plain paragraph"))[0]
        self.assertTrue(label.get_selectable())
        self.assertFalse(label.get_focusable())


if __name__ == "__main__":
    unittest.main()
