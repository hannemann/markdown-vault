"""Tests for the autosave manager (src/autosave.py)."""

import unittest
from unittest.mock import MagicMock, patch

from markdown_vault.editor.autosave import AutosaveManager


class TestAutosaveManager(unittest.TestCase):
    """Tests for AutosaveManager timer lifecycle and tick logic."""

    def _make_manager(self, interval=30):
        self.get_dirty = MagicMock(return_value=[])
        self.save_tab = MagicMock(return_value=True)
        self.on_failed = MagicMock()
        return AutosaveManager(
            interval=interval,
            get_dirty_tabs=self.get_dirty,
            save_tab=self.save_tab,
            on_save_failed=self.on_failed,
        )

    # ── Timer lifecycle ─────────────────────────────────────────────

    @patch("markdown_vault.editor.autosave.GLib")
    def test_start_creates_timer(self, mock_glib):
        mock_glib.timeout_add_seconds.return_value = 42
        mgr = self._make_manager(interval=30)
        mgr.start()
        mock_glib.timeout_add_seconds.assert_called_once_with(30, mgr._tick)
        self.assertEqual(mgr._timer_id, 42)

    @patch("markdown_vault.editor.autosave.GLib")
    def test_start_skips_zero_interval(self, mock_glib):
        mgr = self._make_manager(interval=0)
        mgr.start()
        mock_glib.timeout_add_seconds.assert_not_called()
        self.assertIsNone(mgr._timer_id)

    @patch("markdown_vault.editor.autosave.GLib")
    def test_cancel_removes_timer(self, mock_glib):
        mgr = self._make_manager()
        mgr._timer_id = 99
        mgr.cancel()
        mock_glib.source_remove.assert_called_once_with(99)
        self.assertIsNone(mgr._timer_id)

    @patch("markdown_vault.editor.autosave.GLib")
    def test_cancel_noop_when_no_timer(self, mock_glib):
        mgr = self._make_manager()
        mgr.cancel()
        mock_glib.source_remove.assert_not_called()

    @patch("markdown_vault.editor.autosave.GLib")
    def test_restart_cancels_and_starts(self, mock_glib):
        mock_glib.timeout_add_seconds.return_value = 7
        mgr = self._make_manager(interval=10)
        mgr._timer_id = 5
        mgr.restart()
        mock_glib.source_remove.assert_called_once_with(5)
        mock_glib.timeout_add_seconds.assert_called_once_with(10, mgr._tick)
        self.assertEqual(mgr._timer_id, 7)

    @patch("markdown_vault.editor.autosave.GLib")
    def test_update_interval(self, mock_glib):
        mock_glib.timeout_add_seconds.return_value = 1
        mgr = self._make_manager(interval=30)
        mgr.update_interval(60)
        self.assertEqual(mgr._interval, 60)
        mock_glib.timeout_add_seconds.assert_called_with(60, mgr._tick)

    # ── Tick behaviour ──────────────────────────────────────────────

    def test_tick_returns_true(self):
        mgr = self._make_manager()
        result = mgr._tick()
        self.assertTrue(result)

    def test_tick_saves_dirty_tabs(self):
        mgr = self._make_manager()
        tab = MagicMock()
        tab.save_error = None
        tab.editor.is_modified = True
        tab.editor.file_path = "/tmp/test.md"
        self.get_dirty.return_value = [tab]

        mgr._tick()

        self.save_tab.assert_called_once_with(tab)

    def test_tick_skips_tabs_with_save_error(self):
        mgr = self._make_manager()
        tab = MagicMock()
        tab.save_error = "disk full"
        tab.file_path = "/tmp/test.md"
        self.get_dirty.return_value = [tab]

        mgr._tick()

        self.save_tab.assert_not_called()
        self.on_failed.assert_not_called()

    def test_tick_calls_on_failed_when_save_fails(self):
        mgr = self._make_manager()
        tab = MagicMock()
        tab.save_error = None
        tab.file_path = "/tmp/test.md"
        tab.editor.file_path = "/tmp/test.md"
        tab.editor.is_modified = True
        self.get_dirty.return_value = [tab]
        self.save_tab.return_value = False

        mgr._tick()

        self.on_failed.assert_called_once()
        args = self.on_failed.call_args[0]
        self.assertEqual(args[0], "/tmp/test.md")
        self.assertIn("Could not save", args[1])

    def test_tick_saves_multiple_tabs(self):
        mgr = self._make_manager()
        tabs = []
        for i in range(3):
            t = MagicMock()
            t.save_error = None
            t.editor.is_modified = True
            t.editor.file_path = f"/tmp/{i}.md"
            tabs.append(t)
        self.get_dirty.return_value = tabs

        mgr._tick()

        self.assertEqual(self.save_tab.call_count, 3)

    def test_tick_no_dirty_tabs(self):
        mgr = self._make_manager()
        self.get_dirty.return_value = []
        mgr._tick()
        self.save_tab.assert_not_called()
        self.on_failed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
