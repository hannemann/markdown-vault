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

    def _tab(self, path, mode, *, escroll=0.0, ecursor=0, pscroll=0.0, showing=True):
        tab = unittest.mock.Mock()
        tab.file_path = path
        tab.view_mode = mode
        tab.editor.capture_scroll_position.return_value = (escroll, ecursor)
        tab.preview.preview_scroll_position.return_value = pscroll
        tab.preview.showing_note.return_value = showing
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

    # ── record before closing (the last readable moment) ────────────

    def test_record_if_current_saves_when_path_is_current(self):
        self.hist.push("/a.md")
        self.tab_bar.get_tab.return_value = self._tab("/a.md", "edit", ecursor=3)
        self.mem.record_if_current("/a.md")
        self.assertEqual(self.hist.current_entry.editor_cursor, 3)

    def test_record_if_current_is_noop_for_a_non_current_path(self):
        self.hist.push("/a.md")
        self.mem.record_if_current("/b.md")
        self.tab_bar.get_tab.assert_not_called()

    def test_record_if_current_ignores_suppression(self):
        # A vault switch closes tabs under suppression, but the leaving position
        # must still be captured (unlike save_leaving, which honours it).
        self.hist.push("/a.md")
        self.hist.suppress = True
        self.tab_bar.get_tab.return_value = self._tab("/a.md", "edit", ecursor=4)
        self.mem.record_if_current("/a.md")
        self.assertEqual(self.hist.current_entry.editor_cursor, 4)

    # ── restore on return ────────────────────────────────────────────

    def test_restore_applies_editor_position_and_focuses(self):
        self.hist.push("/a.md", editor_scroll=200.0, editor_cursor=9)
        tab = self._tab("/a.md", "edit")
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.editor.restore_scroll_position.assert_called_once_with(200.0, 9)
        tab.editor.grab_editor_focus.assert_called_once()
        tab.preview.scroll_to_position.assert_not_called()

    def test_restore_scrolls_now_when_the_note_is_already_shown(self):
        # In-page back/forward: the note is rendered, so scroll immediately.
        self.hist.push("/a.md", preview_scroll=333.0)
        tab = self._tab("/a.md", "render", showing=True)
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.preview.scroll_to_position.assert_called_once_with(333.0)
        tab.preview.arm_scroll.assert_not_called()
        tab.editor.grab_editor_focus.assert_not_called()

    def test_restore_arms_the_scroll_when_a_note_switch_reloads(self):
        # A different note is coming: its content reloads (deferred), so arm the
        # scroll for the load's FINISHED instead of running it on the old page —
        # scrolling now would land on stale content and be wiped by the reload.
        self.hist.push("/a.md", preview_scroll=333.0)
        tab = self._tab("/a.md", "render", showing=False)
        self.tab_bar.get_current_tab.return_value = tab
        self.mem.restore_current()
        tab.preview.arm_scroll.assert_called_once_with(333.0)
        tab.preview.scroll_to_position.assert_not_called()

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
