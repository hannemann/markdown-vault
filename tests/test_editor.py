"""Tests for markdown_vault.editor — GtkSourceView editor widget.

Editor requires GTK widgets for full behavioral testing. These tests
verify the module structure and API surface without a display server.
"""

import unittest
from pathlib import Path


_SRC = Path(__file__).resolve().parent.parent / "src" / "lib" / "python3.13" / "site-packages" / "markdown_vault" / "editor.py"


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


if __name__ == "__main__":
    unittest.main()
