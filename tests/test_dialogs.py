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
# confirm_file_exists
# ---------------------------------------------------------------------------


class TestConfirmFileExists(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_presents_with_file_name(self, MockDialog):
        parent = MagicMock()
        dialogs.confirm_file_exists(parent, "/vault/note.md", lambda c: None)

        MockDialog.assert_called_once()
        kwargs = MockDialog.call_args[1]
        self.assertEqual(kwargs["heading"], "File Already Exists")
        self.assertIn("note.md", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_open_response_calls_callback_true(self, MockGLib, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_file_exists(
            parent, "/vault/note.md", lambda c: responses.append(c),
        )

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "open")
        self.assertEqual(responses, [True])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.GLib")
    def test_cancel_response_calls_callback_false(self, MockGLib, MockDialog):
        parent = MagicMock()
        responses = []
        dialogs.confirm_file_exists(
            parent, "/vault/note.md", lambda c: responses.append(c),
        )

        dlg = MockDialog.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "cancel")
        self.assertEqual(responses, [False])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_response_buttons_configured(self, MockDialog):
        parent = MagicMock()
        dialogs.confirm_file_exists(parent, "/vault/note.md", lambda c: None)

        dlg = MockDialog.return_value
        responses = [
            call[0][0] for call in dlg.add_response.call_args_list
        ]
        self.assertIn("cancel", responses)
        self.assertIn("open", responses)


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
    def test_dir_body_for_empty_folder(self, MockDialog):
        parent = MagicMock()
        dialogs.confirm_delete(parent, self._tmp, lambda c: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("all its contents", kwargs["body"])

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_dir_body_mentions_contents(self, MockDialog):
        for i in range(3):
            with open(os.path.join(self._tmp, f"f{i}.md"), "w") as f:
                f.write("x")

        parent = MagicMock()
        dialogs.confirm_delete(parent, self._tmp, lambda c: None)

        kwargs = MockDialog.call_args[1]
        self.assertIn("all its contents", kwargs["body"])

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


# ---------------------------------------------------------------------------
# show_rename_vault_dialog
# ---------------------------------------------------------------------------

class TestShowRenameVaultDialog(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.config")
    def test_creates_dialog_with_entry(self, MockConfig, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        on_rename = MagicMock()
        dialogs.show_rename_vault_dialog(parent, "/a", "A", on_rename)
        MockAlertDialog.assert_called_once_with(
            heading="Rename Vault",
            body="Enter a new name for the vault.\n"
                 "Name cannot contain spaces or: / \\ > | # [ ]",
        )
        dialog = MockAlertDialog.return_value
        dialog.set_extra_child.assert_called_once()
        MockEntry.assert_called_once_with(placeholder_text="Enter new vault name")
        entry = MockEntry.return_value
        entry.set_text.assert_called_once_with("A")

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.dialogs.GLib.idle_add")
    @patch("markdown_vault.config")
    def test_presents_to_parent(self, MockConfig, MockIdleAdd, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        dialogs.show_rename_vault_dialog(parent, "/a", "A", lambda *a: None)
        dialog = MockAlertDialog.return_value
        dialog.present.assert_called_once_with(parent)


# ---------------------------------------------------------------------------
# show_remove_vault_dialog
# ---------------------------------------------------------------------------

class TestShowRemoveVaultDialog(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_presents_with_vault_name(self, MockAlertDialog):
        parent = MagicMock()
        dialogs.show_remove_vault_dialog(parent, "/a", "MyVault", lambda p: None)
        MockAlertDialog.new.assert_called_once()
        call_args = MockAlertDialog.new.call_args
        self.assertIn("MyVault", str(call_args))

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_calls_on_remove_on_confirm(self, MockAlertDialog):
        parent = MagicMock()
        on_remove = MagicMock()
        dialogs.show_remove_vault_dialog(parent, "/a", "MyVault", on_remove)
        dlg = MockAlertDialog.new.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "remove")
        on_remove.assert_called_once_with("/a")

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_noop_on_cancel(self, MockAlertDialog):
        parent = MagicMock()
        on_remove = MagicMock()
        dialogs.show_remove_vault_dialog(parent, "/a", "MyVault", on_remove)
        dlg = MockAlertDialog.new.return_value
        response_cb = dlg.connect.call_args[0][1]
        response_cb(dlg, "cancel")
        on_remove.assert_not_called()

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_remove_response_added_before_appearance(self, MockAlertDialog):
        # R19.5: set_response_appearance must run AFTER add_response("remove").
        dialogs.show_remove_vault_dialog(MagicMock(), "/a", "MyVault", lambda p: None)
        calls = MockAlertDialog.new.return_value.mock_calls
        add_idx = next(
            i for i, c in enumerate(calls)
            if c[0] == "add_response" and c.args and c.args[0] == "remove"
        )
        app_idx = next(
            i for i, c in enumerate(calls) if c[0] == "set_response_appearance"
        )
        self.assertLess(add_idx, app_idx)

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    def test_english_response_labels(self, MockAlertDialog):
        dialogs.show_remove_vault_dialog(MagicMock(), "/a", "MyVault", lambda p: None)
        dlg = MockAlertDialog.new.return_value
        labels = {c.args for c in dlg.add_response.call_args_list}
        self.assertIn(("cancel", "Cancel"), labels)
        self.assertIn(("remove", "Remove"), labels)


# ---------------------------------------------------------------------------
# show_add_vault_name_dialog
# ---------------------------------------------------------------------------

class TestShowAddVaultNameDialog(unittest.TestCase):

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.dialogs.GLib.idle_add")
    @patch("markdown_vault.config")
    def test_creates_dialog_with_default_name(self, MockConfig, MockIdleAdd, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        dialogs.show_add_vault_name_dialog(parent, "/b", "MyVault", lambda *a: None)
        MockAlertDialog.assert_called_once_with(
            heading="Vault Name Collision",
            body="Enter a unique vault name.\n"
                 "Name cannot contain spaces or: / \\ > | # [ ]",
        )
        dialog = MockAlertDialog.return_value
        dialog.set_extra_child.assert_called_once()
        MockEntry.assert_called_once_with(placeholder_text="Enter a unique vault name")
        entry = MockEntry.return_value
        entry.set_text.assert_called_once_with("MyVault")

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.dialogs.GLib.idle_add")
    @patch("markdown_vault.config")
    def test_presents_to_parent(self, MockConfig, MockIdleAdd, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        dialogs.show_add_vault_name_dialog(parent, "/b", "MyVault", lambda *a: None)
        dialog = MockAlertDialog.return_value
        dialog.present.assert_called_once_with(parent)

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.dialogs.GLib.idle_add")
    @patch("markdown_vault.config")
    def test_calls_on_add_on_confirm(self, MockConfig, MockIdleAdd, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        on_add = MagicMock()
        entry_mock = MockEntry.return_value
        entry_mock.get_text.return_value.strip.return_value = "MyVault"
        dialogs.show_add_vault_name_dialog(parent, "/b", "MyVault", on_add)
        dialog = MockAlertDialog.return_value
        response_cb = dialog.connect.call_args[0][1]
        response_cb(dialog, "add")
        on_add.assert_called_once_with("/b", "MyVault", "MyVault", dialog)

    @patch("markdown_vault.dialogs.Adw.AlertDialog")
    @patch("markdown_vault.dialogs.Gtk.Entry")
    @patch("markdown_vault.dialogs.GLib.idle_add")
    @patch("markdown_vault.config")
    def test_noop_on_cancel(self, MockConfig, MockIdleAdd, MockEntry, MockAlertDialog):
        MockConfig.load_vaults.return_value = [{"path": "/a", "name": "A"}]
        parent = MagicMock()
        on_add = MagicMock()
        dialogs.show_add_vault_name_dialog(parent, "/b", "MyVault", on_add)
        dialog = MockAlertDialog.return_value
        response_cb = dialog.connect.call_args[0][1]
        response_cb(dialog, "cancel")
        on_add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
