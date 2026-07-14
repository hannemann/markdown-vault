"""Markdown Vault — main application window.

Assembles the three-panel layout (vault tree | editor/preview | sidebar),
the tab bar, and the bottom search bar.  Each open file gets its own
``Editor`` and ``Preview`` instance so that buffer state and scroll
position are preserved across tab switches.

Dark mode is controlled via ``Adw.StyleManager`` and exposed through
the hamburger menu.
"""

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from .vault_tree import VaultTree
from .editor import Editor
from .preview import Preview
from .tabs import TabBar
from .sidebar import Sidebar
from .search import SearchBar
from .preferences import PreferencesDialog
from . import config
from . import session
from . import mru


def _load_gtk_css() -> None:
    """Load GTK CSS for tab bar and other widgets."""
    css_provider = Gtk.CssProvider()
    css_path = Path(__file__).parent.parent / "data" / "css" / "gtk.css"
    if css_path.exists():
        css_provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def _apply_theme(color_scheme: int) -> None:
    """Set the application-wide colour scheme."""
    Adw.StyleManager.get_default().set_color_scheme(color_scheme)


def _make_theme_handler(scheme: int):
    """Return a callback that applies *scheme*."""
    def _handler(_action, _param):
        _apply_theme(scheme)
    return _handler


_ZOOM_STEP = 0.1
_ZOOM_MIN = 0.25
_ZOOM_MAX = 5.0


class MainWindow(Adw.ApplicationWindow):
    """Top-level application window."""

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Markdown Vault")

        _load_gtk_css()

        self._view_mode: str = "edit"
        self._setup_complete = False
        self._autosave_id: int | None = None
        self._view_toggle_buttons: dict[str, Gtk.ToggleButton] = {}
        self._active_vault: str | None = None
        self._settings = config.load_settings()

        # MRU tab manager.
        self.mru = mru.MRUManager()

        # Navigation history (browser-style back/forward).
        self._nav_history: list[str] = []
        self._nav_pos: int = -1
        self._suppress_history: bool = False

        # Load session for window geometry.
        _ses = session.load_session()
        w = _ses["window"]
        self.set_default_size(w.get("width", 1200), w.get("height", 800))

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(root)

        root.append(self._build_header())

        main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        main_paned.set_wide_handle(True)

        self._vault_tree = VaultTree()
        self._vault_tree.connect("file-selected", self._on_file_selected_from_tree)
        self._vault_tree.connect("vault-activated", self._on_vault_activated)
        self._vault_tree.connect("vault-added", self._on_vault_added)
        self._vault_tree.connect("new-file-requested", self._on_new_file_requested)
        self._vault_tree.connect("new-folder-requested", self._on_new_folder_requested)
        self._vault_tree.connect("delete-requested", self._on_delete_requested)
        self._vault_tree.connect("close-file-requested", self._on_close_file_requested)
        self._vault_tree.connect("file-renamed", self._on_file_renamed)
        main_paned.set_start_child(self._vault_tree)
        main_paned.set_resize_start_child(True)
        main_paned.set_shrink_start_child(False)

        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._tab_bar = TabBar()
        self._tab_bar.connect("tab-changed", self._on_tab_changed)
        self._tab_bar.connect("tab-closed", self._on_tab_closed)
        self._tab_bar.connect("tab-renamed", self._on_tab_renamed)
        centre.append(self._tab_bar)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_vexpand(True)

        # Welcome placeholder shown when no file is open.
        self._welcome = self._build_welcome()
        self._content_stack.add_named(self._welcome, "__welcome__")
        self._content_stack.set_visible_child_name("__welcome__")
        centre.append(self._content_stack)

        main_paned.set_end_child(centre)
        main_paned.set_resize_end_child(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.append(main_paned)

        self._sidebar = Sidebar()
        self._sidebar.connect("file-open-requested", self._on_sidebar_file_requested)
        self._sidebar.connect("outline-clicked", self._on_outline_clicked)
        outer.append(self._sidebar)

        self._search_bar = SearchBar(get_vault_paths=self._vault_tree.get_vault_paths)
        self._search_bar.connect("file-selected", self._on_search_result_selected)
        self._search_bar.connect("close-requested", self._on_search_close_requested)

        self._search_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._search_paned.set_wide_handle(True)
        self._search_paned.set_start_child(outer)
        self._search_paned.set_resize_start_child(True)
        self._search_paned.set_shrink_start_child(False)
        self._search_paned.set_end_child(self._search_bar)
        self._search_paned.set_resize_end_child(False)
        self._search_paned.set_shrink_end_child(True)

        root.append(self._search_paned)

        self._register_actions()
        self._load_vaults()

        # Restore session: sidebar, search, tabs, active tab, expanded vaults.
        sidebar_visible = _ses.get("sidebar_visible", False)
        self._sidebar.set_visible(sidebar_visible)
        self._sidebar_toggle.set_active(sidebar_visible)
        search_pos = _ses.get("search_paned_position", 0)
        if search_pos > 0:
            self._search_paned.set_position(search_pos)
        if _ses.get("search_visible", False):
            self._search_bar.set_visible(True)
            self._search_toggle.set_active(True)

        # Determine active vault and restore its session.
        self._active_vault = _ses.get("active_vault")
        if self._active_vault and self._active_vault not in self._vault_tree.get_vault_paths():
            self._active_vault = None
        if not self._active_vault:
            vaults = self._vault_tree.get_vault_paths()
            if vaults:
                self._active_vault = vaults[0]

        self._vault_tree.set_active_vault(self._active_vault)

        # Restore tabs for the active vault.
        self._suppress_history = True
        if self._active_vault:
            vault_data = _ses.get("vault_sessions", {}).get(self._active_vault, {})
            vault_data = session.prune_vault_session(vault_data)
            for tab_data in vault_data.get("tabs", []):
                fp = tab_data.get("path", "")
                if fp and Path(fp).exists():
                    self._open_file(
                        fp,
                        view_mode=tab_data.get("view_mode", "edit"),
                        split_position=tab_data.get("split_position", 600),
                        editor_zoom=tab_data.get("editor_zoom", 1.0),
                        preview_zoom=tab_data.get("preview_zoom", 1.0),
                    )
            self._suppress_history = False
            active_tab = vault_data.get("active_tab")
            if active_tab and active_tab in self._tab_bar.get_all_paths():
                self._tab_bar.set_active_tab(active_tab)
                self._push_history(active_tab)
            # Restore MRU from session.
            mru_data = vault_data.get("mru", [])
            if mru_data:
                self.mru.clear()
                for fp in reversed(mru_data):
                    if fp in self._tab_bar.get_all_paths():
                        self.mru.push(fp)
            else:
                # Fallback: rebuild MRU from tab order.
                for tab_data in reversed(vault_data.get("tabs", [])):
                    fp = tab_data.get("path", "")
                    if fp and fp in self._tab_bar.get_all_paths():
                        self.mru.push(fp)
                if active_tab and active_tab in self._tab_bar.get_all_paths():
                    self.mru.push(active_tab)
        else:
            self._suppress_history = False

        # Defer expansion so the tree view is fully mapped first.
        expanded = _ses.get("expanded_vaults", [])
        if expanded:
            GLib.idle_add(self._vault_tree.expand_paths, expanded)

        self.connect("close-request", self._on_close_request)
        self._setup_autosave()
        self._setup_complete = True

        # Re-apply editor colour scheme when the user switches dark/light.
        # Defer so GTK has time to propagate the new style.
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_: GLib.idle_add(self._on_color_scheme_changed),
        )

        # Ctrl+Wheel zoom on the centre content area.
        self._scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        self._scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._scroll_ctrl.connect("scroll", self._on_scroll)
        self._content_stack.add_controller(self._scroll_ctrl)

        # Track pointer position for keyboard zoom.
        self._ptr_x: float = 0.0
        self._ptr_y: float = 0.0
        self._motion_ctrl = Gtk.EventControllerMotion.new()
        self._motion_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._motion_ctrl.connect("motion", self._on_motion)
        self._content_stack.add_controller(self._motion_ctrl)

        # Add global shortcut controller for dynamic tab switching shortcuts.
        self._tab_shortcut_ctrl = Gtk.ShortcutController.new()
        self._tab_shortcut_ctrl.set_scope(Gtk.ShortcutScope.GLOBAL)
        self._tab_shortcuts: list[Gtk.Shortcut] = []
        self.add_controller(self._tab_shortcut_ctrl)
        self._update_tab_shortcuts()

    def _update_tab_shortcuts(self) -> None:
        """Update dynamic tab switching shortcuts in the global shortcut controller."""
        if not hasattr(self, "_tab_shortcut_ctrl"):
            return
        for shortcut in self._tab_shortcuts:
            self._tab_shortcut_ctrl.remove_shortcut(shortcut)
        self._tab_shortcuts = []

        is_mru = self._settings.get("tab_switch_mode", "mru") == "mru"
        if is_mru:
            return  # MRU mode uses application accelerators only

        next_accel = self._settings.get("keybinding_next_tab", "<Control>Tab")
        prev_accel = self._settings.get("keybinding_prev_tab", "<Shift><Control>Tab")
        if next_accel:
            trigger = Gtk.ShortcutTrigger.parse_string(next_accel)
            action = Gtk.NamedAction.new("win.next-tab")
            shortcut = Gtk.Shortcut.new(trigger, action)
            self._tab_shortcut_ctrl.add_shortcut(shortcut)
            self._tab_shortcuts.append(shortcut)
        if prev_accel:
            trigger = Gtk.ShortcutTrigger.parse_string(prev_accel)
            action = Gtk.NamedAction.new("win.prev-tab")
            shortcut = Gtk.Shortcut.new(trigger, action)
            self._tab_shortcut_ctrl.add_shortcut(shortcut)
            self._tab_shortcuts.append(shortcut)

    # ── Welcome view ───────────────────────────────────────────────

    def _build_welcome(self) -> Gtk.Box:
        """Create the welcome/placeholder view shown when no file is open."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )

        icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        box.append(icon)

        title = Gtk.Label(label="Markdown Vault")
        title.add_css_class("title-1")
        box.append(title)

        subtitle = Gtk.Label(label="Open a file from the vault tree or create a new one")
        subtitle.add_css_class("dim-label")
        box.append(subtitle)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          halign=Gtk.Align.CENTER)
        new_btn = Gtk.Button(label="New File")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda *_: self._on_new_file())
        btn_box.append(new_btn)

        open_btn = Gtk.Button(label="Open Vault")
        open_btn.connect("clicked", lambda *_: self._vault_tree._on_add_vault_clicked(None))
        btn_box.append(open_btn)

        box.append(btn_box)
        return box

    def _update_content_visibility(self) -> None:
        """Switch between welcome view and tab content."""
        if self._tab_bar.has_tabs():
            tab = self._tab_bar.get_current_tab()
            if tab and self._content_stack.get_child_by_name(tab.file_path):
                self._content_stack.set_visible_child_name(tab.file_path)
        else:
            self._content_stack.set_visible_child_name("__welcome__")

    # ── Header ─────────────────────────────────────────────────────

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        # New file + save buttons (left side).
        new_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_btn.set_tooltip_text("New file (Ctrl+N)")
        new_btn.connect("clicked", lambda *_: self._on_new_file())
        header.pack_start(new_btn)

        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.set_tooltip_text("Save (Ctrl+S)")
        save_btn.connect("clicked", lambda *_: self._save_current())
        header.pack_start(save_btn)

        # Navigation history buttons.
        self._back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self._back_btn.set_tooltip_text("Back (Alt+Left)")
        self._back_btn.set_sensitive(False)
        self._back_btn.connect("clicked", lambda *_: self._nav_back())
        header.pack_start(self._back_btn)

        self._forward_btn = Gtk.Button(icon_name="go-next-symbolic")
        self._forward_btn.set_tooltip_text("Forward (Alt+Right)")
        self._forward_btn.set_sensitive(False)
        self._forward_btn.connect("clicked", lambda *_: self._nav_forward())
        header.pack_start(self._forward_btn)

        # View-mode toggle buttons (center).
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
            self._view_toggle_buttons[mode] = btn
            view_box.append(btn)
        header.set_title_widget(view_box)

        # Hamburger menu (rightmost).
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()

        theme_section = Gio.Menu()
        theme_section.append("Follow System", "win.theme-system")
        theme_section.append("Light Mode", "win.theme-light")
        theme_section.append("Dark Mode", "win.theme-dark")
        menu.append_section(None, theme_section)

        action_section = Gio.Menu()
        action_section.append("Add Vault", "win.add-vault")
        action_section.append("New File", "win.new-file")
        action_section.append("Toggle Sidebar", "win.toggle-sidebar")
        menu.append_section(None, action_section)

        prefs_section = Gio.Menu()
        prefs_section.append("Preferences", "win.preferences")
        menu.append_section(None, prefs_section)

        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        # Sidebar toggle button (left of hamburger).
        self._sidebar_toggle = Gtk.ToggleButton(icon_name="view-right-sidebar-symbolic")
        self._sidebar_toggle.set_tooltip_text("Toggle Sidebar (Ctrl+B)")
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        header.pack_end(self._sidebar_toggle)

        # Search toggle button (left of sidebar).
        self._search_toggle = Gtk.ToggleButton(icon_name="edit-find-symbolic")
        self._search_toggle.set_tooltip_text("Full-Text Search (Ctrl+F)")
        self._search_toggle.connect("toggled", self._on_search_toggled)
        header.pack_end(self._search_toggle)

        return header

    # ── Actions ────────────────────────────────────────────────────

    def _register_actions(self) -> None:
        for name, scheme in (
            ("theme-system", Adw.ColorScheme.DEFAULT),
            ("theme-light", Adw.ColorScheme.FORCE_LIGHT),
            ("theme-dark", Adw.ColorScheme.FORCE_DARK),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", _make_theme_handler(scheme))
            self.add_action(action)

        action = Gio.SimpleAction.new("add-vault", None)
        action.connect("activate", lambda *_: self._vault_tree._on_add_vault_clicked(None))
        self.add_action(action)

        action = Gio.SimpleAction.new("new-file", None)
        action.connect("activate", lambda *_: self._on_new_file())
        self.add_action(action)

        action = Gio.SimpleAction.new("toggle-sidebar", None)
        action.connect("activate", lambda *_: self._toggle_sidebar())
        self.add_action(action)

        action = Gio.SimpleAction.new("toggle-search", None)
        action.connect("activate", lambda *_: self._toggle_search())
        self.add_action(action)

        action = Gio.SimpleAction.new("save", None)
        action.connect("activate", lambda *_: self._save_current())
        self.add_action(action)

        action = Gio.SimpleAction.new("close-tab", None)
        action.connect("activate", lambda *_: self._close_current_tab())
        self.add_action(action)

        action = Gio.SimpleAction.new("preferences", None)
        action.connect("activate", lambda *_: self._open_preferences())
        self.add_action(action)

        action = Gio.SimpleAction.new("zoom-in", None)
        action.connect("activate", lambda *_: self._zoom_active(+1))
        self.add_action(action)

        action = Gio.SimpleAction.new("zoom-out", None)
        action.connect("activate", lambda *_: self._zoom_active(-1))
        self.add_action(action)

        action = Gio.SimpleAction.new("zoom-reset", None)
        action.connect("activate", lambda *_: self._zoom_reset())
        self.add_action(action)

        action = Gio.SimpleAction.new("nav-back", None)
        action.connect("activate", lambda *_: self._nav_back())
        self.add_action(action)

        action = Gio.SimpleAction.new("nav-forward", None)
        action.connect("activate", lambda *_: self._nav_forward())
        self.add_action(action)

        action = Gio.SimpleAction.new("next-tab", None)
        action.connect("activate", lambda *_: self._next_tab())
        self.add_action(action)

        action = Gio.SimpleAction.new("prev-tab", None)
        action.connect("activate", lambda *_: self._prev_tab())
        self.add_action(action)

        action = Gio.SimpleAction.new("mru-switcher-next", None)
        action.connect("activate", lambda *_: self._show_mru_switcher(+1))
        self.add_action(action)

        action = Gio.SimpleAction.new("mru-switcher-prev", None)
        action.connect("activate", lambda *_: self._show_mru_switcher(-1))
        self.add_action(action)

        self._apply_keybindings()

    # ── New file ───────────────────────────────────────────────────

    def _resolve_active_vault(self) -> str:
        """Determine the vault root for a new file.

        Priority: open tab's file → vault tree selection → fallback to last vault.
        """
        vaults = self._vault_tree.get_vault_paths()

        # 1. Derive from the currently open tab's file path.
        tab = self._tab_bar.get_current_tab()
        if tab and tab.editor.file_path:
            file_parent = str(Path(tab.editor.file_path).parent)
            for v in vaults:
                if file_parent == v or file_parent.startswith(v + os.sep):
                    return v

        # 2. Derive from vault tree selection.
        selected = self._vault_tree.get_selected_path()
        if selected:
            for v in vaults:
                if selected == v or selected.startswith(v + os.sep):
                    return v

        # 3. Fallback to stored active vault if valid.
        if self._active_vault and self._active_vault in vaults:
            return self._active_vault

        # 4. Last resort: most recently added vault.
        return vaults[-1]

    def _on_new_file(self) -> None:
        """Prompt for a filename and create it in the active vault."""
        vaults = self._vault_tree.get_vault_paths()
        if not vaults:
            dialog = Adw.AlertDialog(
                heading="No Vault Open",
                body="Add a vault directory first before creating files.",
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)
            return

        default_dir = self._resolve_active_vault()

        dialog = Adw.AlertDialog(heading="New File", body="File name:")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry(placeholder_text="e.g. My Note.md")
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        dialog.connect("response", self._on_new_file_response, entry, default_dir)
        dialog.present(self)

    def _on_new_file_response(self, dialog, response, entry, default_dir):
        """Handle the new-file dialog response."""
        if response != "create":
            return
        name = entry.get_text().strip()
        if not name:
            return
        if not name.endswith(".md"):
            name += ".md"
        file_path = os.path.join(default_dir, name)
        try:
            Path(file_path).touch()
        except OSError:
            return
        self._vault_tree.refresh()
        self._open_file(file_path)

    # ── Vault loading ──────────────────────────────────────────────

    def _load_vaults(self) -> None:
        vaults = config.load_vaults()
        paths = [v["path"] for v in vaults]
        self._vault_tree.set_vaults(paths)
        self._sidebar.set_vault_paths(paths)

    # ── Vault switching ──────────────────────────────────────────

    def _find_vault_for_file(self, file_path: str) -> str | None:
        """Return the vault root that contains *file_path*, or ``None``."""
        file_parent = str(Path(file_path).parent)
        for v in self._vault_tree.get_vault_paths():
            if file_parent == v or file_parent.startswith(v + os.sep):
                return v
        return None

    def _switch_vault(self, new_vault: str) -> None:
        """Switch to *new_vault*, saving the current vault's session first."""
        if new_vault == self._active_vault:
            return
        # Save current vault state.
        self._save_vault_session()
        # Close all open tabs.
        for path in list(self._tab_bar.get_all_paths()):
            self._tab_bar.close_tab(path)
        self.mru.clear()
        self._nav_history.clear()
        self._nav_pos = -1
        self._update_nav_buttons()
        # Switch.
        self._active_vault = new_vault
        self._vault_tree.set_active_vault(new_vault)
        # Restore target vault state.
        self._restore_vault_session(new_vault)

    def _save_vault_session(self) -> None:
        """Save the current vault's tab state to the session on disk."""
        if not self._active_vault:
            return
        ses = session.load_session()
        vault_sessions = ses.get("vault_sessions", {})
        vault_sessions[self._active_vault] = {
            "tabs": self._collect_tab_data(),
            "active_tab": self._tab_bar.get_current_path(),
            "mru": self.mru.tabs,
        }
        alloc = self.get_allocation()
        session.save_session(
            width=alloc.width,
            height=alloc.height,
            sidebar_visible=self._sidebar.get_visible(),
            active_vault=self._active_vault,
            vault_sessions=vault_sessions,
            expanded_vaults=self._vault_tree.get_expanded_paths(),
            search_visible=self._search_bar.get_visible(),
            search_paned_position=self._search_paned.get_position(),
        )

    def _restore_vault_session(self, vault_path: str) -> None:
        """Restore tabs for *vault_path* from the persisted session."""
        ses = session.load_session()
        vault_data = ses.get("vault_sessions", {}).get(vault_path, {})
        vault_data = session.prune_vault_session(vault_data)
        self._suppress_history = True
        for tab_data in vault_data.get("tabs", []):
            fp = tab_data.get("path", "")
            if fp and Path(fp).exists():
                self._open_file(
                    fp,
                    view_mode=tab_data.get("view_mode", "edit"),
                    split_position=tab_data.get("split_position", 600),
                    editor_zoom=tab_data.get("editor_zoom", 1.0),
                    preview_zoom=tab_data.get("preview_zoom", 1.0),
                )
        self._suppress_history = False
        active_tab = vault_data.get("active_tab")
        if active_tab and active_tab in self._tab_bar.get_all_paths():
            self._tab_bar.set_active_tab(active_tab)
            self._push_history(active_tab)
        # Restore MRU.
        mru_data = vault_data.get("mru", [])
        if mru_data:
            for fp in reversed(mru_data):
                if fp in self._tab_bar.get_all_paths():
                    self.mru.push(fp)

    # ── File opening ───────────────────────────────────────────────

    def _open_file(
        self,
        file_path: str,
        *,
        view_mode: str | None = None,
        split_position: int = 600,
        editor_zoom: float = 1.0,
        preview_zoom: float = 1.0,
        _from_nav: bool = False,
    ) -> None:
        """Open *file_path* in a new or existing tab.

        When *view_mode* is ``None`` the current tab's view mode is
        inherited (or ``"edit"`` when no tab exists yet).  Session restore
        passes an explicit mode so it stays independent.  *_from_nav* is
        ``True`` for programmatic back/forward navigation and suppresses
        history pushes.
        """
        for path in self._tab_bar.get_all_paths():
            if path == file_path:
                self._tab_bar.set_active_tab(file_path)
                if not _from_nav:
                    self._push_history(file_path)
                return

        if view_mode is None:
            cur = self._tab_bar.get_current_tab()
            view_mode = cur.view_mode if cur else "edit"

        editor = Editor(
            base_font_size=self._settings.get("editor_font_size", 14),
            tab_width=self._settings.get("editor_tab_width", 4),
            wrap_text=self._settings.get("editor_wrap_text", True),
        )
        preview = Preview()
        preview.set_vault_paths(self._vault_tree.get_vault_paths())
        preview.connect("link-clicked", self._on_preview_link_clicked)
        editor.open_file(file_path)

        # Apply per-tab zoom.
        editor.zoom_factor = editor_zoom
        preview.zoom_level = preview_zoom

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.set_start_child(editor)
        split.set_end_child(preview)
        split.set_position(split_position)

        editor.connect("text-changed", self._on_editor_text_changed)

        self._content_stack.add_named(split, file_path)

        tab = self._tab_bar.add_tab(file_path, editor, preview)
        tab.view_mode = view_mode

        # Sync the header toggle buttons to match the restored view mode.
        self._sync_view_toggle(view_mode)

        self._content_stack.set_visible_child_name(file_path)
        self._apply_view_mode()
        self._refresh_preview()
        self._sidebar.update_for_file(file_path, editor.get_text())
        if not _from_nav:
            self._push_history(file_path)

    # ── Tab callbacks ──────────────────────────────────────────────

    def _on_file_selected_from_tree(self, _tree, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault)
        self._open_file(file_path)

    def _on_vault_activated(self, _tree, vault_path: str) -> None:
        """Handle double-click on a vault root in the tree."""
        if vault_path != self._active_vault:
            self._switch_vault(vault_path)

    def _on_vault_added(self, _tree, vault_path: str) -> None:
        """Handle a new vault being added."""
        self._switch_vault(vault_path)

    def _on_tab_changed(self, _tab_bar, file_path: str) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        self._content_stack.set_visible_child_name(file_path)
        self._sync_view_toggle(tab.view_mode)
        self._apply_view_mode()
        self._refresh_preview()
        self._sidebar.update_for_file(file_path, tab.editor.get_text())
        self._push_history(file_path)
        self.mru.push(file_path)
        # Keep active vault in sync with the open tab.
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._active_vault = vault
            self._vault_tree.set_active_vault(vault)

    def _on_tab_closed(self, _tab_bar, file_path: str) -> None:
        self.mru.remove(file_path)
        child = self._content_stack.get_child_by_name(file_path)
        if child:
            self._content_stack.remove(child)
        self._update_content_visibility()
        if not self._tab_bar.has_tabs():
            self._sidebar.update_for_file(None)

    def _on_sidebar_file_requested(self, _sidebar, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault)
        self._open_file(file_path)

    def _on_outline_clicked(self, _sidebar, line: int) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        tab.editor.scroll_to_line(line)
        text = tab.editor.get_text()
        tab.preview.scroll_to_line(line, text)

    def _on_search_result_selected(self, _search_bar, file_path: str, line_num: int) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault)
        self._open_file(file_path)
        tab = self._tab_bar.get_current_tab()
        if tab:
            tab.editor.scroll_to_line(line_num - 1)
            text = tab.editor.get_text()
            tab.preview.scroll_to_line(line_num - 1, text)

    def _on_preview_link_clicked(self, _preview, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault)
        self._open_file(file_path)

    # ── Vault tree file operations ───────────────────────────────

    def _on_new_file_requested(self, _tree, parent_dir: str) -> None:
        """Handle 'New File' from the vault tree context menu."""
        dialog = Adw.AlertDialog(heading="New File", body="File name:")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry(placeholder_text="e.g. My Note.md")
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        dialog.connect("response", self._on_new_file_response, entry, parent_dir)
        dialog.present(self)

    def _on_new_folder_requested(self, _tree, parent_dir: str) -> None:
        """Handle 'New Folder' from the vault tree context menu."""
        dialog = Adw.AlertDialog(heading="New Folder", body="Folder name:")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry(placeholder_text="e.g. My Folder")
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        dialog.connect("response", self._on_new_folder_response, entry, parent_dir)
        dialog.present(self)

    def _on_new_folder_response(self, dialog, response, entry, parent_dir):
        """Handle the new-folder dialog response."""
        if response != "create":
            return
        name = entry.get_text().strip()
        if not name:
            return
        folder_path = os.path.join(parent_dir, name)
        try:
            os.mkdir(folder_path)
        except OSError:
            return
        self._vault_tree.refresh()

    def _on_delete_requested(self, _tree, path: str) -> None:
        """Handle 'Delete' from the vault tree context menu."""
        name = Path(path).name
        is_dir = Path(path).is_dir()

        if is_dir:
            # Count contents for the warning.
            try:
                count = sum(1 for _ in Path(path).rglob("*"))
            except PermissionError:
                count = -1
            if count > 0:
                body = (
                    f"Delete \"{name}\" and all {count} contained items? "
                    "This cannot be undone."
                )
            else:
                body = f"Delete empty folder \"{name}\"?"
        else:
            body = f"Delete \"{name}\"?"

        dialog = Adw.AlertDialog(heading="Delete?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        dialog.connect("response", self._on_delete_response, path)
        dialog.present(self)

    def _on_delete_response(self, dialog, response, path):
        """Handle the delete confirmation response."""
        if response != "delete":
            return
        is_dir = Path(path).is_dir()

        # Close any open tabs for files under this path.
        if is_dir:
            for tab_path in list(self._tab_bar.get_all_paths()):
                if tab_path == path or tab_path.startswith(path + os.sep):
                    self._tab_bar.close_tab(tab_path)
        else:
            if path in self._tab_bar.get_all_paths():
                self._tab_bar.close_tab(path)

        # Remove from MRU.
        self.mru.remove(path)
        if is_dir:
            for tab_path in list(self.mru.tabs):
                if tab_path == path or tab_path.startswith(path + os.sep):
                    self.mru.remove(tab_path)

        # Remove from nav history.
        self._nav_history = [
            p for p in self._nav_history
            if p != path and not (is_dir and p.startswith(path + os.sep))
        ]
        self._nav_pos = min(self._nav_pos, len(self._nav_history) - 1)

        # Delete from filesystem.
        try:
            if is_dir:
                import shutil
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            return

        self._vault_tree.refresh()

    def _on_close_file_requested(self, _tree, file_path: str) -> None:
        """Handle 'Close File' from the vault tree context menu."""
        if file_path in self._tab_bar.get_all_paths():
            self._tab_bar.close_tab(file_path)

    def _on_file_renamed(self, _tree, old_path: str, new_path: str) -> None:
        """Handle file/folder rename from the vault tree."""
        # Update all open tabs whose path starts with old_path (dir rename).
        for tab_path in list(self._tab_bar.get_all_paths()):
            if tab_path == old_path or tab_path.startswith(old_path + os.sep):
                new_tab_path = new_path + tab_path[len(old_path):]
                self._tab_bar.update_path(tab_path, new_tab_path)

        # Update nav history.
        self._nav_history = [
            new_path + p[len(old_path):]
            if p == old_path or p.startswith(old_path + os.sep)
            else p
            for p in self._nav_history
        ]
        self._nav_pos = min(self._nav_pos, len(self._nav_history) - 1)

        # Update MRU.
        for tab_path in list(self.mru.tabs):
            if tab_path == old_path or tab_path.startswith(old_path + os.sep):
                new_tab_path = new_path + tab_path[len(old_path):]
                self.mru.remove(tab_path)
                self.mru.push(new_tab_path)

    def _on_tab_renamed(self, _tab_bar, old_path: str, new_path: str) -> None:
        """Handle tab path change — update the content stack key."""
        child = self._content_stack.get_child_by_name(old_path)
        if child:
            self._content_stack.remove(child)
            self._content_stack.add_named(child, new_path)
            if self._tab_bar.get_current_path() == new_path:
                self._content_stack.set_visible_child_name(new_path)

    # ── Navigation history ─────────────────────────────────────────

    def _push_history(self, file_path: str) -> None:
        """Append *file_path* to the navigation history.

        Consecutive duplicates are collapsed and any forward history is
        discarded, matching standard browser behaviour.
        """
        if self._suppress_history:
            return
        # Don't push if we're already at this position.
        if (self._nav_pos >= 0
                and self._nav_history[self._nav_pos] == file_path):
            return
        # Truncate forward history.
        self._nav_history = self._nav_history[: self._nav_pos + 1]
        self._nav_history.append(file_path)
        self._nav_pos = len(self._nav_history) - 1
        self._update_nav_buttons()

    def _nav_back(self) -> None:
        """Navigate to the previous entry in history, skipping missing files."""
        while self._nav_pos > 0:
            self._nav_pos -= 1
            file_path = self._nav_history[self._nav_pos]
            if Path(file_path).exists():
                self._open_file(file_path, _from_nav=True)
                self._update_nav_buttons()
                return
        self._update_nav_buttons()

    def _nav_forward(self) -> None:
        """Navigate to the next entry in history, skipping missing files."""
        while self._nav_pos < len(self._nav_history) - 1:
            self._nav_pos += 1
            file_path = self._nav_history[self._nav_pos]
            if Path(file_path).exists():
                self._open_file(file_path, _from_nav=True)
                self._update_nav_buttons()
                return
        self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        self._back_btn.set_sensitive(self._nav_pos > 0)
        self._forward_btn.set_sensitive(
            self._nav_pos < len(self._nav_history) - 1,
        )

    def _next_tab(self) -> None:
        if self._settings.get("tab_switch_mode", "mru") == "mru":
            self._mru_next()
        else:
            self._cycle_tab(+1)

    def _prev_tab(self) -> None:
        if self._settings.get("tab_switch_mode", "mru") == "mru":
            self._mru_prev()
        else:
            self._cycle_tab(-1)

    def _mru_next(self) -> None:
        """Ctrl+Tab: switch to the previously active tab (Alt+Tab style)."""
        target = self.mru.next()
        if target:
            self._open_file(target, _from_nav=True)

    def _mru_prev(self) -> None:
        """Ctrl+Shift+Tab: switch forward in MRU list."""
        target = self.mru.prev()
        if target:
            self._open_file(target, _from_nav=True)

    def _show_mru_switcher(self, direction: int) -> None:
        """Show the MRU tab switcher (triggered by Ctrl+Tab / Ctrl+Shift+Tab).

        Args:
            direction: +1 for Ctrl+Tab (next MRU), -1 for Ctrl+Shift+Tab (prev MRU)
        """
        if mru.MRUSwitcher.is_open():
            mru.MRUSwitcher.cycle_existing(direction)
            return
        mru_tabs = self.mru.tabs
        if len(mru_tabs) < 2:
            return
        mru.MRUSwitcher(self, mru_tabs, self._open_file)

    def _cycle_tab(self, direction: int) -> None:
        paths = self._tab_bar.get_all_paths()
        if len(paths) < 2:
            return
        current = self._tab_bar.get_current_path()
        try:
            idx = paths.index(current)
        except ValueError:
            return
        self._tab_bar.set_active_tab(paths[(idx + direction) % len(paths)])

    def _apply_keybindings(self) -> None:
        app = self.get_application()
        if not app:
            return
        app.set_accels_for_action("win.nav-back", ["<Alt>Left"])
        app.set_accels_for_action("win.nav-forward", ["<Alt>Right"])
        is_mru = self._settings.get("tab_switch_mode", "mru") == "mru"
        if is_mru:
            next_accel = self._settings.get("keybinding_next_tab", "<Control>Tab")
            prev_accel = self._settings.get("keybinding_prev_tab", "<Shift><Control>Tab")
            app.set_accels_for_action("win.mru-switcher-next", [next_accel] if next_accel else [])
            app.set_accels_for_action("win.mru-switcher-prev", [prev_accel] if prev_accel else [])
            app.set_accels_for_action("win.next-tab", [])
            app.set_accels_for_action("win.prev-tab", [])
        else:
            for setting_key, cycle_action in (
                ("keybinding_next_tab", "next-tab"),
                ("keybinding_prev_tab", "prev-tab"),
            ):
                accel = self._settings.get(setting_key, "")
                if accel:
                    app.set_accels_for_action(f"win.{cycle_action}", [accel])
                else:
                    app.set_accels_for_action(f"win.{cycle_action}", [])
            app.set_accels_for_action("win.mru-switcher-next", [])
            app.set_accels_for_action("win.mru-switcher-prev", [])
        self._update_tab_shortcuts()

    # ── View mode ──────────────────────────────────────────────────

    def _on_view_mode_toggled(self, toggle_btn: Gtk.ToggleButton) -> None:
        if not self._setup_complete:
            return
        if not toggle_btn.get_active():
            return
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        tab.view_mode = toggle_btn._mode  # type: ignore[attr-defined]
        self._apply_view_mode()

    def _apply_view_mode(self) -> None:
        """Show/hide editor and preview based on the current tab's view mode."""
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        mode = tab.view_mode
        tab.editor.set_visible(mode in ("edit", "split"))
        tab.preview.set_visible(mode in ("render", "split"))
        if mode in ("render", "split"):
            self._refresh_preview()

    def _sync_view_toggle(self, mode: str) -> None:
        """Set the header toggle buttons to reflect *mode* without triggering."""
        btn = self._view_toggle_buttons.get(mode)
        if btn:
            btn.set_active(True)

    # ── Editor callbacks ────────────────────────────────────────────

    def _on_editor_text_changed(self, editor: Editor) -> None:
        """Update preview and sidebar when editor content changes."""
        tab = self._tab_bar.get_current_tab()
        if tab and tab.editor is editor:
            if tab.preview.get_visible():
                self._refresh_preview()
            self._sidebar.update_for_file(editor.file_path, editor.get_text())

    # ── Preview ────────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        """Update the preview for the current tab."""
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        text = tab.editor.get_text()
        base_dir = str(Path(tab.editor.file_path).parent) if tab.editor.file_path else ""
        tab.preview.update_from_text(text, base_dir)

    # ── Misc ───────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        visible = self._sidebar.get_visible()
        self._sidebar.set_visible(not visible)
        self._sidebar_toggle.set_active(not visible)

    def _on_sidebar_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._sidebar.set_visible(btn.get_active())

    def _toggle_search(self) -> None:
        visible = self._search_bar.get_visible()
        self._search_bar.set_visible(not visible)
        self._search_toggle.set_active(not visible)
        if not visible:
            self._search_bar.focus()

    def _on_search_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._search_bar.set_visible(btn.get_active())
        if btn.get_active():
            self._search_bar.focus()

    def _on_search_close_requested(self, _search_bar) -> None:
        self._search_bar.set_visible(False)
        self._search_toggle.set_active(False)

    def _save_current(self) -> None:
        tab = self._tab_bar.get_current_tab()
        if tab:
            tab.editor.save()

    def _close_current_tab(self) -> None:
        path = self._tab_bar.get_current_path()
        if path:
            self._tab_bar.close_tab(path)

    # ── Session persistence ────────────────────────────────────────

    def _collect_tab_data(self) -> list[dict]:
        """Gather per-tab state for session saving."""
        data = []
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if not tab:
                continue
            # Find the Paned to read its split position.
            split_pos = 600
            child = self._content_stack.get_child_by_name(path)
            if isinstance(child, Gtk.Paned):
                split_pos = child.get_position()
            data.append({
                "path": path,
                "view_mode": tab.view_mode,
                "split_position": split_pos,
                "editor_zoom": tab.editor.zoom_factor,
                "preview_zoom": tab.preview.zoom_level,
            })
        return data

    def _save_session(self) -> None:
        """Persist the current window state to disk."""
        # Load existing session to preserve other vaults' state.
        ses = session.load_session()
        vault_sessions = ses.get("vault_sessions", {})
        # Update only the current vault's entry.
        if self._active_vault:
            vault_sessions[self._active_vault] = {
                "tabs": self._collect_tab_data(),
                "active_tab": self._tab_bar.get_current_path(),
                "mru": self.mru.tabs,
            }
        alloc = self.get_allocation()
        session.save_session(
            width=alloc.width,
            height=alloc.height,
            sidebar_visible=self._sidebar.get_visible(),
            active_vault=self._active_vault,
            vault_sessions=vault_sessions,
            expanded_vaults=self._vault_tree.get_expanded_paths(),
            search_visible=self._search_bar.get_visible(),
            search_paned_position=self._search_paned.get_position(),
        )

    def _on_close_request(self, *_args) -> bool:
        """Save session before the window closes."""
        self._cancel_autosave()
        self._save_session()
        return False  # Allow the close to proceed.

    # ── Autosave ───────────────────────────────────────────────────

    def _setup_autosave(self) -> None:
        """Start the autosave idle timer with the configured interval."""
        settings = config.load_settings()
        interval = settings.get("autosave_interval", 30)
        if interval > 0:
            self._autosave_id = GLib.timeout_add_seconds(
                interval, self._autosave_tick,
            )

    def _autosave_tick(self) -> bool:
        """Save all modified buffers; returns True to keep the timer alive."""
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor.is_modified:
                tab.editor.save()
        return True  # Keep the GLib timeout running.

    def _cancel_autosave(self) -> None:
        if self._autosave_id is not None:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = None

    def _on_color_scheme_changed(self) -> None:
        """Re-apply editor colour schemes and refresh all previews."""
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if tab:
                tab.editor.update_color_scheme()
                tab.preview.update_theme()
                text = tab.editor.get_text()
                base_dir = str(Path(tab.editor.file_path).parent) if tab.editor.file_path else ""
                tab.preview.update_from_text(text, base_dir)
        return False  # remove idle handler

    # ── Preferences ────────────────────────────────────────────────

    def _open_preferences(self) -> None:
        dlg = PreferencesDialog()
        dlg.connect("settings-changed", self._on_preferences_changed)
        dlg.present(self)

    def _on_preferences_changed(self, _dlg) -> None:
        self._settings = config.load_settings()
        self._apply_keybindings()
        # Apply to all open editors.
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if tab:
                tab.editor.update_settings(
                    font_size=self._settings.get("editor_font_size", 14),
                    tab_width=self._settings.get("editor_tab_width", 4),
                    wrap_text=self._settings.get("editor_wrap_text", True),
                )
        # Restart autosave with new interval.
        self._cancel_autosave()
        self._setup_autosave()

    # ── Zoom ────────────────────────────────────────────────────────

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        """Track pointer position inside _content_stack."""
        self._ptr_x = x
        self._ptr_y = y

    def _widget_origin_in_stack(self, widget: Gtk.Widget) -> tuple[int, int]:
        """Walk up from *widget* to _content_stack, accumulating offsets."""
        x, y = 0, 0
        cur = widget
        while cur is not None and cur is not self._content_stack:
            a = cur.get_allocation()
            x += a.x
            y += a.y
            cur = cur.get_parent()
        return x, y

    def _is_pointer_over_preview(self, tab, px: float, py: float) -> bool:
        """Check if (px, py) in _content_stack coords is over the preview."""
        if not tab.preview.get_visible():
            return False
        ox, oy = self._widget_origin_in_stack(tab.preview)
        a = tab.preview.get_allocation()
        return ox <= px < ox + a.width and oy <= py < oy + a.height

    def _zoom_active(self, direction: int) -> None:
        """Zoom the widget under the mouse pointer (keyboard shortcut)."""
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        if self._is_pointer_over_preview(tab, self._ptr_x, self._ptr_y):
            tab.preview.zoom_level = round(
                tab.preview.zoom_level + direction * _ZOOM_STEP, 2,
            )
        else:
            tab.editor.zoom_factor = round(
                tab.editor.zoom_factor + direction * _ZOOM_STEP, 2,
            )

    def _zoom_reset(self) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        if self._is_pointer_over_preview(tab, self._ptr_x, self._ptr_y):
            tab.preview.zoom_level = 1.0
        else:
            tab.editor.zoom_factor = 1.0

    def _on_scroll(self, _ctrl, _dx, dy: float) -> bool:
        """Ctrl+Wheel zoom handler."""
        event = _ctrl.get_current_event()
        if event is None:
            return False
        state = event.get_modifier_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return False
        direction = -1 if dy > 0 else 1
        if self._is_pointer_over_preview(tab, self._ptr_x, self._ptr_y):
            tab.preview.zoom_level = round(
                tab.preview.zoom_level + direction * _ZOOM_STEP, 2,
            )
        else:
            tab.editor.zoom_factor = round(
                tab.editor.zoom_factor + direction * _ZOOM_STEP, 2,
            )
        return True
