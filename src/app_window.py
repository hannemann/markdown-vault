"""Markdown Vault — main application window.

Assembles the three-panel layout (vault tree | editor/preview | sidebar),
the tab bar, and the bottom search bar.  Each open file gets its own
``Editor`` and ``Preview`` instance so that buffer state and scroll
position are preserved across tab switches.

Dark mode is controlled via ``Adw.StyleManager`` and exposed through
the hamburger menu.
"""

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

        self._view_mode: str = "edit"
        self._setup_complete = False
        self._autosave_id: int | None = None
        self._view_toggle_buttons: dict[str, Gtk.ToggleButton] = {}
        self._active_vault: str | None = None
        self._settings = config.load_settings()

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
        main_paned.set_start_child(self._vault_tree)
        main_paned.set_resize_start_child(True)
        main_paned.set_shrink_start_child(False)

        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._tab_bar = TabBar()
        self._tab_bar.connect("tab-changed", self._on_tab_changed)
        self._tab_bar.connect("tab-closed", self._on_tab_closed)
        centre.append(self._tab_bar)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

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
        outer.append(self._sidebar)

        root.append(outer)

        self._search_bar = SearchBar(get_vault_paths=self._vault_tree.get_vault_paths)
        self._search_bar.connect("file-selected", self._on_search_result_selected)
        root.append(self._search_bar)

        self._register_actions()
        self._load_vaults()

        # Restore session: sidebar, tabs, active tab, expanded vaults.
        self._sidebar.set_visible(_ses.get("sidebar_visible", False))
        for tab_data in _ses.get("tabs", []):
            fp = tab_data.get("path", "")
            if fp and Path(fp).exists():
                self._open_file(
                    fp,
                    view_mode=tab_data.get("view_mode", "edit"),
                    split_position=tab_data.get("split_position", 600),
                    editor_zoom=tab_data.get("editor_zoom", 1.0),
                    preview_zoom=tab_data.get("preview_zoom", 1.0),
                )
        active = _ses.get("active_tab")
        if active and active in self._tab_bar.get_all_paths():
            self._tab_bar.set_active_tab(active)
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

        # Hamburger menu (right side).
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
        action_section.append("Full-Text Search", "win.toggle-search")
        menu.append_section(None, action_section)

        prefs_section = Gio.Menu()
        prefs_section.append("Preferences", "win.preferences")
        menu.append_section(None, prefs_section)

        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

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

    # ── New file ───────────────────────────────────────────────────

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

        # Use the active vault, or fall back to the most recently added.
        default_dir = self._active_vault
        if not default_dir or default_dir not in vaults:
            default_dir = vaults[-1]

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

    # ── File opening ───────────────────────────────────────────────

    def _open_file(
        self,
        file_path: str,
        *,
        view_mode: str = "edit",
        split_position: int = 600,
        editor_zoom: float = 1.0,
        preview_zoom: float = 1.0,
    ) -> None:
        """Open *file_path* in a new or existing tab."""
        for path in self._tab_bar.get_all_paths():
            if path == file_path:
                self._tab_bar.set_active_tab(file_path)
                return

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

    # ── Tab callbacks ──────────────────────────────────────────────

    def _on_file_selected_from_tree(self, _tree, file_path: str) -> None:
        self._active_vault = str(Path(file_path).parent)
        self._open_file(file_path)

    def _on_tab_changed(self, _tab_bar, file_path: str) -> None:
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        self._content_stack.set_visible_child_name(file_path)
        self._sync_view_toggle(tab.view_mode)
        self._apply_view_mode()
        self._refresh_preview()
        self._sidebar.update_for_file(file_path, tab.editor.get_text())

    def _on_tab_closed(self, _tab_bar, file_path: str) -> None:
        child = self._content_stack.get_child_by_name(file_path)
        if child:
            self._content_stack.remove(child)
        self._update_content_visibility()
        if not self._tab_bar.has_tabs():
            self._sidebar.update_for_file(None)

    def _on_sidebar_file_requested(self, _sidebar, file_path: str) -> None:
        self._open_file(file_path)

    def _on_search_result_selected(self, _search_bar, file_path: str) -> None:
        self._open_file(file_path)

    def _on_preview_link_clicked(self, _preview, file_path: str) -> None:
        self._open_file(file_path)

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
        alloc = self.get_allocation()
        session.save_session(
            width=alloc.width,
            height=alloc.height,
            sidebar_visible=self._sidebar.get_visible(),
            tabs=self._collect_tab_data(),
            active_tab=self._tab_bar.get_current_path(),
            expanded_vaults=self._vault_tree.get_expanded_paths(),
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
