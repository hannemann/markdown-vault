"""Preferences — Web page: import defaults and the WebKit renderer switches."""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Adw", "1")

from gi.repository import Adw

from markdown_vault.core.i18n import _

from markdown_vault.core import config


class WebPageMixin:
    def _build_web_page(self) -> None:
        # ── Web page ────────────────────────────────────────────────
        web = Adw.PreferencesPage(
            title=_("Web"), icon_name="applications-internet-symbolic",
        )
        web.set_name("web")   # addressable via PreferencesDialog.open_page

        web_group = Adw.PreferencesGroup(title=_("WebKit Rendering"))
        web.add(web_group)

        self._dmabuf_row = Adw.SwitchRow(title=_("Disable DMA-BUF renderer"))
        self._dmabuf_row.subtitle = _(
            "Lowers GPU/video memory usage "
            "(WEBKIT_DISABLE_DMABUF_RENDERER). Takes effect after restart."
        )
        self._dmabuf_row.set_active(
            config.get_setting(self._settings, "webkit.disable_dmabuf", False),
        )
        self._dmabuf_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit.disable_dmabuf",
        )
        web_group.add(self._dmabuf_row)

        self._compositing_row = Adw.SwitchRow(
            title=_("Disable hardware acceleration"),
        )
        self._compositing_row.subtitle = _(
            "Render without the GPU (WEBKIT_DISABLE_COMPOSITING_MODE). "
            "Takes effect after restart."
        )
        self._compositing_row.set_active(
            config.get_setting(self._settings, "webkit.disable_compositing", False),
        )
        self._compositing_row.connect(
            "notify::active", self._on_webkit_toggle_changed,
            "webkit.disable_compositing",
        )
        web_group.add(self._compositing_row)

        self.add(web)

    def _on_webkit_toggle_changed(
        self, row: Adw.SwitchRow, _pspec, key: str,
    ) -> None:
        config.set_setting(self._settings, key, row.get_active())
        self._persist()
