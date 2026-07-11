"""Markdown Vault — main application window.

Assembles the three-panel layout (vault tree | editor/preview | sidebar),
the tab bar, and the bottom search bar.  Each open file gets its own
``Editor`` and ``Preview`` instance so that buffer state and scroll
position are preserved across tab switches.

Dark mode is controlled via ``Adw.StyleManager`` and exposed through
the hamburger menu.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib

from .vault_tree import VaultTree
from .editor import Editor
from .preview import Preview
from .tabs import TabBar
from .sidebar import Sidebar
from .search import SearchBar
from . import config


# ── Theme helpers ──────────────────────────────────────────────────────


def _apply_theme(color_scheme: int) -> None:
    """Set the application-wide colour scheme.

    Args:
        color_scheme: One of ``Adw.ColorScheme.DEFAULT``,
            ``Adw.ColorScheme.FORCE_LIGHT``, or
            ``Adw.ColorScheme.FORCE_DARK``.
    """
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(color_scheme)


# ── Window ─────────────────────────────────────────────────────────────


class MainWindow(Adw.ApplicationWindow):
    """Top-level application window."""

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Markdown Vault")
        self.set_default_size(1200, 800)

        self._view_mode: str = "edit"

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(root)

        # Header bar with view-switcher and hamburger menu.
        root.append(self._build_header())

        # Main horizontal paned: vault tree | centre content.
        main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        main_paned.set_wide_handle(True)

        self._vault_tree = VaultTree()
        self._vault_tree.connect("file-selected", self._on_file_selected_from_tree)
        main_paned.set_start_child(self._vault_tree)
        main_paned.set_resize_start_child(True)
        main_paned.set_shrink_start_child(False)

        # Centre: tab bar + content stack.
        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._tab_bar = TabBar()
        self._tab_bar.connect("tab-changed", self._on_tab_changed)
        self._tab_bar.connect("tab-closed", self._on_tab_closed)
        centre.append(self._tab_bar)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        centre.append(self._content_stack)

        main_paned.set_end_child(centre)
        main_paned.set_resize_end_child(True)

        # Outer horizontal box: main paned + sidebar.
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.append(main_paned)

        self._sidebar = Sidebar()
        self._sidebar.connect("file-open-requested", self._on_sidebar_file_requested)
        outer.append(self._sidebar)

        root.append(outer)

        # Bottom search bar.
        self._search_bar = SearchBar(get_vault_paths=self._vault_tree.get_vault_paths)
        self._search_bar.connect("file-selected", self._on_search_result_selected)
        root.append(self._search_bar)

        self._register_actions()
        self._load_vaults()

    # ── Header ─────────────────────────────────────────────────────

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        # View-mode toggle buttons (edit / render / split).
        view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        group = None
        for mode, icon, tooltip in (
            ("edit", "document-edit-symbolic", "Edit"),
            ("render", "document-properties-symbolic", "Render"),
            ("split", "view-dual-symbolic", "Split"),
        ):
            btn = Gtk.ToggleButton(icon_name=icon)
            btn.set_tooltip_text(tooltip)
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn._mode = mode  # type: ignore[attr-defined]
            btn.connect("toggled", self._on_view_mode_toggled)
            if mode == "edit":
                btn.set_active(True)
            view_box.append(btn)
        header.set_title_widget(view_box)

        # Hamburger menu.
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()

        # Theme sub-menu.
        theme_section = Gio.Menu()
        theme_section.append("Follow System", "win.theme-system")
        theme_section.append("Light Mode", "win.theme-light")
        theme_section.append("Dark Mode", "win.theme-dark")
        menu.append_section(None, theme_section)

        action_section = Gio.Menu()
        action_section.append("Add Vault", "win.add-vault")
        action_section.append("Toggle Sidebar", "win.toggle-sidebar")
        action_section.append("Full-Text Search", "win.toggle-search")
        menu.append_section(None, action_section)

        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        return header

    # ── Actions & keyboard shortcuts ───────────────────────────────

    def _register_actions(self) -> None:
        # Theme actions.
        for name, scheme in (
            ("theme-system", Adw.ColorScheme.DEFAULT),
            ("theme-light", Adw.ColorScheme.FORCE_LIGHT),
            ("theme-dark", Adw.ColorScheme.FORCE_DARK),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, s=scheme: _apply_theme(s))
            self.add_action(action)

        # Vault management.
        action = Gio.SimpleAction.new("add-vault", None)
        action.connect("activate", lambda *_: self._vault_tree._on_add_vault_clicked(None))
        self.add_action(action)

        # Sidebar / search toggles.
        action = Gio.SimpleAction.new("toggle-sidebar", None)
        action.connect("activate", lambda *_: self._toggle_sidebar())
        self.add_action(action)
        self.set_accels_for_action("win.toggle-sidebar", ["<Control>b"])

        action = Gio.SimpleAction.new("toggle-search", None)
        action.connect("activate", lambda *_: self._toggle_search())
        self.add_action(action)
        self.set_accels_for_action("win.toggle-search", ["<Control>f"])

        # File operations.
        action = Gio.SimpleAction.new("save", None)
        action.connect("activate", lambda *_: self._save_current())
        self.add_action(action)
        self.set_accels_for_action("win.save", ["<Control>s"])

        action = Gio.SimpleAction.new("close-tab", None)
        action.connect("activate", lambda *_: self._close_current_tab())
        self.add_action(action)
        self.set_accels_for_action("win.close-tab", ["<Control>w"])

    # ── Vault loading ──────────────────────────────────────────────

    def _load_vaults(self) -> None:
        vaults = config.load_vaults()
        paths = [v["path"] for v in vaults]
        self._vault_tree.set_vaults(paths)
        self._sidebar.set_vault_paths(paths)

    # ── File opening (creates a new editor per tab) ────────────────

    def _open_file(self, file_path: str) -> None:
        """Open *file_path* in a new or existing tab."""
        existing = self._tab_bar.get_current_tab()
        if existing and existing.file_path == file_path:
            return  # already the active tab

        # Check if this file already has a tab.
        for path in self._tab_bar.get_all_paths():
            if path == file_path:
                self._tab_bar.set_active_tab(file_path)
                return

        # Create dedicated editor + preview for this tab.
        editor = Editor()
        preview = Preview()
        editor.open_file(file_path)

        tab = self._tab_bar.add_tab(file_path, editor, preview)

        # Build a split pane for this tab.
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.set_start_child(editor)
        split.set_end_child(preview)
        split.set_position(600)

        self._content_stack.add_named(split, file_path)
        self._content_stack.set_visible_child_name(file_path)

        self._refresh_preview(tab)
        self._sidebar.update_for_file(file_path, editor.get_text())

    # ── Tab callbacks ──────────────────────────────────────────────

    def _on_file_selected_from_tree(self, _tree, file_path: str) -> None:
        self._open_file(file_path)

    def _on_tab_changed(self, _tab_bar, file_path: str) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        self._content_stack.set_visible_child_name(file_path)
        self._refresh_preview(tab)
        self._sidebar.update_for_file(file_path, tab.editor.get_text())

    def _on_tab_closed(self, _tab_bar, file_path: str) -> None:
        child = self._content_stack.get_child_by_name(file_path)
        if child:
            self._content_stack.remove(child)
        if not self._tab_bar.has_tabs():
            self._sidebar.update_for_file(None)

    def _on_sidebar_file_requested(self, _sidebar, file_path: str) -> None:
        self._open_file(file_path)

    def _on_search_result_selected(self, _search_bar, file_path: str) -> None:
        self._open_file(file_path)

    # ── View mode ──────────────────────────────────────────────────

    def _on_view_mode_toggled(self, toggle_btn: Gtk.ToggleButton) -> None:
        if not toggle_btn.get_active():
            return
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        mode = toggle_btn._mode  # type: ignore[attr-defined]
        tab.view_mode = mode
        if mode == "edit":
            tab.editor.set_visible(True)
            tab.preview.set_visible(False)
        elif mode == "render":
            tab.editor.set_visible(False)
            tab.preview.set_visible(True)
            self._refresh_preview(tab)
        elif mode == "split":
            tab.editor.set_visible(True)
            tab.preview.set_visible(True)
            self._refresh_preview(tab)

    # ── Preview refresh ────────────────────────────────────────────

    def _refresh_preview(self, tab) -> None:
        """Update the preview pane for *tab*."""
        text = tab.editor.get_text()
        base_dir = str(tab.editor.file_path.parent) if tab.editor.file_path else ""
        tab.preview.update_from_text(text, base_dir)
        self._sidebar.update_for_file(tab.editor.file_path, text)

    # ── Misc actions ───────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        self._sidebar.set_visible(not self._sidebar.get_visible())

    def _toggle_search(self) -> None:
        self._search_bar.focus()

    def _save_current(self) -> None:
        tab = self._tab_bar.get_current_tab()
        if tab:
            tab.editor.save()

    def _close_current_tab(self) -> None:
        path = self._tab_bar.get_current_path()
        if path:
            self._tab_bar.close_tab(path)
