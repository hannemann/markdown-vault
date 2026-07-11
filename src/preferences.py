"""Markdown Vault — preferences dialog.

Provides an ``Adw.PreferencesDialog`` for editing application settings
such as autosave interval, editor appearance, and default view mode.
Changes are applied immediately and persisted to ``vaults.yaml``.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GObject

from . import config

_VIEW_MODES = {"edit": "Edit", "render": "Render", "split": "Split"}


class PreferencesDialog(Adw.PreferencesDialog):
    """Application preferences dialog.

    Signals:
        settings-changed(): Emitted whenever a setting is modified.
    """

    __gsignals__ = {
        "settings-changed": (GObject.SIGNAL_RUN_LAST, None, ()),
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

        font_group = Adw.PreferencesGroup(title="Font & Layout")
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
