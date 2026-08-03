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
import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

import traceback
import sys
import faulthandler
import threading

from . import logging_setup
from .vault_tree import VaultTree
from .editor import Editor
from .preview import Preview
from .tabs import TabBar
from .sidebar import Sidebar
from .search import SearchBar
from .preferences import PreferencesDialog
from .monitor_handler import MonitorHandler
from .tab_manager import TabOrchestrator
from .session_manager import SessionManager
from .markdown_help import MarkdownHelpOverlay
from .autosave import AutosaveManager
from .file_ops import FileOps
from .view_mode_manager import ViewModeManager
from .content_changes import ContentChangeHandler
from .input_manager import InputManager
from .file_manager import FileManager
from . import config
from . import dialogs
from . import banners as banner_mod
from . import session
from . import mru
from . import history
from . import path_utils
from . import validation
from . import vault_monitor
from .backlink_index import BacklinkIndex, scan_vaults
from .event_router import FileEventDispatcher
from .file_index import FileIndex

logger = logging.getLogger(__name__)


def _load_gtk_css() -> None:
    """Load GTK CSS for tab bar and other widgets."""
    css_provider = Gtk.CssProvider()
    try:
        import importlib.resources

        css_file = importlib.resources.files("markdown_vault").joinpath("css", "gtk.css")
        with css_file.open("rb") as f:
            css_provider.load_from_data(f.read())
    except Exception:
        logger.warning("Could not load GTK CSS from package", exc_info=True)
        return
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

# R17.1: debounce window for coalescing backlink-build reschedules, so a
# sustained burst of incremental edits cannot livelock the async build.
_BACKLINK_REBUILD_COOLDOWN_MS = 500


class MainWindow(Adw.ApplicationWindow):
    """Top-level application window."""

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Markdown Vault")

        _load_gtk_css()
        self._settings = config.load_settings()

        self._view_mode: str = "edit"
        self._setup_complete = False
        self._view_toggle_buttons: dict[str, Gtk.ToggleButton] = {}
        self._active_vault: str | None = None

        # Guard against re-entrant position clamping.
        self._paned_clamping: bool = False
        self._close_window_pending: bool = False
        self._switch_vault_pending: bool = False

        # MRU tab manager.
        self.mru = mru.MRUManager()

        # Navigation history (browser-style back/forward).
        self._nav_history = history.NavHistory()

        # Load session for window geometry.
        _ses = session.load_session()
        w = _ses["window"]
        self.set_default_size(w.get("width", 1200), w.get("height", 800))

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        root_overlay = Gtk.Overlay()
        root_overlay.set_child(root_box)
        self._help_overlay = MarkdownHelpOverlay()
        root_overlay.add_overlay(self._help_overlay)
        self.set_content(root_overlay)

        root_box.append(self._build_header())

        self._main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._main_paned.set_wide_handle(True)

        self._vault_tree = VaultTree()
        self._vault_tree.connect("file-selected", self._on_file_selected_from_tree)
        self._vault_tree.connect("vault-activated", self._on_vault_activated)
        self._vault_tree.connect("vault-added", self._on_vault_added)
        self._vault_tree.connect("new-file-requested", self._on_new_file_requested)
        self._vault_tree.connect("new-folder-requested", self._on_new_folder_requested)
        self._vault_tree.connect("delete-requested", self._on_delete_requested)
        self._vault_tree.connect("close-file-requested", self._on_close_file_requested)
        self._vault_tree.connect("file-renamed", self._on_file_renamed)
        self._vault_tree.connect("vault-renamed", self._on_vault_renamed)
        self._vault_tree.connect("vault-removed", self._on_vault_removed)
        self._vault_tree.connect("focus-current-file", self._on_focus_current_file_clicked)

        self._vault_monitor = vault_monitor.VaultMonitor()
        self._vault_tree.vault_monitor = self._vault_monitor
        self._file_ops = FileOps(skip_fn=self._vault_monitor.skip_next_event)
        self._main_paned.set_start_child(self._vault_tree)
        self._main_paned.set_resize_start_child(False)
        self._main_paned.set_shrink_start_child(False)

        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._tab_bar = TabBar()
        self._tab_bar.connect("tab-changed", self._on_tab_changed)
        self._tab_bar.connect("tab-closed", self._on_tab_closed)
        self._tab_bar.connect("tab-renamed", self._on_tab_renamed)
        self._tab_bar.set_close_request_callback(self._on_tab_close_requested)
        centre.append(self._tab_bar)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_vexpand(True)

        # Welcome placeholder shown when no file is open.
        self._welcome = self._build_welcome()
        self._content_stack.add_named(self._welcome, "__welcome__")
        self._content_stack.set_visible_child_name("__welcome__")
        centre.append(self._content_stack)

        self._main_paned.set_end_child(centre)
        self._main_paned.set_resize_end_child(True)

        self._backlink_index = BacklinkIndex()
        self._file_index = FileIndex()
        # R16.2: monotonic generation for the async backlink build — a worker
        # result from a superseded schedule is discarded on apply.
        self._build_generation = 0
        # R17.1: debounce timer for coalesced backlink-build reschedules.
        self._rebuild_timeout = None

        self._sidebar = Sidebar(
            backlink_index=self._backlink_index,
            get_active_tab_info=self._get_active_tab_info,
        )
        self._sidebar.connect("file-open-requested", self._on_sidebar_file_requested)
        self._sidebar.connect("outline-clicked", self._on_outline_clicked)

        # Event dispatcher for decoupled sidebar refresh.
        self._event_dispatcher = FileEventDispatcher(
            sidebar_refresher=self._sidebar,
            backlink_index=self._backlink_index,
            file_index=self._file_index,
            debug_fn=self._dump_debug,
        )

        # Connect VaultMonitor signals to MonitorHandler.
        self._monitor_handler = MonitorHandler(
            self._backlink_index,
            self._file_index,
            self._vault_tree,
            self._tab_bar,
            self._event_dispatcher,
            self._dump_debug,
            notify_banner_cb=lambda vp, fp: self._content_change_handler.handle_external_change(fp),
        )
        self._vault_monitor.connect("external-file-created", self._monitor_handler.on_file_created)
        self._vault_monitor.connect("external-file-deleted", self._monitor_handler.on_file_deleted)
        self._vault_monitor.connect("external-file-moved", self._monitor_handler.on_file_moved)
        # Two handlers for external-content-changed:
        # 1. MonitorHandler: updates backlink index + sidebar (data layer).
        # 2. ContentChangeHandler: shows warning banner (UI layer).
        self._vault_monitor.connect("external-content-changed", self._monitor_handler.on_content_changed)
        self._vault_monitor.connect(
            "external-content-changed",
            lambda _vp, fp: self._content_change_handler.handle_external_change(fp),
        )

        # View mode manager.
        self._view_mode_manager = ViewModeManager(
            tab_bar=self._tab_bar,
            view_toggle_buttons=self._view_toggle_buttons,
            sidebar=self._sidebar,
            backlink_index=self._backlink_index,
        )

        # Content change handler for external file modifications.
        self._content_change_handler = ContentChangeHandler(
            tab_bar=self._tab_bar, parent=self
        )

        # Tab lifecycle orchestrator.
        self._tab_orchestrator = TabOrchestrator(
            tab_bar=self._tab_bar,
            mru_manager=self.mru,
            sidebar=self._sidebar,
            settings=self._settings,
            content_stack=self._content_stack,
            file_index=self._file_index,
            backlink_index=self._backlink_index,
            vault_tree=self._vault_tree,
            callbacks={
                "on_preview_link_clicked": self._on_preview_link_clicked,
                "on_preview_link_not_found": self._on_preview_link_not_found,
                "on_preview_checkbox_toggled": self._on_preview_checkbox_toggled,
                "on_editor_text_changed": self._on_editor_text_changed,
                "on_editor_modified": self._on_editor_modified,
                "apply_view_mode": self._view_mode_manager.apply_view_mode,
                "sync_view_toggle": self._view_mode_manager.sync_view_toggle,
                "refresh_preview": self._view_mode_manager.refresh_preview,
                "push_history": self._push_history,
                "on_banner_reload": self._content_change_handler.reload_content,
                "on_banner_dismiss": self._content_change_handler.dismiss_content,
                "dump_debug": self._dump_debug,
            },
        )

        # Input manager (shortcuts + navigation).
        self._input_manager = InputManager(
            application=self,
            on_nav_file_opened=self._open_file,
            nav_history=self._nav_history,
            back_btn=self._back_btn,
            forward_btn=self._forward_btn,
            settings=self._settings,
        )

        # Session persistence manager.
        self._session_mgr = SessionManager(
            get_window_state=self._get_window_state,
            tab_bar=self._tab_bar,
            mru_manager=self.mru,
        )

        # File manager (new file / new folder dialogs + delete).
        self._file_manager = FileManager(
            open_tab_fn=self._open_file,
            vault_tree=self._vault_tree,
            file_ops=self._file_ops,
            show_error_fn=self._show_error,
            tab_bar=self._tab_bar,
            mru=self.mru,
            nav_history=self._nav_history,
        )

        self._sidebar_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._sidebar_paned.set_wide_handle(True)
        self._sidebar_paned.set_start_child(self._main_paned)
        self._sidebar_paned.set_resize_start_child(True)
        self._sidebar_paned.set_shrink_start_child(False)
        self._sidebar_paned.set_end_child(self._sidebar)
        self._sidebar_paned.set_resize_end_child(False)
        self._sidebar_paned.set_shrink_end_child(True)

        self._search_bar = SearchBar(get_vault_paths=self._vault_tree.get_vault_paths)
        self._search_bar.connect("file-selected", self._on_search_result_selected)
        self._search_bar.connect("close-requested", self._on_search_close_requested)

        self._search_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._search_paned.set_wide_handle(True)
        self._search_paned.set_start_child(self._sidebar_paned)
        self._search_paned.set_resize_start_child(True)
        self._search_paned.set_shrink_start_child(False)
        self._search_paned.set_end_child(self._search_bar)
        self._search_paned.set_resize_end_child(False)
        self._search_paned.set_shrink_end_child(True)

        # Clamp positions so end children never go below 20px.
        self._sidebar_paned.connect("notify::position", self._clamp_sidebar_position)
        self._search_paned.connect("notify::position", self._clamp_search_position)

        self._search_paned.set_vexpand(True)
        root_box.append(self._search_paned)

        # Global shortcut controller for dynamic tab switching shortcuts.
        self._tab_shortcut_ctrl = Gtk.ShortcutController.new()
        self._tab_shortcut_ctrl.set_scope(Gtk.ShortcutScope.GLOBAL)
        self._tab_shortcuts: list[Gtk.Shortcut] = []

        self._register_actions()
        self._load_vaults()
        self._tab_bar.set_tab_min_width(self._settings.get("tab_min_width", 100))

        # Restore session: sidebar, search, tabs, active tab, expanded vaults.
        sidebar_visible = _ses.get("sidebar_visible", False)
        self._sidebar.set_visible(sidebar_visible)
        self._sidebar_toggle.set_active(sidebar_visible)
        sidebar_pos = _ses.get("sidebar_paned_position", 0)
        if sidebar_pos > 0:
            self._sidebar_paned.set_position(sidebar_pos)
        search_pos = _ses.get("search_paned_position", 0)
        if search_pos > 0:
            self._search_paned.set_position(search_pos)
        main_pos = _ses.get("main_paned_position", 0)
        if main_pos > 0:
            self._main_paned.set_position(main_pos)
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
        if self._active_vault:
            self._session_mgr.restore_vault_session(
                self._active_vault,
                open_file_fn=self._open_file,
                push_history_fn=self._push_history,
                suppress_nav_fn=lambda s: setattr(self._nav_history, "suppress", s),
                mru_push_fn=self.mru.push,
            )

        # Defer expansion so the tree view is fully mapped first.
        expanded = _ses.get("expanded_vaults", [])
        if expanded:
            GLib.idle_add(self._vault_tree.expand_paths, expanded)

        self.connect("close-request", self._on_close_request)
        self._autosave = AutosaveManager(
            interval=self._settings.get("autosave_interval", 30),
            get_dirty_tabs=self._get_autosave_dirty_tabs,
            save_tab=self._autosave_save_tab,
            on_save_failed=self._autosave_on_failed,
        )
        self._autosave.start()
        self._setup_complete = True

        # Responsive header: hide buttons when window is narrow.
        self.connect("notify::default-width", self._on_window_resize)
        self.connect("notify::default-height", self._on_window_resize)
        GLib.idle_add(self._update_header_buttons)

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

        self._update_tab_shortcuts()
        self.add_controller(self._tab_shortcut_ctrl)

    def _update_tab_shortcuts(self) -> None:
        """Update dynamic tab switching shortcuts — delegates to
        :class:`InputManager`."""
        self._input_manager.update_tab_shortcuts()

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

        self._save_btn = Gtk.Button(icon_name="document-save-symbolic")
        self._save_btn.set_tooltip_text("Save (Ctrl+S)")
        self._save_btn.connect("clicked", lambda *_: self._save_current())
        header.pack_start(self._save_btn)

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
            ("edit",   "document-edit-symbolic",        "Edit (Ctrl+1)"),
            ("split",  "view-dual-symbolic",            "Split (Ctrl+2)"),
            ("render", "document-properties-symbolic",  "Preview (Ctrl+3)"),
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

        # Debug dump toggle (only visible in debug loglevel).
        self._debug_toggle = Gtk.ToggleButton(icon_name="applications-utilities-symbolic")
        self._debug_toggle.set_tooltip_text("Dump FileIndex on changes")
        self._debug_toggle.connect("toggled", self._on_debug_toggled)
        is_debug = self._settings.get("loglevel", "info") == "debug"
        self._debug_toggle.set_visible(is_debug)
        if is_debug:
            # Block signal during restore — widgets don't exist yet.
            self._debug_toggle.handler_block_by_func(self._on_debug_toggled)
            self._debug_toggle.set_active(self._settings.get("debug_active", False))
            self._debug_toggle.handler_unblock_by_func(self._on_debug_toggled)
            if self._debug_toggle.get_active():
                self._debug_toggle.add_css_class("suggested-action")
        header.pack_end(self._debug_toggle)

        # Sidebar toggle button (left of hamburger).
        self._sidebar_toggle = Gtk.ToggleButton(icon_name="user-bookmarks-symbolic")
        self._sidebar_toggle.set_tooltip_text("Toggle Sidebar (Ctrl+B)")
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        header.pack_end(self._sidebar_toggle)

        # Search toggle button (left of sidebar).
        self._search_toggle = Gtk.ToggleButton(icon_name="edit-find-symbolic")
        self._search_toggle.set_tooltip_text("Full-Text Search (Ctrl+F)")
        self._search_toggle.connect("toggled", self._on_search_toggled)
        header.pack_end(self._search_toggle)

        return header

    def _on_window_resize(self, *_args) -> None:
        self._update_header_buttons()

    def _update_header_buttons(self) -> None:
        """Show/hide header buttons based on window width."""
        w = self.get_width()
        # Narrow (<550): only new + hamburger + search
        # Medium (<750): + save, hide nav
        # Wide (>=750): all visible
        narrow = w < 550
        medium = w < 750
        self._save_btn.set_visible(not narrow)
        self._back_btn.set_visible(not medium)
        self._forward_btn.set_visible(not medium)

    # ── Debug dump ─────────────────────────────────────────────────

    def _on_debug_toggled(self, toggle: Gtk.ToggleButton) -> None:
        """Toggle debug index dump mode."""
        if toggle.get_active():
            toggle.add_css_class("suggested-action")
            self._dump_debug(["file_index", "backlink_index", "vault_tree",
                              "tabs", "sidebar", "preview_html"])
        else:
            toggle.remove_css_class("suggested-action")
        self._settings["debug_active"] = toggle.get_active()
        config.save_settings(self._settings)

    def _dump_debug(self, components: list[str]) -> None:
        """Write enabled debug dumps to JSON files."""
        if not self._debug_toggle.get_active():
            return
        if self._settings.get("loglevel", "info") != "debug":
            return
        state = config.STATE_DIR
        dumpers = {
            "file_index": lambda: self._file_index.dump_to_file(state / "debug-file-index.json"),
            "backlink_index": lambda: self._backlink_index.dump_to_file(state / "debug-backlink-index.json"),
            "preview_html": lambda: self._tab_bar.get_current_tab().preview.dump_html(state / "debug-preview.html") if self._tab_bar.get_current_tab() else None,
            "vault_tree": lambda: self._vault_tree.dump_to_file(state / "debug-vault-tree.json"),
            "tabs": lambda: self._tab_bar.dump_to_file(state / "debug-tabs.json"),
            "sidebar": lambda: self._sidebar.dump_to_file(state / "debug-sidebar.json"),
        }
        for comp in components:
            if self._settings.get(f"debug_dump_{comp}", False):
                try:
                    dumpers[comp]()
                except Exception:
                    logger.warning("Failed to dump %s", comp, exc_info=True)

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

        action = Gio.SimpleAction.new("toggle-help", None)
        action.connect("activate", lambda *_: self._help_overlay.toggle())
        self.add_action(action)

        for mode in ("edit", "split", "render"):
            action = Gio.SimpleAction.new(f"view-{mode}", None)
            action.connect(
                "activate",
                lambda _a, _p, m=mode: self._set_view_mode(m),
            )
            self.add_action(action)

        self._apply_keybindings()

    # ── New file ───────────────────────────────────────────────────

    def _resolve_active_vault(self) -> str:
        """Determine the vault root for a new file."""
        vaults = self._vault_tree.get_vault_paths()
        tab = self._tab_bar.get_current_tab()
        selected = self._vault_tree.get_selected_path()
        return self._file_ops.resolve_active_vault(
            tab, selected, self._active_vault, vaults,
        )

    def _on_new_file(self) -> None:
        """Prompt for a filename and create it in the active vault."""
        default_dir = self._resolve_active_vault()
        vaults = self._vault_tree.get_vault_paths()
        self._file_manager.prompt_new_file(self, vaults, default_dir)

    # ── Vault loading ──────────────────────────────────────────────

    def _load_vaults(self) -> None:
        vaults = config.load_vaults()
        paths = [v["path"] for v in vaults]
        self._vault_tree.set_vaults(vaults)
        self._vault_monitor.set_vaults(paths)
        self._sidebar.set_vault_paths(paths)
        self._schedule_backlink_build(vaults)
        self._file_index.build(vaults)
        self._dump_debug(["file_index", "vault_tree"])

    def _schedule_backlink_build(self, vaults: list[dict[str, str]]) -> None:
        """Scan vaults for backlinks off the main thread (R5.3).

        The disk scan (walk every vault, read every ``.md``) runs in a
        daemon thread so large vaults do not freeze the UI at startup or
        on vault add.  The result is swapped into the index atomically
        on the main thread via ``GLib.idle_add``.

        R16: a monotonic build generation is captured here and re-checked
        in :meth:`_apply_backlink_build`, so a worker result from a
        superseded schedule (e.g. a vault add racing the startup build) is
        discarded instead of replacing the index.  The mutation sequence of
        the live index is captured too: if incremental edits land during the
        scan window, the snapshot is stale and a fresh scan is rescheduled.
        """
        self._build_generation += 1
        generation = self._build_generation
        start_mutation_seq = self._backlink_index.mutation_seq

        def worker() -> None:
            try:
                target_to_sources, source_to_targets = scan_vaults(vaults)
            except Exception:
                logger.error("Backlink scan failed", exc_info=True)
                return
            GLib.idle_add(
                self._apply_backlink_build,
                generation, start_mutation_seq,
                target_to_sources, source_to_targets,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_backlink_build(
        self,
        generation: int,
        start_mutation_seq: int,
        target_to_sources: dict[str, set[str]],
        source_to_targets: dict[str, set[str]],
    ) -> bool:
        """Swap the scanned index in on the main thread (idle callback).

        Discards the result if a newer build was scheduled (R16.2) or if the
        live index mutated during the scan window (R16.1) — in the latter
        case a fresh scan of the full vault list is rescheduled so no
        incremental edits are lost.  On success the sidebar backlinks are
        refreshed for the active file (R16.3).
        """
        if generation != self._build_generation:
            # A newer build superseded this worker — drop the stale result.
            return False
        if self._backlink_index.mutation_seq != start_mutation_seq:
            # Incremental edits landed during the scan; a wholesale swap
            # would discard them.  Coalesce a fresh rescan instead (R17.1:
            # debounced, so a sustained edit burst cannot livelock the build).
            self._coalesce_backlink_rebuild()
            return False
        self._backlink_index.set_index(target_to_sources, source_to_targets)
        file_path, _text = self._get_active_tab_info()
        if file_path:
            self._sidebar.refresh_backlinks(file_path)
        self._dump_debug(["backlink_index"])
        return False  # remove idle handler

    def _coalesce_backlink_rebuild(self) -> None:
        """Schedule a fresh full rescan, coalescing rapid reschedules (R17.1).

        While a rebuild timer is already pending, further calls are ignored so
        consecutive edits collapse into a single rescan after the cooldown.
        """
        if self._rebuild_timeout is not None:
            return
        self._rebuild_timeout = GLib.timeout_add(
            _BACKLINK_REBUILD_COOLDOWN_MS,
            self._do_backlink_rebuild,
        )

    def _do_backlink_rebuild(self) -> bool:
        """Debounced rescan trigger (idle timeout callback)."""
        self._rebuild_timeout = None
        # Rebuild from the config SSOT, not private vault-tree state.
        self._schedule_backlink_build(config.load_vaults())
        return False  # remove timer source

    def _cancel_backlink_rebuild(self) -> None:
        """Cancel a pending debounced backlink rebuild (R18.1).

        Called on window close so a pending timer cannot fire after teardown
        and spawn a worker + idle callback against the closing window.
        """
        if self._rebuild_timeout is not None:
            GLib.source_remove(self._rebuild_timeout)
            self._rebuild_timeout = None

    # ── Vault switching ──────────────────────────────────────────

    def _find_vault_for_file(self, file_path: str) -> str | None:
        """Return the vault root that contains *file_path*, or ``None``."""
        file_parent = str(Path(file_path).parent)
        return path_utils.find_vault_for_dir(file_parent)

    def _switch_vault(
        self, new_vault: str, *,
        open_file_path: str | None = None,
        post_open_fn=None,
    ) -> None:
        """Switch to *new_vault*, saving the current vault's session first.

        Phase 1: Save current vault, start dirty-check.
        Phase 2 (via ``_switch_vault_complete``): Clear MRU/nav, switch,
        restore target vault.  Phase 2 runs in the dialog's confirm-callback
        so that Cancel fully aborts the switch.

        If *open_file_path* is given, the file is opened in Phase 2 after
        the switch is confirmed.
        *post_open_fn* is called after opening (e.g. for scrolling).
        """
        if new_vault == self._active_vault:
            return
        if self._switch_vault_pending:
            return
        self._switch_vault_pending = True
        # Save current vault state.
        self._session_mgr.save_vault_session(self._active_vault, self._content_stack)
        # Close all open tabs with dirty-check; on confirm continue in Phase 2.
        logger.info("switch-vault: phase 1 — saving current vault session, checking dirty tabs (target=%s, open_file=%s)", new_vault, open_file_path)
        self._close_all_tabs_with_dirty_check(
            on_confirm=lambda: self._switch_vault_complete(new_vault, open_file_path, post_open_fn)
        )

    def _open_file_and_scroll(self, file_path: str, line_num: int) -> None:
        """Open *file_path* and scroll to *line_num* (used after vault switch)."""
        self._open_file(file_path)
        tab = self._tab_bar.get_current_tab()
        if tab:
            tab.editor.scroll_to_line(line_num - 1)
            text = tab.editor.get_text()
            tab.preview.scroll_to_line(line_num - 1, text)

    def _switch_vault_complete(
        self, new_vault: str, open_file_path: str | None = None,
        post_open_fn=None,
    ) -> None:
        """Phase 2 of vault switching — called after dirty-check is confirmed.

        Closes all open tabs, clears MRU/navigation, sets the active vault,
        and restores the target vault's session.
        If *open_file_path* is given, opens the file after the switch.
        *post_open_fn* is called after opening (e.g. for scrolling).
        """
        self._switch_vault_pending = False
        logger.info("switch-vault: phase 2 — switching to %s (open_file=%s)", new_vault, open_file_path)
        self.mru.clear()
        self._nav_history.clear()
        self._update_nav_buttons()
        # Switch.
        self._active_vault = new_vault
        self._vault_tree.set_active_vault(new_vault)
        # Defer tab closure + restore to next idle iteration (avoid GTK widget lifecycle conflict).
        GLib.idle_add(self._switch_vault_complete_phase3, new_vault, open_file_path, post_open_fn)

    def _switch_vault_complete_phase3(
        self, new_vault: str, open_file_path: str | None = None,
        post_open_fn=None,
    ) -> bool:
        """Phase 3 of vault switching — deferred via GLib.idle_add.

        Closes all open tabs, restores target vault state, and opens the
        requested file. Deferred to next idle iteration to avoid GTK
        widget lifecycle conflicts.
        """
        logger.info("switch-vault: phase 3 — closing tabs and restoring %s", new_vault)
        self._do_close_paths(self._tab_bar.get_all_paths())
        self._session_mgr.restore_vault_session(
            new_vault,
            open_file_fn=self._open_file,
            push_history_fn=self._push_history,
            suppress_nav_fn=lambda s: setattr(self._nav_history, "suppress", s),
            mru_push_fn=self.mru.push,
        )
        if open_file_path is not None:
            logger.info("switch-vault: opening %s in new vault", open_file_path)
            self._open_file(open_file_path)
            if post_open_fn is not None:
                GLib.idle_add(post_open_fn)
        return False  # Remove from idle queue after execution

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

        Delegates to :class:`TabOrchestrator`.
        """
        self._tab_orchestrator.open_tab(
            file_path,
            view_mode=view_mode,
            split_position=split_position,
            editor_zoom=editor_zoom,
            preview_zoom=preview_zoom,
            from_nav=_from_nav,
        )

    # ── Tab callbacks ──────────────────────────────────────────────

    def _on_file_selected_from_tree(self, _tree, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault, open_file_path=file_path)
        else:
            self._open_file(file_path)

    def _on_vault_activated(self, _tree, vault_path: str) -> None:
        """Handle double-click on a vault root in the tree."""
        if vault_path != self._active_vault:
            self._switch_vault(vault_path)

    def _on_vault_added(self, _tree, vault_path: str) -> None:
        """Handle a new vault being added."""
        # R16.2/R17.1: rebuild from the config SSOT (full list), never a
        # single vault — a partial build racing the startup build would
        # replace the whole index (same applies to the root-only FileIndex).
        vaults = config.load_vaults()
        if any(v["path"] == vault_path for v in vaults):
            self._schedule_backlink_build(vaults)
            self._file_index.build(vaults)
        self._dump_debug(["file_index", "vault_tree"])
        self._switch_vault(vault_path)

    def _on_tab_changed(self, _tab_bar, file_path: str) -> None:
        """Handle tab change — delegates to :class:`TabOrchestrator`."""
        self._tab_orchestrator.on_tab_changed(file_path)
        # Keep active vault in sync with the open tab.
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._active_vault = vault
            self._vault_tree.set_active_vault(vault)

    def _on_editor_modified(self, editor: Editor, dirty: bool) -> None:
        """Update the italic indicator on the tab for *dirty*."""
        logger.debug("_on_editor_modified: path=%s dirty=%s",
                      editor.file_path, dirty)
        if editor.file_path:
            self._tab_bar._set_tab_unmodified(editor.file_path, dirty)
        if not dirty and self._sidebar.get_visible():
            self._sidebar._refresh_git(editor.file_path)

    def _on_tab_closed(self, _tab_bar, file_path: str) -> None:
        """Cleanup after a tab has been closed (tab-closed signal)."""
        self.mru.remove(file_path)
        child = self._content_stack.get_child_by_name(file_path)
        if child:
            self._content_stack.remove(child)
        self._update_content_visibility()
        if not self._tab_bar.has_tabs():
            self._sidebar.update_for_file(None)

    def _on_tab_close_requested(self, paths_to_close, on_confirm=None) -> None:
        """Handle tab close request with dirty-check (R4.2).

        Called via TabBar's close_request_callback for both single and
        bulk close.  When *paths_to_close* is a string it is a single
        tab; when it is a list it is a bulk close.
        """
        if isinstance(paths_to_close, str):
            paths_to_close = [paths_to_close]

        logger.info("close: _on_tab_close_requested called (paths=%s, on_confirm=%s)",
                     [Path(p).name for p in paths_to_close], on_confirm is not None)

        # Check if any of the paths are dirty.
        dirty = []
        for path in paths_to_close:
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor:
                logger.debug("close: tab %s is_modified=%s", Path(path).name, tab.editor.is_modified)
                if tab.editor.is_modified:
                    dirty.append(path)
            else:
                logger.debug("close: tab %s tab=None or tab.editor=None", Path(path).name)

        logger.info("close: dirty count = %d", len(dirty))

        if dirty:
            # Bulk close: on_confirm calls _do_close_paths(all_paths).
            # Single close: on_confirm is None → close the tab after save.
            close_cb = on_confirm if on_confirm else lambda: self._do_close_paths(paths_to_close)
            logger.info("close: showing save dialog for %d dirty tab(s)", len(dirty))
            self._show_save_dialog(dirty, close_cb)
            logger.info("close: save dialog returned (user responded)")
        else:
            # All clean — close directly.
            logger.info("close: no dirty tabs, closing directly")
            if on_confirm:
                on_confirm()
            else:
                self._do_close_paths(paths_to_close)

    def _show_save_dialog(self, dirty_paths: list[str], on_confirm=None) -> None:
        """Show aggregated save/discard dialog for *dirty_paths*."""
        logger.info("save-dialog: creating dialog for %d tab(s)", len(dirty_paths))

        def _on_response(resp):
            logger.info("save-dialog: response=%s", resp)
            self._on_save_dialog_response(resp, dirty_paths, on_confirm)

        dialogs.confirm_discard_unsaved(self, dirty_paths, _on_response)
        logger.info("save-dialog: dialog.present() called")

    def _on_save_dialog_response(self, response: str, dirty_paths: list[str], on_confirm) -> None:
        """Handle save/discard/cancel response."""
        if response == "cancel":
            logger.info("save-dialog: user cancelled, tabs remain open (paths=%s)", [Path(p).name for p in dirty_paths])
            if self._close_window_pending:
                self._close_window_pending = False
                self._autosave.restart()
            self._switch_vault_pending = False
            return
        if response == "save":
            failed = self._save_dirty_tabs(dirty_paths)
            if failed:
                logger.warning("save-dialog: save failed for %d tab(s) (paths=%s)",
                               len(failed), [Path(p).name for p in failed])
                body = (
                    f"Could not save {len(failed)} tab(s):\n\n"
                    + "\n".join(f"\u2013 {Path(p).name}" for p in failed)
                )

                def _on_error_dismissed(_dlg=None, _resp=None):
                    if self._close_window_pending:
                        self._close_window_pending = False
                        self._autosave.restart()
                    self._switch_vault_pending = False

                dialogs.show_error(self, "Save Failed", body)
                _on_error_dismissed()
                return
            logger.info("save-dialog: saved %d tab(s) (paths=%s)", len(dirty_paths), [Path(p).name for p in dirty_paths])
        if response == "discard":
            logger.info("save-dialog: discarded %d tab(s) (paths=%s)", len(dirty_paths), [Path(p).name for p in dirty_paths])
        # Proceed with close for save/discard (not cancel).
        if on_confirm:
            on_confirm()
        else:
            # Single-close: on_confirm is None → close directly.
            self._do_close_paths(dirty_paths)

    def _save_dirty_tabs(self, paths: list[str]) -> list[str]:
        """Save all dirty tabs in *paths*.

        Returns a list of paths whose ``save()`` failed (empty on
        success).  Callers must not close tabs that failed to save.
        """
        failed: list[str] = []
        for path in paths:
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor.is_modified:
                self._vault_monitor.skip_next_event(tab.editor.file_path)
                if not tab.editor.save():
                    failed.append(path)
        return failed

    def _do_close_paths(self, paths: list[str]) -> None:
        """Close the given paths (called after dirty-dialog confirmation)."""
        for path in list(paths):
            if path in self._tab_bar.get_all_paths():
                self._tab_bar.close_tab(path)

    def _close_all_tabs_with_dirty_check(self, on_confirm=None) -> None:
        """Close all open tabs with dirty-check (R4.2).

        If any tabs are dirty, an aggregated save/discard dialog is shown.
        *on_confirm* is called after the tabs are closed (Save/Discard path).
        """
        all_paths = self._tab_bar.get_all_paths()
        if not all_paths:
            if on_confirm:
                on_confirm()
            return
        dirty = []
        for path in all_paths:
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor and tab.editor.is_modified:
                dirty.append(path)
        if dirty:
            GLib.idle_add(self._show_save_dialog, dirty, on_confirm)
        else:
            if on_confirm:
                on_confirm()

    def _on_sidebar_file_requested(self, _sidebar, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault, open_file_path=file_path)
        else:
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
            def _scroll():
                tab = self._tab_bar.get_current_tab()
                if tab:
                    tab.editor.scroll_to_line(line_num - 1)
                    text = tab.editor.get_text()
                    tab.preview.scroll_to_line(line_num - 1, text)
            self._switch_vault(vault, open_file_path=file_path, post_open_fn=_scroll)
        else:
            self._open_file(file_path)
            tab = self._tab_bar.get_current_tab()
            if tab:
                tab.editor.scroll_to_line(line_num - 1)
                text = tab.editor.get_text()
                tab.preview.scroll_to_line(line_num - 1, text)

    def _on_preview_link_clicked(self, _preview, file_path: str) -> None:
        vault = self._find_vault_for_file(file_path)
        if vault and vault != self._active_vault:
            self._switch_vault(vault, open_file_path=file_path)
        else:
            self._open_file(file_path)

    # Matches a Markdown checkbox on a list line (group 4 = state: space or x/X).
    _CHECKBOX_RE = re.compile(r'^(>\s*)*(\s*)([-*+]|\d+\.)\s+\[([ xX])\]')

    def _on_preview_checkbox_toggled(self, preview, line: int, checked: bool) -> None:
        """Handle checkbox toggle — flip the checkbox at the given source line."""
        logger.debug("Checkbox toggled: line=%s checked=%s", line, checked)
        # Resolve the tab from the emitting preview, not the current tab (R7.4).
        tab = None
        for t in self._tab_bar._tabs.values():
            if t.preview is preview:
                tab = t
                break
        if not tab or not tab.editor.file_path:
            return

        text = tab.editor.get_text()
        lines = text.split('\n')

        if line < 0 or line >= len(lines):
            logger.debug("Checkbox line %s out of range (total %s)", line, len(lines))
            return

        original_line = lines[line]
        match = self._CHECKBOX_RE.match(original_line)
        if not match:
            logger.debug("Line %s is not a checkbox line", line)
            return

        new_state = "x" if checked else " "
        old_state = match.group(4)
        if old_state.lower() == new_state:
            return

        new_line = (
            original_line[:match.start(4)]
            + new_state
            + original_line[match.end(4):]
        )

        # Replace the line in the buffer (undoable via begin_user_action).
        buffer = tab.editor._buffer
        _ok, line_start = buffer.get_iter_at_line(line)
        _ok, line_end = buffer.get_iter_at_line(line)
        line_end.forward_to_line_end()
        buffer.begin_user_action()
        buffer.delete(line_start, line_end)
        buffer.insert(line_start, new_line)
        buffer.end_user_action()

        logger.debug("Checkbox toggled on line %s: %s -> %s", line, old_state, new_state)

        # Schedule preview refresh if visible.
        if tab.preview.get_visible():
            self._schedule_preview_refresh()

        # Update sidebar if visible.
        if self._sidebar.get_visible():
            self._sidebar.update_text_only(tab.editor.file_path, tab.editor.get_text())

    def _on_preview_link_not_found(self, _preview, path_str: str) -> None:
        """Show a dialog when a wikilink cannot be resolved."""
        dialogs.show_link_not_found(self, self._wikilink_display_name(path_str))

    def _wikilink_display_name(self, uri: str) -> str:
        """Render a user-friendly target name from a ``vault:`` URI."""
        if not uri.startswith("vault:"):
            return uri
        vault, rel, _fragment = path_utils.parse_wikilink_url(uri)
        if not rel:
            return vault
        return f"{vault}>{rel}"

    # ── Vault tree file operations ───────────────────────────────

    def _on_new_file_requested(self, _tree, parent_dir: str) -> None:
        """Handle 'New File' from the vault tree context menu."""
        self._file_manager.prompt_new_file(self, None, parent_dir)

    def _on_new_folder_requested(self, _tree, parent_dir: str) -> None:
        """Handle 'New Folder' from the vault tree context menu."""
        self._file_manager.prompt_new_folder(self, parent_dir)

    def _show_error(self, heading: str, body: str) -> None:
        """Show an error dialog with the given message."""
        dialogs.show_error(self, heading, body)

    def _on_delete_requested(self, _tree, path: str) -> None:
        """Handle 'Delete' from the vault tree context menu."""
        self._file_manager.prompt_delete(self, path)

    def _on_close_file_requested(self, _tree, file_path: str) -> None:
        """Handle 'Close File' from the vault tree context menu."""
        if file_path in self._tab_bar.get_all_paths():
            self._tab_bar._on_close_button_clicked(file_path)

    def _on_file_renamed(self, _tree, old_path: str, new_path: str) -> None:
        """Handle file/folder rename from the vault tree."""
        # Update wikilinks in other files BEFORE index update
        self._backlink_index.rename_wikilinks(old_path, new_path)
        self._backlink_index.rename_file(old_path, new_path)
        self._file_index.rename_file(old_path, new_path)
        self._dump_debug(["file_index", "backlink_index", "vault_tree", "tabs"])
        # Update all open tabs whose path starts with old_path (dir rename).
        for tab_path in list(self._tab_bar.get_all_paths()):
            if tab_path == old_path or tab_path.startswith(old_path + os.sep):
                new_tab_path = new_path + tab_path[len(old_path):]
                self._tab_bar.update_path(tab_path, new_tab_path)

        # Update nav history.
        self._nav_history.remap_paths(old_path, new_path)

        # Update MRU — use in-place rename to preserve order.
        for tab_path in list(self.mru.tabs):
            if tab_path == old_path or tab_path.startswith(old_path + os.sep):
                new_tab_path = new_path + tab_path[len(old_path):]
                self.mru.rename(tab_path, new_tab_path)

        self._refresh_sidebar_backlinks()

    def _on_vault_renamed(self, _tree, _vault_path: str, new_name: str) -> None:
        """Handle vault rename from the vault tree."""
        # Vault path unchanged — no index updates needed.
        logger.info("Vault renamed: %s → %s", _vault_path, new_name)

    def _on_vault_removed(self, _tree, vault_path: str) -> None:
        """Handle vault removal from the vault tree."""
        logger.info("Vault removed: %s", vault_path)
        # Close all tabs belonging to the removed vault.
        for tab_path in list(self._tab_bar.get_all_paths()):
            if tab_path.startswith(vault_path + os.sep) or tab_path == vault_path:
                self._tab_bar._on_close_button_clicked(tab_path)
        # Remove vault from vault monitor.
        self._vault_monitor.remove_vault(vault_path)
        # Purge backlink/file index entries.
        self._backlink_index.remove_vault(vault_path)
        self._file_index.remove_vault(vault_path)

    def _on_focus_current_file_clicked(self, _tree) -> None:
        """Focus the current file in the vault tree."""
        current_path = self._tab_bar.get_current_path()
        if current_path:
            self._vault_tree.focus_file(current_path)

    def _on_tab_renamed(self, _tab_bar, old_path: str, new_path: str) -> None:
        """Handle tab path change — update the content stack key."""
        child = self._content_stack.get_child_by_name(old_path)
        if child:
            self._content_stack.remove(child)
            self._content_stack.add_named(child, new_path)
            if self._tab_bar.get_current_path() == new_path:
                self._content_stack.set_visible_child_name(new_path)

        # Sync the unmodified indicator with the renamed tab.
        tab = self._tab_bar.get_tab(new_path)
        if tab:
            self._tab_bar._set_tab_unmodified(new_path, tab.editor.is_modified)

        # Defer view-mode and preview update so the stack re-layout completes first.
        def _deferred():
            t = self._tab_bar.get_tab(new_path)
            if t:
                t.preview.reset()
                self._apply_view_mode()
                self._refresh_preview()
            return False
        GLib.idle_add(_deferred)

    # ── Navigation history ─────────────────────────────────────────

    def _push_history(self, file_path: str) -> None:
        """Append *file_path* to the navigation history — delegates to
        :class:`InputManager`."""
        self._input_manager.push_history(file_path)

    def _nav_back(self) -> None:
        """Navigate back — delegates to :class:`InputManager`."""
        self._input_manager.nav_back()

    def _nav_forward(self) -> None:
        """Navigate forward — delegates to :class:`InputManager`."""
        self._input_manager.nav_forward()

    def _update_nav_buttons(self) -> None:
        """Update navigation button state — delegates to :class:`InputManager`."""
        self._input_manager.update_nav_buttons()

    def _next_tab(self) -> None:
        """Switch to the next tab — delegates to :class:`TabOrchestrator`."""
        self._tab_orchestrator.next_tab()

    def _prev_tab(self) -> None:
        """Switch to the previous tab — delegates to :class:`TabOrchestrator`."""
        self._tab_orchestrator.prev_tab()

    def _mru_next(self) -> None:
        """Ctrl+Tab: switch to the previously active tab (Alt+Tab style)."""
        self._tab_orchestrator._mru_next()

    def _mru_prev(self) -> None:
        """Ctrl+Shift+Tab: switch forward in MRU list."""
        self._tab_orchestrator._mru_prev()

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
        """Cycle through tabs — delegates to :class:`TabOrchestrator`."""
        self._tab_orchestrator.cycle_tab(direction)

    def _apply_keybindings(self) -> None:
        """Set application accelerators and dynamic tab shortcuts — delegates
        to :class:`InputManager`."""
        self._input_manager.apply_keybindings(
            tab_shortcut_ctrl=self._tab_shortcut_ctrl,
            tab_shortcuts=self._tab_shortcuts,
        )

    # ── View mode ──────────────────────────────────────────────────

    def _on_view_mode_toggled(self, toggle_btn: Gtk.ToggleButton) -> None:
        if not self._setup_complete:
            return
        if not toggle_btn.get_active():
            return
        self._view_mode_manager.set_view_mode(toggle_btn._mode)  # type: ignore[attr-defined]

    def _apply_view_mode(self) -> None:
        self._view_mode_manager.apply_view_mode()

    def _sync_view_toggle(self, mode: str) -> None:
        self._view_mode_manager.sync_view_toggle(mode)

    def _set_view_mode(self, mode: str) -> None:
        self._view_mode_manager.set_view_mode(mode)

    # ── Editor callbacks ────────────────────────────────────────────

    def _on_editor_text_changed(self, editor: Editor) -> None:
        self._view_mode_manager.on_editor_text_changed(editor)

    # ── Preview ────────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        self._view_mode_manager.refresh_preview()

    # ── Misc ───────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        visible = self._sidebar.get_visible()
        self._sidebar.set_visible(not visible)
        self._sidebar_toggle.set_active(not visible)

    def _on_sidebar_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._sidebar.set_visible(btn.get_active())

    def _get_active_tab_info(self) -> tuple[str | None, str]:
        """Return ``(file_path, text)`` for the currently active tab."""
        tab = self._tab_bar.get_current_tab()
        if tab is not None and tab.editor is not None:
            return (tab.editor.file_path, tab.editor.get_text())
        return (None, "")

    def _refresh_sidebar_backlinks(self) -> None:
        """Refresh sidebar for the currently active tab."""
        tab = self._tab_bar.get_current_tab()
        if tab is not None and tab.editor is not None:
            self._sidebar.update_for_file(
                tab.editor.file_path, tab.editor.get_text(),
            )

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

    def _clamp_sidebar_position(self, paned: Gtk.Paned, _pspec) -> None:
        if self._paned_clamping:
            return
        width = paned.get_allocated_width()
        if width <= 0:
            return
        pos = paned.get_position()
        max_pos = width - 20
        if pos > max_pos:
            self._paned_clamping = True
            paned.set_position(max_pos)
            self._paned_clamping = False

    def _clamp_search_position(self, paned: Gtk.Paned, _pspec) -> None:
        if self._paned_clamping:
            return
        height = paned.get_allocated_height()
        if height <= 0:
            return
        pos = paned.get_position()
        max_pos = height - 20
        if pos > max_pos:
            self._paned_clamping = True
            paned.set_position(max_pos)
            self._paned_clamping = False

    def _save_current(self) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        self._vault_monitor.skip_next_event(tab.editor.file_path)
        if tab.editor.save():
            self._tab_bar.clear_tab_error(tab.file_path)
            self._tab_bar.hide_error_banner(tab.file_path)
        else:
            msg = f'Could not save "{Path(tab.file_path).name}"'
            self._tab_bar.set_tab_error(tab.file_path, "save_error", msg)
            self._tab_bar.show_error_banner(
                tab.file_path, msg,
                buttons=[("Dismiss", lambda: self._tab_bar.hide_error_banner(tab.file_path))],
            )
            dialogs.show_error(self, "Save Failed", msg)

    def _close_current_tab(self) -> None:
        path = self._tab_bar.get_current_path()
        if path:
            self._tab_bar._on_close_button_clicked(path)

    # ── Session persistence ────────────────────────────────────────

    def _get_window_state(self) -> dict:
        """Return window geometry and layout state for session saving."""
        return {
            "width": self.get_width(),
            "height": self.get_height(),
            "sidebar_visible": self._sidebar.get_visible(),
            "expanded_vaults": self._vault_tree.get_expanded_paths(),
            "search_visible": self._search_bar.get_visible(),
            "search_paned_position": self._search_paned.get_position(),
            "sidebar_paned_position": self._sidebar_paned.get_position(),
            "main_paned_position": self._main_paned.get_position(),
        }

    def _on_close_request(self, *_args) -> bool:
        """Dirty-check before the window closes (R6.2).

        Returns ``True`` to hold the close while a save/discard dialog
        is open.  On discard or successful save the surface is destroyed
        explicitly; on cancel or save-failure the close is aborted.
        """
        if self._close_window_pending:
            return True
        self._autosave.cancel()
        self._view_mode_manager.cancel_preview_debounce()
        self._cancel_backlink_rebuild()

        # Collect dirty tabs across all open files.
        all_paths = self._tab_bar.get_all_paths()
        dirty = []
        for path in all_paths:
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor and tab.editor.is_modified:
                dirty.append(path)

        if not dirty:
            self._vault_monitor.cleanup()
            self._session_mgr.save_session(self._active_vault, self._content_stack)
            return False  # No unsaved changes → allow close.

        # Show the async dialog; hold the close until the user responds.
        self._close_window_pending = True
        GLib.idle_add(self._show_save_dialog, dirty,
                      lambda: self._on_close_request_confirmed())
        return True

    def _on_close_request_confirmed(self) -> None:
        """Called after the user chose Discard or Save (no failures)."""
        self._vault_monitor.cleanup()
        self._session_mgr.save_session(self._active_vault, self._content_stack)
        self.get_surface().destroy()

    # ── Autosave ───────────────────────────────────────────────────

    def _get_autosave_dirty_tabs(self) -> list:
        """Return all tabs whose editor buffer is modified."""
        dirty = []
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if tab and tab.editor.is_modified:
                dirty.append(tab)
        return dirty

    def _autosave_save_tab(self, tab) -> bool:
        """Save a single tab and notify the vault monitor. Returns True on success."""
        self._vault_monitor.skip_next_event(tab.editor.file_path)
        return tab.editor.save()

    def _autosave_on_failed(self, file_path: str, msg: str) -> None:
        """Handle autosave failure — show error banner and dialog."""
        self._tab_bar.set_tab_error(file_path, "save_error", msg)
        self._tab_bar.show_error_banner(
            file_path, msg,
            buttons=[("Dismiss", lambda: self._tab_bar.hide_error_banner(file_path))],
        )
        dialogs.show_error(self, "Save Failed", msg)

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
        try:
            config.check_config_access()
        except OSError as e:
            self._show_error("Cannot Open Preferences", str(e))
            return
        dlg = PreferencesDialog(
            glib_loglevel_callback=logging_setup.update_glib_loglevel,
        )
        dlg.connect("settings-changed", self._on_preferences_changed)
        dlg.present(self)

    def _on_preferences_changed(self, _dlg) -> None:
        self._settings = config.load_settings()
        self._apply_keybindings()
        self._tab_bar.set_tab_min_width(self._settings.get("tab_min_width", 100))
        # Update debug toggle visibility and state.
        is_debug = self._settings.get("loglevel", "info") == "debug"
        self._debug_toggle.set_visible(is_debug)
        if is_debug:
            self._debug_toggle.set_active(self._settings.get("debug_active", False))
        elif self._debug_toggle.get_active():
            self._debug_toggle.set_active(False)
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
        self._autosave.update_interval(self._settings.get("autosave_interval", 30))

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
        return ox <= px < ox + tab.preview.get_width() and oy <= py < oy + tab.preview.get_height()

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

    # ── AppWindow alias (for tests) ────────────────────────────────


AppWindow = MainWindow
