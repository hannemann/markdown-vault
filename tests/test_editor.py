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


class TestClampScroll(unittest.TestCase):
    """The pure scroll-clamp helper — the part reload_editor's pattern is built
    on: never scroll past the last page, never below zero."""

    def test_in_range_value_kept(self):
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(1234.0, upper=9016.0, page_size=500.0), 1234.0)

    def test_value_past_end_clamped_to_last_page(self):
        # Returning into a note that has since become shorter: 1234 no longer
        # exists, so land at the end (upper - page_size) instead of the void.
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(1234.0, upper=500.0, page_size=100.0), 400.0)

    def test_never_negative(self):
        from markdown_vault.editor.editor import _clamp_scroll
        self.assertEqual(_clamp_scroll(50.0, upper=80.0, page_size=200.0), 0.0)


class TestEditorScrollPosition(unittest.TestCase):
    """Capture + restore of the reader's caret and scroll — feature: the
    history restores where the reader was."""

    def _editor(self, text):
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._buffer.set_text(text)
        return ed

    def _cursor(self, ed):
        buf = ed._buffer
        return buf.get_iter_at_mark(buf.get_insert()).get_offset()

    def test_capture_reads_cursor_offset(self):
        ed = self._editor("hello world")
        ed._buffer.place_cursor(ed._buffer.get_iter_at_offset(6))
        _scroll, cursor = ed.capture_scroll_position()
        self.assertEqual(cursor, 6)

    def test_restore_places_cursor(self):
        ed = self._editor("hello world")
        ed.restore_scroll_position(cursor=3)
        self.assertEqual(self._cursor(ed), 3)

    def test_restore_clamps_cursor_into_shorter_buffer(self):
        # The note is now shorter than when the position was captured; the caret
        # must land at the end, not raise past the character count.
        ed = self._editor("short")  # 5 chars
        ed.restore_scroll_position(cursor=9999)
        self.assertEqual(self._cursor(ed), 5)

    def test_restore_without_cursor_leaves_caret(self):
        ed = self._editor("hello world")
        ed._buffer.place_cursor(ed._buffer.get_iter_at_offset(4))
        ed.restore_scroll_position(scroll=10.0)  # cursor omitted
        self.assertEqual(self._cursor(ed), 4)

    def _flush_idle(self):
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    def test_restore_instant_sets_the_adjustment_value(self):
        # A note switch (smooth=False, the default) jumps: the saved pixel offset
        # is written straight onto the adjustment.
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        vadj.get_upper.return_value = 1000.0
        vadj.get_page_size.return_value = 200.0
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, cursor=3, smooth=False)
            self._flush_idle()
        vadj.set_value.assert_called_once()

    def test_restore_smooth_targets_the_saved_offset_not_the_caret(self):
        # In-page back/forward (smooth=True) animates via scroll_to_iter to the
        # line at the SAVED offset — no direct adjustment jump. The caret is
        # routinely far from the viewport (a freshly opened note keeps it at 0
        # while the reader scrolls down), so aligning on it would land at the
        # caret instead of the saved spot.
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        target = object()
        ed._view = m.MagicMock()
        ed._view.get_iter_at_location.return_value = (False, target)
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, cursor=0, smooth=True)
            self._flush_idle()
        ed._view.get_iter_at_location.assert_called_once_with(0, 120)
        ed._view.scroll_to_iter.assert_called_once_with(target, 0.0, True, 0.0, 0.0)
        vadj.set_value.assert_not_called()

    def test_restore_smooth_needs_no_caret(self):
        # An entry may carry a scroll and no cursor; the animation no longer
        # depends on the caret, so it must still run (it used to fall back to
        # the instant jump).
        import unittest.mock as m
        ed = self._editor("line one\nline two\nline three")
        vadj = m.MagicMock()
        ed._view = m.MagicMock()
        ed._view.get_iter_at_location.return_value = (False, object())
        with m.patch.object(ed, "get_vadjustment", return_value=vadj):
            ed.restore_scroll_position(scroll=120.0, smooth=True)
            self._flush_idle()
        ed._view.scroll_to_iter.assert_called_once()
        vadj.set_value.assert_not_called()


class TestInsertImageOutsideVault(unittest.TestCase):
    """The caller side of the attachments VaultFS migration: a note outside every configured
    vault makes store_image refuse with VaultWriteError — which is NOT an OSError. insert_image
    must catch it and insert nothing, not crash. Tests the caller, not only the receiver: a bare
    `except OSError` would let the new type through (the exact wiring the migration fixed)."""

    def test_insert_image_outside_any_vault_is_caught_not_crashing(self):
        from unittest.mock import patch

        from markdown_vault.core import vault_fs
        from markdown_vault.editor.editor import Editor
        ed = Editor()
        ed._file_path = "/loose/note.md"          # not inside any configured vault
        before = ed.get_text()
        with patch("markdown_vault.core.attachments.store_image",
                   side_effect=vault_fs.VaultWriteError("outside every vault")):
            ed.insert_image(b"PNGDATA", "pic.png")   # must not raise
        self.assertEqual(ed.get_text(), before)      # nothing inserted


if __name__ == "__main__":
    unittest.main()
