"""Tests for markdown_vault.ui.preferences — preferences dialog structure."""

import unittest
from pathlib import Path


class TestPreferencesModuleStructure(unittest.TestCase):
    """Verify the preferences module exports the expected class."""

    def test_module_has_preferences_dialog(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("class PreferencesDialog", source)

    def test_dialog_has_settings_changed_signal(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("settings-changed", source)

    def test_dialog_has_all_pages(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("General", source)
        self.assertIn("Editor", source)
        self.assertIn("Preview", source)
        self.assertIn("Web", source)

    def test_dialog_has_all_setting_rows(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("autosave_interval", source)
        self.assertIn("default_view_mode", source)
        self.assertIn("editor_font_size", source)
        self.assertIn("editor_tab_width", source)
        self.assertIn("editor_wrap_text", source)
        self.assertIn("preview_zoom", source)

    def test_dialog_has_local_llm_rows(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        for token in ("ask_gguf_url", "ask_gguf_path", "ask_n_gpu_layers",
                      "ask_n_threads", "_on_download_gguf", "ask_engine",
                      "_update_ask_rows", "supports_gpu", "_ask_gguf_combo",
                      "_refresh_gguf_models", "model_filename_from_url"):
            self.assertIn(token, source)

    def test_dialog_persists_settings(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("save_settings", source)
        self.assertIn("emit", source)

    def test_dialog_has_webkit_switches(self):
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("webkit_disable_dmabuf", source)
        self.assertIn("webkit_disable_compositing", source)
        self.assertIn("WEBKIT_DISABLE_DMABUF_RENDERER", source)
        self.assertIn("WEBKIT_DISABLE_COMPOSITING_MODE", source)

    def test_spin_rows_initialized_from_settings(self):
        """R14.4: SpinRow values read from settings, not hardcoded literals."""
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        for key in ("autosave_interval", "editor_font_size",
                    "editor_tab_width", "tab_min_width"):
            self.assertIn(f'self._settings.get("{key}"', source)


class TestHttpsOnlyRedirect(unittest.TestCase):
    """The download redirect guard must refuse leaving HTTPS (S3). Pure, no net."""

    def _redirect_to(self, newurl):
        import email.message
        import urllib.request
        from markdown_vault.ui.preferences import _HttpsOnlyRedirect
        handler = _HttpsOnlyRedirect()
        req = urllib.request.Request("https://example.com/model.gguf")
        return handler.redirect_request(
            req, None, 302, "Found", email.message.Message(), newurl)

    def test_allows_https_to_https(self):
        out = self._redirect_to("https://cdn.example.com/model.gguf")
        self.assertIsNotNone(out)              # a follow-on Request is built

    def test_refuses_downgrade_to_http(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            self._redirect_to("http://evil.example.com/model.gguf")

    def test_refuses_ftp(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            self._redirect_to("ftp://evil.example.com/model.gguf")

    def test_worker_wires_the_guard(self):
        # Pin the integration point: swapping opener.open back to the bare
        # urllib.request.urlopen would silently drop the redirect protection.
        src = Path(__file__).resolve().parent.parent / "src" / "markdown_vault" / "ui" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("build_opener(_HttpsOnlyRedirect())", source)
        self.assertIn('urlparse(url).scheme != "https"', source)


if __name__ == "__main__":
    unittest.main()
