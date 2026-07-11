"""Tests for markdown_vault.tabs — tab management."""

import unittest

from src.tabs import Tab, TabBar


class TestTab(unittest.TestCase):
    """Unit tests for the Tab data class."""

    def test_tab_stores_attributes(self):
        tab = Tab(file_path="/tmp/doc.md", title="doc.md", editor=None, preview=None)
        self.assertEqual(tab.file_path, "/tmp/doc.md")
        self.assertEqual(tab.title, "doc.md")
        self.assertIsNone(tab.editor)
        self.assertIsNone(tab.preview)
        self.assertEqual(tab.view_mode, "edit")


class TestTabBar(unittest.TestCase):
    """Tests for the TabBar widget (structural)."""

    def test_can_be_instantiated(self):
        bar = TabBar()
        self.assertFalse(bar.has_tabs())
        self.assertIsNone(bar.get_current_path())
        self.assertIsNone(bar.get_current_tab())

    def test_get_all_paths_empty(self):
        bar = TabBar()
        self.assertEqual(bar.get_all_paths(), [])


if __name__ == "__main__":
    unittest.main()
