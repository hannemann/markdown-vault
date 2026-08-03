"""Tests for markdown_vault.event_router — FileEventDispatcher."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from markdown_vault.event_router import (
    FileEvent,
    FileEventDispatcher,
    SidebarRefresher,
)


class TestFileEvent(unittest.TestCase):
    """Tests for the FileEvent NamedTuple."""

    def test_file_event_fields(self):
        """FileEvent should set all fields correctly."""
        event = FileEvent(
            vault_path="/vault",
            file_path="/vault/foo.md",
            event_type="created",
            other_path=None,
        )
        self.assertEqual(event.vault_path, "/vault")
        self.assertEqual(event.file_path, "/vault/foo.md")
        self.assertEqual(event.event_type, "created")
        self.assertIsNone(event.other_path)

    def test_file_event_with_other_path(self):
        """FileEvent should store other_path for moved/renamed events."""
        event = FileEvent(
            vault_path="/vault",
            file_path="/vault/new.md",
            event_type="moved",
            other_path="/vault/old.md",
        )
        self.assertEqual(event.other_path, "/vault/old.md")

    def test_file_event_defaults_other_path(self):
        """FileEvent should default other_path to None."""
        event = FileEvent(
            vault_path="/vault",
            file_path="/vault/foo.md",
            event_type="deleted",
        )
        self.assertIsNone(event.other_path)


class TestSidebarRefresher(unittest.TestCase):
    """Tests for SidebarRefresher Protocol."""

    def test_protocol_structural_typing(self):
        """Any object with refresh(FileEvent) is structurally compatible."""
        class Stub:
            def refresh(self, event):
                pass

        stub = Stub()
        self.assertTrue(hasattr(stub, "refresh"))


class TestFileEventDispatcher(unittest.TestCase):
    """Tests for FileEventDispatcher."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.mock_sidebar = MagicMock()
        self.mock_backlink = MagicMock()
        self.mock_file_index = MagicMock()
        self.mock_debug_fn = MagicMock()
        self._dispatcher = FileEventDispatcher(
            sidebar_refresher=self.mock_sidebar,
            backlink_index=self.mock_backlink,
            file_index=self.mock_file_index,
            debug_fn=self.mock_debug_fn,
        )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_on_file_created_calls_sidebar_refresh(self):
        """File created event should call Sidebar.refresh() with 'created'."""
        self._dispatcher.on_file_created("/vault", "/vault/foo.md")
        self.mock_sidebar.refresh.assert_called_once()
        event = self.mock_sidebar.refresh.call_args[0][0]
        self.assertEqual(event.event_type, "created")
        self.assertEqual(event.file_path, "/vault/foo.md")
        self.assertEqual(event.vault_path, "/vault")

    def test_on_file_deleted_calls_sidebar_refresh(self):
        """File deleted event should call Sidebar.refresh() with 'deleted'."""
        self._dispatcher.on_file_deleted("/vault", "/vault/foo.md")
        self.mock_sidebar.refresh.assert_called_once()
        event = self.mock_sidebar.refresh.call_args[0][0]
        self.assertEqual(event.event_type, "deleted")
        self.assertEqual(event.file_path, "/vault/foo.md")

    def test_on_file_moved_calls_sidebar_refresh(self):
        """File moved event should call Sidebar.refresh() with 'moved'."""
        self._dispatcher.on_file_moved(
            "/vault", "/vault/new.md", "/vault/old.md",
        )
        self.mock_sidebar.refresh.assert_called_once()
        event = self.mock_sidebar.refresh.call_args[0][0]
        self.assertEqual(event.event_type, "moved")
        self.assertEqual(event.file_path, "/vault/new.md")
        self.assertEqual(event.other_path, "/vault/old.md")

    def test_on_file_moved_without_other_path(self):
        """File moved event without other_path should send 'moved' with None."""
        self._dispatcher.on_file_moved("/vault", "/vault/new.md", None)
        self.mock_sidebar.refresh.assert_called_once()
        event = self.mock_sidebar.refresh.call_args[0][0]
        self.assertEqual(event.event_type, "moved")
        self.assertIsNone(event.other_path)

    def test_on_content_changed_calls_sidebar_refresh(self):
        """Content changed event should call Sidebar.refresh() with 'content_changed'."""
        self._dispatcher.on_content_changed("/vault", "/vault/foo.md")
        self.mock_sidebar.refresh.assert_called_once()
        event = self.mock_sidebar.refresh.call_args[0][0]
        self.assertEqual(event.event_type, "content_changed")


class TestSidebarRefreshEvent(unittest.TestCase):
    """Tests for Sidebar.refresh() dispatching on FileEvent.

    The sidebar must always refresh with the active tab's info,
    never with the event's file path and empty text.
    """

    def _make_sidebar(self, active_file="/vault/active.md", active_text="# Active"):
        from markdown_vault.sidebar import Sidebar

        sidebar = Sidebar.__new__(Sidebar)
        sidebar._get_active_tab_info = lambda: (active_file, active_text)
        sidebar._current_file = active_file
        sidebar._vault_paths = []
        sidebar._refresh_outline = MagicMock()
        sidebar._refresh_backlinks = MagicMock()
        sidebar._refresh_metadata = MagicMock()
        sidebar._refresh_details = MagicMock()
        sidebar._refresh_git = MagicMock()
        sidebar.get_visible = MagicMock(return_value=False)
        return sidebar

    def test_sidebar_refresh_match_created(self):
        """Sidebar.refresh() with 'created' should call update_for_file()
        with the active tab's info, not the event file."""
        sidebar = self._make_sidebar()

        sidebar.refresh(
            FileEvent("/vault", "/vault/other.md", "created"),
        )
        sidebar._refresh_outline.assert_called_once_with("# Active")
        sidebar._refresh_backlinks.assert_called_once_with("/vault/active.md")
        sidebar._refresh_details.assert_called_once_with("/vault/active.md", "# Active")

    def test_sidebar_refresh_match_deleted(self):
        """Sidebar.refresh() with 'deleted' should call update_for_file()."""
        sidebar = self._make_sidebar()

        sidebar.refresh(
            FileEvent("/vault", "/vault/other.md", "deleted"),
        )
        sidebar._refresh_backlinks.assert_called_once_with("/vault/active.md")

    def test_sidebar_refresh_match_moved(self):
        """Sidebar.refresh() with 'moved' should call update_for_file()."""
        sidebar = self._make_sidebar()

        sidebar.refresh(
            FileEvent("/vault", "/vault/other.md", "moved", "/vault/old.md"),
        )
        sidebar._refresh_backlinks.assert_called_once_with("/vault/active.md")

    def test_sidebar_refresh_match_content_changed(self):
        """Sidebar.refresh() with 'content_changed' should call
        update_text_only() with the active tab's info."""
        sidebar = self._make_sidebar()
        sidebar.get_visible = MagicMock(return_value=True)
        sidebar._stack = MagicMock()
        sidebar._stack.get_visible_child_name = MagicMock(return_value="git")

        sidebar.refresh(
            FileEvent("/vault", "/vault/other.md", "content_changed"),
        )
        sidebar._refresh_backlinks.assert_not_called()
        sidebar._refresh_outline.assert_called_once_with("# Active")
        sidebar._refresh_details.assert_called_once_with("/vault/active.md", "# Active")

    def test_sidebar_refresh_without_callback_does_nothing(self):
        """When _get_active_tab_info is not set, refresh does nothing
        (safe no-op: never hijack to event file)."""
        from markdown_vault.sidebar import Sidebar

        sidebar = Sidebar.__new__(Sidebar)
        sidebar._refresh_outline = MagicMock()
        sidebar._refresh_backlinks = MagicMock()
        sidebar._refresh_details = MagicMock()
        sidebar.get_visible = MagicMock(return_value=False)

        sidebar.refresh(
            FileEvent("/vault", "/vault/other.md", "created"),
        )
        sidebar._refresh_outline.assert_not_called()
        sidebar._refresh_backlinks.assert_not_called()
        sidebar._refresh_details.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
