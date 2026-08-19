"""Tests for markdown_vault.app.scroll_memory — ScrollMemory.

A plain object (history + tab bar, no window), so it is tested as one: the
reading position is saved into history entries on leave and restored on return,
with the view mode deciding which surface carries it.
"""

import unittest
import unittest.mock

from markdown_vault.app.scroll_memory import ScrollMemory
from markdown_vault.core.history import NavHistory


class ScrollMemoryTest(unittest.TestCase):
    def setUp(self):
        self.hist = NavHistory()
        self.tab_bar = unittest.mock.Mock()
        self.mem = ScrollMemory(self.hist, self.tab_bar)

    def _tab(self, path, mode, *, escroll=0.0, ecursor=0, pscroll=0.0):
        tab = unittest.mock.Mock()
        tab.file_path = path
        tab.view_mode = mode
        tab.editor.capture_scroll_position.return_value = (escroll, ecursor)
        tab.preview.preview_scroll_position.return_value = pscroll
        return tab

    # ── record: mode decides which fields are stored ─────────────────

    def test_record_edit_mode_stores_editor_only(self):
        self.hist.push("/a.md")
        self.mem.record_from_tab(self._tab("/a.md", "edit", escroll=120.0, ecursor=42))
        e = self.hist.current_entry
        self.assertEqual((e.editor_scroll, e.editor_cursor), (120.0, 42))
        self.assertIsNone(e.preview_scroll)

    def test_record_render_mode_stores_preview_only(self):
        self.hist.push("/a.md")
        self.mem.record_from_tab(self._tab("/a.md", "render", pscroll=300.0))
        e = self.hist.current_entry
        self.assertEqual(e.preview_scroll, 300.0)
        self.assertIsNone(e.editor_scroll)

    def test_record_split_mode_stores_both(self):
        self.hist.push("/a.md")
        self.mem.record_from_tab(
            self._tab("/a.md", "split", escroll=10.0, ecursor=5, pscroll=50.0))
        e = self.hist.current_entry
        self.assertEqual((e.editor_scroll, e.editor_cursor, e.preview_scroll),
                         (10.0, 5, 50.0))

    # ── save on leave ────────────────────────────────────────────────

    def test_save_leaving_reads_the_tab_of_the_current_entry(self):
        self.hist.push("/a.md")
        self.tab_bar.get_tab.return_value = self._tab("/a.md", "edit",
                                                      escroll=7.0, ecursor=3)
        self.mem.save_leaving()
        self.tab_bar.get_tab.assert_called_once_with("/a.md")
        self.assertEqual(self.hist.current_entry.editor_scroll, 7.0)

    def test_save_leaving_is_noop_with_empty_history(self):
        self.mem.save_leaving()
        self.tab_bar.get_tab.assert_not_called()

    def test_save_leaving_is_noop_while_suppressed(self):
        # During a programmatic open (the vault switch's own restore) saving
        # would overwrite the entry being opened with its fresh (scroll 0)
        # position — the cross-vault back/forward failure.
        self.hist.push("/a.md")
        self.hist.suppress = True
        self.mem.save_leaving()
        self.tab_bar.get_tab.assert_not_called()

    # ── restore on return ────────────────────────────────────────────

    def test_restore_applies_editor_position_and_focuses(self):
        self.hist.push("/a.md", editor_scroll=200.0, editor_cursor=9)
        tab = self._tab("/a.md", "edit")
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.editor.restore_scroll_position.assert_called_once_with(200.0, 9)
        tab.editor.grab_editor_focus.assert_called_once()
        tab.preview.scroll_to_position.assert_not_called()

    def test_restore_applies_preview_position_in_render_without_focus(self):
        self.hist.push("/a.md", preview_scroll=333.0)
        tab = self._tab("/a.md", "render")
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.preview.scroll_to_position.assert_called_once_with(333.0)
        tab.editor.grab_editor_focus.assert_not_called()

    def test_restore_is_noop_on_path_mismatch(self):
        self.hist.push("/a.md", editor_scroll=200.0)
        tab = self._tab("/other.md", "edit")
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.editor.restore_scroll_position.assert_not_called()

    def test_restore_is_noop_without_a_saved_position(self):
        self.hist.push("/a.md")
        tab = self._tab("/a.md", "edit")
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.editor.restore_scroll_position.assert_not_called()


if __name__ == "__main__":
    unittest.main()
