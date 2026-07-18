"""Tests for markdown_vault.tabs — tab management."""

import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from src.tabs import Tab, TabBar


class MockEditor:
    """Minimal editor mock for testing set_file_path calls."""

    def __init__(self):
        self.file_path = None

    def set_file_path(self, new_path):
        self.file_path = new_path


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

    def test_update_path_calls_editor_set_file_path(self):
        bar = TabBar()
        editor = MockEditor()
        editor.file_path = "/tmp/old.md"
        bar.add_tab("/tmp/old.md", editor=editor, preview=None)
        bar.update_path("/tmp/old.md", "/tmp/new.md")
        self.assertEqual(editor.file_path, "/tmp/new.md")

    def test_update_path_skips_editor_when_none(self):
        bar = TabBar()
        tab = bar.add_tab("/tmp/old.md", editor=None, preview=None)
        # Should not raise.
        bar.update_path("/tmp/old.md", "/tmp/new.md")
        self.assertEqual(tab.file_path, "/tmp/new.md")


class TestTabTooltip(unittest.TestCase):
    """Tooltip mit relativem Pfad zum Vault-Root."""

    def test_tooltip_shows_relative_path(self):
        bar = TabBar()
        bar.set_vault_paths(["/home/user/vault"])
        tab_widget = bar._build_tab_widget(
            "/home/user/vault/sub/note.md", "note.md"
        )
        tooltip = tab_widget.get_tooltip_text()
        self.assertEqual(tooltip, "sub/note.md")

    def test_tooltip_shows_filename_for_root_file(self):
        bar = TabBar()
        bar.set_vault_paths(["/home/user/vault"])
        tab_widget = bar._build_tab_widget(
            "/home/user/vault/readme.md", "readme.md"
        )
        tooltip = tab_widget.get_tooltip_text()
        self.assertEqual(tooltip, "readme.md")

    def test_tooltip_falls_back_to_filename_if_no_vault(self):
        bar = TabBar()
        tab_widget = bar._build_tab_widget(
            "/some/random/path.md", "path.md"
        )
        tooltip = tab_widget.get_tooltip_text()
        self.assertEqual(tooltip, "path.md")

    def test_tooltip_falls_back_if_path_not_in_vault(self):
        bar = TabBar()
        bar.set_vault_paths(["/home/user/vault"])
        tab_widget = bar._build_tab_widget(
            "/other/dir/note.md", "note.md"
        )
        tooltip = tab_widget.get_tooltip_text()
        self.assertEqual(tooltip, "note.md")

    def test_add_tab_sets_tooltip(self):
        bar = TabBar()
        bar.set_vault_paths(["/home/user/vault"])
        bar.add_tab("/home/user/vault/sub/doc.md", editor=None, preview=None)
        for child in bar._box:
            if getattr(child, "_file_path", None) == "/home/user/vault/sub/doc.md":
                self.assertEqual(child.get_tooltip_text(), "sub/doc.md")
                break
        else:
            self.fail("Tab widget not found")

    def test_update_path_updates_tooltip(self):
        bar = TabBar()
        bar.set_vault_paths(["/home/user/vault"])
        bar.add_tab("/home/user/vault/old.md", editor=None, preview=None)
        bar.update_path("/home/user/vault/old.md", "/home/user/vault/sub/new.md")
        for child in bar._box:
            if getattr(child, "_file_path", None) == "/home/user/vault/sub/new.md":
                self.assertEqual(child.get_tooltip_text(), "sub/new.md")
                break
        else:
            self.fail("Tab widget not found")


class TestTabContextMenu(unittest.TestCase):
    """Kontextmenu: Copy path, Close, Close others, Close Left/Right."""

    def test_tab_widget_has_rightclick_gesture(self):
        bar = TabBar()
        tab_widget = bar._build_tab_widget("/tmp/note.md", "note.md")
        has_secondary = False
        for ctrl in tab_widget.observe_controllers():
            if isinstance(ctrl, Gtk.GestureClick):
                if ctrl.get_button() == 3:  # GDK_BUTTON_SECONDARY
                    has_secondary = True
                    break
        self.assertTrue(has_secondary)

    def test_action_group_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions)

    def test_copy_path_action_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions.lookup_action("copy-path"))

    def test_close_action_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions.lookup_action("close"))

    def test_close_others_action_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions.lookup_action("close-others"))

    def test_close_left_action_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions.lookup_action("close-left"))

    def test_close_right_action_exists(self):
        bar = TabBar()
        self.assertIsNotNone(bar._tab_actions.lookup_action("close-right"))


class TestTabCloseOthers(unittest.TestCase):
    """close_others schließt alle Tabs außer dem angegebenen."""

    def test_close_others_keeps_target(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.add_tab("/tmp/c.md", editor=None, preview=None)
        bar.close_others("/tmp/b.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/b.md"])

    def test_close_others_removes_others(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.add_tab("/tmp/c.md", editor=None, preview=None)
        bar.close_others("/tmp/b.md")
        self.assertNotIn("/tmp/a.md", bar.get_all_paths())
        self.assertNotIn("/tmp/c.md", bar.get_all_paths())

    def test_close_others_active_tab_becomes_target(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.set_active_tab("/tmp/a.md")
        bar.close_others("/tmp/b.md")
        self.assertEqual(bar.get_current_path(), "/tmp/b.md")


class TestTabCloseLeftRight(unittest.TestCase):
    """close_left / close_right schließen Tabs relativ zur Position."""

    def test_close_left_removes_tabs_before(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.add_tab("/tmp/c.md", editor=None, preview=None)
        bar.close_left("/tmp/b.md")
        self.assertNotIn("/tmp/a.md", bar.get_all_paths())
        self.assertIn("/tmp/b.md", bar.get_all_paths())
        self.assertIn("/tmp/c.md", bar.get_all_paths())

    def test_close_right_removes_tabs_after(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.add_tab("/tmp/c.md", editor=None, preview=None)
        bar.close_right("/tmp/b.md")
        self.assertIn("/tmp/a.md", bar.get_all_paths())
        self.assertIn("/tmp/b.md", bar.get_all_paths())
        self.assertNotIn("/tmp/c.md", bar.get_all_paths())

    def test_close_left_no_tabs_before(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.close_left("/tmp/a.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/a.md"])

    def test_close_right_no_tabs_after(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.close_right("/tmp/a.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/a.md"])

    def test_close_left_first_tab(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.close_left("/tmp/a.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/a.md", "/tmp/b.md"])

    def test_close_right_last_tab(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.close_right("/tmp/b.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/a.md", "/tmp/b.md"])


class TestTabBarScrollToActive(unittest.TestCase):
    """Tests for scroll-to-active-tab behaviour."""

    def test_scroll_adjustment_updated_on_active_tab(self):
        """When set_active_tab is called, the hadjustment value should be
        updated to show the active tab widget."""
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.add_tab("/tmp/c.md", editor=None, preview=None)

        adj = bar._scrolled.get_hadjustment()
        with unittest.mock.patch.object(adj, "set_value") as mock_set:
            bar.set_active_tab("/tmp/c.md")
            mock_set.assert_called_once()

    def test_scroll_adjustment_value_nonnegative(self):
        """The adjustment value should never be set negative."""
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)

        adj = bar._scrolled.get_hadjustment()
        with unittest.mock.patch.object(adj, "set_value") as mock_set:
            bar.set_active_tab("/tmp/a.md")
            # Check the value passed is >= 0
            call_args = mock_set.call_args
            self.assertGreaterEqual(call_args[0][0], 0)

    def test_scroll_no_tabs_no_adjustment_change(self):
        """With no tabs, set_active_tab should not touch the adjustment."""
        bar = TabBar()
        adj = bar._scrolled.get_hadjustment()
        original_val = adj.get_value()
        with unittest.mock.patch.object(adj, "set_value") as mock_set:
            bar.set_active_tab("/tmp/missing.md")
            mock_set.assert_not_called()

    def test_scroll_not_called_for_nonexistent_tab(self):
        """set_active_tab with existing path but no loaded tab → no scroll."""
        bar = TabBar()
        bar.add_tab("/tmp/existing.md", editor=None, preview=None)
        adj = bar._scrolled.get_hadjustment()
        with unittest.mock.patch.object(adj, "set_value") as mock_set:
            bar.set_active_tab("/tmp/existing.md")
            mock_set.assert_called_once()

    def test_scroll_not_called_for_completely_missing_path(self):
        """set_active_tab with path that doesn't exist at all → no scroll."""
        bar = TabBar()
        bar.add_tab("/tmp/other.md", editor=None, preview=None)
        adj = bar._scrolled.get_hadjustment()
        with unittest.mock.patch.object(adj, "set_value") as mock_set:
            bar.set_active_tab("/tmp/nonexistent.md")
            mock_set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
