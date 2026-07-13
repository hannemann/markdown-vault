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

    def test_update_path_renames_tab(self):
        bar = TabBar()
        tab = bar.add_tab("/tmp/old.md", editor=None, preview=None)
        bar.update_path("/tmp/old.md", "/tmp/new.md")
        self.assertEqual(tab.file_path, "/tmp/new.md")
        self.assertEqual(tab.title, "new.md")
        self.assertIn("/tmp/new.md", bar.get_all_paths())
        self.assertNotIn("/tmp/old.md", bar.get_all_paths())

    def test_update_path_updates_current_path(self):
        bar = TabBar()
        bar.add_tab("/tmp/old.md", editor=None, preview=None)
        bar.update_path("/tmp/old.md", "/tmp/new.md")
        self.assertEqual(bar.get_current_path(), "/tmp/new.md")

    def test_update_path_emits_signal(self):
        bar = TabBar()
        bar.add_tab("/tmp/old.md", editor=None, preview=None)
        received = []
        bar.connect("tab-renamed", lambda _, o, n: received.append((o, n)))
        bar.update_path("/tmp/old.md", "/tmp/new.md")
        self.assertEqual(received, [("/tmp/old.md", "/tmp/new.md")])

    def test_update_path_noop_for_missing(self):
        bar = TabBar()
        # Should not raise.
        bar.update_path("/tmp/nonexistent.md", "/tmp/new.md")
        self.assertEqual(bar.get_all_paths(), [])


if __name__ == "__main__":
    unittest.main()
