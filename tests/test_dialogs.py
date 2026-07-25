"""Tests for markdown_vault.dialogs — dialog helper functions.

Tests callback wiring and response normalisation by mocking
Adw.AlertDialog so no real display is needed.
"""

import os
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from markdown_vault import dialogs


# ---------------------------------------------------------------------------
# show_error
# ---------------------------------------------------------------------------

class TestShowError(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_presents_with_heading_and_body(self, MockDialog):
        parent = MagicMock()
        dialogs.show_error(parent, "Oops", "Something broke")

        MockDialog.assert_called_once_with(heading="Oops", body="Something broke")
        dlg = MockDialog.return_value
        dlg.add_response.assert_called_once_with("ok", "OK")
        dlg.present.assert_called_once_with(parent)


# ---------------------------------------------------------------------------
# show_link_not_found
# ---------------------------------------------------------------------------

class TestShowLinkNotFound(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_presents_with_path(self, MockDialog):
        parent = MagicMock()
        dialogs.show_link_not_found(parent, "my-page")

        MockDialog.assert_called_once()
        kwargs = MockDialog.call_args[1]
        self.assertIn("my-page", kwargs["body"])
        dlg = MockDialog.return_value
        dlg.add_response.assert_called_once_with("close", "Close")


# ---------------------------------------------------------------------------
# prompt_new_item
# ---------------------------------------------------------------------------

class TestPromptNewItem(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_create_calls_on_response_with_name(self, MockGLib, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.prompt_new_item(
            parent, "New File", "Body", "placeholder",
            on_response=lambda name: responses.append(name),
        )

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]

        # Simulate the entry returning a name
        entry = dlg.set_extra_child.call_args[0][0]
        entry.set_text("  My Note  ")

        response_cb(dlg, "create")
        self.assertEqual(responses, ["My Note"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_cancel_calls_on_response_with_none(self, MockGLib, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.prompt_new_item(
            parent, "New File", "Body", "placeholder",
            on_response=lambda name: responses.append(name),
        )

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "cancel")
        self.assertEqual(responses, [None])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_empty_input_calls_on_response_with_none(self, MockGLib, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.prompt_new_item(
            parent, "New File", "Body", "placeholder",
            on_response=lambda name: responses.append(name),
        )

        dlg = MockDialog.return_value
        entry = dlg.set_extra_child.call_args[0][0]
        entry.set_text("   ")

        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "create")
        self.assertEqual(responses, [None])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_sets_up_entry_correctly(self, MockGLib, MockDialog):
        parent = MagicMock()
        dialogs.prompt_new_item(
            parent, "New File", "Body", "My Placeholder",
            on_response=lambda name: None,
        )

        dlg = MockDialog.return_value
        entry = dlg.set_extra_child.call_args[0][0]
        # Gtk.Entry is real — verify the property was set
        self.assertTrue(entry.get_activates_default())


# ---------------------------------------------------------------------------
# confirm_delete
# ---------------------------------------------------------------------------

class TestConfirmDelete(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_file_body(self, MockDialog):
        fpath = os.path.join(self._tmp, "note.md")
        with open(fpath, "w") as f:
            f.write("test")

        parent = MagicMock()
        dialogs.confirm_delete(parent, fpath, lambda c: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("note.md", kwargs["body"])
        self.assertNotIn("contained items", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_empty_dir_body(self, MockDialog):
        parent = MagicMock()
        dialogs.confirm_delete(parent, self._tmp, lambda c: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("empty folder", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_nonempty_dir_body_shows_count(self, MockDialog):
        for i in range(3):
            with open(os.path.join(self._tmp, f"f{i}.md"), "w") as f:
                f.write("x")

        parent = MagicMock()
        dialogs.confirm_delete(parent, self._tmp, lambda c: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("3 contained items", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_confirm_calls_true(self, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_delete(parent, self._tmp, lambda c: responses.append(c))

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "delete")
        self.assertEqual(responses, [True])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_cancel_calls_false(self, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_delete(parent, self._tmp, lambda c: responses.append(c))

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "cancel")
        self.assertEqual(responses, [False])


# ---------------------------------------------------------------------------
# confirm_discard_unsaved
# ---------------------------------------------------------------------------

class TestConfirmDiscardUnsaved(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def _get_response_cb(self, MockDialog):
        """Helper: call the function and return the wired response callback."""
        parent = MagicMock()
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: None,
        )
        return MockDialog.return_value.connect.call_args[0][1]

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_save_response(self, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: responses.append(r),
        )
        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "save")
        self.assertEqual(responses, ["save"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_discard_response(self, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: responses.append(r),
        )
        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "discard")
        self.assertEqual(responses, ["discard"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_cancel_response(self, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: responses.append(r),
        )
        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "cancel")
        self.assertEqual(responses, ["cancel"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_close_button_normalised_to_cancel(self, MockDialog):
        """The close button sends 'close' — must be mapped to 'cancel'."""
        parent = MagicMock()
        responses = []
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: responses.append(r),
        )
        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "close")
        self.assertEqual(responses, ["cancel"],
                         "BUG: 'close' response not normalised to 'cancel'")

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_body_shows_tab_count(self, MockDialog):
        parent = MagicMock()
        paths = ["/a/note.md", "/b/other.md"]
        dialogs.confirm_discard_unsaved(parent, paths, lambda r: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("2 tabs", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_body_shows_single_tab(self, MockDialog):
        parent = MagicMock()
        dialogs.confirm_discard_unsaved(
            parent, ["/a/note.md"], lambda r: None,
        )

        kwargs = MockDialog.call_args[1]
        self.assertIn("1 tab ", kwargs["body"])
        self.assertNotIn("tabs", kwargs["body"])


if __name__ == "__main__":
    unittest.main()
