"""Markdown Vault — left-panel vault tree browser.

Displays all configured vaults as expandable directory trees, similar
to an IDE project browser.  Only ``.md`` files are shown; hidden
files and directories (prefixed with ``.``) are skipped.

Built on the GTK4 "scalable list" widgets (``Gtk.ListView`` +
``Gtk.TreeListModel`` + ``Gtk.TreeExpander``) rather than the deprecated
``Gtk.TreeView``/``Gtk.TreeStore`` family.  The tree is modelled as a
hierarchy of :class:`VaultNode` objects held in per-directory
``Gio.ListStore`` instances; a ``Gtk.TreeListModel`` exposes that
hierarchy lazily to the view.
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

from markdown_vault.core import attachments
from markdown_vault.core import validation
from markdown_vault.uikit import dialogs
from markdown_vault.core import config
from markdown_vault.markdown import frontmatter

logger = logging.getLogger(__name__)

FILE_ICON = "text-x-generic-symbolic"
FOLDER_ICON = "folder-symbolic"

# OKF reserved files: a folder's overview (index.md) and its update log (log.md)
# get their own icon, and index.md sorts to the top of its folder.
RESERVED_ICONS = {
    "index.md": "emblem-documents-symbolic",
    "log.md": "document-open-recent-symbolic",
}

# Fallback emoji shown for a vault root that has no configured icon.
DEFAULT_VAULT_ICON = "🗄️"

# Row layout metrics (px): one guide column per ancestor level, plus a fixed
# column for the disclosure arrow.
_INDENT_WIDTH = 16
_ARROW_WIDTH = 16


class VaultNode(GObject.Object):
    """A single row in the vault tree — a directory or a ``.md`` file.

    Directories carry a ``children`` :class:`Gio.ListStore` of further
    :class:`VaultNode` objects; files leave it ``None``.
    """

    __gtype_name__ = "MvVaultNode"

    def __init__(self, name: str, path: str, is_dir: bool) -> None:
        super().__init__()
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.icon = (FOLDER_ICON if is_dir
                     else RESERVED_ICONS.get(name.lower(), FILE_ICON))
        # Directories get a child store; files stay None.
        self.children: Gio.ListStore | None = (
            Gio.ListStore(item_type=VaultNode) if is_dir else None
        )


def _reserved_rank(node: VaultNode) -> int:
    """0 for a folder's ``index.md`` (leads the folder), 1 for everything else."""
    return 0 if not node.is_dir and node.name.lower() == "index.md" else 1


def _node_cmp(a: VaultNode, b: VaultNode) -> int:
    """``index.md`` first, then directories, then case-insensitive alphabetical."""
    ra, rb = _reserved_rank(a), _reserved_rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if a.is_dir and not b.is_dir:
        return -1
    if not a.is_dir and b.is_dir:
        return 1
    return GLib.strcmp0(a.name.lower(), b.name.lower())


def _insert_sorted(store: Gio.ListStore, node: VaultNode) -> None:
    """Insert *node* into *store* keeping the ``_node_cmp`` order."""
    lo, hi = 0, store.get_n_items()
    while lo < hi:
        mid = (lo + hi) // 2
        if _node_cmp(store.get_item(mid), node) < 0:
            lo = mid + 1
        else:
            hi = mid
    store.insert(lo, node)


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
        # Import a note from a URL into the given directory (vault root or folder).
        "import-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "close-file-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "file-renamed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "focus-current-file": (GObject.SignalFlags.RUN_LAST, None, ()),
        # The user toggled "hide deprecated" — the window persists it and applies
        # the same filter to the search surfaces.
        "hide-deprecated-changed": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._vault_paths: list[str] = []
        self._vaults: list[dict[str, str]] = []
        self._active_vault: str | None = None
        self._context_path: str | None = None
        self._context_is_dir: bool = False
        self._popover: Gtk.PopoverMenu | None = None
        self._open_file_path: str | None = None
        self._vault_icons: dict[str, str] = {}  # vault path -> emoji
        self._vault_mono: dict[str, bool] = {}  # vault path -> monochrome flag

        # path -> VaultNode for O(1) lookups; label widget bookkeeping so the
        # active-vault highlight and drop-target highlight can be refreshed
        # without a full rebuild.
        self._node_by_path: dict[str, VaultNode] = {}
        self._labels_by_node: dict[VaultNode, Gtk.Label] = {}
        self._drop_hover_path: str | None = None

        # --- Header with title and buttons ---
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

        self._hide_dep_btn = Gtk.ToggleButton(icon_name="view-conceal-symbolic")
        self._hide_dep_btn.add_css_class("flat")
        self._hide_dep_btn.add_css_class("circular")
        self._hide_dep_btn.set_tooltip_text("Hide deprecated notes")
        self._hide_dep_btn.connect("toggled", self._on_hide_deprecated_toggled)
        header.append(self._hide_dep_btn)

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

        # --- Model: roots -> TreeListModel -> SingleSelection ---
        self._roots = Gio.ListStore(item_type=VaultNode)
        self._tree_model = Gtk.TreeListModel.new(
            self._roots,
            False,   # passthrough: rows are GtkTreeListRow, not the item
            False,   # autoexpand
            self._create_child_model,
        )
        # "Hide deprecated" filter sits between the tree model and the selection —
        # a view layer, so toggling it never touches expansion state.
        self._hide_deprecated = False
        self._dep_filter = Gtk.CustomFilter.new(self._filter_visible)
        self._filtered_model = Gtk.FilterListModel.new(self._tree_model,
                                                       self._dep_filter)
        self._selection = Gtk.SingleSelection(model=self._filtered_model)
        self._selection.set_autoselect(False)
        self._selection.set_can_unselect(True)

        # --- Factory: TreeExpander[ Box[ icon, label ] ] ---
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        self._list_view = Gtk.ListView(model=self._selection, factory=factory)
        self._list_view.set_single_click_activate(True)
        self._list_view.add_css_class("vault-tree")
        self._list_view.connect("activate", self._on_row_activated)

        # Double-click on a vault root activates it (open-in-workspace).  Run in
        # the CAPTURE phase so we see the press before ListView's own click
        # handler and can count the double reliably (otherwise it only
        # registers on a third click).
        self._dbl_click = Gtk.GestureClick()
        self._dbl_click.set_button(1)
        self._dbl_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._dbl_click.connect("pressed", self._on_double_press)
        self._list_view.add_controller(self._dbl_click)

        # Right-click on empty space -> minimal context menu.
        self._menu_click = Gtk.GestureClick()
        self._menu_click.set_button(3)
        self._menu_click.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self._menu_click.connect("pressed", self._on_empty_right_click)
        self._list_view.add_controller(self._menu_click)

        # F2 rename / Delete shortcuts.
        self._shortcut_ctrl = Gtk.ShortcutController.new()
        self._shortcut_ctrl.set_scope(Gtk.ShortcutScope.LOCAL)
        self._shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("F2"),
            Gtk.CallbackAction.new(lambda *_: self._start_rename()),
        ))
        self._shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Delete"),
            Gtk.CallbackAction.new(lambda *_: self._on_delete_shortcut()),
        ))
        self._list_view.add_controller(self._shortcut_ctrl)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_child(self._list_view)
        self._scrolled.set_vexpand(True)
        self.append(self._scrolled)

    # ------------------------------------------------------------------
    # Model plumbing
    # ------------------------------------------------------------------

    def _create_child_model(self, item) -> Gio.ListModel | None:
        """TreeListModel create-func: expose a directory's children."""
        node = item if isinstance(item, VaultNode) else item.get_item()
        if node.is_dir and node.children is not None:
            return node.children
        return None

    def _register(self, node: VaultNode) -> None:
        self._node_by_path[node.path] = node

    def _unregister_subtree(self, node: VaultNode) -> None:
        self._node_by_path.pop(node.path, None)
        if node.children is not None:
            for child in list(node.children):
                self._unregister_subtree(child)

    def _iter_all_nodes(self):
        """Depth-first iterator over every node in the tree."""
        def _walk(store):
            for node in store:
                yield node
                if node.children is not None:
                    yield from _walk(node.children)
        yield from _walk(self._roots)

    # ------------------------------------------------------------------
    # Factory callbacks
    # ------------------------------------------------------------------

    def _on_factory_setup(self, _factory, list_item) -> None:
        # Custom indentation (guide columns + our own arrow) instead of the
        # TreeExpander's built-in indent/arrow, so per-level guide lines can be
        # shared between a parent and its children (continuous, not broken).
        expander = Gtk.TreeExpander()
        expander.set_indent_for_depth(False)
        expander.set_hide_expander(True)
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        expander.set_child(row_box)
        list_item.set_child(expander)

        # Per-row gestures / DnD so we never need coordinate hit-testing.
        row_click = Gtk.GestureClick()
        row_click.set_button(3)
        row_click.connect("pressed", self._on_row_right_click, list_item)
        expander.add_controller(row_click)

        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare, list_item)
        expander.add_controller(drag)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._on_drop_motion, list_item)
        drop.connect("leave", self._on_drop_leave, list_item)
        drop.connect("drop", self._on_drop, list_item)
        expander.add_controller(drop)

    @staticmethod
    def _lifecycle_badge(status: str, stale: bool):
        """A trailing pill box for a non-``stable`` lifecycle, or ``None``."""
        pills = []
        if status == "deprecated":
            pills.append(("deprecated", "tree-badge-deprecated"))
        elif status == "draft":
            pills.append(("draft", "tree-badge-draft"))
        if stale:
            pills.append(("stale", "tree-badge-stale"))
        if not pills:
            return None
        box = Gtk.Box(spacing=4)
        box.set_valign(Gtk.Align.CENTER)
        box.add_css_class("tree-badges")
        for text, css in pills:
            pill = Gtk.Label(label=text)
            pill.add_css_class("tree-badge")
            pill.add_css_class(css)
            box.append(pill)
        return box

    def _apply_lifecycle(self, node: "VaultNode", label: Gtk.Label,
                         row_box: Gtk.Box) -> None:
        """Set (or refresh) a row's lifecycle styling and trailing pill from the
        note's frontmatter. Touches only this row's widgets — no model or
        expansion change — so it is safe on a fresh bind and as an in-place
        update."""
        label.remove_css_class("tree-deprecated")
        label.remove_css_class("tree-draft")
        child = row_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if isinstance(child, Gtk.Box) and child.has_css_class("tree-badges"):
                row_box.remove(child)
            child = nxt
        if node.is_dir or not node.name.lower().endswith(".md"):
            return
        status, stale = frontmatter.lifecycle_of(node.path)
        if status == "deprecated":
            label.add_css_class("tree-deprecated")
        elif status == "draft":
            label.add_css_class("tree-draft")
        badge = self._lifecycle_badge(status, stale)
        if badge is not None:
            row_box.append(badge)

    def _apply_internal(self, node: "VaultNode", label: Gtk.Label,
                        row_box: Gtk.Box) -> None:
        """Mark the app-managed attachments tree: dim the row and give a directory
        an ``internal`` pill, so it reads as off-limits (it is also not a drop
        target — see ``validation.validate_drop``)."""
        if not attachments.is_internal(node.path):
            return
        label.add_css_class("tree-internal")
        # The pill marks only the attachments root, not every dimmed subdirectory.
        if node.is_dir and Path(node.path).name == "attachments":
            box = Gtk.Box(spacing=4)
            box.set_valign(Gtk.Align.CENTER)
            box.add_css_class("tree-badges")
            pill = Gtk.Label(label="internal")
            pill.add_css_class("tree-badge")
            pill.add_css_class("tree-badge-internal")
            box.append(pill)
            row_box.append(box)

    def refresh_lifecycle(self, path: str) -> None:
        """Re-read a note's frontmatter and update just its row's lifecycle badge
        in place — called on save so an edited status shows without a re-bind and
        without collapsing the tree. A no-op if the row isn't currently bound
        (off-screen); it re-reads on its next bind anyway."""
        frontmatter.invalidate(path)
        node = self._node_by_path.get(path)
        label = self._labels_by_node.get(node) if node is not None else None
        if label is not None:
            row_box = label.get_parent()
            if row_box is not None:
                self._apply_lifecycle(node, label, row_box)
        if self._hide_deprecated:
            # the note may have (un)become deprecated — re-evaluate the filter so
            # it appears/disappears from the hidden set too.
            self._dep_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _filter_visible(self, row) -> bool:
        """Filter predicate for the "hide deprecated" toggle: with it on, hide
        deprecated leaf notes. Directories and vault roots are never hidden."""
        if not self._hide_deprecated:
            return True
        node = row.get_item()
        if node is None or node.is_dir or not node.name.lower().endswith(".md"):
            return True
        status, _stale = frontmatter.lifecycle_of(node.path)
        return status != "deprecated"

    def _on_hide_deprecated_toggled(self, button: Gtk.ToggleButton) -> None:
        self._hide_deprecated = button.get_active()
        self._dep_filter.changed(Gtk.FilterChange.DIFFERENT)
        self.emit("hide-deprecated-changed", self._hide_deprecated)

    def set_hide_deprecated(self, active: bool) -> None:
        """Apply the shared 'hide deprecated' state (restored from settings) —
        updates the toggle and filter WITHOUT re-emitting the change signal."""
        active = bool(active)
        self._hide_deprecated = active
        self._hide_dep_btn.handler_block_by_func(self._on_hide_deprecated_toggled)
        self._hide_dep_btn.set_active(active)
        self._hide_dep_btn.handler_unblock_by_func(self._on_hide_deprecated_toggled)
        self._dep_filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_factory_bind(self, _factory, list_item) -> None:
        row = list_item.get_item()          # GtkTreeListRow
        node = row.get_item()               # VaultNode
        expander = list_item.get_child()
        row_box = expander.get_child()
        expander.set_list_row(row)

        # Rebuild the row content (guide columns depend on depth).
        child = row_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            row_box.remove(child)
            child = nxt

        # One guide column per ancestor level; the line sits on the column's
        # right edge so a parent's own column and its children's matching column
        # line up into one continuous vertical rule.
        for _ in range(row.get_depth()):
            cell = Gtk.Box()
            cell.set_size_request(_INDENT_WIDTH, -1)
            cell.add_css_class("tree-guide-cell")
            row_box.append(cell)

        arrow = Gtk.Image()
        arrow.set_size_request(_ARROW_WIDTH, -1)
        arrow.set_halign(Gtk.Align.CENTER)
        # Non-empty directories get a chevron; empty folders (and files) keep the
        # column as a blank spacer.  Vault roots always keep their chevron.
        has_children = (
            node.is_dir and node.children is not None
            and node.children.get_n_items() > 0
        )
        if has_children or self._is_vault_root(node):
            arrow.add_css_class("tree-arrow")
            arrow.set_from_icon_name(
                "pan-down-symbolic" if row.get_expanded() else "pan-end-symbolic"
            )
            hid = row.connect("notify::expanded", self._on_row_expanded_changed, arrow)
            list_item._mv_expand = (row, hid)
            # Clicking the arrow toggles expansion for every directory — the only
            # way to collapse a vault root (whose row-body click is reserved for
            # double-click "open vault").  Claim the click so the ListView's
            # single-click activation doesn't also fire and cancel the toggle.
            arrow_click = Gtk.GestureClick()
            arrow_click.set_button(1)
            arrow_click.connect("pressed", self._on_arrow_pressed, row)
            arrow.add_controller(arrow_click)
        else:
            list_item._mv_expand = None
        row_box.append(arrow)

        if self._is_vault_root(node):
            icon = Gtk.Label(label=self._vault_icons.get(node.path, DEFAULT_VAULT_ICON))
            icon.set_size_request(_INDENT_WIDTH + 4, -1)
            icon.add_css_class("vault-emoji")
            if self._vault_mono.get(node.path):
                icon.add_css_class("vault-emoji-mono")
        else:
            icon = Gtk.Image.new_from_icon_name(node.icon)
            icon.add_css_class("tree-folder-icon" if node.is_dir else "tree-file-icon")
        row_box.append(icon)

        # Files show their stem — the ".md" extension is pure noise here.
        name = node.name
        if not node.is_dir and name.lower().endswith(".md"):
            name = name[:-3]
        label = Gtk.Label(xalign=0, label=name)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        # Show the full name on hover, but only when it is actually truncated.
        label.set_has_tooltip(True)
        label.connect("query-tooltip", self._on_label_query_tooltip, name)
        label.set_hexpand(True)  # push a trailing kebab (future) to the right
        label.add_css_class("tree-label")
        label.add_css_class("tree-folder" if node.is_dir else "tree-file")
        row_box.append(label)

        # OKF lifecycle: deprecated notes are struck through and dimmed, drafts
        # dimmed, and a trailing pill names the state. 'stable' (the default) is
        # unmarked. Read lazily per visible row and cached.
        self._apply_lifecycle(node, label, row_box)
        self._apply_internal(node, label, row_box)

        # Vault roots get a full-width background bar (section-header look); the
        # trailing space is reserved for a future kebab menu.
        if self._is_vault_root(node):
            expander.add_css_class("vault-root-row")
        else:
            expander.remove_css_class("vault-root-row")

        self._labels_by_node[node] = label
        self._apply_root_style(node, label)
        self._apply_open_style(node, expander)
        self._apply_drop_style(node, expander)

    def _on_label_query_tooltip(self, label, _x, _y, _keyboard, tooltip, full_name) -> bool:
        """Only show the name tooltip when the label is actually ellipsized."""
        layout = label.get_layout()
        if layout is not None and layout.is_ellipsized():
            tooltip.set_text(full_name)
            return True
        return False

    def _on_row_expanded_changed(self, row, _pspec, arrow) -> None:
        arrow.set_from_icon_name(
            "pan-down-symbolic" if row.get_expanded() else "pan-end-symbolic"
        )

    def _on_arrow_pressed(self, gesture, _n, _x, _y, row) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        row.set_expanded(not row.get_expanded())

    def _on_factory_unbind(self, _factory, list_item) -> None:
        tup = getattr(list_item, "_mv_expand", None)
        if tup is not None:
            expand_row, hid = tup
            expand_row.disconnect(hid)
            list_item._mv_expand = None
        row = list_item.get_item()
        if row is not None:
            self._labels_by_node.pop(row.get_item(), None)

    def _is_vault_root(self, node: VaultNode) -> bool:
        return node.is_dir and node.path in self._vault_paths

    def _apply_root_style(self, node: VaultNode, label: Gtk.Label) -> None:
        if self._is_vault_root(node) and node.path == self._active_vault:
            label.add_css_class("vault-root-active")
        else:
            label.remove_css_class("vault-root-active")

    def _apply_open_style(self, node: VaultNode, expander: Gtk.TreeExpander) -> None:
        if not node.is_dir and node.path == self._open_file_path:
            expander.add_css_class("open-file")
        else:
            expander.remove_css_class("open-file")

    def _apply_drop_style(self, node: VaultNode, expander: Gtk.TreeExpander) -> None:
        if node.is_dir and node.path == self._drop_hover_path:
            expander.add_css_class("drop-target")
        else:
            expander.remove_css_class("drop-target")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_vaults(self, vaults: list[dict[str, str]]) -> None:
        """Replace the entire tree with the given vault directories."""
        self._vault_paths = [v["path"] for v in vaults]
        self._vaults = list(vaults)
        self._vault_icons = {
            v["path"]: (v.get("icon") or DEFAULT_VAULT_ICON) for v in vaults
        }
        self._vault_mono = {v["path"]: bool(v.get("mono")) for v in vaults}
        self._roots.remove_all()
        self._node_by_path.clear()
        self._labels_by_node.clear()
        for v in vaults:
            self._populate_directory(Path(v["path"]), self._roots, name=v["name"])

    def get_vault_paths(self) -> list[str]:
        """Return the list of currently loaded vault root paths."""
        return list(self._vault_paths)

    def get_selected_path(self) -> str | None:
        """Return the path of the currently selected row, or ``None``."""
        row = self._selection.get_selected_item()
        if row is None:
            return None
        return row.get_item().path

    def set_active_vault(self, vault_path: str | None) -> None:
        """Set the active vault root and update visual highlighting."""
        self._active_vault = vault_path
        for node, label in self._labels_by_node.items():
            self._apply_root_style(node, label)

    def set_open_file(self, file_path: str | None) -> None:
        """Persistently highlight the row of the currently open *file_path*."""
        self._open_file_path = file_path or None
        for node, label in self._labels_by_node.items():
            box = label.get_parent()
            expander = box.get_parent() if box is not None else None
            if isinstance(expander, Gtk.TreeExpander):
                self._apply_open_style(node, expander)

    def focus_file(self, file_path: str) -> None:
        """Select and scroll to *file_path* in the tree, expanding parents."""
        node = self._node_by_path.get(file_path)
        if node is None:
            return
        self._expand_ancestors(file_path)
        pos = self._position_for_path(file_path)
        if pos is None:
            return
        self._selection.set_selected(pos)
        self._list_view.scroll_to(pos, Gtk.ListScrollFlags.NONE, None)

    def refresh(self) -> None:
        """Rebuild the tree from the current vault paths, preserving expansion."""
        expanded = self.get_expanded_paths()
        self.set_vaults(self._vaults)
        if expanded:
            self.expand_paths(expanded)

    def get_expanded_paths(self) -> list[str]:
        """Return all currently expanded directory paths."""
        expanded: list[str] = []
        for pos in range(self._tree_model.get_n_items()):
            row = self._tree_model.get_row(pos)
            if row is not None and row.get_expanded():
                node = row.get_item()
                if node.is_dir:
                    expanded.append(node.path)
        return expanded

    def expand_paths(self, paths: list[str]) -> None:
        """Expand the directories listed in *paths*."""
        wanted = set(paths)
        # Expanding a parent reveals new rows, so loop until no change.
        changed = True
        while changed:
            changed = False
            for pos in range(self._tree_model.get_n_items()):
                row = self._tree_model.get_row(pos)
                if row is None:
                    continue
                node = row.get_item()
                if node.path in wanted and not row.get_expanded():
                    row.set_expanded(True)
                    changed = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _position_for_path(self, path: str) -> int | None:
        """Row position of *path* in the (expanded) view, or None."""
        for pos in range(self._tree_model.get_n_items()):
            row = self._tree_model.get_row(pos)
            if row is not None and row.get_item().path == path:
                return pos
        return None

    def _expand_ancestors(self, path: str) -> None:
        """Expand every ancestor directory of *path* so its row is visible."""
        ancestors: set[str] = set()
        for vault in self._vault_paths:
            if path == vault or path.startswith(vault + os.sep):
                cur = str(Path(path).parent)
                while True:
                    ancestors.add(cur)
                    if cur == vault or len(cur) <= len(vault):
                        break
                    cur = str(Path(cur).parent)
                break
        if ancestors:
            self.expand_paths(list(ancestors))

    def _populate_directory(self, path: Path, parent_store: Gio.ListStore,
                            *, name: str | None = None) -> VaultNode:
        """Recursively add *path* and its children under *parent_store*."""
        display_name = name if name else path.name
        node = VaultNode(display_name, str(path), True)
        _insert_sorted(parent_store, node)
        self._register(node)
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            logger.warning("Could not list directory: %s", path, exc_info=True)
            return node
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                self._populate_directory(entry, node.children)
            elif entry.suffix.lower() == ".md" or attachments.is_internal(entry):
                # Non-.md files stay hidden everywhere except the attachments tree,
                # where images are shown (dimmed) so they are visible, not lost.
                child = VaultNode(entry.name, str(entry), False)
                _insert_sorted(node.children, child)
                self._register(child)
        return node

    # ------------------------------------------------------------------
    # Activation / selection
    # ------------------------------------------------------------------

    def _on_row_activated(self, _list_view, position: int) -> None:
        """Single-click / Enter: open files, toggle directory expansion."""
        row = self._tree_model.get_row(position)
        if row is None:
            return
        node = row.get_item()
        if node.is_dir:
            # Any directory — including a vault root (its bar) — toggles on a
            # single click.  Double-click still opens the vault: the double-click
            # gesture claims the second press, so only the first click's toggle
            # runs alongside the open.
            row.set_expanded(not row.get_expanded())
            return
        if not node.name.lower().endswith(".md"):
            return          # attachments images are view-only, not openable notes
        self.emit("file-selected", node.path)

    def _on_double_press(self, gesture, n_press: int, x: float, y: float) -> None:
        """Double-click on a vault root activates it."""
        if n_press != 2:
            return
        node = self._node_at(x, y)
        if node is None or not node.is_dir or node.path not in self._vault_paths:
            return
        # Claim the sequence so ListView doesn't also fire its single-click
        # activation (which would toggle the root's expansion under us).
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.emit("vault-activated", node.path)

    def _node_at(self, x: float, y: float) -> VaultNode | None:
        """Return the VaultNode of the row under the (x, y) list coordinate."""
        widget = self._list_view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self._list_view:
            expander = self._expander_of(widget)
            if expander is not None:
                lrow = expander.get_list_row()
                if lrow is not None:
                    return lrow.get_item()
            widget = widget.get_parent()
        return None

    @staticmethod
    def _expander_of(widget) -> Gtk.TreeExpander | None:
        cur = widget
        while cur is not None:
            if isinstance(cur, Gtk.TreeExpander):
                return cur
            cur = cur.get_parent()
        return None

    def _on_delete_shortcut(self) -> None:
        """Handle Delete key: emit delete-requested for the selected item."""
        path = self.get_selected_path()
        if not path:
            return
        if path in self._vault_paths:
            return
        self.emit("delete-requested", path)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_row_right_click(self, _gesture, _n: int, x: float, y: float,
                            list_item) -> None:
        """Right-click on a row: open its context menu."""
        row = list_item.get_item()
        if row is None:
            return
        node = row.get_item()
        self._context_path = node.path
        self._context_is_dir = node.is_dir
        expander = list_item.get_child()
        # Translate the row-local coordinate into the scrolled-window space the
        # popover is parented in.  translate_coordinates returns (x, y) here
        # (older bindings prepend a success bool), so unpack defensively.
        res = expander.translate_coordinates(self._scrolled, x, y)
        if not res:
            px, py = x, y
        elif len(res) == 3:
            _ok, px, py = res
        else:
            px, py = res
        self._show_context_menu(int(px), int(py))

    def _on_empty_right_click(self, _gesture, _n: int, x: float, y: float) -> None:
        """Right-click on empty space -> minimal menu (New File/Folder)."""
        if self._node_at(x, y) is not None:
            return  # handled by the per-row gesture
        self._context_path = None
        self._context_is_dir = False
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

        # The attachments tree is app-managed: no manual create/import into it.
        if parent_dir and not attachments.is_internal(parent_dir):
            menu.append("New File", "ctx.new-file")
            menu.append("New Folder", "ctx.new-folder")
            menu.append("Import…", "ctx.import")

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

        # Build action group.
        action_group = Gio.SimpleActionGroup()

        if parent_dir:
            action = Gio.SimpleAction.new("new-file", None)
            action.connect("activate", lambda *_: self.emit("new-file-requested", parent_dir))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("new-folder", None)
            action.connect("activate", lambda *_: self.emit("new-folder-requested", parent_dir))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("import", None)
            action.connect("activate", lambda *_: self.emit("import-requested", parent_dir))
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
            menu.append("Change Icon…", "ctx.vault-icon")
            menu.append("Rename Vault", "ctx.rename-vault")
            menu.append("Remove Vault", "ctx.remove-vault")

            vault_name = self._context_path
            for v in self._vaults:
                if v["path"] == self._context_path:
                    vault_name = v["name"]
                    break

            ctx_path = self._context_path
            ctx_vault_name = vault_name
            action = Gio.SimpleAction.new("vault-icon", None)
            action.connect("activate", lambda *_: self._show_vault_icon_dialog(ctx_path))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("rename-vault", None)
            action.connect("activate", lambda *_: self._show_rename_dialog(ctx_path, ctx_vault_name))
            action_group.add_action(action)

            action = Gio.SimpleAction.new("remove-vault", None)
            action.connect("activate", lambda *_: self._show_remove_dialog(ctx_path, ctx_vault_name))
            action_group.add_action(action)

        if menu.get_n_items() == 0:
            return

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
            self._scrolled.insert_action_group("ctx", None)
            return False
        GLib.timeout_add(50, _cleanup)

    def _show_vault_icon_dialog(self, vault_path: str) -> None:
        """Show the icon picker for a vault."""
        win = self.get_root()
        if win is None:
            return
        current = self._vault_icons.get(vault_path, DEFAULT_VAULT_ICON)
        current_mono = self._vault_mono.get(vault_path, False)
        dialogs.show_vault_icon_dialog(
            win, current, current_mono,
            lambda icon, mono: self._apply_vault_icon(vault_path, icon, mono),
        )

    def _apply_vault_icon(self, vault_path: str, icon: str | None, mono: bool) -> None:
        """Persist and apply a vault's icon + monochrome flag, keeping expansion."""
        config.set_vault_icon(vault_path, icon, mono)
        for v in self._vaults:
            if v["path"] == vault_path:
                if icon:
                    v["icon"] = icon
                else:
                    v.pop("icon", None)
                if mono:
                    v["mono"] = True
                else:
                    v.pop("mono", None)
        self._set_vaults_keep_expansion(self._vaults)

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
        self._set_vaults_keep_expansion(updated)
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
        self._set_vaults_keep_expansion(updated)
        self.emit("vault-removed", vault_path)

    def _set_vaults_keep_expansion(self, vaults: list[dict]) -> None:
        """Reload the tree from *vaults* without losing which folders are open."""
        expanded = self.get_expanded_paths()
        self.set_vaults(vaults)
        self.expand_paths(expanded)

    def _is_open_file(self, file_path: str) -> bool:
        """Check if *file_path* is currently open in a tab."""
        from markdown_vault.editor.tabs import TabBar
        win = self.get_root()
        if win is None:
            return False
        tab_bar = getattr(win, "_tab_bar", None)
        if isinstance(tab_bar, TabBar):
            return file_path in tab_bar.get_all_paths()
        return False

    # ------------------------------------------------------------------
    # Inline rename
    # ------------------------------------------------------------------

    def _start_rename(self) -> None:
        """Rename the currently selected row."""
        path = self.get_selected_path()
        if path:
            self._start_rename_for_path(path)

    def _start_rename_for_path(self, path: str) -> None:
        """Prompt for a new name for *path* and apply the rename."""
        node = self._node_by_path.get(path)
        if node is None or self._is_vault_root(node):
            return
        win = self.get_root()
        if win is None:
            return
        dialogs.prompt_rename(
            win, node.name,
            lambda new_name: self._commit_rename(node, new_name) if new_name else None,
        )

    def _commit_rename(self, node: VaultNode, new_name: str) -> None:
        """Validate, perform the filesystem rename and update the model."""
        old_path = node.path
        parent = self._parent_store_of(node)
        sibling_names = [c.name for c in parent] if parent is not None else []
        if node.name in sibling_names:
            sibling_names.remove(node.name)

        is_vault_root = self._is_vault_root(node)
        target_exists = Path(old_path).parent.joinpath(new_name).exists()
        error = validation.validate_rename(
            new_name=new_name,
            old_name=node.name,
            sibling_names=sibling_names,
            is_vault_root=is_vault_root,
            target_exists=target_exists,
        )
        if error:
            return

        parent_dir = str(Path(old_path).parent)
        new_path = os.path.join(parent_dir, new_name)

        if getattr(self, "vault_monitor", None):
            self.vault_monitor.skip_next_event(old_path)
            self.vault_monitor.skip_next_event(new_path)
        try:
            os.rename(old_path, new_path)
        except OSError:
            logger.warning("Failed to rename %s → %s", old_path, new_path, exc_info=True)
            return

        self._rename_node(node, new_name, new_path)
        # Re-sorting removed and re-inserted the item, clearing the selection;
        # restore it so a subsequent F2 still has a selected row to act on.
        self.focus_file(new_path)
        self.emit("file-renamed", old_path, new_path)

    def _parent_store_of(self, node: VaultNode) -> Gio.ListStore | None:
        """Return the child store holding *node* (its parent's), or roots."""
        parent_dir = str(Path(node.path).parent)
        parent = self._node_by_path.get(parent_dir)
        if parent is not None and parent.children is not None:
            return parent.children
        if node.path in self._vault_paths:
            return self._roots
        return None

    def _rename_node(self, node: VaultNode, new_name: str, new_path: str) -> None:
        """Update *node* (and any descendants) after a rename/move on disk."""
        store = self._parent_store_of(node)
        old_path = node.path
        self._unregister_subtree(node)
        node.name = new_name
        node.path = new_path
        node.icon = FOLDER_ICON if node.is_dir else FILE_ICON
        if node.is_dir:
            self._rebase_children(node, old_path, new_path)
        self._reregister_subtree(node)
        # Re-sort within the parent store.
        if store is not None:
            found = store.find(node)
            if found[0]:
                store.remove(found[1])
                _insert_sorted(store, node)

    def _rebase_children(self, node: VaultNode, old_base: str, new_base: str) -> None:
        if node.children is None:
            return
        for child in node.children:
            child.path = child.path.replace(old_base, new_base, 1)
            if child.is_dir:
                self._rebase_children(child, old_base, new_base)

    def _reregister_subtree(self, node: VaultNode) -> None:
        self._register(node)
        if node.children is not None:
            for child in node.children:
                self._reregister_subtree(child)

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def _on_drag_prepare(self, _source, x, y, list_item):
        row = list_item.get_item()
        if row is None:
            return None
        node = row.get_item()
        if self._is_vault_root(node) or attachments.is_internal(node.path):
            return None  # don't drag vault roots or app-managed attachments
        return Gdk.ContentProvider.new_for_value(node.path)

    def _on_drop_motion(self, _target, _x, _y, list_item):
        row = list_item.get_item()
        new_path = None
        internal = False
        if row is not None:
            node = row.get_item()
            if node.is_dir:
                if attachments.is_internal(node.path):
                    internal = True     # app-managed: no highlight, no drop
                else:
                    new_path = node.path
        if new_path != self._drop_hover_path:
            self._drop_hover_path = new_path
            self._refresh_drop_highlight()
        return Gdk.DragAction(0) if internal else Gdk.DragAction.MOVE

    def _on_drop_leave(self, _target, list_item):
        if self._drop_hover_path is not None:
            self._drop_hover_path = None
            self._refresh_drop_highlight()

    def _refresh_drop_highlight(self) -> None:
        for node, label in self._labels_by_node.items():
            box = label.get_parent()
            expander = box.get_parent() if box is not None else None
            if isinstance(expander, Gtk.TreeExpander):
                self._apply_drop_style(node, expander)

    def _on_drop(self, _target, value, _x, _y, list_item):
        row = list_item.get_item()
        if row is None:
            return False
        return self._perform_drop(str(value), row.get_item())

    def _perform_drop(self, source_path: str, target_node: VaultNode) -> bool:
        """Move *source_path* into *target_node* (a directory) and refresh."""
        self._drop_hover_path = None
        if target_node is None or not target_node.is_dir:
            return False
        target_dir = target_node.path

        err = validation.validate_drop(source_path, target_dir, True)
        if err is not None:
            return False

        source_name = Path(source_path).name
        dest_path = os.path.join(target_dir, source_name)

        if getattr(self, "vault_monitor", None):
            self.vault_monitor.skip_next_event(source_path)
            self.vault_monitor.skip_next_event(dest_path)
        try:
            import shutil
            shutil.move(source_path, dest_path)
        except OSError:
            logger.warning("Failed to move %s → %s", source_path, dest_path, exc_info=True)
            return False

        # Emit rename FIRST so MainWindow repoints tab/index/sidebar, then defer
        # the tree rebuild to the main loop (a synchronous rebuild inside the
        # drop handler is fragile).
        self.emit("file-renamed", source_path, dest_path)
        GLib.idle_add(self._refresh_after_drop)
        return True

    def _refresh_after_drop(self) -> bool:
        """Deferred tree rebuild after a drag-and-drop move (idle callback)."""
        self.refresh()
        return False  # one-shot

    # ------------------------------------------------------------------
    # Incremental updates from the file-system monitor
    # ------------------------------------------------------------------

    def _handle_file_created(self, vault_or_parent: str, file_path: str) -> None:
        """Handle a newly created file/dir by adding it to the tree."""
        if file_path in self._node_by_path:
            return  # dedup

        parent_path = str(Path(file_path).parent)
        parent = self._find_or_create_parent(parent_path, vault_or_parent)
        if parent is None or parent.children is None:
            return

        is_dir = os.path.isdir(file_path) if file_path else False
        if is_dir:
            node = VaultNode(Path(file_path).name, file_path, True)
        else:
            if not file_path.endswith(".md"):
                return
            node = VaultNode(Path(file_path).name, file_path, False)
        _insert_sorted(parent.children, node)
        self._register(node)

    def _find_or_create_parent(self, parent_path: str, vault_or_parent: str) -> VaultNode | None:
        """Find existing parent node or create intermediate directory nodes."""
        existing = self._node_by_path.get(parent_path)
        if existing is not None:
            return existing

        vault_path = None
        for vp in self._vault_paths:
            if parent_path.startswith(vp + os.sep) or parent_path == vp:
                vault_path = vp
                break
        if vault_path is None:
            return None

        anchor = self._node_by_path.get(vault_path)
        if anchor is None:
            return None

        parts = Path(parent_path).parts
        vault_depth = len(Path(vault_path).parts)
        current = anchor
        for i in range(vault_depth, len(parts)):
            dir_path = os.path.join(*parts[: i + 1])
            child = self._node_by_path.get(dir_path)
            if child is None or child.children is None:
                child = VaultNode(parts[i], dir_path, True)
                _insert_sorted(current.children, child)
                self._register(child)
            current = child
        return current

    def _handle_file_deleted(self, file_path: str) -> None:
        """Handle a deleted file/dir by removing it from the tree."""
        node = self._node_by_path.get(file_path)
        if node is None:
            return
        store = self._parent_store_of(node)
        self._remove_node_from_store(node, store)

        # Clean up empty parent directories, but never remove vault roots.
        parent_path = str(Path(file_path).parent)
        while parent_path and parent_path not in self._vault_paths:
            parent = self._node_by_path.get(parent_path)
            if parent is None or parent.children is None:
                break
            if parent.children.get_n_items() == 0:
                grandparent_path = str(Path(parent_path).parent)
                self._remove_node_from_store(parent, self._parent_store_of(parent))
                parent_path = grandparent_path
            else:
                break

    def _remove_node_from_store(self, node: VaultNode, store: Gio.ListStore | None) -> None:
        self._unregister_subtree(node)
        if store is not None:
            found = store.find(node)
            if found[0]:
                store.remove(found[1])

    def _handle_file_moved(self, old_path: str, new_parent: str, new_path: str) -> None:
        """Handle a moved file/dir by relocating its node in the tree."""
        node = self._node_by_path.get(old_path)
        if node is None:
            return
        parent = self._node_by_path.get(new_parent)
        if parent is None or parent.children is None:
            return

        old_store = self._parent_store_of(node)
        # A rename to a non-.md name drops the node entirely (files only).
        if not node.is_dir and not new_path.endswith(".md"):
            self._remove_node_from_store(node, old_store)
            return

        # Detach, rebase paths, re-attach under the new parent.
        self._remove_node_from_store(node, old_store)
        node.name = Path(new_path).name
        old_base = node.path
        node.path = new_path
        node.icon = FOLDER_ICON if node.is_dir else FILE_ICON
        if node.is_dir:
            self._rebase_children(node, old_base, new_path)
        self._reregister_subtree(node)
        _insert_sorted(parent.children, node)

    # ------------------------------------------------------------------
    # Add vault
    # ------------------------------------------------------------------

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
        except GLib.Error as exc:
            # A cancel and a real portal/backend failure raise the same type; stay
            # silent on cancel, surface a genuine failure instead of dropping it.
            if not dialogs.dialog_cancelled(exc):
                logger.warning("vault-folder chooser failed", exc_info=True)
                dialogs.show_error(self.get_root(), "Folder Selection Failed",
                                   "Could not open the folder chooser.")
            return
        if folder:
            path = folder.get_path()
            if path and path not in self._vault_paths:
                default_name = Path(path).name
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
        try:
            updated = config.add_vault(name, path)
        except OSError as e:
            logger.warning("could not save the new vault %s", path, exc_info=True)
            dialogs.show_error(self.get_root(), "Save Failed", str(e))
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
        def _walk(store):
            nodes = []
            for node in store:
                entry = {"name": node.name, "path": node.path, "is_dir": node.is_dir}
                if node.is_dir and node.children is not None:
                    children = _walk(node.children)
                    if children:
                        entry["children"] = children
                nodes.append(entry)
            return nodes

        try:
            data = _walk(self._roots)
            Path(path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            logger.warning("Failed to dump VaultTree to %s", path, exc_info=True)
