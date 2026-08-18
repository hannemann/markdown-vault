"""Preferences — Preview page: zoom and the remote-image opt-in."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw


class PreviewPageMixin:
    def _build_preview_page(self) -> None:
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

    def _on_zoom_changed(self, _row: Adw.SpinRow, _pspec) -> None:
        self._settings["preview_zoom"] = round(self._zoom_row.get_adjustment().get_value(), 2)
        self._persist()

    def _on_remote_images_changed(self, switch: Adw.SwitchRow, _pspec) -> None:
        self._settings["preview_allow_remote_images"] = switch.get_active()
        self._persist()
