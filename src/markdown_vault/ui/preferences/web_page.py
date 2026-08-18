"""Preferences — Web page: import defaults and the WebKit renderer switches."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Adw", "1")

from gi.repository import Adw


class WebPageMixin:
    def _build_web_page(self) -> None:
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

    def _on_webkit_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        self._settings[key] = row.get_active()
        self._persist()
