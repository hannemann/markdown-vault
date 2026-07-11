"""Tests for markdown_vault.preferences — preferences dialog structure."""

import unittest
from pathlib import Path


class TestPreferencesModuleStructure(unittest.TestCase):
    """Verify the preferences module exports the expected class."""

    def test_module_has_preferences_dialog(self):
        src = Path(__file__).resolve().parent.parent / "src" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("class PreferencesDialog", source)

    def test_dialog_has_settings_changed_signal(self):
        src = Path(__file__).resolve().parent.parent / "src" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("settings-changed", source)

    def test_dialog_has_all_pages(self):
        src = Path(__file__).resolve().parent.parent / "src" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("General", source)
        self.assertIn("Editor", source)
        self.assertIn("Preview", source)

    def test_dialog_has_all_setting_rows(self):
        src = Path(__file__).resolve().parent.parent / "src" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("autosave_interval", source)
        self.assertIn("default_view_mode", source)
        self.assertIn("editor_font_size", source)
        self.assertIn("editor_tab_width", source)
        self.assertIn("editor_wrap_text", source)
        self.assertIn("preview_zoom", source)

    def test_dialog_persists_settings(self):
        src = Path(__file__).resolve().parent.parent / "src" / "preferences.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("save_settings", source)
        self.assertIn("emit", source)


if __name__ == "__main__":
    unittest.main()
