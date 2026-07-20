"""Tests: Verify dialog is actually shown and callback works."""

import os
import tempfile
import shutil
import unittest
import unittest.mock

from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


class TestDialogIsShown(unittest.TestCase):
    """Verify _show_save_dialog is actually called."""

    def _make_fake_window(self, tab_bar, editor1, editor2):
        """Minimal FakeWindow with close-request methods."""
        import markdown_vault.app_window as aw

        class FakeWindow:
            def __init__(self, tb, e1, e2):
                self._tab_bar = tb
                self._editor1 = e1
                self._editor2 = e2
                self._vault_monitor = unittest.mock.Mock()
                self._vault_monitor.skip_next_event = unittest.mock.Mock()

            # Copy methods from real window
            _on_tab_close_requested = aw.MainWindow._on_tab_close_requested
            _show_save_dialog = aw.MainWindow._show_save_dialog
            _do_close_paths = aw.MainWindow._do_close_paths

        return FakeWindow(tab_bar, editor1, editor2)

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._md1 = os.path.join(self._tmp, "note1.md")
        self._md2 = os.path.join(self._tmp, "note2.md")
        for p in (self._md1, self._md2):
            with open(p, "w") as f:
                f.write("# Note")

        self._editor1 = unittest.mock.Mock()
        self._editor2 = unittest.mock.Mock()
        self._tab_bar = unittest.mock.Mock()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_show_save_dialog_called_on_dirty_close(self):
        """_show_save_dialog is called when dirty tabs exist."""
        # Mock: tab.editor.is_modified
        tab1 = unittest.mock.Mock()
        tab1.editor = self._editor1
        self._editor1.is_modified = True
        tab2 = unittest.mock.Mock()
        tab2.editor = self._editor2
        self._editor2.is_modified = False
        self._tab_bar.get_tab = lambda p: tab1 if p == self._md1 else tab2

        win = self._make_fake_window(self._tab_bar, self._editor1, self._editor2)
        with unittest.mock.patch.object(win, '_show_save_dialog') as mock_dialog:
            win._on_tab_close_requested([self._md1], on_confirm=lambda: None)
            mock_dialog.assert_called_once()

    def test_show_save_dialog_not_called_on_clean_close(self):
        """_show_save_dialog is NOT called when no dirty tabs."""
        tab1 = unittest.mock.Mock()
        tab1.editor = self._editor1
        self._editor1.is_modified = False
        tab2 = unittest.mock.Mock()
        tab2.editor = self._editor2
        self._editor2.is_modified = False
        self._tab_bar.get_tab = lambda p: tab1 if p == self._md1 else tab2

        win = self._make_fake_window(self._tab_bar, self._editor1, self._editor2)
        with unittest.mock.patch.object(win, '_show_save_dialog') as mock_dialog:
            # on_confirm is called instead of _do_close_paths when tabs are clean
            on_confirm_called = []
            def fake_on_confirm():
                on_confirm_called.append(True)
            win._on_tab_close_requested([self._md1], on_confirm=fake_on_confirm)
            mock_dialog.assert_not_called()
            self.assertEqual(on_confirm_called, [True],
                            "on_confirm should be called (tabs will be closed)")

    def test_save_dialog_receives_on_confirm(self):
        """_show_save_dialog receives on_confirm callback."""
        tab1 = unittest.mock.Mock()
        tab1.editor = self._editor1
        self._editor1.is_modified = True
        tab2 = unittest.mock.Mock()
        tab2.editor = self._editor2
        self._editor2.is_modified = False
        self._tab_bar.get_tab = lambda p: tab1 if p == self._md1 else tab2

        win = self._make_fake_window(self._tab_bar, self._editor1, self._editor2)
        on_confirm_called = []
        def fake_on_confirm():
            on_confirm_called.append(True)

        with unittest.mock.patch.object(win, '_show_save_dialog') as mock_dialog:
            win._on_tab_close_requested([self._md1], on_confirm=fake_on_confirm)
            args = mock_dialog.call_args
            self.assertEqual(args[0][0], [self._md1])
            self.assertIsNotNone(args[0][1])  # on_confirm is not None

    def test_close_left_via_callback_calls_dialog(self):
        """close_left with dirty tabs triggers dialog flow."""
        from markdown_vault.tabs import TabBar

        tab_bar = TabBar()
        e1 = unittest.mock.Mock()
        e1.is_modified = True
        e1.file_path = self._md1
        e2 = unittest.mock.Mock()
        e2.is_modified = False

        tab_bar.add_tab(self._md1, editor=e1, preview=None)
        tab_bar.add_tab(self._md2, editor=e2, preview=None)

        win = self._make_fake_window(tab_bar, e1, e2)
        tab_bar.set_close_request_callback(win._on_tab_close_requested)

        # call close_left
        tab_bar.close_left(self._md2)

        # Dialog should have been triggered (e1 is dirty)
        # We verify the tab is still open (would be closed if dialog ignored)
        self.assertIn(self._md1, tab_bar.get_all_paths(),
                      "Tab should remain open (dialog requires confirmation)")

    def test_on_confirm_closes_paths(self):
        """on_confirm closes the tabs."""
        from markdown_vault.tabs import TabBar

        tab_bar = TabBar()
        e1 = unittest.mock.Mock()
        e1.is_modified = True
        e1.file_path = self._md1
        e2 = unittest.mock.Mock()
        e2.is_modified = False

        tab_bar.add_tab(self._md1, editor=e1, preview=None)
        tab_bar.add_tab(self._md2, editor=e2, preview=None)

        win = self._make_fake_window(tab_bar, e1, e2)
        tab_bar.set_close_request_callback(win._on_tab_close_requested)

        # call close_left
        tab_bar.close_left(self._md2)

        # simulate on_confirm (would be called by dialog)
        # Since _show_save_dialog is not actually called, we check directly
        win._do_close_paths([self._md1])

        self.assertNotIn(self._md1, tab_bar.get_all_paths(),
                        "Tab should be closed after on_confirm")


if __name__ == "__main__":
    unittest.main()
