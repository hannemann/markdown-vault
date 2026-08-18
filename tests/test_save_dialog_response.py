"""The save/discard/cancel answer to "you have unsaved changes".

Cancel must leave every tab open and unsaved — this is the last gate before the
user loses work, and the file's original docstring recorded a real data-loss bug
found here.

Runs against the real window now (`AppWindowTest`). It used to copy four methods
onto a hand-built `FakeWindow`; the editors and the tab bar were already real, so
what the workaround left untested was exactly the part that a split moves: how
those methods reach the rest of the window (`_autosave`, `_close_window_pending`,
`_switch_vault_pending`, the real close path).
"""
import os
import shutil
import tempfile
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from markdown_vault.editor.editor import Editor

from test_app_window_construction import AppWindowTest


class SaveDialogTest(AppWindowTest):
    """Two real notes, open in the window's real tab bar."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._md1 = os.path.join(self._tmp, "note1.md")
        self._md2 = os.path.join(self._tmp, "note2.md")
        for path in (self._md1, self._md2):
            with open(path, "w") as fh:
                fh.write("# Note")
        self._editor1 = self._open(self._md1)
        self._editor2 = self._open(self._md2)

    def _open(self, path):
        """A tab as the window makes them: in the tab bar *and* in the content
        stack. Skipping the stack child leaves the close path removing something
        that was never there (a GTK-WARNING per test, and a suite that stops
        being silent)."""
        editor = Editor(base_font_size=14)
        editor.open_file(path)
        # Stack child first: adding the tab fires "tab changed", which switches
        # the stack to that name right away.
        self.win._content_stack.add_named(Gtk.Box(), path)
        self.win._tab_bar.add_tab(path, editor, unittest.mock.Mock())
        return editor

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _open_paths(self):
        return self.win._tab_bar.get_all_paths()

    def _make_dirty(self, editor):
        editor._buffer.set_text("# Dirty", -1)


class TestCancel(SaveDialogTest):

    def test_cancel_does_not_call_on_confirm(self):
        confirmed = []
        self.win._on_save_dialog_response(
            "cancel", [self._md1], lambda: confirmed.append(True))
        self.assertEqual(confirmed, [],
                         "on_confirm on cancel would close the tabs")
        self.assertIn(self._md1, self._open_paths(), "cancel closed a tab")

    def test_cancel_does_not_save(self):
        self._make_dirty(self._editor1)
        before = self._editor1.get_text()
        self.win._on_save_dialog_response("cancel", [self._md1], None)
        self.assertEqual(self._editor1.get_text(), before)
        self.assertTrue(self._editor1.is_modified)

    def test_cancel_clears_the_pending_flags(self):
        # Only visible on the real window: a cancelled close leaves the window
        # and the vault switch armed unless both flags are cleared, and the next
        # attempt then behaves as if a switch were already running.
        self.win._close_window_pending = True
        self.win._switch_vault_pending = True
        self.win._on_save_dialog_response("cancel", [self._md1], None)
        self.assertFalse(self.win._close_window_pending)
        self.assertFalse(self.win._switch_vault_pending)

    def test_bulk_cancel_keeps_all_tabs(self):
        self._make_dirty(self._editor1)
        self._make_dirty(self._editor2)
        self.win._on_save_dialog_response("cancel", [self._md1, self._md2], None)
        self.assertEqual(set(self._open_paths()), {self._md1, self._md2})


class TestSave(SaveDialogTest):

    def test_save_writes_and_closes(self):
        self._make_dirty(self._editor1)
        self.win._on_save_dialog_response("save", [self._md1], None)
        self.assertFalse(self._editor1.is_modified)
        self.assertNotIn(self._md1, self._open_paths())

    def test_save_with_on_confirm_calls_confirm(self):
        self._make_dirty(self._editor1)
        confirmed = []
        self.win._on_save_dialog_response(
            "save", [self._md1], lambda: confirmed.append(True))
        self.assertEqual(confirmed, [True])
        self.assertFalse(self._editor1.is_modified)

    def test_bulk_save_closes_all(self):
        self._make_dirty(self._editor1)
        self._make_dirty(self._editor2)
        self.win._on_save_dialog_response("save", [self._md1, self._md2], None)
        self.assertEqual(self._open_paths(), [])


class TestDiscard(SaveDialogTest):

    def test_discard_does_not_save_but_closes(self):
        self._make_dirty(self._editor1)
        self.win._on_save_dialog_response("discard", [self._md1], None)
        with open(self._md1) as fh:
            self.assertEqual(fh.read(), "# Note")     # the edit was dropped
        self.assertNotIn(self._md1, self._open_paths())

    def test_discard_with_on_confirm_calls_confirm(self):
        self._make_dirty(self._editor1)
        confirmed = []
        self.win._on_save_dialog_response(
            "discard", [self._md1], lambda: confirmed.append(True))
        self.assertEqual(confirmed, [True])


if __name__ == "__main__":
    unittest.main()
