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
        self.mock_sidebar = MagicMock()
        self.mock_debug_fn = MagicMock()
        self.mock_refresh = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            sidebar=self.mock_sidebar,
            debug_fn=self.mock_debug_fn,
            refresh_sidebar=self.mock_refresh,
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
        self.mock_sidebar = MagicMock()
        self.mock_debug_fn = MagicMock()
        self.mock_refresh = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            sidebar=self.mock_sidebar,
            debug_fn=self.mock_debug_fn,
            refresh_sidebar=self.mock_refresh,
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

    def test_refresh_sidebar_called(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.mock_refresh.assert_called()

    def test_debug_fn_called(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_deleted("/vault", "/vault/foo.md")
            self.assertIn(
                call(["file_index", "backlink_index", "vault_tree", "tabs"]),
                [c for c in self.mock_debug_fn.call_args_list],
            )


class TestMonitorHandlerFileMoved(unittest.TestCase):
    """Tests for MonitorHandler.on_file_moved."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_vault_tree = MagicMock()
        self.mock_tab_bar = MagicMock()
        self.mock_sidebar = MagicMock()
        self.mock_debug_fn = MagicMock()
        self.mock_refresh = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            sidebar=self.mock_sidebar,
            debug_fn=self.mock_debug_fn,
            refresh_sidebar=self.mock_refresh,
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

    def test_renamed_file_refreshes_sidebar(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_refresh.assert_called()

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
        self.mock_sidebar = MagicMock()
        self.mock_debug_fn = MagicMock()
        self.mock_refresh = MagicMock()
        self._Handler = _handler_class()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make(self):
        return self._Handler(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            sidebar=self.mock_sidebar,
            debug_fn=self.mock_debug_fn,
            refresh_sidebar=self.mock_refresh,
        )

    def test_updates_backlink_index(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/foo.md")
            self.mock_backlink.update_file.assert_called()

    def test_refresh_sidebar_called(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_content_changed("/vault", "/vault/foo.md")
            self.mock_refresh.assert_called()

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
            sidebar=MagicMock(),
            debug_fn=MagicMock(),
            refresh_sidebar=MagicMock(),
        )
        self.assertTrue(hasattr(h, "on_file_created"))
        self.assertTrue(hasattr(h, "on_file_deleted"))
        self.assertTrue(hasattr(h, "on_file_moved"))
        self.assertTrue(hasattr(h, "on_content_changed"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
