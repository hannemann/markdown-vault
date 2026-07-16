"""Markdown Vault — preferences dialog.

Provides an ``Adw.PreferencesDialog`` for editing application settings
such as autosave interval, editor appearance, and default view mode.
Changes are applied immediately and persisted to ``vaults.yaml``.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, GObject, Gdk

from . import config

_VIEW_MODES = {"edit": "Edit", "render": "Render", "split": "Split"}


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

    def __init__(self) -> None:
        super().__init__(title="Preferences")

        self._settings = config.load_settings()

        # ── General page ────────────────────────────────────────────
        general = Adw.PreferencesPage(title="General", icon_name="preferences-other-symbolic")

        # Autosave group.
        autosave_group = Adw.PreferencesGroup(title="Autosave")
        general.add(autosave_group)

        self._autosave_row = Adw.ActionRow(title="Autosave interval (seconds)")
        self._autosave_spin = Gtk.SpinButton.new_with_range(0, 600, 5)
        self._autosave_spin.set_value(self._settings.get("autosave_interval", 30))
        self._autosave_spin.connect("value-changed", self._on_autosave_changed)
        self._autosave_row.add_suffix(self._autosave_spin)
        self._autosave_row.activatable_widget = self._autosave_spin
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

        self._font_row = Adw.ActionRow(title="Font size")
        self._font_spin = Gtk.SpinButton.new_with_range(8, 72, 1)
        self._font_spin.set_value(self._settings.get("editor_font_size", 14))
        self._font_spin.connect("value-changed", self._on_font_size_changed)
        self._font_row.add_suffix(self._font_spin)
        self._font_row.activatable_widget = self._font_spin
        font_group.add(self._font_row)

        self._tab_row = Adw.ActionRow(title="Tab width")
        self._tab_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        self._tab_spin.set_value(self._settings.get("editor_tab_width", 4))
        self._tab_spin.connect("value-changed", self._on_tab_width_changed)
        self._tab_row.add_suffix(self._tab_spin)
        self._tab_row.activatable_widget = self._tab_spin
        font_group.add(self._tab_row)

        self._wrap_row = Adw.SwitchRow(title="Word wrap")
        self._wrap_switch = Gtk.Switch()
        self._wrap_switch.set_active(self._settings.get("editor_wrap_text", True))
        self._wrap_switch.connect("notify::active", self._on_wrap_changed)
        self._wrap_row.set_child(self._wrap_switch)
        font_group.add(self._wrap_row)

        self.add(editor)

        # ── Preview page ────────────────────────────────────────────
        preview = Adw.PreferencesPage(title="Preview", icon_name="document-properties-symbolic")

        zoom_group = Adw.PreferencesGroup(title="Zoom")
        preview.add(zoom_group)

        self._zoom_row = Adw.ActionRow(title="Default zoom level")
        self._zoom_spin = Gtk.SpinButton.new_with_range(0.25, 5.0, 0.05)
        self._zoom_spin.set_digits(2)
        self._zoom_spin.set_value(self._settings.get("preview_zoom", 1.0))
        self._zoom_spin.connect("value-changed", self._on_zoom_changed)
        self._zoom_row.add_suffix(self._zoom_spin)
        self._zoom_row.activatable_widget = self._zoom_spin
        zoom_group.add(self._zoom_row)

        self.add(preview)

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

    # ── Handlers ────────────────────────────────────────────────────

    def _persist(self) -> None:
        config.save_settings(self._settings)
        self.emit("settings-changed")

    def _on_autosave_changed(self, spin: Gtk.SpinButton) -> None:
        self._settings["autosave_interval"] = int(spin.get_value())
        self._persist()

    def _on_view_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        modes = list(_VIEW_MODES.keys())
        idx = row.get_selected()
        if idx < len(modes):
            self._settings["default_view_mode"] = modes[idx]
            self._persist()

    def _on_font_size_changed(self, spin: Gtk.SpinButton) -> None:
        self._settings["editor_font_size"] = int(spin.get_value())
        self._persist()

    def _on_tab_width_changed(self, spin: Gtk.SpinButton) -> None:
        self._settings["editor_tab_width"] = int(spin.get_value())
        self._persist()

    def _on_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._settings["editor_wrap_text"] = switch.get_active()
        self._persist()

    def _on_zoom_changed(self, spin: Gtk.SpinButton) -> None:
        self._settings["preview_zoom"] = round(spin.get_value(), 2)
        self._persist()

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
