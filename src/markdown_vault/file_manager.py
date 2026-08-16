"""FileManager — file and folder creation dialogs.

Extracted from ``MainWindow`` to eliminate a chunk of the god-node.
Handles user dialogs, validation, file/folder creation and tab opening
for new-item workflows.
"""

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

from markdown_vault.uikit import dialogs
from markdown_vault.core import validation
from .file_ops import FileOps

logger = logging.getLogger(__name__)


class FileManager:
    """Manages new-file, new-folder creation and delete workflows.

    Parameters
    ----------
    open_tab_fn : callable
        Callback ``(file_path)`` to open a file in a new tab.
    vault_tree : VaultTree
        Used to query vault paths and refresh the tree view.
    file_ops : FileOps
        Filesystem operations (resolve vault, create file/folder).
    show_error_fn : callable
        Callback ``(heading, body)`` to show error dialogs.
    tab_bar : TabBar
        Used to close tabs for deleted files/directories.
    mru : MRUManager
        Used to clean up MRU entries for deleted files.
    nav_history : NavHistory
        Used to clean up navigation history entries for deleted files.
    """

    def __init__(
        self, open_tab_fn, vault_tree, file_ops, show_error_fn,
        tab_bar=None, mru=None, nav_history=None,
    ) -> None:
        self._open_tab = open_tab_fn
        self._vault_tree = vault_tree
        self._file_ops = file_ops
        self._show_error = show_error_fn
        self._tab_bar = tab_bar
        self._mru = mru
        self._nav_history = nav_history

    # ── New file ───────────────────────────────────────────────────────

    def prompt_new_file(
        self, parent: Gtk.Widget, vaults: list[str] | None, default_dir: str
    ) -> None:
        """Show the new-file dialog and handle the full creation workflow.

        If *vaults* is ``None`` or empty, the dialog is shown directly
        without a vault-existence check (used when called from a context
        menu inside a known vault directory).
        """
        if vaults is not None and not vaults:
            self._show_error(
                "No Vault Open",
                "Add a vault directory first before creating files.",
            )
            return

        dialogs.prompt_new_item(
            parent=parent,
            heading="New File",
            body="File name (.md is added automatically):",
            placeholder="e.g. My Note",
            on_response=lambda name: self._handle_new_file_response(name, default_dir, parent),
        )

    def _handle_new_file_response(
        self, name: str | None, default_dir: str, parent: Gtk.Widget
    ) -> None:
        """Process the new-file dialog response."""
        if not name:
            return
        if not name.endswith(".md"):
            name += ".md"
        err = validation.validate_new_item(name, default_dir)
        if err:
            logger.warning("Invalid file name: %s — %s", name, err)
            self._show_error("Invalid Name", err)
            return
        file_path = os.path.join(default_dir, name)
        if os.path.exists(file_path):
            logger.warning("File already exists: %s", file_path)
            self._show_file_exists_dialog(file_path, parent)
            return
        err = self._file_ops.create_file(default_dir, name)
        if err:
            logger.error("Failed to create file %s: %s", file_path, err)
            self._show_error("Create Failed", err)
            return
        self._vault_tree.refresh()
        self._open_tab(file_path)

    def _show_file_exists_dialog(self, file_path: str, parent: Gtk.Widget) -> None:
        """Show confirmation dialog when file already exists and open if confirmed."""
        dialogs.confirm_file_exists(
            parent,
            file_path,
            on_response=lambda open: self._open_tab(file_path) if open else None,
        )

    # ── New folder ─────────────────────────────────────────────────────

    def prompt_new_folder(
        self, parent: Gtk.Widget, parent_dir: str
    ) -> None:
        """Show the new-folder dialog and handle the full creation workflow."""
        dialogs.prompt_new_item(
            parent=parent,
            heading="New Folder",
            body="Folder name:",
            placeholder="e.g. My Folder",
            on_response=lambda name: self._handle_new_folder_response(name, parent_dir),
        )

    def _handle_new_folder_response(self, name: str | None, parent_dir: str) -> None:
        """Process the new-folder dialog response."""
        if not name:
            return
        err = validation.validate_new_item(name, parent_dir)
        if err:
            logger.warning("Invalid folder name: %s — %s", name, err)
            self._show_error("Invalid Name", err)
            return
        err = self._file_ops.create_folder(parent_dir, name)
        if err:
            logger.error("Failed to create folder %s: %s", name, err)
            self._show_error("Create Failed", err)
            return
        self._vault_tree.refresh()

    # ── Delete ─────────────────────────────────────────────────────────

    def prompt_delete(self, parent: Gtk.Widget, path: str) -> None:
        """Show the delete dialog and handle the full delete workflow."""
        dialogs.confirm_delete(
            parent=parent,
            path=path,
            on_response=lambda confirmed: self.handle_delete_response(confirmed, path),
        )

    def handle_delete_response(self, confirmed: bool, path: str) -> None:
        """Handle the delete confirmation response.

        Attempts filesystem delete FIRST, only cleans up UI state on success.
        Shows error dialog on failure.
        """
        if not confirmed:
            return

        is_dir = Path(path).is_dir()

        # 1. Attempt filesystem delete FIRST
        err = self._file_ops.delete_path(path)
        if err:
            self._show_error("Delete Failed", err)
            return

        # 2. Only on success: drop the note/folder's downloaded images, then
        #    close tabs, remove from MRU/history, refresh tree.
        from markdown_vault.core import attachments, path_utils
        vault = path_utils.find_vault_for_dir(str(Path(path).parent)) or str(Path(path).parent)
        try:
            attachments.remove(vault, path)
        except OSError as exc:
            logger.warning("attachments: remove failed for %s: %s", path, exc, exc_info=True)

        self._close_tabs_for_path(path, is_dir)
        self._cleanup_mru(path, is_dir)
        self._cleanup_nav_history(path, is_dir)
        self._vault_tree.refresh()

    def _close_tabs_for_path(self, path: str, is_dir: bool) -> None:
        """Close all tabs whose path matches *path* (file) or are inside *path* (directory)."""
        if not self._tab_bar:
            return
        if is_dir:
            paths_to_close = [
                tab_path for tab_path in self._tab_bar.get_all_paths()
                if tab_path == path or tab_path.startswith(path + os.sep)
            ]
            if paths_to_close:
                GLib.idle_add(self._close_tabs_batch, paths_to_close)
        else:
            if path in self._tab_bar.get_all_paths():
                self._tab_bar.close_tab(path)

    def _close_tabs_batch(self, paths: list[str]) -> bool:
        """Close multiple tabs via idle callback to avoid GTK segfault."""
        all_paths = self._tab_bar.get_all_paths() if self._tab_bar else []
        for tab_path in paths:
            if tab_path in all_paths:
                self._tab_bar.close_tab(tab_path)
                all_paths = self._tab_bar.get_all_paths()
        return False  # Do not repeat

    def _cleanup_mru(self, path: str, is_dir: bool) -> None:
        """Remove *path* and (if directory) all contained paths from the MRU list."""
        if not self._mru:
            return
        self._mru.remove(path)
        if is_dir:
            for tab_path in list(self._mru.tabs):
                if tab_path == path or tab_path.startswith(path + os.sep):
                    self._mru.remove(tab_path)

    def _cleanup_nav_history(self, path: str, is_dir: bool) -> None:
        """Remove *path* from the navigation history."""
        if not self._nav_history:
            return
        self._nav_history.remove_path(path, is_dir)
