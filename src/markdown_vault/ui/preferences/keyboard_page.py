"""Preferences — Keyboard page: shortcut capture and the tab-switch mode."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk

from markdown_vault.ui.preferences.constants import _RELEVANT_MODS, _accel_to_label


class KeyboardPageMixin:
    def _build_keyboard_page(self) -> None:
        # ── Keyboard page ──────────────────────────────────────────
        keyboard = Adw.PreferencesPage(title="Keyboard", icon_name="input-keyboard-symbolic")
        keyboard.set_name("keyboard")   # addressable via PreferencesDialog.open_page

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
