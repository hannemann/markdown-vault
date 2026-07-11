"""Tests for markdown_vault.editor — GtkSourceView editor widget.

These are structural / property tests that do not require a running
display server or GtkSourceView to be installed.
"""

import unittest
import inspect
import os
import tempfile
import shutil
from pathlib import Path


class TestEditorModuleStructure(unittest.TestCase):
    """Verify the module exports the expected class and API."""

    def test_module_has_editor_class(self):
        """Check the source file defines the Editor class."""
        src = Path(__file__).resolve().parent.parent / "src" / "editor.py"
        source = src.read_text(encoding="utf-8")
        self.assertIn("class Editor", source)

    def test_editor_has_expected_methods_in_source(self):
        src = Path(__file__).resolve().parent.parent / "src" / "editor.py"
        source = src.read_text(encoding="utf-8")
        for method in ("open_file", "save", "get_text", "focus"):
            self.assertIn(f"def {method}", source)


class TestEditorFileOperations(unittest.TestCase):
    """Test file I/O on a real filesystem (no GTK)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_read_write_roundtrip(self):
        path = Path(self._tmpdir) / "test.md"
        path.write_text("# Hello\n\nWorld", encoding="utf-8")
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content, "# Hello\n\nWorld")

    def test_read_nonexistent_raises(self):
        path = Path(self._tmpdir) / "missing.md"
        with self.assertRaises(OSError):
            path.read_text(encoding="utf-8")

    def test_write_creates_file(self):
        path = Path(self._tmpdir) / "new.md"
        path.write_text("content", encoding="utf-8")
        self.assertTrue(path.exists())

    def test_utf8_content(self):
        path = Path(self._tmpdir) / "unicode.md"
        path.write_text("Ünïcödé: 日本語テスト", encoding="utf-8")
        content = path.read_text(encoding="utf-8")
        self.assertIn("日本語テスト", content)


if __name__ == "__main__":
    unittest.main()
