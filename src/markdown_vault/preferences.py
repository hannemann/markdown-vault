"""Markdown Vault — preferences dialog.

Provides an ``Adw.PreferencesDialog`` for editing application settings
such as autosave interval, editor appearance, and default view mode.
Changes are applied immediately and persisted to ``vaults.yaml``.
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, GObject, Gdk

from . import config
from . import dialogs

_VIEW_MODES = {"edit": "Edit", "render": "Render", "split": "Split"}
_LOGLEVELS = {"debug": "Debug", "info": "Info", "warning": "Warning", "error": "Error"}
_GLIB_LOGLEVELS = {
    "all": "All (debug+)",
    "warning": "Warning and up",
    "critical": "Critical and up",
    "error": "Error only",
}
_LOGLEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _accel_to_label(accel: str) -> str:
    """Convert a GTK accelerator string to a human-readable label."""
    if not accel:
        return "None"
    ok, keyval, mods = Gtk.accelerator_parse(accel)
    if not ok or keyval == 0:
        return accel
    parts = []
    if mods & Gdk.ModifierType.SHIFT_MASK:
        parts.append("Shift")
    if mods & Gdk.ModifierType.CONTROL_MASK:
        parts.append("Ctrl")
    if mods & Gdk.ModifierType.ALT_MASK:
        parts.append("Alt")
    if mods & Gdk.ModifierType.SUPER_MASK:
        parts.append("Super")
    key_name = Gdk.keyval_name(keyval)
    if key_name:
        parts.append(key_name.capitalize())
    return "+".join(parts)


_RELEVANT_MODS = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


class PreferencesDialog(Adw.PreferencesDialog):
    """Application preferences dialog.

    Signals:
        settings-changed(): Emitted whenever a setting is modified.
    """

    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, *, glib_loglevel_callback=None) -> None:
        """Initialize preferences dialog.

        *glib_loglevel_callback* is an optional callable that is invoked
        when the GLib log level changes (allows live reconfiguration
        without restart).
        """
        super().__init__(title="Preferences")

        self._settings = config.load_settings()
        self._glib_loglevel_callback = glib_loglevel_callback

        # ── General page ────────────────────────────────────────────
        general = Adw.PreferencesPage(title="General", icon_name="preferences-other-symbolic")

        # Autosave group.
        autosave_group = Adw.PreferencesGroup(title="Autosave")
        general.add(autosave_group)

        self._autosave_row = Adw.SpinRow(
            title="Autosave interval (seconds)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("autosave_interval", 30), 0, 600, 5, 10, 0,
            ),
        )
        self._autosave_row.connect("notify::value", self._on_autosave_changed)
        autosave_group.add(self._autosave_row)

        # Default view mode group.
        view_group = Adw.PreferencesGroup(title="Default View Mode")
        general.add(view_group)

        self._view_row = Adw.ComboRow(
            title="View mode for new tabs",
            model=Gtk.StringList.new(list(_VIEW_MODES.values())),
        )
        modes = list(_VIEW_MODES.keys())
        current_mode = self._settings.get("default_view_mode", "edit")
        self._view_row.set_selected(modes.index(current_mode) if current_mode in modes else 0)
        self._view_row.connect("notify::selected", self._on_view_mode_changed)
        view_group.add(self._view_row)

        self.add(general)

        # ── Editor page ─────────────────────────────────────────────
        editor = Adw.PreferencesPage(title="Editor", icon_name="document-edit-symbolic")

        font_group = Adw.PreferencesGroup(title="Font &amp; Layout")
        editor.add(font_group)

        self._font_row = Adw.SpinRow(
            title="Font size",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("editor_font_size", 14), 8, 72, 1, 5, 0,
            ),
        )
        self._font_row.connect("notify::value", self._on_font_size_changed)
        font_group.add(self._font_row)

        self._tab_row = Adw.SpinRow(
            title="Tab width",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("editor_tab_width", 4), 1, 16, 1, 4, 0,
            ),
        )
        self._tab_row.connect("notify::value", self._on_tab_width_changed)
        font_group.add(self._tab_row)

        self._wrap_row = Adw.SwitchRow(title="Word wrap")
        self._wrap_row.set_active(self._settings.get("editor_wrap_text", True))
        self._wrap_row.connect("notify::active", self._on_wrap_changed)
        font_group.add(self._wrap_row)

        # Tabs group.
        tabs_group = Adw.PreferencesGroup(title="Tabs")
        editor.add(tabs_group)

        self._tab_width_row = Adw.SpinRow(
            title="Minimum tab width (px)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("tab_min_width", 150), 50, 300, 10, 50, 0,
            ),
        )
        self._tab_width_row.connect("notify::value", self._on_tab_min_width_changed)
        tabs_group.add(self._tab_width_row)

        self._tab_wrap_row = Adw.SwitchRow(title="Wrap tabs")
        self._tab_wrap_row.set_active(self._settings.get("tab_wrap", False))
        self._tab_wrap_row.connect("notify::active", self._on_tab_wrap_changed)
        tabs_group.add(self._tab_wrap_row)

        # Wikilinks group.
        wikilink_group = Adw.PreferencesGroup(
            title="Wikilinks",
            description="Autofix and validation of [[wikilinks]] when saving.",
        )
        editor.add(wikilink_group)

        self._wl_rows: dict[str, Adw.SwitchRow] = {}
        for key, title, subtitle in (
            ("wikilink_autofix_normalize", "Normalize on save",
             "Trim whitespace inside [[…]] when saving."),
            ("wikilink_autofix_relink", "Auto-fix moved links",
             "Redirect a broken link when exactly one matching file exists."),
            ("wikilink_warn_on_save", "Warn about broken links",
             "After saving, show a notice for links that can't be resolved."),
            ("wikilink_mark_broken", "Mark broken links in the editor",
             "Gutter warning triangle and red underline on unresolved links."),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(self._settings.get(key, False))
            row.connect("notify::active", self._on_wikilink_toggle_changed, key)
            wikilink_group.add(row)
            self._wl_rows[key] = row

        self.add(editor)

        # ── Preview page ────────────────────────────────────────────
        preview = Adw.PreferencesPage(title="Preview", icon_name="document-properties-symbolic")

        zoom_group = Adw.PreferencesGroup(title="Zoom")
        preview.add(zoom_group)

        zoom_value = self._settings.get("preview_zoom", 1.0)
        self._zoom_row = Adw.SpinRow(
            title="Default zoom level",
            adjustment=Gtk.Adjustment.new(zoom_value, 0.25, 5.0, 0.05, 0.25, 0),
            digits=2,
        )
        self._zoom_row.connect("notify::value", self._on_zoom_changed)
        zoom_group.add(self._zoom_row)

        privacy_group = Adw.PreferencesGroup(title="Privacy")
        preview.add(privacy_group)

        self._remote_images_row = Adw.SwitchRow(
            title="Allow remote images",
            subtitle=(
                "Load images from https:// URLs in notes. Off blocks them so a "
                "note cannot beacon your IP; scripts stay blocked either way."
            ),
        )
        self._remote_images_row.set_active(
            self._settings.get("preview_allow_remote_images", False)
        )
        self._remote_images_row.connect(
            "notify::active", self._on_remote_images_changed,
        )
        privacy_group.add(self._remote_images_row)

        self.add(preview)

        # ── Web page ────────────────────────────────────────────────
        web = Adw.PreferencesPage(
            title="Web", icon_name="applications-internet-symbolic",
        )

        web_group = Adw.PreferencesGroup(title="WebKit Rendering")
        web.add(web_group)

        self._dmabuf_row = Adw.SwitchRow(title="Disable DMA-BUF renderer")
        self._dmabuf_row.subtitle = (
            "Lowers GPU/video memory usage "
            "(WEBKIT_DISABLE_DMABUF_RENDERER). Takes effect after restart."
        )
        self._dmabuf_row.set_active(
            self._settings.get("webkit_disable_dmabuf", False),
        )
        self._dmabuf_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit_disable_dmabuf",
        )
        web_group.add(self._dmabuf_row)

        self._compositing_row = Adw.SwitchRow(
            title="Disable hardware acceleration",
        )
        self._compositing_row.subtitle = (
            "Render without the GPU (WEBKIT_DISABLE_COMPOSITING_MODE). "
            "Takes effect after restart."
        )
        self._compositing_row.set_active(
            self._settings.get("webkit_disable_compositing", False),
        )
        self._compositing_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit_disable_compositing",
        )
        web_group.add(self._compositing_row)

        self.add(web)

        # ── Search page ────────────────────────────────────────────
        search = Adw.PreferencesPage(title="Search", icon_name="edit-find-symbolic")
        sem_group = Adw.PreferencesGroup(
            title="Semantic search",
            description=(
                "Find notes by meaning via a local Ollama server. Off by "
                "default; nothing is downloaded or contacted while disabled. "
                "Run e.g. ‘ollama pull nomic-embed-text’ first. "
                "Changes take effect after restart."
            ),
        )
        search.add(sem_group)

        self._sem_enabled_row = Adw.SwitchRow(title="Enable semantic search")
        self._sem_enabled_row.set_active(
            self._settings.get("semantic_search_enabled", False))
        self._sem_enabled_row.connect(
            "notify::active", self._on_toggle_setting, "semantic_search_enabled")
        sem_group.add(self._sem_enabled_row)

        self._sem_url_row = Adw.EntryRow(title="Ollama URL")
        self._sem_url_row.set_text(
            self._settings.get("semantic_ollama_url", "http://localhost:11434"))
        self._sem_url_row.connect("changed", self._on_entry_setting, "semantic_ollama_url")
        sem_group.add(self._sem_url_row)

        self._sem_model_row = Adw.EntryRow(title="Embedding model")
        self._sem_model_row.set_text(
            self._settings.get("semantic_ollama_model", "nomic-embed-text"))
        self._sem_model_row.connect("changed", self._on_entry_setting, "semantic_ollama_model")
        sem_group.add(self._sem_model_row)

        self._sem_score_row = Adw.SpinRow(
            title="Minimum similarity",
            subtitle="Higher = stricter (fewer, closer matches)",
            adjustment=Gtk.Adjustment.new(
                self._settings.get("semantic_min_score", 0.35), 0.0, 1.0, 0.05, 0.1, 0.0,
            ),
            digits=2,
        )
        self._sem_score_row.connect("notify::value", self._on_min_score_changed)
        sem_group.add(self._sem_score_row)

        self.add(search)

        # ── Keyboard page ──────────────────────────────────────────
        keyboard = Adw.PreferencesPage(title="Keyboard", icon_name="input-keyboard-symbolic")

        kb_group = Adw.PreferencesGroup(title="Keybindings")
        keyboard.add(kb_group)

        self._next_tab_row = Adw.ActionRow(title="Next tab")
        self._next_tab_btn = Gtk.Button()
        self._next_tab_btn.add_css_class("flat")
        self._next_tab_btn.set_valign(Gtk.Align.CENTER)
        self._setup_keybinding_button(
            self._next_tab_btn, "keybinding_next_tab",
        )
        self._next_tab_row.add_suffix(self._next_tab_btn)
        kb_group.add(self._next_tab_row)

        self._prev_tab_row = Adw.ActionRow(title="Previous tab")
        self._prev_tab_btn = Gtk.Button()
        self._prev_tab_btn.add_css_class("flat")
        self._prev_tab_btn.set_valign(Gtk.Align.CENTER)
        self._setup_keybinding_button(
            self._prev_tab_btn, "keybinding_prev_tab",
        )
        self._prev_tab_row.add_suffix(self._prev_tab_btn)
        kb_group.add(self._prev_tab_row)

        switch_group = Adw.PreferencesGroup(title="Tab switching")
        keyboard.add(switch_group)

        self._mode_row = Adw.ComboRow(
            title="Tab switch behaviour",
            subtitle="MRU switches to the most recently used tab, Cycle goes in order",
            model=Gtk.StringList.new(["Most Recently Used", "Cycle in Order"]),
        )
        current_mode = self._settings.get("tab_switch_mode", "mru")
        self._mode_row.set_selected(0 if current_mode == "mru" else 1)
        self._mode_row.connect("notify::selected", self._on_tab_switch_mode_changed)
        switch_group.add(self._mode_row)

        self.add(keyboard)

        # ── Debug page ──────────────────────────────────────────────
        debug = Adw.PreferencesPage(title="Debug", icon_name="utilities-system-monitor-symbolic")

        log_group = Adw.PreferencesGroup(title="Logging")
        debug.add(log_group)

        self._loglevel_row = Adw.ComboRow(
            title="Log level",
            model=Gtk.StringList.new(list(_LOGLEVELS.values())),
        )
        current_level = self._settings.get("loglevel", "info")
        levels = list(_LOGLEVELS.keys())
        self._loglevel_row.set_selected(
            levels.index(current_level) if current_level in levels else 1
        )
        self._loglevel_row.connect("notify::selected", self._on_loglevel_changed)
        log_group.add(self._loglevel_row)

        self._tp_loglevel_row = Adw.ComboRow(
            title="Third-party log level",
            subtitle="markdown, pymdownx, pygments, xml",
            model=Gtk.StringList.new(list(_LOGLEVELS.values())),
        )
        tp_level = self._settings.get("third_party_loglevel", "warning")
        self._tp_loglevel_row.set_selected(
            levels.index(tp_level) if tp_level in levels else 2
        )
        self._tp_loglevel_row.connect("notify::selected", self._on_tp_loglevel_changed)
        log_group.add(self._tp_loglevel_row)

        # GLib log level (for Gtk/WebKit/GJS messages).
        self._glib_loglevel_row = Adw.ComboRow(
            title="GLib log level",
            subtitle="Gtk, WebKit, Gjs messages",
            model=Gtk.StringList.new(list(_GLIB_LOGLEVELS.values())),
        )
        glib_level = self._settings.get("glib_loglevel", "critical")
        glib_levels = list(_GLIB_LOGLEVELS.keys())
        self._glib_loglevel_row.set_selected(
            glib_levels.index(glib_level) if glib_level in glib_levels
            else glib_levels.index("warning")
        )
        self._glib_loglevel_row.connect("notify::selected", self._on_glib_loglevel_changed)
        log_group.add(self._glib_loglevel_row)

        # Debug dump toggles (only useful in debug mode).
        dump_group = Adw.PreferencesGroup(title="Debug Dumps")
        debug.add(dump_group)

        self._dump_toggles: dict[str, Adw.SwitchRow] = {}
        for key, title in (
            ("file_index", "File Index"),
            ("backlink_index", "Backlink Index"),
            ("preview_html", "Preview HTML"),
            ("vault_tree", "Vault Tree"),
            ("tabs", "Tabs"),
            ("sidebar", "Sidebar"),
        ):
            row = Adw.SwitchRow(title=title)
            row.set_active(self._settings.get(f"debug_dump_{key}", False))
            row.connect("notify::active", self._on_dump_toggle_changed, key)
            dump_group.add(row)
            self._dump_toggles[key] = row

        self.add(debug)

    # ── Handlers ────────────────────────────────────────────────────

    def _on_toggle_setting(self, row, _pspec, key) -> None:
        self._settings[key] = row.get_active()
        self._persist()

    def _on_entry_setting(self, row, key) -> None:
        self._settings[key] = row.get_text().strip()
        self._persist()

    def _on_min_score_changed(self, _row, _pspec) -> None:
        self._settings["semantic_min_score"] = round(
            self._sem_score_row.get_adjustment().get_value(), 2)
        self._persist()

    def _persist(self) -> None:
        try:
            config.save_settings(self._settings)
        except OSError as e:
            import logging
            logging.getLogger(__name__).error("Failed to save settings: %s", e)
            dialogs.show_error(self.get_root(), "Save Failed", str(e))
            return
        self.emit("settings-changed")

    def _on_autosave_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["autosave_interval"] = int(self._autosave_row.get_adjustment().get_value())
        self._persist()

    def _on_view_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        modes = list(_VIEW_MODES.keys())
        idx = row.get_selected()
        if idx < len(modes):
            self._settings["default_view_mode"] = modes[idx]
            self._persist()

    def _on_font_size_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["editor_font_size"] = int(self._font_row.get_adjustment().get_value())
        self._persist()

    def _on_tab_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["editor_tab_width"] = int(self._tab_row.get_adjustment().get_value())
        self._persist()

    def _on_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._settings["editor_wrap_text"] = switch.get_active()
        self._persist()

    def _on_tab_min_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["tab_min_width"] = int(self._tab_width_row.get_adjustment().get_value())
        self._persist()

    def _on_tab_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._settings["tab_wrap"] = switch.get_active()
        self._persist()

    def _on_zoom_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["preview_zoom"] = round(self._zoom_row.get_adjustment().get_value(), 2)
        self._persist()

    def _on_remote_images_changed(self, switch: Adw.SwitchRow, _pspec) -> None:
        self._settings["preview_allow_remote_images"] = switch.get_active()
        self._persist()

    def _on_glib_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        levels = list(_GLIB_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["glib_loglevel"] = levels[idx]
            self._persist()
            # Notify caller to reconfigure GLib logging (live).
            if self._glib_loglevel_callback:
                self._glib_loglevel_callback(levels[idx])

    # ── Keybinding capture ──────────────────────────────────────────

    def _setup_keybinding_button(
        self, button: Gtk.Button, setting_key: str,
    ) -> None:
        """Configure *button* to capture and display a keyboard shortcut."""
        button._setting_key = setting_key  # type: ignore[attr-defined]
        button._capturing = False  # type: ignore[attr-defined]
        button._key_controller = None  # type: ignore[attr-defined]
        self._update_keybinding_button(button)
        button.connect("clicked", self._on_keybinding_clicked)

    def _update_keybinding_button(self, button: Gtk.Button) -> None:
        accel = self._settings.get(button._setting_key, "")
        button.set_label(_accel_to_label(accel))

    def _on_keybinding_clicked(self, button: Gtk.Button) -> None:
        if button._capturing:
            return
        button._capturing = True
        button.set_label("Press shortcut...")
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_keybinding_key_pressed, button)
        button.add_controller(ctrl)
        button._key_controller = ctrl

    def _on_keybinding_key_pressed(
        self, _ctrl, keyval: int, _keycode: int, state: int, button: Gtk.Button,
    ) -> bool:
        button._capturing = False
        if button._key_controller:
            button.remove_controller(button._key_controller)
            button._key_controller = None

        # Escape cancels.
        if keyval == Gdk.KEY_Escape:
            self._update_keybinding_button(button)
            return True

        # Ignore bare modifier presses.
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
                       Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                       Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                       Gdk.KEY_Super_L, Gdk.KEY_Super_R):
            self._update_keybinding_button(button)
            return True

        state &= _RELEVANT_MODS
        accel = Gtk.accelerator_name(keyval, state)
        self._settings[button._setting_key] = accel
        self._persist()
        self._update_keybinding_button(button)
        return True

    def _on_tab_switch_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        self._settings["tab_switch_mode"] = "mru" if row.get_selected() == 0 else "cycle"
        self._persist()

    def _on_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        levels = list(_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["loglevel"] = levels[idx]
            self._persist()
            level = _LOGLEVEL_MAP[levels[idx]]
            logging.getLogger().setLevel(level)
            logging.getLogger("markdown-vault").setLevel(level)

    def _on_tp_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        from .logging_setup import set_third_party_loglevel
        levels = list(_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["third_party_loglevel"] = levels[idx]
            self._persist()
            set_third_party_loglevel(levels[idx])

    def _on_dump_toggle_changed(self, row: Adw.SwitchRow, _pspec, key: str) -> None:
        self._settings[f"debug_dump_{key}"] = row.get_active()
        self._persist()

    def _on_webkit_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        self._settings[key] = row.get_active()
        self._persist()

    def _on_wikilink_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        self._settings[key] = row.get_active()
        self._persist()
