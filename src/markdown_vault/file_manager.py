"""FileManager — file and folder creation dialogs.

Extracted from ``MainWindow`` to eliminate a chunk of the god-node.
Handles user dialogs, validation, file/folder creation and tab opening
for new-item workflows.
"""

import logging
import os

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from . import dialogs, validation
from .file_ops import FileOps

logger = logging.getLogger(__name__)


class FileManager:
    """Manages new-file and new-folder creation workflows.

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
    """

    def __init__(self, open_tab_fn, vault_tree, file_ops, show_error_fn) -> None:
        self._open_tab = open_tab_fn
        self._vault_tree = vault_tree
        self._file_ops = file_ops
        self._show_error = show_error_fn

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
