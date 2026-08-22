"""Preferences — General page: autosave interval and the default view mode."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw

from markdown_vault.ui.preferences.constants import _VIEW_MODES


class GeneralPageMixin:
    def _build_general_page(self) -> None:
        # ── General page ────────────────────────────────────────────
        general = Adw.PreferencesPage(title="General", icon_name="preferences-other-symbolic")
        general.set_name("general")   # addressable via PreferencesDialog.open_page

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

    def _on_autosave_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["autosave_interval"] = int(self._autosave_row.get_adjustment().get_value())
        self._persist()

    def _on_view_mode_changed(self, row: Adw.ComboRow, _pspec) -> None:
        modes = list(_VIEW_MODES.keys())
        idx = row.get_selected()
        if idx < len(modes):
            self._settings["default_view_mode"] = modes[idx]
            self._persist()
