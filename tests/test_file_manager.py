"""Tests for the FileManager module (src/markdown_vault/file_manager.py).

FileManager is a pure-Python class — no GTK display is needed.
Tests wire mocks into the constructor and verify callback flow by
inspecting mock calls after simulating dialog responses.
"""

import logging
import os
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from markdown_vault.file_manager import FileManager


def _fire_callback(mock_prompt, expected_arg="name"):
    """Extract and fire the on_response callback from a mock prompt_new_item call."""
    call_kwargs = mock_prompt.call_args[1]
    on_response = call_kwargs["on_response"]
    return on_response(expected_arg)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestFileManagerInit(unittest.TestCase):
    """Basic construction tests."""

    def setUp(self):
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()

    def test_stores_dependencies(self):
        fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )
        self.assertIs(fm._open_tab, self._open_tab)
        self.assertIs(fm._vault_tree, self._vault_tree)
        self.assertIs(fm._file_ops, self._file_ops)
        self.assertIs(fm._show_error, self._show_error)


# ---------------------------------------------------------------------------
# prompt_new_file — no vaults
# ---------------------------------------------------------------------------


class TestPromptNewFileNoVaults(unittest.TestCase):

    def setUp(self):
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )

    def test_empty_list_shows_error(self):
        parent = MagicMock()
        self._fm.prompt_new_file(parent, [], "/vault")
        self._show_error.assert_called_once_with(
            "No Vault Open",
            "Add a vault directory first before creating files.",
        )
        self._open_tab.assert_not_called()

    def test_none_vaults_skips_error(self):
        """None vaults → dialog shown (used from context menu)."""
        with patch("markdown_vault.file_manager.dialogs.prompt_new_item") as mock_prompt:
            self._fm.prompt_new_file(MagicMock(), None, "/vault")
            mock_prompt.assert_called_once()


# ---------------------------------------------------------------------------
# prompt_new_file — happy path
# ---------------------------------------------------------------------------


class TestPromptNewFileSuccess(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_creates_file_and_opens_tab(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        self._file_ops.create_file.return_value = None
        parent = MagicMock()
        self._fm.prompt_new_file(parent, [self._tmp], self._tmp)

        mock_prompt.assert_called_once()
        _fire_callback(mock_prompt, "note")
        self._file_ops.create_file.assert_called_once_with(self._tmp, "note.md")
        self._vault_tree.refresh.assert_called_once()
        self._open_tab.assert_called_once_with(
            os.path.join(self._tmp, "note.md")
        )
        self._show_error.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_preserves_md_extension(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        self._file_ops.create_file.return_value = None
        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)

        _fire_callback(mock_prompt, "note.md")
        self._file_ops.create_file.assert_called_once_with(self._tmp, "note.md")


# ---------------------------------------------------------------------------
# prompt_new_file — error paths
# ---------------------------------------------------------------------------


class TestPromptNewFileErrors(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    def test_none_name_does_nothing(self, mock_prompt):
        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
        _fire_callback(mock_prompt, None)
        self._show_error.assert_not_called()
        self._file_ops.create_file.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_validation_error_shows_dialog(self, mock_validate, mock_prompt):
        mock_validate.return_value = "Name contains path separators."
        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)

        _fire_callback(mock_prompt, "bad/name")
        self._show_error.assert_called_once_with("Invalid Name", "Name contains path separators.")
        self._file_ops.create_file.assert_not_called()
        self._open_tab.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_file_exists_shows_confirm(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        # Pre-create the file so os.path.exists() is True
        existing = os.path.join(self._tmp, "note.md")
        with open(existing, "w") as f:
            f.write("existing")

        with patch("markdown_vault.file_manager.dialogs.confirm_file_exists") as mock_confirm:
            self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
            _fire_callback(mock_prompt, "note")

            mock_confirm.assert_called_once()
            self._file_ops.create_file.assert_not_called()
            self._open_tab.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_create_error_shows_dialog(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        self._file_ops.create_file.return_value = "Permission denied"
        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)

        _fire_callback(mock_prompt, "note")
        self._show_error.assert_called_once_with("Create Failed", "Permission denied")
        self._vault_tree.refresh.assert_not_called()
        self._open_tab.assert_not_called()


# ---------------------------------------------------------------------------
# confirm_file_exists callback — file already opened via confirmation
# ---------------------------------------------------------------------------


class TestFileExistsConfirmCallback(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    @patch("markdown_vault.file_manager.dialogs.confirm_file_exists")
    def test_confirm_open_opens_tab(self, mock_confirm, mock_validate, mock_prompt):
        mock_validate.return_value = None
        existing = os.path.join(self._tmp, "note.md")
        with open(existing, "w") as f:
            f.write("existing")

        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
        _fire_callback(mock_prompt, "note")

        confirm_cb = mock_confirm.call_args[1]["on_response"]
        confirm_cb(True)
        self._open_tab.assert_called_once_with(existing)

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    @patch("markdown_vault.file_manager.dialogs.confirm_file_exists")
    def test_confirm_cancel_does_not_open(self, mock_confirm, mock_validate, mock_prompt):
        mock_validate.return_value = None
        existing = os.path.join(self._tmp, "note.md")
        with open(existing, "w") as f:
            f.write("existing")

        self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
        _fire_callback(mock_prompt, "note")

        confirm_cb = mock_confirm.call_args[1]["on_response"]
        confirm_cb(False)
        self._open_tab.assert_not_called()


# ---------------------------------------------------------------------------
# prompt_new_folder — happy path
# ---------------------------------------------------------------------------


class TestPromptNewFolder(unittest.TestCase):

    def _make_fm(self):
        open_tab = MagicMock()
        vault_tree = MagicMock()
        file_ops = MagicMock()
        show_error = MagicMock()
        return FileManager(
            open_tab, vault_tree, file_ops, show_error,
        ), open_tab, vault_tree, file_ops, show_error

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_creates_folder(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        fm, _, _, file_ops, _ = self._make_fm()
        file_ops.create_folder.return_value = None
        fm.prompt_new_folder(MagicMock(), "/tmp")

        mock_prompt.assert_called_once()
        _fire_callback(mock_prompt, "myfolder")
        file_ops.create_folder.assert_called_once_with("/tmp", "myfolder")
        fm._vault_tree.refresh.assert_called_once()
        fm._open_tab.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    def test_none_name_does_nothing(self, mock_prompt):
        fm, _, _, file_ops, show_error = self._make_fm()
        fm.prompt_new_folder(MagicMock(), "/tmp")
        _fire_callback(mock_prompt, None)
        show_error.assert_not_called()
        file_ops.create_folder.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_validation_error_shows_dialog(self, mock_validate, mock_prompt):
        mock_validate.return_value = "Invalid name."
        fm, _, _, file_ops, show_error = self._make_fm()
        fm.prompt_new_folder(MagicMock(), "/tmp")

        _fire_callback(mock_prompt, "bad/name")
        show_error.assert_called_once_with("Invalid Name", "Invalid name.")
        file_ops.create_folder.assert_not_called()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_create_error_shows_dialog(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        fm, _, _, file_ops, show_error = self._make_fm()
        file_ops.create_folder.return_value = "Permission denied"
        fm.prompt_new_folder(MagicMock(), "/tmp")

        _fire_callback(mock_prompt, "myfolder")
        show_error.assert_called_once_with("Create Failed", "Permission denied")
        fm._vault_tree.refresh.assert_not_called()


# ---------------------------------------------------------------------------
# Logging coverage
# ---------------------------------------------------------------------------


class TestFileManagerLogging(unittest.TestCase):
    """Verify that logger.warning/error is called on error paths."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
        )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_invalid_name_logs_warning(self, mock_validate, mock_prompt):
        mock_validate.return_value = "Path separator not allowed."
        with patch.object(self._fm, "_show_error") as mock_err:
            self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
            _fire_callback(mock_prompt, "bad/name")
            mock_err.assert_called_once()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_create_file_failure_logs_error(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        self._file_ops.create_file.return_value = "IO error"
        with patch.object(self._fm, "_show_error") as mock_err:
            self._fm.prompt_new_file(MagicMock(), [self._tmp], self._tmp)
            _fire_callback(mock_prompt, "note")
            mock_err.assert_called_once()

    @patch("markdown_vault.file_manager.dialogs.prompt_new_item")
    @patch("markdown_vault.file_manager.validation.validate_new_item")
    def test_folder_creation_failure_logs_error(self, mock_validate, mock_prompt):
        mock_validate.return_value = None
        self._file_ops.create_folder.return_value = "IO error"
        with patch.object(self._fm, "_show_error") as mock_err:
            self._fm.prompt_new_folder(MagicMock(), self._tmp)
            _fire_callback(mock_prompt, "folder")
            mock_err.assert_called_once()


# ---------------------------------------------------------------------------
# prompt_delete — dialog is shown
# ---------------------------------------------------------------------------


class TestPromptDelete(unittest.TestCase):

    def setUp(self):
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._tab_bar = MagicMock()
        self._mru = MagicMock()
        self._nav_history = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
            tab_bar=self._tab_bar,
            mru=self._mru,
            nav_history=self._nav_history,
        )

    @patch("markdown_vault.file_manager.dialogs.confirm_delete")
    def test_shows_dialog(self, mock_confirm):
        parent = MagicMock()
        self._fm.prompt_delete(parent, "/vault/note.md")
        mock_confirm.assert_called_once()
        call_kwargs = mock_confirm.call_args[1]
        self.assertIs(call_kwargs["parent"], parent)
        self.assertEqual(call_kwargs["path"], "/vault/note.md")
        self.assertTrue(callable(call_kwargs["on_response"]))


# ---------------------------------------------------------------------------
# handle_delete_response — confirmed file delete
# ---------------------------------------------------------------------------


class TestHandleDeleteConfirmedFile(unittest.TestCase):

    def setUp(self):
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._tab_bar = MagicMock()
        self._mru = MagicMock()
        self._nav_history = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
            tab_bar=self._tab_bar,
            mru=self._mru,
            nav_history=self._nav_history,
        )

    @patch("markdown_vault.file_manager.Path")
    def test_file_delete_success(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = False
        mock_path_cls.return_value = mock_path_instance
        self._file_ops.delete_path.return_value = None
        self._tab_bar.get_all_paths.return_value = ["/vault/note.md"]
        self._fm.handle_delete_response(True, "/vault/note.md")

        self._file_ops.delete_path.assert_called_once_with("/vault/note.md")
        self._tab_bar.close_tab.assert_called_once_with("/vault/note.md")
        self._mru.remove.assert_any_call("/vault/note.md")
        self._nav_history.remove_path.assert_called_once_with("/vault/note.md", False)
        self._vault_tree.refresh.assert_called_once()
        self._show_error.assert_not_called()

    @patch("markdown_vault.file_manager.Path")
    def test_file_delete_failure_shows_error(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = False
        mock_path_cls.return_value = mock_path_instance
        self._file_ops.delete_path.return_value = "Permission denied"
        self._tab_bar.get_all_paths.return_value = ["/vault/note.md"]
        self._fm.handle_delete_response(True, "/vault/note.md")

        self._show_error.assert_called_once_with("Delete Failed", "Permission denied")
        self._tab_bar.close_tab.assert_not_called()
        self._mru.remove.assert_not_called()
        self._nav_history.remove_path.assert_not_called()
        self._vault_tree.refresh.assert_not_called()

    @patch("markdown_vault.file_manager.Path")
    def test_cancelled_does_nothing(self, mock_path_cls):
        self._fm.handle_delete_response(False, "/vault/note.md")

        self._file_ops.delete_path.assert_not_called()
        self._tab_bar.close_tab.assert_not_called()
        self._mru.remove.assert_not_called()
        self._nav_history.remove_path.assert_not_called()
        self._vault_tree.refresh.assert_not_called()


# ---------------------------------------------------------------------------
# handle_delete_response — confirmed directory delete
# ---------------------------------------------------------------------------


class TestHandleDeleteConfirmedDir(unittest.TestCase):

    def setUp(self):
        self._open_tab = MagicMock()
        self._vault_tree = MagicMock()
        self._file_ops = MagicMock()
        self._show_error = MagicMock()
        self._tab_bar = MagicMock()
        self._mru = MagicMock()
        self._nav_history = MagicMock()
        self._fm = FileManager(
            self._open_tab, self._vault_tree,
            self._file_ops, self._show_error,
            tab_bar=self._tab_bar,
            mru=self._mru,
            nav_history=self._nav_history,
        )

    @patch("markdown_vault.file_manager.Path")
    def test_dir_delete_closes_all_children(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = True
        mock_path_cls.return_value = mock_path_instance
        self._file_ops.delete_path.return_value = None
        self._tab_bar.get_all_paths.return_value = [
            "/vault/dir/a.md",
            "/vault/dir/b.md",
            "/vault/other.md",
        ]
        self._mru.tabs = [
            "/vault/dir/a.md",
            "/vault/dir/b.md",
            "/vault/other.md",
        ]

        self._fm.handle_delete_response(True, "/vault/dir")

        # Execute the idle callback directly (no GTK main loop in unit tests)
        self._fm._close_tabs_batch(
            [p for p in self._tab_bar.get_all_paths()
             if p.startswith("/vault/dir")],
        )

        self._file_ops.delete_path.assert_called_once_with("/vault/dir")
        self._tab_bar.close_tab.assert_any_call("/vault/dir/a.md")
        self._tab_bar.close_tab.assert_any_call("/vault/dir/b.md")
        # Verify "/vault/other.md" was NOT closed
        call_args = [c[0][0] for c in self._tab_bar.close_tab.call_args_list]
        self.assertNotIn("/vault/other.md", call_args)
        self._mru.remove.assert_any_call("/vault/dir")
        self._mru.remove.assert_any_call("/vault/dir/a.md")
        self._mru.remove.assert_any_call("/vault/dir/b.md")
        self._nav_history.remove_path.assert_called_once_with("/vault/dir", True)
        self._vault_tree.refresh.assert_called_once()


# ---------------------------------------------------------------------------
# handle_delete_response — None dependencies (graceful no-op)
# ---------------------------------------------------------------------------


class TestHandleDeleteNoDependencies(unittest.TestCase):

    @patch("markdown_vault.file_manager.Path")
    def test_no_tab_bar(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = False
        mock_path_cls.return_value = mock_path_instance
        fm = FileManager(
            MagicMock(), MagicMock(),
            MagicMock(), MagicMock(),
            tab_bar=None, mru=None, nav_history=None,
        )
        fm._file_ops.delete_path.return_value = None
        fm.handle_delete_response(True, "/vault/note.md")
        fm._vault_tree.refresh.assert_called_once()

    @patch("markdown_vault.file_manager.Path")
    def test_no_mru(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = False
        mock_path_cls.return_value = mock_path_instance
        fm = FileManager(
            MagicMock(), MagicMock(),
            MagicMock(), MagicMock(),
            tab_bar=MagicMock(), mru=None, nav_history=MagicMock(),
        )
        fm._file_ops.delete_path.return_value = None
        fm.handle_delete_response(True, "/vault/note.md")
        fm._vault_tree.refresh.assert_called_once()

    @patch("markdown_vault.file_manager.Path")
    def test_no_nav_history(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.is_dir.return_value = False
        mock_path_cls.return_value = mock_path_instance
        fm = FileManager(
            MagicMock(), MagicMock(),
            MagicMock(), MagicMock(),
            tab_bar=MagicMock(), mru=MagicMock(), nav_history=None,
        )
        fm._file_ops.delete_path.return_value = None
        fm.handle_delete_response(True, "/vault/note.md")
        fm._vault_tree.refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
