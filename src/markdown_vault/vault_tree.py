"""Markdown Vault — left-panel vault tree browser.

Displays all configured vaults as expandable directory trees, similar
to an IDE project browser.  Only ``.md`` files are shown; hidden
files and directories (prefixed with ``.``) are skipped.
"""

import json
import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, GLib, GObject, Pango, Gio, Gdk

from . import validation
from . import dialogs
from . import config

logger = logging.getLogger(__name__)

# Column indices for the TreeStore: name, path, is_dir, icon_name, hint.
_COL_NAME = 0
_COL_PATH = 1
_COL_IS_DIR = 2
_COL_ICON = 3
_COL_HINT = 4  # written but not read — reserved for future use

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
        vault-activated(str): Emitted when a vault root is double-clicked.
        vault-added(str): Emitted when a new vault is added.
        new-file-requested(str): Emitted with parent dir path.
        new-folder-requested(str): Emitted with parent dir path.
        delete-requested(str): Emitted with path to delete.
        close-file-requested(str): Emitted with file path to close tab.
        file-renamed(str, str): Emitted after successful rename (old, new).
    """

    __gsignals__ = {
        "file-selected": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "vault-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "vault-added": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "vault-renamed": (GObject.SignalFlags.RUN_LAST, None, (str, str, str)),
        "vault-removed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "new-file-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "new-folder-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "close-file-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "file-renamed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "focus-current-file": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._vault_paths: list[str] = []
        self._vaults: list[dict[str, str]] = []
        self._active_vault: str | None = None
        self._context_path: str | None = None
        self._context_is_dir: bool = False
        self._popover: Gtk.PopoverMenu | None = None

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

        focus_btn = Gtk.Button(icon_name="find-location-symbolic")
        focus_btn.add_css_class("flat")
        focus_btn.add_css_class("circular")
        focus_btn.set_tooltip_text("Focus current file in tree")
        focus_btn.connect("clicked", self._on_focus_clicked)
        header.append(focus_btn)

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

        # Double-click on vault root activates it (separate from single-click files).
        self._dbl_click = Gtk.GestureClick()
        self._dbl_click.set_button(1)
        self._dbl_click.connect("pressed", self._on_double_press)
        self._tree_view.add_controller(self._dbl_click)

        # Right-click context menu.
        self._menu_click = Gtk.GestureClick()
        self._menu_click.set_button(3)
        self._menu_click.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self._menu_click.connect("pressed", self._on_right_click)
        self._tree_view.add_controller(self._menu_click)

        # F2 shortcut for rename.
        self._shortcut_ctrl = Gtk.ShortcutController.new()
        self._shortcut_ctrl.set_scope(Gtk.ShortcutScope.LOCAL)
        trigger = Gtk.ShortcutTrigger.parse_string("F2")
        action = Gtk.CallbackAction.new(lambda *_: self._start_rename())
        shortcut = Gtk.Shortcut.new(trigger, action)
        self._shortcut_ctrl.add_shortcut(shortcut)

        # Delete shortcut.
        trigger_del = Gtk.ShortcutTrigger.parse_string("Delete")
        action_del = Gtk.CallbackAction.new(lambda *_: self._on_delete_shortcut())
        shortcut_del = Gtk.Shortcut.new(trigger_del, action_del)
        self._shortcut_ctrl.add_shortcut(shortcut_del)

        self._tree_view.add_controller(self._shortcut_ctrl)

        # Cell renderers: icon + text.
        self._icon_renderer = Gtk.CellRendererPixbuf()
        self._icon_renderer.set_property("mode", Gtk.CellRendererMode.INERT)
        self._cell_renderer = Gtk.CellRendererText()
        self._cell_renderer.set_property("ellipsize", 3)
        self._cell_renderer.set_property("editable", False)
        self._cell_renderer.connect("edited", self._on_inline_edited)
        self._cell_renderer.connect("editing-canceled", self._on_inline_editing_canceled)

        column = Gtk.TreeViewColumn()
        column.pack_start(self._icon_renderer, False)
        column.pack_start(self._cell_renderer, True)
        column.add_attribute(self._icon_renderer, "icon-name", _COL_ICON)
        column.add_attribute(self._cell_renderer, "text", _COL_NAME)
        column.set_cell_data_func(self._cell_renderer, self._cell_data_func)
        self._tree_view.append_column(column)

        # Drag & Drop: move files between directories.
        self._drag_source = Gtk.DragSource.new()
        self._drag_source.set_button(1)
        self._drag_source.set_actions(Gdk.DragAction.MOVE)
        self._drag_source.connect("prepare", self._on_drag_prepare)
        self._drag_source.connect("drag-begin", self._on_drag_begin)
        self._tree_view.add_controller(self._drag_source)

        self._drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        self._drop_target.connect("motion", self._on_drop_motion)
        self._drop_target.connect("drop", self._on_drop)
        self._drop_target.connect("leave", self._on_drop_leave)
        self._tree_view.add_controller(self._drop_target)
        self._drop_hover_path: str | None = None

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_child(self._tree_view)
        self._scrolled.set_vexpand(True)
        self.append(self._scrolled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_vaults(self, vaults: list[dict[str, str]]) -> None:
        """Replace the entire tree with the given vault directories."""
        self._vault_paths = [v["path"] for v in vaults]
        self._vaults = list(vaults)
        self._store.clear()
        for v in vaults:
            self._populate_directory(Path(v["path"]), None, name=v["name"])

    def get_vault_paths(self) -> list[str]:
        """Return the list of currently loaded vault root paths."""
        return list(self._vault_paths)

    def get_selected_path(self) -> str | None:
        """Return the path of the currently selected row, or ``None``."""
        sel = self._tree_view.get_selection()
        model, iter_ = sel.get_selected()
        if iter_ is None:
            return None
        return self._store.get_value(iter_, _COL_PATH)

    def set_active_vault(self, vault_path: str | None) -> None:
        """Set the active vault root and update visual highlighting."""
        self._active_vault = vault_path
        # Force redraw of the cell data func for all visible rows.
        self._tree_view.queue_draw()

    def focus_file(self, file_path: str) -> None:
        """Select and scroll to *file_path* in the tree, expanding parents."""
        iter_ = self._find_iter(file_path)
        if iter_ is None:
            return
        self._expand_parents(iter_)
        tree_path = self._store.get_path(iter_)
        self._tree_view.get_selection().select_path(tree_path)
        self._tree_view.scroll_to_cell(tree_path, None, True, 0.5, 0.0)

    def refresh(self) -> None:
        """Rebuild the tree from the current vault paths, preserving expansion."""
        expanded = self.get_expanded_paths()
        self.set_vaults(self._vaults)
        if expanded:
            self.expand_paths(expanded)

    def get_expanded_paths(self) -> list[str]:
        """Return all currently expanded directory paths."""
        expanded: list[str] = []
        def _walk(iter_):
            path = self._store.get_value(iter_, _COL_PATH)
            if self._tree_view.row_expanded(
                self._store.get_path(iter_)
            ):
                expanded.append(path)
            child = self._store.iter_children(iter_)
            while child:
                if self._store.get_value(child, _COL_IS_DIR):
                    _walk(child)
                child = self._store.iter_next(child)
        # Walk ALL top-level items (one per vault).
        iter_ = self._store.get_iter_first()
        while iter_:
            _walk(iter_)
            iter_ = self._store.iter_next(iter_)
        return expanded

    def expand_paths(self, paths: list[str]) -> None:
        """Expand the directories listed in *paths*."""
        path_set = set(paths)
        def _walk(iter_):
            dir_path = self._store.get_value(iter_, _COL_PATH)
            if dir_path in path_set:
                tree_path = self._store.get_path(iter_)
                self._tree_view.expand_row(tree_path, False)
            child = self._store.iter_children(iter_)
            while child:
                if self._store.get_value(child, _COL_IS_DIR):
                    _walk(child)
                child = self._store.iter_next(child)
        # Walk ALL top-level items (one per vault).
        iter_ = self._store.get_iter_first()
        while iter_:
            _walk(iter_)
            iter_ = self._store.iter_next(iter_)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_iter(self, target_path: str):
        """Walk the TreeStore to find the iter for *target_path*, or None."""
        def _walk(iter_):
            while iter_:
                if self._store.get_value(iter_, _COL_PATH) == target_path:
                    return iter_
                child = self._store.iter_children(iter_)
                if child:
                    result = _walk(child)
                    if result is not None:
                        return result
                iter_ = self._store.iter_next(iter_)
        first = self._store.get_iter_first()
        if first is None:
            return None
        return _walk(first)

    def _expand_parents(self, iter_) -> None:
        """Expand all parent directories of *iter_* so the row is visible."""
        parent = self._store.iter_parent(iter_)
        while parent is not None:
            tree_path = self._store.get_path(parent)
            self._tree_view.expand_row(tree_path, False)
            parent = self._store.iter_parent(parent)

    def _cell_data_func(self, _column, cell, model, iter_, _data) -> None:
        """Apply bold styling to active vault root and highlight drop target."""
        path = model.get_value(iter_, _COL_PATH)
        is_dir = model.get_value(iter_, _COL_IS_DIR)
        # A row is a vault root if it's a directory with no parent (top-level).
        is_vault_root = is_dir and model.iter_parent(iter_) is None
        if is_vault_root and path == self._active_vault:
            cell.set_property("weight", Pango.Weight.BOLD)
        else:
            cell.set_property("weight", Pango.Weight.NORMAL)
        # Vault roots are not inline-editable (use context menu instead).
        cell.set_property("editable", not is_vault_root)
        # Drop target highlight.
        if is_dir and path == self._drop_hover_path:
            ctx = self._tree_view.get_style_context()
            found, rgba = ctx.lookup_color("accent_bg_color")
            if found:
                cell.set_property("cell-background-rgba", rgba)
        else:
            cell.set_property("cell-background-rgba", None)

    def _on_delete_shortcut(self) -> None:
        """Handle Delete key: emit delete-requested for the selected item."""
        path = self.get_selected_path()
        if not path:
            return
        if path in self._vault_paths:
            return
        self.emit("delete-requested", path)

    def _on_right_click(self, _gesture, n_press: int, x: float, y: float) -> None:
        """Show context menu on right-click."""
        path_info = self._tree_view.get_path_at_pos(int(x), int(y))
        if path_info is None:
            # Right-click on empty space — show minimal menu.
            self._context_path = None
            self._context_is_dir = False
            self._show_context_menu(int(x), int(y))
            return
        tree_path = path_info[0]
        iter_ = self._store.get_iter(tree_path)
        self._context_path = self._store.get_value(iter_, _COL_PATH)
        self._context_is_dir = self._store.get_value(iter_, _COL_IS_DIR)
        self._show_context_menu(int(x), int(y))

    def _resolve_context_parent_dir(self) -> str | None:
        """Determine parent directory for New File / New Folder context action."""
        if self._context_path and self._context_is_dir:
            return self._context_path
        elif self._context_path:
            return str(Path(self._context_path).parent)
        else:
            return self._active_vault if self._active_vault else (
                self._vault_paths[0] if self._vault_paths else None
            )

    def _show_context_menu(self, x: int, y: int) -> None:
        """Build and display the context menu at (x, y)."""
        parent_dir = self._resolve_context_parent_dir()

        menu = Gio.Menu()

        if parent_dir:
            menu.append("New File", "ctx.new-file")
            menu.append("New Folder", "ctx.new-folder")

        is_vault_root = (
            self._context_is_dir
            and self._context_path
            and self._context_path in self._vault_paths
        )
        if self._context_path and not is_vault_root:
            menu.append("Rename", "ctx.rename")

        if self._context_path and not is_vault_root:
            menu.append("Delete", "ctx.delete")

        if self._context_path and not self._context_is_dir and self._is_open_file(self._context_path):
            menu.append("Close File", "ctx.close-file")

        if menu.get_n_items() == 0:
            return

        # Build action group.
        action_group = Gio.SimpleActionGroup()

        if parent_dir:
            action = Gio.SimpleAction.new("new-file", None)
            action.connect("activate", lambda *_: self.emit("new-file-requested", parent_dir))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("new-folder", None)
            action.connect("activate", lambda *_: self.emit("new-folder-requested", parent_dir))
            action_group.add_action(action)

        if self._context_path and not is_vault_root:
            action = Gio.SimpleAction.new("rename", None)
            action.connect("activate", lambda *_: self._start_rename_for_path(self._context_path))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("delete", None)
            action.connect("activate", lambda *_: self.emit("delete-requested", self._context_path))
            action_group.add_action(action)

        if self._context_path and not self._context_is_dir and self._is_open_file(self._context_path):
            path = self._context_path
            action = Gio.SimpleAction.new("close-file", None)
            action.connect("activate", lambda *_: self.emit("close-file-requested", path))
            action_group.add_action(action)

        if is_vault_root and self._context_path:
            menu.append("Rename Vault", "ctx.rename-vault")
            menu.append("Remove Vault", "ctx.remove-vault")

            vault_name = self._context_path
            for v in self._vaults:
                if v["path"] == self._context_path:
                    vault_name = v["name"]
                    break

            ctx_path = self._context_path
            ctx_vault_name = vault_name
            action = Gio.SimpleAction.new("rename-vault", None)
            action.connect("activate", lambda *_: self._show_rename_dialog(ctx_path, ctx_vault_name))
            action_group.add_action(action)

            ctx_path = self._context_path
            ctx_vault_name = vault_name
            action = Gio.SimpleAction.new("remove-vault", None)
            action.connect("activate", lambda *_: self._show_remove_dialog(ctx_path, ctx_vault_name))
            action_group.add_action(action)

        # Parent the action group on the ScrolledWindow (parent of PopoverMenu
        # in the widget hierarchy, so PopoverMenu can resolve ctx.* actions).
        # PopoverMenu is also parented on ScrolledWindow to fix hover highlighting
        # (known GTK4 bug: PopoverMenu hover breaks when parented on TreeView).
        logger.debug("Context menu: inserting action group 'ctx' on scrolled window")
        self._scrolled.insert_action_group("ctx", action_group)

        if self._popover is None:
            self._popover = Gtk.PopoverMenu.new_from_model(menu)
            self._popover.set_parent(self._scrolled)
            self._popover.set_has_arrow(False)
            self._popover.connect("closed", self._on_popover_closed)
        else:
            self._popover.set_menu_model(menu)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        self._popover.set_pointing_to(rect)
        self._popover.popup()

    def _on_popover_closed(self, _popover) -> None:
        """Remove action group after a short delay to let pending actions fire."""
        def _cleanup():
            logger.debug("Context menu: removing action group 'ctx'")
            self._scrolled.insert_action_group("ctx", None)
            return False
        GLib.timeout_add(50, _cleanup)

    def _show_rename_dialog(self, vault_path: str, vault_name: str) -> None:
        """Show a dialog to rename the vault."""
        win = self.get_root()
        if win is None:
            return
        dialogs.show_rename_vault_dialog(
            win, vault_path, vault_name, self._do_rename_vault,
        )

    def _do_rename_vault(self, vault_path: str, new_name: str, dialog: Adw.Dialog) -> None:
        """Execute the vault rename."""
        new_name = new_name.strip()
        if not new_name or validation.validate_vault_name(new_name):
            return
        # Capture the current (old) name before the config is updated, and
        # double-check uniqueness (safety net).
        old_name = ""
        for v in config.load_vaults():
            if v["path"] == vault_path:
                old_name = v["name"]
            elif v["name"] == new_name:
                return
        if not old_name:  # vault vanished between dialog open and confirm (R21.19)
            dialog.close()
            return
        updated = config.rename_vault(vault_path, new_name)
        self.set_vaults(updated)
        self.emit("vault-renamed", vault_path, old_name, new_name)
        dialog.close()

    def _show_remove_dialog(self, vault_path: str, vault_name: str) -> None:
        """Show a confirmation dialog to remove the vault."""
        win = self.get_root()
        if win is None:
            return
        dialogs.show_remove_vault_dialog(win, vault_path, vault_name, self._do_remove_vault)

    def _do_remove_vault(self, vault_path: str) -> None:
        """Execute the vault removal."""
        updated = config.remove_vault(vault_path)
        self.set_vaults(updated)
        self.emit("vault-removed", vault_path)

    def _is_open_file(self, file_path: str) -> bool:
        """Check if *file_path* is currently open in a tab.

        Walks up to the MainWindow to check the TabBar.
        """
        # Import here to avoid circular imports.
        from .tabs import TabBar
        win = self.get_root()
        if win is None:
            return False
        tab_bar = getattr(win, "_tab_bar", None)
        if isinstance(tab_bar, TabBar):
            return file_path in tab_bar.get_all_paths()
        return False

    def _start_rename(self) -> None:
        """Start inline rename for the currently selected row."""
        sel = self._tree_view.get_selection()
        model, iter_ = sel.get_selected()
        if iter_ is None:
            return
        path = self._store.get_value(iter_, _COL_PATH)
        is_dir = self._store.get_value(iter_, _COL_IS_DIR)
        # Don't allow renaming vault roots.
        if is_dir and path in self._vault_paths:
            return
        tree_path = self._store.get_path(iter_)
        self._tree_view.set_cursor(tree_path, self._tree_view.get_column(0), True)

    def _start_rename_for_path(self, path: str) -> None:
        """Start inline rename for a specific *path*."""
        def _find_and_edit():
            iter_ = self._store.get_iter_first()
            result = self._find_iter_for_path(iter_, path)
            if result is not None:
                tree_path = self._store.get_path(result)
                self._tree_view.set_cursor(tree_path, self._tree_view.get_column(0), True)
            return False
        GLib.idle_add(_find_and_edit)

    def _find_iter_for_path(self, iter_, target_path: str):
        """Recursively find the iter with the given path."""
        while iter_:
            if self._store.get_value(iter_, _COL_PATH) == target_path:
                return iter_
            child = self._store.iter_children(iter_)
            if child:
                result = self._find_iter_for_path(child, target_path)
                if result is not None:
                    return result
            iter_ = self._store.iter_next(iter_)
        return None

    def _on_inline_edited(self, _renderer, path_str: str, new_name: str) -> None:
        """Handle completion of inline rename."""
        new_name = new_name.strip()
        if not new_name:
            return
        iter_ = self._store.get_iter_from_string(path_str)
        old_path = self._store.get_value(iter_, _COL_PATH)
        old_name = self._store.get_value(iter_, _COL_NAME)
        is_dir = self._store.get_value(iter_, _COL_IS_DIR)

        # Collect sibling names for validation.
        sibling_names = []
        parent_iter = self._store.iter_parent(iter_)
        if parent_iter:
            child = self._store.iter_children(parent_iter)
            while child:
                if child != iter_:
                    sibling_names.append(self._store.get_value(child, _COL_NAME))
                child = self._store.iter_next(child)

        is_vault_root = is_dir and old_path in self._vault_paths
        target_exists = Path(old_path).parent.joinpath(new_name).exists()

        error = validation.validate_rename(
            new_name=new_name,
            old_name=old_name,
            sibling_names=sibling_names,
            is_vault_root=is_vault_root,
            target_exists=target_exists,
        )
        if error:
            return

        parent_dir = str(Path(old_path).parent)
        new_path = os.path.join(parent_dir, new_name)

        # Perform filesystem rename.
        if hasattr(self, "vault_monitor") and self.vault_monitor:
            self.vault_monitor.skip_next_event(old_path)
            self.vault_monitor.skip_next_event(new_path)
        try:
            os.rename(old_path, new_path)
        except OSError:
            logger.warning("Failed to rename %s → %s", old_path, new_path, exc_info=True)
            return

        # Update the tree store.
        self._store.set_value(iter_, _COL_NAME, new_name)
        self._store.set_value(iter_, _COL_PATH, new_path)
        if is_dir:
            self._store.set_value(iter_, _COL_ICON, FOLDER_ICON)
        else:
            self._store.set_value(iter_, _COL_ICON, FILE_ICON)

        # If directory, recursively update all child paths.
        if is_dir:
            self._update_child_paths(iter_, old_path, new_path)

        # Emit signal so MainWindow can update tabs.
        self.emit("file-renamed", old_path, new_path)

    def _update_child_paths(self, dir_iter, old_base: str, new_base: str) -> None:
        """Recursively update all child paths after a directory rename."""
        child = self._store.iter_children(dir_iter)
        while child:
            child_old_path = self._store.get_value(child, _COL_PATH)
            child_new_path = child_old_path.replace(old_base, new_base, 1)
            self._store.set_value(child, _COL_PATH, child_new_path)
            if self._store.get_value(child, _COL_IS_DIR):
                self._update_child_paths(child, old_base, new_base)
            child = self._store.iter_next(child)

    def _on_inline_editing_canceled(self, _renderer) -> None:
        """Handle canceled inline rename (no-op)."""
        pass

    def _populate_directory(self, path: Path, parent_iter, *, name: str | None = None) -> None:
        """Recursively add *path* and its children to the tree store."""
        display_name = name if name else path.name
        dir_iter = self._store.append(
            parent_iter, [display_name, str(path), True, FOLDER_ICON, ""]
        )
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            logger.warning("Could not list directory: %s", path, exc_info=True)
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

    def _on_drag_prepare(self, _source, x, y):
        """Provide content for a drag operation."""
        path_info = self._tree_view.get_path_at_pos(int(x), int(y))
        if path_info is None:
            return None
        iter_ = self._store.get_iter(path_info[0])
        # Don't drag vault roots.
        if self._store.get_value(iter_, _COL_IS_DIR) and self._store.get_value(iter_, _COL_PATH) in self._vault_paths:
            return None
        path = self._store.get_value(iter_, _COL_PATH)
        return Gdk.ContentProvider.new_for_value(path)

    def _on_drag_begin(self, _source, _drag):
        """Store the source path for the drag operation."""
        pass  # Source path is carried in the content provider value.

    def _on_drop_motion(self, _target, x, y):
        """Highlight the directory being hovered during drag."""
        path_info = self._tree_view.get_path_at_pos(int(x), int(y))
        new_path = None
        if path_info:
            iter_ = self._store.get_iter(path_info[0])
            if self._store.get_value(iter_, _COL_IS_DIR):
                new_path = self._store.get_value(iter_, _COL_PATH)
        if new_path != self._drop_hover_path:
            self._drop_hover_path = new_path
            self._tree_view.queue_draw()
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, _target):
        """Remove drop target highlight."""
        if self._drop_hover_path is not None:
            self._drop_hover_path = None
            self._tree_view.queue_draw()

    def _on_drop(self, _target, value, x, y):
        """Handle a file/folder being dropped onto a directory."""
        self._drop_hover_path = None
        source_path = str(value)

        # Determine the drop target.
        path_info = self._tree_view.get_path_at_pos(int(x), int(y))
        if path_info is None:
            return False
        target_iter = self._store.get_iter(path_info[0])
        if not self._store.get_value(target_iter, _COL_IS_DIR):
            return False  # Can only drop on directories.
        target_dir = self._store.get_value(target_iter, _COL_PATH)

        # Validate the drop operation.
        err = validation.validate_drop(source_path, target_dir, True)
        if err is not None:
            return False

        # Perform the move.
        source_name = Path(source_path).name
        dest_path = os.path.join(target_dir, source_name)

        if hasattr(self, "vault_monitor") and self.vault_monitor:
            self.vault_monitor.skip_next_event(source_path)
            self.vault_monitor.skip_next_event(dest_path)
        try:
            import shutil
            shutil.move(source_path, dest_path)
        except OSError:
            logger.warning("Failed to move %s → %s", source_path, dest_path, exc_info=True)
            return False

        # Emit the rename FIRST so MainWindow updates tabs, indexes and the
        # sidebar.  This must not depend on — or be skipped by — the tree
        # rebuild below (rebuilding synchronously here left the moved file's
        # tab/sidebar pointing at the old path when the rebuild disrupted the
        # handler).
        self.emit("file-renamed", source_path, dest_path)

        # Rebuild the tree store AFTER the drop completes.  Rebuilding it
        # inside the drop handler triggers a GtkCssNode assertion
        # (gtk_css_node_insert_after), so defer it to the main loop.
        GLib.idle_add(self._refresh_after_drop)
        return True

    def _refresh_after_drop(self) -> bool:
        """Deferred tree rebuild after a drag-and-drop move (idle callback)."""
        self.refresh()
        return False  # one-shot

    def _on_row_activated(self, _tree_view, path, _column) -> None:
        """Handle double-click / Enter on a tree row."""
        iter_ = self._store.get_iter(path)
        if self._store.get_value(iter_, _COL_IS_DIR):
            return
        self.emit("file-selected", self._store.get_value(iter_, _COL_PATH))

    def _on_double_press(self, _gesture, n_press: int, x: float, y: float) -> None:
        """Handle press: activate vault root only on double-click."""
        if n_press < 2:
            return
        path_info = self._tree_view.get_path_at_pos(int(x), int(y))
        if path_info is None:
            return
        tree_path = path_info[0]
        iter_ = self._store.get_iter(tree_path)
        if not self._store.get_value(iter_, _COL_IS_DIR):
            return
        if self._store.iter_parent(iter_) is None:
            self.emit("vault-activated", self._store.get_value(iter_, _COL_PATH))

    def _handle_file_created(self, vault_or_parent: str, file_path: str) -> None:
        """Handle a newly created file by adding it to the tree.

        Args:
            vault_or_parent: Vault root or parent directory path
            file_path: Full path of the new file
        """
        self._do_handle_file_created(vault_or_parent, file_path)

    def _do_handle_file_created(self, vault_or_parent: str, file_path: str) -> bool:
        """Add the file or directory node to the tree store."""
        file_name = Path(file_path).name
        parent_path = str(Path(file_path).parent)

        # Check if already in tree (dedup)
        iter_ = self._store.get_iter_first()
        existing = self._find_iter_for_path(iter_, file_path)
        if existing is not None:
            return False

        # Find or create parent directory node
        parent_iter = self._find_or_create_parent(parent_path, vault_or_parent)
        if parent_iter is None:
            return False

        is_dir = os.path.isdir(file_path) if file_path else False
        if is_dir:
            self._store.append(
                parent_iter,
                [file_name, file_path, True, FOLDER_ICON, ""],
            )
        else:
            if not file_path.endswith(".md"):
                return False
            self._store.append(
                parent_iter,
                [file_name, file_path, False, FILE_ICON, "markdown"],
            )
        return False

    def _find_or_create_parent(self, parent_path: str, vault_or_parent: str):
        """Find existing parent iter or create intermediate directory nodes."""
        parts = Path(parent_path).parts

        # Try to find existing parent first
        iter_ = self._store.get_iter_first()
        result = self._find_iter_for_path(iter_, parent_path)
        if result is not None:
            return result

        # Find the vault root that contains this parent_path
        vault_path = None
        for vp in self._vault_paths:
            if parent_path.startswith(vp + os.sep) or parent_path == vp:
                vault_path = vp
                break
        if vault_path is None:
            return None

        vault_parts = Path(vault_path).parts
        anchor_iter = self._find_iter_for_path(self._store.get_iter_first(), vault_path)
        if anchor_iter is None:
            return None

        current_iter = anchor_iter
        current_depth = len(vault_parts)

        # Walk through remaining parts to create intermediate dirs
        for i in range(current_depth, len(parts)):
            dir_name = parts[i]
            dir_path = os.path.join(*parts[:i + 1])

            # Check if child already exists
            child = self._store.iter_children(current_iter)
            found = False
            while child:
                if self._store.get_value(child, _COL_PATH) == dir_path:
                    current_iter = child
                    found = True
                    break
                child = self._store.iter_next(child)

            if not found:
                current_iter = self._store.append(
                    current_iter,
                    [dir_name, dir_path, True, FOLDER_ICON, ""],
                )

        return current_iter

    def _handle_file_deleted(self, file_path: str) -> None:
        """Handle a deleted file by removing it from the tree.

        Args:
            file_path: Full path of the deleted file
        """
        logger.debug("_handle_file_deleted called for %s", file_path)
        self._do_handle_file_deleted(file_path)

    def _do_handle_file_deleted(self, file_path: str) -> bool:
        """Remove the file or directory node from the tree store."""
        iter_ = self._store.get_iter_first()
        to_remove = self._find_iter_for_path(iter_, file_path)
        if to_remove is not None:
            parent = self._store.iter_parent(to_remove)
            logger.debug("_do_handle_file_deleted: removing %s, parent=%s, vault_paths=%s",
                         file_path, parent, self._vault_paths)
            self._store.remove(to_remove)
            # Clean up empty parent directories, but never remove vault roots
            while parent is not None:
                parent_path = self._store.get_value(parent, _COL_PATH)
                logger.debug("_do_handle_file_deleted: checking parent %s (in vault_paths=%s)",
                             parent_path, parent_path in self._vault_paths)
                if parent_path in self._vault_paths:
                    break
                if self._store.iter_n_children(parent) == 0:
                    grandparent = self._store.iter_parent(parent)
                    logger.debug("_do_handle_file_deleted: removing empty parent %s", parent_path)
                    self._store.remove(parent)
                    parent = grandparent
                else:
                    break
        return False

    def _handle_file_moved(self, old_path: str, new_parent: str, new_path: str) -> None:
        """Handle a moved file by updating its path in the tree.

        Args:
            old_path: Previous full path of the file
            new_parent: New parent directory path
            new_path: New full path of the file
        """
        if os.path.isdir(old_path) or os.path.isdir(new_path):
            is_dir = True
        else:
            is_dir = False
        self._do_handle_file_moved(old_path, new_parent, new_path, is_dir)

    def _do_handle_file_moved(self, old_path: str, new_parent: str, new_path: str,
                               is_dir: bool) -> bool:
        """Update the file node path in the tree store."""
        # Find and remove old node
        iter_ = self._store.get_iter_first()
        to_remove = self._find_iter_for_path(iter_, old_path)
        if to_remove is None:
            return False

        # Check new parent exists in tree
        parent_iter = self._find_iter_for_path(self._store.get_iter_first(), new_parent)
        if parent_iter is None:
            return False

        old_iter = to_remove
        file_name = Path(new_path).name

        if is_dir:
            new_iter = self._store.append(parent_iter, [
                file_name, new_path, True, FOLDER_ICON, "",
            ])
        else:
            # Non-markdown files are never shown in the tree (consistent with
            # _do_handle_file_created) — remove the old node, do not re-add.
            if not new_path.endswith(".md"):
                self._store.remove(old_iter)
                return False
            new_iter = self._store.append(parent_iter, [
                file_name, new_path, False, FILE_ICON, "markdown",
            ])

        # Remove the old iter
        self._store.remove(old_iter)
        return False

    def _on_focus_clicked(self, _btn) -> None:
        """Emit focus-current-file so the app can call focus_file()."""
        self.emit("focus-current-file")

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
            logger.debug("Folder chooser cancelled or failed", exc_info=True)
            return
        if folder:
            path = folder.get_path()
            if path and path not in self._vault_paths:
                default_name = Path(path).name
                # A folder name may collide with an existing vault or contain
                # characters that corrupt the wikilink key format (R19.4); both
                # route through the name dialog so the user picks a valid name.
                if (self._name_collision(default_name)
                        or validation.validate_vault_name(default_name)):
                    self._show_add_vault_name_dialog(path, default_name)
                    return
                self._add_vault(path, default_name)

    def _name_collision(self, name: str) -> bool:
        """Check if *name* collides with an existing vault name."""
        for v in config.load_vaults():
            if v["name"] == name:
                return True
        return False

    def _add_vault(self, path: str, name: str) -> None:
        """Add a vault to the tree and persist it."""
        self._vault_paths.append(path)
        self._populate_directory(Path(path), None, name=name)
        try:
            updated = config.add_vault(name, path)
        except OSError as e:
            dialogs.show_error(self.get_root(), "Save Failed", str(e))
            self._vault_paths.remove(path)
            # Remove the row we just added.
            model, iter_ = self._store.get_iter_first()
            while iter_ is not None:
                p = self._store.get_value(iter_, _COL_PATH)
                if p == path:
                    self._store.remove(iter_)
                    break
                iter_ = self._store.iter_next(iter_)
            return
        self.set_vaults(updated)
        self.emit("vault-added", path)

    def _show_add_vault_name_dialog(self, vault_path: str, default_name: str) -> None:
        """Show dialog to resolve a vault name collision."""
        win = self.get_root()
        if win is None:
            return
        dialogs.show_add_vault_name_dialog(
            win, vault_path, default_name, self._do_add_vault_with_name,
        )

    def _do_add_vault_with_name(self, vault_path: str, _default_name: str,
                                 new_name: str, dialog: Adw.Dialog) -> None:
        """Execute adding a vault with a user-specified name."""
        new_name = new_name.strip()
        if (not new_name or validation.validate_vault_name(new_name)
                or self._name_collision(new_name)):
            return
        dialog.close()
        self._add_vault(vault_path, new_name)

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_to_file(self, path: str | Path) -> None:
        """Write the vault tree structure as JSON to *path* (overwrites)."""
        def _walk(iter_):
            nodes = []
            while iter_ is not None:
                name = self._store.get_value(iter_, _COL_NAME)
                fpath = self._store.get_value(iter_, _COL_PATH)
                is_dir = self._store.get_value(iter_, _COL_IS_DIR)
                node = {"name": name, "path": fpath, "is_dir": is_dir}
                if is_dir:
                    children = _walk(self._store.iter_children(iter_))
                    if children:
                        node["children"] = children
                nodes.append(node)
                iter_ = self._store.iter_next(iter_)
            return nodes

        try:
            data = _walk(self._store.get_iter_first())
            Path(path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            logger.warning("Failed to dump VaultTree to %s", path, exc_info=True)
