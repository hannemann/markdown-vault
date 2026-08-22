"""Preferences — Debug page: log levels and the debug dumps."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw

from markdown_vault.ui.preferences.constants import (
    _GLIB_LOGLEVELS, _LOGLEVELS, _LOGLEVEL_MAP,
)


class DebugPageMixin:
    def _build_debug_page(self) -> None:
        # ── Debug page ──────────────────────────────────────────────
        debug = Adw.PreferencesPage(title="Debug", icon_name="utilities-system-monitor-symbolic")
        debug.set_name("debug")   # addressable via PreferencesDialog.open_page

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

    def _on_glib_loglevel_changed(self, row: Adw.ComboRow, _pspec) -> None:
        levels = list(_GLIB_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["glib_loglevel"] = levels[idx]
            self._persist()
            # Notify caller to reconfigure GLib logging (live).
            if self._glib_loglevel_callback:
                self._glib_loglevel_callback(levels[idx])

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
        from markdown_vault.core.logging_setup import set_third_party_loglevel
        levels = list(_LOGLEVELS.keys())
        idx = row.get_selected()
        if idx < len(levels):
            self._settings["third_party_loglevel"] = levels[idx]
            self._persist()
            set_third_party_loglevel(levels[idx])

    def _on_dump_toggle_changed(self, row: Adw.SwitchRow, _pspec, key: str) -> None:
        self._settings[f"debug_dump_{key}"] = row.get_active()
        self._persist()
