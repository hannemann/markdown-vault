"""Markdown Vault — left-panel vault tree browser.

Displays all configured vaults as expandable directory trees, similar
to an IDE project browser.  Only ``.md`` files are shown; hidden
files and directories (prefixed with ``.``) are skipped.
"""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GLib, GObject

# Column indices for the TreeStore: name, path, is_dir, icon_name, hint.
_COL_NAME = 0
_COL_PATH = 1
_COL_IS_DIR = 2
_COL_ICON = 3
_COL_HINT = 4

FILE_ICON = "text-x-generic-symbolic"
FOLDER_ICON = "folder-symbolic"


def _tree_sort_func(model, iter_a, iter_b, _data):
    """Directories first, then alphabetical by name (case-insensitive)."""
    is_dir_a = model.get_value(iter_a, _COL_IS_DIR)
    is_dir_b = model.get_value(iter_b, _COL_IS_DIR)
    if is_dir_a and not is_dir_b:
        return -1
    if not is_dir_a and is_dir_b:
        return 1
    return GLib.strcmp0(
        model.get_value(iter_a, _COL_NAME).lower(),
        model.get_value(iter_b, _COL_NAME).lower(),
    )


class VaultTree(Gtk.Box):
    """Left-panel widget showing vault directory trees.

    Signals:
        file-selected(str): Emitted when a ``.md`` file is activated.
    """

    __gsignals__ = {
        "file-selected": (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._vault_paths: list[str] = []

        # --- Header with title and add-button ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        header.set_margin_start(8)
        header.set_margin_end(8)

        title = Gtk.Label(label="Vaults")
        title.add_css_class("heading")
        title.set_xalign(0)
        title.set_hexpand(True)
        header.append(title)

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.add_css_class("circular")
        add_btn.set_tooltip_text("Add vault directory")
        add_btn.connect("clicked", self._on_add_vault_clicked)
        header.append(add_btn)

        self.append(header)

        # --- Tree view ---
        self._store = Gtk.TreeStore(str, str, bool, str, str)
        self._store.set_sort_func(_COL_NAME, _tree_sort_func, None)
        self._store.set_sort_column_id(_COL_NAME, Gtk.SortType.ASCENDING)

        self._tree_view = Gtk.TreeView(model=self._store)
        self._tree_view.set_headers_visible(False)
        self._tree_view.set_activate_on_single_click(True)
        self._tree_view.connect("row-activated", self._on_row_activated)

        cell = Gtk.CellRendererText()
        cell.set_property("ellipsize", 3)
        column = Gtk.TreeViewColumn()
        column.pack_start(cell, True)
        column.add_attribute(cell, "text", _COL_NAME)
        self._tree_view.append_column(column)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._tree_view)
        scrolled.set_vexpand(True)
        self.append(scrolled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_vaults(self, vault_paths: list[str]) -> None:
        """Replace the entire tree with the given vault directories."""
        self._vault_paths = list(vault_paths)
        self._store.clear()
        for vp in vault_paths:
            self._populate_directory(Path(vp), None)

    def get_vault_paths(self) -> list[str]:
        """Return the list of currently loaded vault root paths."""
        return list(self._vault_paths)

    def refresh(self) -> None:
        """Rebuild the tree from the current vault paths."""
        self.set_vaults(self._vault_paths)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _populate_directory(self, path: Path, parent_iter) -> None:
        """Recursively add *path* and its children to the tree store."""
        dir_iter = self._store.append(
            parent_iter, [path.name, str(path), True, FOLDER_ICON, ""]
        )
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                self._populate_directory(entry, dir_iter)
            elif entry.suffix.lower() == ".md":
                self._store.append(
                    dir_iter,
                    [entry.name, str(entry), False, FILE_ICON, "markdown"],
                )

    def _on_row_activated(self, _tree_view, path, _column) -> None:
        """Handle double-click / Enter on a tree row."""
        iter_ = self._store.get_iter(path)
        if self._store.get_value(iter_, _COL_IS_DIR):
            return
        self.emit("file-selected", self._store.get_value(iter_, _COL_PATH))

    def _on_add_vault_clicked(self, _btn) -> None:
        """Open a folder chooser dialog."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Vault Directory")
        dialog.select_folder(None, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result) -> None:
        """Handle the folder chooser response."""
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder:
            path = folder.get_path()
            if path and path not in self._vault_paths:
                self._vault_paths.append(path)
                self._populate_directory(Path(path), None)
                # Persist the new vault.
                from . import config

                config.add_vault(Path(path).name, path)
