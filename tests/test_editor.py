"""Tests for markdown_vault.editor.editor — GtkSourceView editor widget.

Editor requires GTK widgets for full behavioral testing. These tests
verify the module structure and API surface without a display server.
"""

import unittest
from pathlib import Path


_SRC = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "editor" / "editor.py"


class TestEditorModuleStructure(unittest.TestCase):
    """Verify the module exports the expected class and API."""

    def test_module_has_editor_class(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("class Editor", source)

    def test_editor_has_expected_methods_in_source(self):
        source = _SRC.read_text(encoding="utf-8")
        for method in ("open_file", "save", "get_text", "scroll_to_line",
                       "update_settings", "update_color_scheme"):
            self.assertIn(f"def {method}", source)

    def test_editor_has_zoom_factor_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def zoom_factor", source)
        self.assertIn("_zoom_factor", source)

    def test_editor_has_base_font_size_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def base_font_size", source)

    def test_editor_constructor_accepts_font_params(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("base_font_size", source)
        self.assertIn("tab_width", source)
        self.assertIn("wrap_text", source)

    def test_editor_uses_gtksource5(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn('GtkSource", "5"', source)

    def test_editor_has_signals(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("file-changed", source)
        self.assertIn("modified-changed", source)
        self.assertIn("text-changed", source)


class TestEditorSearch(unittest.TestCase):
    """Behavioural tests for the in-editor GtkSource search backend."""

    def _editor(self, text):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._buffer.set_text(text)
        ed._buffer.place_cursor(ed._buffer.get_start_iter())
        return ed

    def _selected(self, ed):
        lo, hi = ed._current_match_iters()
        return ed._buffer.get_text(lo, hi, False)

    def test_search_set_text_selects_first_match(self):
        ed = self._editor("bar foo baz foo")
        ed.search_set_text("foo")
        self.assertEqual(self._selected(ed), "foo")
        self.assertEqual(ed._current_match_iters()[0].get_offset(), 4)

    def test_incremental_typing_tightens_in_place(self):
        # R21.9: refining the query keeps the current match, not the next one.
        ed = self._editor("foo bar foo")
        ed.search_set_text("f")
        ed.search_set_text("fo")
        ed.search_set_text("foo")
        self.assertEqual(ed._current_match_iters()[0].get_offset(), 0)

    def test_search_next_selects_match(self):
        ed = self._editor("foo bar foo")
        ed.search_set_text("foo")
        self.assertTrue(ed.search_next())
        self.assertEqual(self._selected(ed), "foo")

    def test_search_next_advances_then_wraps(self):
        ed = self._editor("a X b X c")
        ed.search_set_text("X")  # already selects the first match
        first = ed._current_match_iters()[0].get_offset()
        ed.search_next()
        second = ed._current_match_iters()[0].get_offset()
        self.assertGreater(second, first)
        ed.search_next()  # wrap around (set_wrap_around True)
        self.assertEqual(ed._current_match_iters()[0].get_offset(), first)

    def test_search_prev_goes_backward(self):
        ed = self._editor("X y X y X")
        ed.search_set_text("X")
        ed.search_next()
        ed.search_next()
        mid = ed._current_match_iters()[0].get_offset()
        ed.search_prev()
        self.assertLess(ed._current_match_iters()[0].get_offset(), mid)

    def test_no_match_returns_false(self):
        ed = self._editor("hello world")
        ed.search_set_text("zzz")
        self.assertFalse(ed.search_next())

    def test_clear_disables_search(self):
        ed = self._editor("foo foo")
        ed.search_set_text("foo")
        ed.search_clear()
        self.assertFalse(ed.search_next())


if __name__ == "__main__":
    unittest.main()
