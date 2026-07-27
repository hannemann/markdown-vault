"""Tests for markdown_vault.editor — GtkSourceView editor widget.

Editor requires GTK widgets for full behavioral testing. These tests
verify the module structure and API surface without a display server.
"""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


_PKG_DIR = Path(__file__).resolve().parent.parent / "src" / "lib" / "python3.13" / "site-packages" / "markdown_vault"
_SRC = _PKG_DIR / "editor.py"
_STYLES_DIR = _PKG_DIR / "styles"


class TestEditorModuleStructure(unittest.TestCase):
    """Verify the module exports the expected class and API."""

    def test_module_has_editor_class(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("class Editor", source)

    def test_editor_has_expected_methods_in_source(self):
        source = _SRC.read_text(encoding="utf-8")
        for method in ("open_file", "save", "get_text", "scroll_to_line",
                       "update_settings", "update_color_scheme"):
            self.assertIn(f"def {method}", source)

    def test_editor_has_zoom_factor_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def zoom_factor", source)
        self.assertIn("_zoom_factor", source)

    def test_editor_has_base_font_size_property(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("def base_font_size", source)

    def test_editor_constructor_accepts_font_params(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("base_font_size", source)
        self.assertIn("tab_width", source)
        self.assertIn("wrap_text", source)

    def test_editor_uses_gtksource5(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn('GtkSource", "5"', source)

    def test_editor_has_signals(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("file-changed", source)
        self.assertIn("modified-changed", source)
        self.assertIn("text-changed", source)


class TestBundledStyleSchemes(unittest.TestCase):
    """The bundled GtkSourceView schemes override the washed-out inline-code
    colour of the stock Adwaita schemes."""

    def _scheme(self, filename: str) -> ET.Element:
        path = _STYLES_DIR / filename
        self.assertTrue(path.exists(), f"missing style scheme: {path}")
        return ET.parse(path).getroot()

    def _inline_code_foreground(self, root: ET.Element) -> str:
        for style in root.iter("style"):
            if style.get("name") == "def:inline-code":
                return style.get("foreground", "")
        self.fail("scheme has no def:inline-code style")

    def test_dark_scheme_inherits_adwaita_dark(self):
        root = self._scheme("markdown-vault-dark.xml")
        self.assertEqual(root.get("id"), "markdown-vault-dark")
        self.assertEqual(root.get("parent-scheme"), "Adwaita-dark")

    def test_light_scheme_inherits_adwaita(self):
        root = self._scheme("markdown-vault-light.xml")
        self.assertEqual(root.get("id"), "markdown-vault-light")
        self.assertEqual(root.get("parent-scheme"), "Adwaita")

    def test_dark_scheme_uses_bright_orange_inline_code(self):
        root = self._scheme("markdown-vault-dark.xml")
        self.assertEqual(self._inline_code_foreground(root).upper(), "#FFBE6F")

    def test_light_scheme_uses_dark_orange_inline_code(self):
        root = self._scheme("markdown-vault-light.xml")
        self.assertEqual(self._inline_code_foreground(root).upper(), "#C64600")

    def test_editor_registers_scheme_search_path(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("append_search_path", source)

    def test_editor_selects_bundled_schemes_with_stock_fallback(self):
        source = _SRC.read_text(encoding="utf-8")
        self.assertIn("markdown-vault-dark", source)
        self.assertIn("markdown-vault-light", source)
        self.assertIn("Adwaita-dark", source)

    def test_schemes_are_installed_by_meson(self):
        meson = (_PKG_DIR / "meson.build").read_text(encoding="utf-8")
        self.assertIn("styles/markdown-vault-dark.xml", meson)
        self.assertIn("styles/markdown-vault-light.xml", meson)


if __name__ == "__main__":
    unittest.main()
