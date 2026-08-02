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
            self.mock_file_index.add_file.assert_called_once_with(str(p), vault_path="/vault")

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

    def test_created_md_with_links_refreshes_sidebar(self):
        """A newly created .md file with links must refresh the sidebar so
        backlinks appear without waiting for a tab switch."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            p = Path(self._tmp) / "new.md"
            p.write_text("[[Target]]", encoding="utf-8")
            h.on_file_created("/vault", str(p))
            self.mock_backlink.update_file.assert_called_once_with(str(p), "[[Target]]")
            self.mock_dispatcher.on_file_created.assert_called_once_with("/vault", str(p))

    def test_created_non_md_does_not_refresh_sidebar(self):
        """A non-.md file must not trigger a sidebar refresh."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_created("/vault", "/vault/readme.txt")
            self.mock_dispatcher.on_file_created.assert_not_called()


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
            self.mock_backlink.remove_wikilinks.assert_called_once_with("/vault/Notes.md")
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
                call(["file_index", "backlink_index", "vault_tree", "tabs", "sidebar"]),
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

    def _make(self, **kwargs):
        args = dict(
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            vault_tree=self.mock_vault_tree,
            tab_bar=self.mock_tab_bar,
            dispatcher=self.mock_dispatcher,
            debug_fn=self.mock_debug_fn,
        )
        args.update(kwargs)
        return self._Handler(**args)

    def test_renamed_file_renames_wikilinks(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_backlink.rename_wikilinks.assert_called_once_with(
                "/vault/Old.md", "/vault/New.md"
            )

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
            self.mock_file_index.add_file.assert_called_once_with(str(p), vault_path="/vault")

    def test_moved_non_md_from_outside_no_index(self):
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            h.on_file_moved("/vault", "/vault/readme.txt", None)
            self.mock_file_index.add_file.assert_not_called()

    def test_move_back_to_md_notifies_sidebar_dispatcher(self):
        """Re-renaming a non-.md file back to .md must refresh the sidebar via
        the dispatcher so backlinks reappear without a tab switch."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            p = Path(self._tmp) / "sub"
            p.mkdir()
            md = p / "Back.md"
            md.write_text("[[Target]]", encoding="utf-8")
            h.on_file_moved("/vault", str(md), str(p / "Back.txt"))
            self.mock_backlink.update_file.assert_called_once_with(str(md), "[[Target]]")
            self.mock_dispatcher.on_file_created.assert_called_once_with("/vault", str(md))
            self.mock_dispatcher.on_content_changed.assert_not_called()

    def test_moved_in_md_with_links_refreshes_sidebar(self):
        """A .md file moved in from outside with links also refreshes the
        sidebar backlinks (consistent with _notify_external_change)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            p = Path(self._tmp) / "arrived.md"
            p.write_text("[[Target]]", encoding="utf-8")
            h.on_file_moved("/vault", str(p), None)
            self.mock_dispatcher.on_file_created.assert_called_once_with("/vault", str(p))

    def test_untracked_md_to_non_md_rename_purges_old_index_entries(self):
        """A .md file that is not open/indexed but renamed to a non-.md
        extension must still have its old index entries purged (treated like
        a deletion — no stale backlinks may remain)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/sub/old.txt", "/vault/sub/old.md")
            self.mock_file_index.remove_file.assert_called_once_with("/vault/sub/old.md")
            self.mock_backlink.remove_file.assert_called_once_with("/vault/sub/old.md")
            self.mock_backlink.rename_file.assert_not_called()

    def test_untracked_md_to_md_rename_purges_old_entry(self):
        """An untracked .md→.md rename removes the old index entry while the
        new one is indexed (no stale path may remain)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            p = Path(self._tmp) / "new.md"
            p.write_text("# New", encoding="utf-8")
            h.on_file_moved("/vault", str(p), "/vault/old.md")
            self.mock_file_index.remove_file.assert_called_once_with("/vault/old.md")
            self.mock_file_index.add_file.assert_called_once_with(str(p), vault_path="/vault")
            self.mock_backlink.update_file.assert_called_once_with(str(p), "# New")

    def test_subdir_md_to_md_rename_rewrites_wikilinks_when_untracked(self):
        """R14.3: an external rename of a subdirectory .md that is not in an
        open tab (and not in the root-only FileIndex) must still rewrite
        inbound backlinks — not be treated as a brand-new file."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/sub/New.md", "/vault/sub/Old.md")
            self.mock_backlink.rename_wikilinks.assert_called_once_with(
                "/vault/sub/Old.md", "/vault/sub/New.md",
            )
            self.mock_backlink.rename_file.assert_called_once_with(
                "/vault/sub/Old.md", "/vault/sub/New.md",
            )
            self.mock_file_index.remove_file.assert_not_called()
            self.mock_dispatcher.on_content_changed.assert_not_called()

    # ── Atomic-save tests ────────────────────────────────────────────

    def test_atomic_save_over_open_file_triggers_external_change(self):
        """Atomic save (temp→target) over an open tab: notify external change."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/target.md"]
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/target.md", "/vault/.target.md.tmp.1234")
            self.mock_backlink.update_file.assert_called()
            self.mock_dispatcher.on_content_changed.assert_called_once_with(
                "/vault", "/vault/target.md",
            )

    def test_atomic_save_over_open_file_fires_banner_callback(self):
        """Atomic save over open tab calls the injected banner callback."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            banner_cb = MagicMock()
            h = self._make(notify_banner_cb=banner_cb)
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/target.md"]
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/target.md", "/vault/.target.md.tmp.1234")
            banner_cb.assert_called_once_with("/vault", "/vault/target.md")

    def test_atomic_save_over_indexed_file_triggers_external_change(self):
        """Atomic save where dest is in file_index (not open tab) also triggers."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.side_effect = (
                lambda p: p == "/vault/target.md"
            )
            h.on_file_moved("/vault", "/vault/target.md", "/vault/.tmp.xyz.md")
            self.mock_dispatcher.on_content_changed.assert_called_once_with(
                "/vault", "/vault/target.md",
            )

    def test_atomic_save_new_file_creates_tree_entry(self):
        """Atomic save to a brand-new (untracked) path: treat as creation."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = []
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/neues.md", "/vault/.neues.md.tmp.42")
            self.mock_vault_tree._handle_file_created.assert_called_once_with(
                "/vault", "/vault/neues.md",
            )
            self.mock_file_index.add_file.assert_called_once_with(
                "/vault/neues.md", vault_path="/vault",
            )

    def test_genuine_rename_still_works(self):
        """Source-tracked rename still takes the rename path (no banner)."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            banner_cb = MagicMock()
            h = self._make(notify_banner_cb=banner_cb)
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/Old.md"]
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/New.md", "/vault/Old.md")
            self.mock_backlink.rename_wikilinks.assert_called_once_with(
                "/vault/Old.md", "/vault/New.md"
            )
            self.mock_backlink.rename_file.assert_called_once_with("/vault/Old.md", "/vault/New.md")
            banner_cb.assert_not_called()

    def test_md_to_non_md_rename_handled_as_deletion(self):
        """Renaming a tracked .md file to a non-.md extension must be treated
        like a deletion: close the tab, remove the tree entry and purge all
        index entries — nothing may remain under the new extension."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/Old.md"]
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/Old.txt", "/vault/Old.md")
            # Tree entry of the old .md path is removed, not renamed to .txt
            self.mock_vault_tree._handle_file_deleted.assert_called_once_with("/vault/Old.md")
            self.mock_vault_tree._handle_file_moved.assert_not_called()
            # Open tab is closed, never kept open under the new extension
            self.mock_tab_bar.close_tab.assert_called_once_with("/vault/Old.md")
            self.mock_tab_bar.update_path.assert_not_called()
            # Backlink + file index entries of the old path are purged
            self.mock_backlink.remove_wikilinks.assert_called_once_with("/vault/Old.md")
            self.mock_backlink.remove_file.assert_called_once_with("/vault/Old.md")
            self.mock_file_index.remove_file.assert_called_once_with("/vault/Old.md")
            # No rename/wikilink redirect onto the new extension
            self.mock_backlink.rename_file.assert_not_called()
            self.mock_backlink.rename_wikilinks.assert_not_called()
            self.mock_file_index.rename_file.assert_not_called()

    def test_md_to_non_md_rename_closes_tab_when_not_indexed(self):
        """A .md→.txt rename of a file that is only open as a tab (not in the
        file index) still closes the tab and purges the backlink entry."""
        with patch("markdown_vault.monitor_handler.GLib", _GLibMock):
            h = self._make()
            self.mock_tab_bar.get_all_paths.return_value = ["/vault/sub/Old.md"]
            self.mock_file_index.has_path.return_value = False
            h.on_file_moved("/vault", "/vault/sub/Old.txt", "/vault/sub/Old.md")
            self.mock_tab_bar.close_tab.assert_called_once_with("/vault/sub/Old.md")
            self.mock_tab_bar.update_path.assert_not_called()
            self.mock_backlink.remove_wikilinks.assert_called_once_with("/vault/sub/Old.md")


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
