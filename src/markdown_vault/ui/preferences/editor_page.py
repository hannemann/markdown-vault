"""Preferences — Editor page: font, layout, tabs and wikilink autofix."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw

from markdown_vault.core.i18n import _

from markdown_vault.core import config


class EditorPageMixin:
    def _build_editor_page(self) -> None:
        # ── Editor page ─────────────────────────────────────────────
        editor = Adw.PreferencesPage(title=_("Editor"), icon_name="document-edit-symbolic")
        editor.set_name("editor")   # addressable via PreferencesDialog.open_page

        font_group = Adw.PreferencesGroup(title=_("Font &amp; Layout"))
        editor.add(font_group)

        self._font_row = Adw.SpinRow(
            title=_("Font size"),
            adjustment=Gtk.Adjustment.new(
                config.get_setting(self._settings, "editor.font_size", 14), 8, 72, 1, 5, 0,
            ),
        )
        self._font_row.connect("notify::value", self._on_font_size_changed)
        font_group.add(self._font_row)

        self._tab_row = Adw.SpinRow(
            title=_("Tab width"),
            adjustment=Gtk.Adjustment.new(
                config.get_setting(self._settings, "editor.tab_width", 4), 1, 16, 1, 4, 0,
            ),
        )
        self._tab_row.connect("notify::value", self._on_tab_width_changed)
        font_group.add(self._tab_row)

        self._wrap_row = Adw.SwitchRow(title=_("Word wrap"))
        self._wrap_row.set_active(config.get_setting(self._settings, "editor.wrap_text", True))
        self._wrap_row.connect("notify::active", self._on_wrap_changed)
        font_group.add(self._wrap_row)

        # Tabs group.
        tabs_group = Adw.PreferencesGroup(title=_("Tabs"))
        editor.add(tabs_group)

        self._tab_width_row = Adw.SpinRow(
            title=_("Minimum tab width (px)"),
            adjustment=Gtk.Adjustment.new(
                config.get_setting(self._settings, "tabs.min_width", 150), 50, 300, 10, 50, 0,
            ),
        )
        self._tab_width_row.connect("notify::value", self._on_tab_min_width_changed)
        tabs_group.add(self._tab_width_row)

        self._tab_wrap_row = Adw.SwitchRow(title=_("Wrap tabs"))
        self._tab_wrap_row.set_active(config.get_setting(self._settings, "tabs.wrap", False))
        self._tab_wrap_row.connect("notify::active", self._on_tab_wrap_changed)
        tabs_group.add(self._tab_wrap_row)

        # Wikilinks group.
        wikilink_group = Adw.PreferencesGroup(
            title=_("Wikilinks"),
            description=_("Autofix and validation of [[wikilinks]] when saving."),
        )
        editor.add(wikilink_group)

        self._wl_rows: dict[str, Adw.SwitchRow] = {}
        for key, title, subtitle in (
            ("wikilink.autofix_normalize", _("Normalize on save"),
             _("Trim whitespace inside [[…]] when saving.")),
            ("wikilink.autofix_relink", _("Auto-fix moved links"),
             _("Redirect a broken link when exactly one matching file exists.")),
            ("wikilink.warn_on_save", _("Warn about broken links"),
             _("After saving, show a notice for links that can't be resolved.")),
            ("wikilink.mark_broken", _("Mark broken links in the editor"),
             _("Gutter warning triangle and red underline on unresolved links.")),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(config.get_setting(self._settings, key, False))
            row.connect("notify::active", self._on_wikilink_toggle_changed, key)
            wikilink_group.add(row)
            self._wl_rows[key] = row

        self.add(editor)

    def _on_font_size_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        config.set_setting(self._settings, "editor.font_size",
                           int(self._font_row.get_adjustment().get_value()))
        self._persist()

    def _on_tab_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        config.set_setting(self._settings, "editor.tab_width",
                           int(self._tab_row.get_adjustment().get_value()))
        self._persist()

    def _on_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        config.set_setting(self._settings, "editor.wrap_text", switch.get_active())
        self._persist()

    def _on_tab_min_width_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        config.set_setting(self._settings, "tabs.min_width",
                           int(self._tab_width_row.get_adjustment().get_value()))
        self._persist()

    def _on_tab_wrap_changed(self, switch: Gtk.Switch, _pspec) -> None:
        config.set_setting(self._settings, "tabs.wrap", switch.get_active())
        self._persist()

    def _on_wikilink_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        config.set_setting(self._settings, key, row.get_active())
        self._persist()
