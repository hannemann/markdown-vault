"""Tests for markdown_vault.monitor_handler — MonitorHandler-Orchestrierung."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch


class _GLibMock:
    """Minimal GLib mock that executes idle_add callbacks immediately."""
    @staticmethod
    def idle_add(func, *args):
        func(*args)
        return False


def _handler_class():
    from markdown_vault.monitor_handler import MonitorHandler
    return MonitorHandler


class TestMonitorHandlerFileCreated(unittest.TestCase):
    """Tests for MonitorHandler.on_file_created."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_vault_tree = MagicMock()
        self.mock_tab_bar = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_debug_fn = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            dispatcher=self.mock_dispatcher,
            debug_fn=self.mock_debug_fn,
        )

    def test_calls_vault_tree_handle_file_created(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_created("/vault", "/vault/foo.md")
            self.mock_vault_tree._handle_file_created.assert_called_once_with(
                "/vault", "/vault/foo.md",
            )

    def test_non_md_skips_index(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_created("/vault", "/vault/foo.txt")
            self.mock_file_index.add_file.assert_not_called()
            self.mock_backlink.update_file.assert_not_called()

    def test_md_calls_file_index_add(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            p = Path(self._tmp) / "foo.md"
            p.write_text("# Foo", encoding="utf-8")
            h.on_file_created("/vault", str(p))
            self.mock_file_index.add_file.assert_called_once_with(str(p))

    def test_non_utf8_file_handled_gracefully(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            p = Path(self._tmp) / "bad.md"
            p.write_bytes(b"\xff\xfe binary garbage")
            h.on_file_created("/vault", str(p))

    def test_debug_fn_called_with_correct_components(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            p = Path(self._tmp) / "foo.md"
            p.write_text("# Foo", encoding="utf-8")
            h.on_file_created("/vault", str(p))
            self.assertIn(
                call(["file_index", "vault_tree"]),
                [c for c in self.mock_debug_fn.call_args_list],
            )


class TestMonitorHandlerFileDeleted(unittest.TestCase):
    """Tests for MonitorHandler.on_file_deleted."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_vault_tree = MagicMock()
        self.mock_tab_bar = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_debug_fn = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            dispatcher=self.mock_dispatcher,
            debug_fn=self.mock_debug_fn,
        )

    def test_calls_vault_tree_handle_file_deleted(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.mock_vault_tree._handle_file_deleted.assert_called_once_with(
                "/vault/foo.md",
            )

    def test_non_md_directory_closes_nested_tabs(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = [
                "/vault/dir/file.md",
                "/vault/dir/other.md",
                "/vault/outside.md",
            ]
            h.on_file_deleted("/vault", "/vault/dir")
            self.mock_tab_bar.close_tab.assert_any_call("/vault/dir/file.md")
            self.mock_tab_bar.close_tab.assert_any_call("/vault/dir/other.md")
            close_paths = [c[0][0] for c in self.mock_tab_bar.close_tab.call_args_list]
            self.assertNotIn("/vault/outside.md", close_paths)

    def test_md_removes_wikilinks_and_file(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/Notes.md")
            self.mock_backlink.remove_wikilinks.assert_called_once_with("Notes")
            self.mock_backlink.remove_file.assert_called_once_with("/vault/Notes.md")
            self.mock_file_index.remove_file.assert_called_once_with("/vault/Notes.md")

    def test_md_closes_open_tab(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/foo.md"]
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.mock_tab_bar.close_tab.assert_called_once_with("/vault/foo.md")

    def test_md_does_not_close_if_not_open(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/other.md"]
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.mock_tab_bar.close_tab.assert_not_called()

    def test_dispatcher_on_file_deleted_called(self):
        """Dispatcher.on_file_deleted should be called."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.mock_dispatcher.on_file_deleted.assert_called_once_with(
                "/vault", "/vault/foo.md",
            )

    def test_debug_fn_called(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.assertIn(
                call(["file_index", "backlink_index", "vault_tree", "tabs"]),
                [c for c in self.mock_debug_fn.call_args_list],
            )

    def test_non_md_directory_purges_non_open_file_index_entries(self):
        """Directory delete must purge ALL .md entries under the directory,
        not just those that are open as tabs (R9.2)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            # Simulate non-open .md files in the file index
            self.mock_file_index._path_to_stem = {
                "/vault/dir/a.md": "a",
                "/vault/dir/b.md": "b",
                "/vault/outside.md": "outside",
            }
            # Only "/vault/dir/a.md" is an open tab
            self.mock_tab_bar.get_all_paths.return_value = [
                "/vault/dir/a.md",
            ]
            h.on_file_deleted("/vault", "/vault/dir")
            # Both files under /vault/dir/ must be removed from file_index
            self.mock_file_index.remove_file.assert_any_call("/vault/dir/a.md")
            self.mock_file_index.remove_file.assert_any_call("/vault/dir/b.md")
            # outside.md must NOT be removed
            for c in self.mock_file_index.remove_file.call_args_list:
                self.assertNotIn("/vault/outside.md", c[0])

    def test_non_md_directory_purges_non_open_backlink_index_entries(self):
        """Directory delete must purge ALL backlink entries under the directory,
        not just those that are open as tabs (R9.2)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            # MagicMock auto-creates _path_to_stem — set to None so the
            # backlink-path branch of _purge_index_prefix is taken.
            h._backlink_index._path_to_stem = None
            h._backlink_index._source_to_targets = {
                "/vault/dir/a.md": {"related"},
                "/vault/dir/b.md": {"related"},
                "/vault/outside.md": {"outside"},
            }
            # Only "/vault/dir/a.md" is an open tab
            self.mock_tab_bar.get_all_paths.return_value = [
                "/vault/dir/a.md",
            ]
            h.on_file_deleted("/vault", "/vault/dir")
            # Both files under /vault/dir/ must trigger _remove_source
            h._backlink_index._remove_source.assert_any_call("/vault/dir/a.md")
            h._backlink_index._remove_source.assert_any_call("/vault/dir/b.md")
            # outside.md must NOT be removed
            for c in h._backlink_index._remove_source.call_args_list:
                self.assertNotIn("/vault/outside.md", c[0])


class TestMonitorHandlerFileMoved(unittest.TestCase):
    """Tests for MonitorHandler.on_file_moved."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_vault_tree = MagicMock()
        self.mock_tab_bar = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_debug_fn = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            dispatcher=self.mock_dispatcher,
            debug_fn=self.mock_debug_fn,
        )

    def test_renamed_file_renames_wikilinks(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_backlink.rename_wikilinks.assert_called_once_with("Old", "New")

    def test_renamed_file_renames_indexes(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_backlink.rename_file.assert_called_once_with("/vault/Old.md", "/vault/New.md")
            self.mock_file_index.rename_file.assert_called_once_with("/vault/Old.md", "/vault/New.md")

    def test_renamed_file_updates_vault_tree(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/dir/New.md", "/vault/dir/Old.md")
            self.mock_vault_tree._handle_file_moved.assert_called_once_with(
                "/vault/dir/Old.md", "/vault/dir", "/vault/dir/New.md",
            )

    def test_renamed_file_updates_tab_path(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/Old.md", "/vault/other.md"]
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_tab_bar.update_path.assert_called_once_with("/vault/Old.md", "/vault/New.md")

    def test_renamed_file_calls_dispatcher(self):
        """Dispatcher.on_file_moved should be called with all paths."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_dispatcher.on_file_moved.assert_called_once_with(
                "/vault", "/vault/New.md", "/vault/Old.md",
            )

    def test_moved_from_outside_calls_handle_file_created(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/new_from_outside.md", None)
            self.mock_vault_tree._handle_file_created.assert_called_once_with(
                "/vault", "/vault/new_from_outside.md",
            )

    def test_moved_from_outside_adds_to_indexes(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            p = Path(self._tmp) / "arrived.md"
            p.write_text("# Arrived", encoding="utf-8")
            h.on_file_moved("/vault", str(p), None)
            self.mock_file_index.add_file.assert_called_once_with(str(p))

    def test_moved_non_md_from_outside_no_index(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/readme.txt", None)
            self.mock_file_index.add_file.assert_not_called()


class TestMonitorHandlerContentChanged(unittest.TestCase):
    """Tests for MonitorHandler.on_content_changed."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_vault_tree = MagicMock()
        self.mock_tab_bar = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_debug_fn = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            dispatcher=self.mock_dispatcher,
            debug_fn=self.mock_debug_fn,
        )

    def test_updates_backlink_index(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/foo.md")
            self.mock_backlink.update_file.assert_called()

    def test_dispatcher_on_content_changed_called(self):
        """Dispatcher.on_content_changed should be called."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/foo.md")
            self.mock_dispatcher.on_content_changed.assert_called_once_with(
                "/vault", "/vault/foo.md",
            )

    def test_debug_fn_called_with_components(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/foo.md")
            self.assertIn(
                call(["backlink_index", "preview_html", "sidebar"]),
                [c for c in self.mock_debug_fn.call_args_list],
            )

    def test_unreadable_file_handled_gracefully(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/nonexistent.md")
            args = self.mock_backlink.update_file.call_args
            self.assertEqual(args[0][1], "")


class TestMonitorHandlerIntegration(unittest.TestCase):
    """Tests that MonitorHandler can be instantiated and called."""

    def test_instantiation(self):
        Handler = _handler_class()
        h = Handler(
            backlink_index=MagicMock(),
            file_index=MagicMock(),
            vault_tree=MagicMock(),
            tab_bar=MagicMock(),
            dispatcher=MagicMock(),
            debug_fn=MagicMock(),
        )
        self.assertTrue(hasattr(h, "on_file_created"))
        self.assertTrue(hasattr(h, "on_file_deleted"))
        self.assertTrue(hasattr(h, "on_file_moved"))
        self.assertTrue(hasattr(h, "on_content_changed"))

    def test_no_refresh_sidebar_methods(self):
        """MonitorHandler should not have _do_refresh_sidebar anymore."""
        Handler = _handler_class()
        h = Handler(
            backlink_index=MagicMock(),
            file_index=MagicMock(),
            vault_tree=MagicMock(),
            tab_bar=MagicMock(),
            dispatcher=MagicMock(),
            debug_fn=MagicMock(),
        )
        self.assertFalse(hasattr(h, "_do_refresh_sidebar"))
        self.assertFalse(hasattr(h, "_refresh_sidebar_fallback"))

    def test_no_banner_reload(self):
        """MonitorHandler should not have _on_banner_reload anymore."""
        Handler = _handler_class()
        h = Handler(
            backlink_index=MagicMock(),
            file_index=MagicMock(),
            vault_tree=MagicMock(),
            tab_bar=MagicMock(),
            dispatcher=MagicMock(),
            debug_fn=MagicMock(),
        )
        self.assertFalse(hasattr(h, "_on_banner_reload"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
