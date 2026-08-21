"""Tests for markdown_vault.app.session_manager — SessionManager."""

import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from markdown_vault.app.session_manager import SessionManager


class _Tab:
    """Minimal tab stand-in for testing."""
    def __init__(self, file_path, view_mode="edit"):
        self.file_path = file_path
        self.view_mode = view_mode
        self.editor = unittest.mock.MagicMock()
        self.editor.file_path = file_path
        self.editor.zoom_factor = 1.0
        self.editor.capture_scroll_position.return_value = (0.0, 0)
        self.preview = unittest.mock.MagicMock()
        self.preview.zoom_level = 1.0
        self.preview.preview_scroll_position.return_value = 0.0


class _TabBarMock:
    """Mock for TabBar."""
    def __init__(self, paths=None, current_path=None):
        self._paths = paths or []
        self._current_path = current_path
        self._active = None
        self._tabs = {}

    def get_all_paths(self):
        return self._paths[:]

    def get_current_path(self):
        return self._current_path

    def get_tab(self, path):
        return self._tabs.get(path)

    def set_active_tab(self, path):
        self._active = path
        self._current_path = path

    def add_tab(self, file_path, editor=None, preview=None):
        tab = _Tab(file_path)
        self._paths.append(file_path)
        self._current_path = file_path
        self._tabs[file_path] = tab
        return tab


class _MRUMock:
    """Mock for MRUManager."""
    def __init__(self):
        self._tabs = []

    @property
    def tabs(self):
        return self._tabs[:]

    def push(self, path):
        if path in self._tabs:
            self._tabs.remove(path)
        self._tabs.append(path)

    def clear(self):
        self._tabs.clear()


class _ContentStackMock:
    """Mock for Gtk.Stack."""
    def __init__(self, children=None):
        self._children = children or {}

    def get_child_by_name(self, name):
        return self._children.get(name)


class _PanedMock:
    """Mock for Gtk.Paned."""
    def __init__(self, position=600):
        self._position = position

    def get_position(self):
        return self._position


# ---------------------------------------------------------------------------
# collect_tab_data
# ---------------------------------------------------------------------------

class TestCollectTabData(unittest.TestCase):
    """collect_tab_data gathers per-tab state."""

    def setUp(self):
        self._tab_bar = _TabBarMock()
        self._content_stack = _ContentStackMock()
        self._mgr = SessionManager(
            get_window_state=lambda: {},
            tab_bar=self._tab_bar,
            mru_manager=_MRUMock(),
        )

    def test_empty_tab_bar(self):
        """No tabs → empty list."""
        self.assertEqual(self._mgr.collect_tab_data(self._content_stack), [])

    def test_single_tab(self):
        """Single tab returns one entry."""
        self._tab_bar.add_tab("/tmp/note.md")
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["path"], "/tmp/note.md")
        self.assertEqual(data[0]["view_mode"], "edit")
        self.assertEqual(data[0]["split_position"], 600)

    def test_multiple_tabs(self):
        """Multiple tabs return correct count."""
        self._tab_bar.add_tab("/tmp/a.md")
        self._tab_bar.add_tab("/tmp/b.md")
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(len(data), 2)

    def test_split_position_default(self):
        """No paned child → default split position 600."""
        self._tab_bar.add_tab("/tmp/note.md")
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(data[0]["split_position"], 600)

    def test_zoom_values(self):
        """Zoom factors come from tab editor and preview."""
        tab = self._tab_bar.add_tab("/tmp/note.md")
        tab.editor.zoom_factor = 1.5
        tab.preview.zoom_level = 0.8
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(data[0]["editor_zoom"], 1.5)
        self.assertEqual(data[0]["preview_zoom"], 0.8)

    def test_scroll_position_fields(self):
        """Each tab carries its editor scroll+cursor and preview scroll so the
        reading position survives a restart — for every tab, not just the active
        one (a background tab keeps its scroll; a never-shown one reports 0)."""
        tab = self._tab_bar.add_tab("/tmp/note.md")
        tab.editor.capture_scroll_position.return_value = (250.0, 42)
        tab.preview.preview_scroll_position.return_value = 900.0
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(data[0]["editor_scroll"], 250.0)
        self.assertEqual(data[0]["editor_cursor"], 42)
        self.assertEqual(data[0]["preview_scroll"], 900.0)

    def test_skips_tabs_without_tab_object(self):
        """Tabs not found in get_tab are skipped."""
        self._tab_bar._paths = ["/tmp/missing.md"]
        self._tab_bar._tabs = {}
        data = self._mgr.collect_tab_data(self._content_stack)
        self.assertEqual(data, [])


# ---------------------------------------------------------------------------
# save_session
# ---------------------------------------------------------------------------

class TestSaveSession(unittest.TestCase):
    """save_session writes correct data to session.save_session."""

    def setUp(self):
        self._tab_bar = _TabBarMock()
        self._tab_bar.add_tab("/tmp/note.md")
        self._content_stack = _ContentStackMock()
        self._mru = _MRUMock()
        self._mgr = SessionManager(
            get_window_state=lambda: {
                "width": 1200, "height": 800,
                "sidebar_visible": True, "expanded_vaults": ["/v1"],
                "search_visible": False, "search_paned_position": 0,
                "sidebar_paned_position": 200, "main_paned_position": 300,
            },
            tab_bar=self._tab_bar,
            mru_manager=self._mru,
        )

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_session_calls_save(self, mock_session):
        """save_session calls session.save_session with correct args."""
        mock_session.load_session.return_value = {"vault_sessions": {}}
        self._mgr.save_session("/tmp/vault", self._content_stack)
        mock_session.save_session.assert_called_once()
        kwargs = mock_session.save_session.call_args[1]
        self.assertEqual(kwargs["width"], 1200)
        self.assertEqual(kwargs["height"], 800)
        self.assertTrue(kwargs["sidebar_visible"])
        self.assertEqual(kwargs["active_vault"], "/tmp/vault")

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_session_preserves_other_vaults(self, mock_session):
        """save_session preserves other vaults' session data."""
        mock_session.load_session.return_value = {
            "vault_sessions": {"/other/vault": {"tabs": []}}
        }
        self._mgr.save_session("/tmp/vault", self._content_stack)
        kwargs = mock_session.save_session.call_args[1]
        self.assertIn("/other/vault", kwargs["vault_sessions"])
        self.assertIn("/tmp/vault", kwargs["vault_sessions"])

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_session_no_active_vault_skips(self, mock_session):
        """save_session with None active_vault skips tab collection."""
        self._mgr.save_session(None, self._content_stack)
        kwargs = mock_session.save_session.call_args[1]
        self.assertIsNone(kwargs["active_vault"])

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_session_includes_mru(self, mock_session):
        """save_session includes MRU data in vault session."""
        mock_session.load_session.return_value = {"vault_sessions": {}}
        self._mru.push("/tmp/note.md")
        self._mgr.save_session("/tmp/vault", self._content_stack)
        kwargs = mock_session.save_session.call_args[1]
        vault_data = kwargs["vault_sessions"]["/tmp/vault"]
        self.assertEqual(vault_data["mru"], ["/tmp/note.md"])


# ---------------------------------------------------------------------------
# save_vault_session
# ---------------------------------------------------------------------------

class TestSaveVaultSession(unittest.TestCase):
    """save_vault_session delegates to save_session."""

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_vault_session_skips_none(self, mock_session):
        """save_vault_session with None vault does nothing."""
        tab_bar = _TabBarMock()
        mgr = SessionManager(
            get_window_state=lambda: {},
            tab_bar=tab_bar,
            mru_manager=_MRUMock(),
        )
        mgr.save_vault_session(None, _ContentStackMock())
        mock_session.save_session.assert_not_called()

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_save_vault_session_delegates(self, mock_session):
        """save_vault_session calls save_session."""
        mock_session.load_session.return_value = {"vault_sessions": {}}
        tab_bar = _TabBarMock()
        mgr = SessionManager(
            get_window_state=lambda: {
                "width": 800, "height": 600,
                "sidebar_visible": False, "expanded_vaults": [],
                "search_visible": False, "search_paned_position": 0,
                "sidebar_paned_position": 0, "main_paned_position": 0,
            },
            tab_bar=tab_bar,
            mru_manager=_MRUMock(),
        )
        mgr.save_vault_session("/v", _ContentStackMock())
        mock_session.save_session.assert_called_once()


# ---------------------------------------------------------------------------
# restore_vault_session
# ---------------------------------------------------------------------------

class TestRestoreVaultSession(unittest.TestCase):
    """restore_vault_session restores tabs from session."""

    def setUp(self):
        self._tab_bar = _TabBarMock()
        self._mru = _MRUMock()
        self._mgr = SessionManager(
            get_window_state=lambda: {},
            tab_bar=self._tab_bar,
            mru_manager=self._mru,
        )
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_opens_tabs(self, mock_session):
        """restore_vault_session opens each saved tab."""
        md_a = os.path.join(self._tmp, "a.md")
        md_b = os.path.join(self._tmp, "b.md")
        Path(md_a).write_text("# A")
        Path(md_b).write_text("# B")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [
                        {"path": md_a, "view_mode": "edit"},
                        {"path": md_b, "view_mode": "split",
                         "split_position": 400},
                    ],
                    "active_tab": md_b,
                    "mru": [md_a, md_b],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d

        opened = []
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: opened.append((fp, kw)),
            mru_push_fn=lambda fp: None,
        )
        self.assertEqual(len(opened), 2)
        self.assertEqual(opened[0][0], md_a)
        self.assertEqual(opened[0][1]["view_mode"], "edit")
        self.assertEqual(opened[1][1]["split_position"], 400)

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_applies_the_saved_scroll_per_tab(self, mock_session):
        """After opening each tab its saved reading position is applied: the
        editor instantly, the preview armed for its render — for every tab."""
        md_a = os.path.join(self._tmp, "a.md")
        Path(md_a).write_text("# A")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [{"path": md_a, "view_mode": "edit",
                              "editor_scroll": 250.0, "editor_cursor": 42,
                              "preview_scroll": 900.0}],
                    "active_tab": md_a, "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: self._tab_bar.add_tab(fp),
            mru_push_fn=lambda fp: None,
        )
        tab = self._tab_bar.get_tab(md_a)
        tab.editor.restore_scroll_position.assert_called_once_with(250.0, 42)
        tab.preview.arm_scroll.assert_called_once_with(900.0)

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_skips_the_nav_target_so_history_wins(self, mock_session):
        """A cross-vault back/forward restores the navigation target from the
        history (post_open_fn -> restore_current). restore_vault_session must NOT
        also apply that tab's saved scroll, or the target moves twice."""
        md_a = os.path.join(self._tmp, "a.md")
        Path(md_a).write_text("# A")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [{"path": md_a, "editor_scroll": 250.0,
                              "editor_cursor": 42, "preview_scroll": 900.0}],
                    "active_tab": md_a, "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: self._tab_bar.add_tab(fp),
            mru_push_fn=lambda fp: None,
            nav_target=md_a,
        )
        tab = self._tab_bar.get_tab(md_a)
        tab.editor.restore_scroll_position.assert_not_called()
        tab.preview.arm_scroll.assert_not_called()

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_ignores_broken_scroll_values(self, mock_session):
        """A corrupted "editor_scroll": "x" must not reach restore_scroll_position;
        broken values are filtered, not passed through (bool is not a number)."""
        md_a = os.path.join(self._tmp, "a.md")
        Path(md_a).write_text("# A")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [{"path": md_a, "editor_scroll": "x",
                              "editor_cursor": None, "preview_scroll": True}],
                    "active_tab": md_a, "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: self._tab_bar.add_tab(fp),
            mru_push_fn=lambda fp: None,
        )
        tab = self._tab_bar.get_tab(md_a)
        tab.editor.restore_scroll_position.assert_not_called()
        tab.preview.arm_scroll.assert_not_called()

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_sets_active_tab(self, mock_session):
        """restore_vault_session sets the active tab."""
        md_a = os.path.join(self._tmp, "a.md")
        Path(md_a).write_text("# A")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [{"path": md_a, "view_mode": "edit"}],
                    "active_tab": md_a,
                    "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._tab_bar.add_tab(md_a)

        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: None,
            mru_push_fn=lambda fp: None,
        )
        self.assertEqual(self._tab_bar._active, md_a)

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_rebuilds_mru(self, mock_session):
        """restore_vault_session rebuilds MRU from session data."""
        md_a = os.path.join(self._tmp, "a.md")
        md_b = os.path.join(self._tmp, "b.md")
        Path(md_a).write_text("# A")
        Path(md_b).write_text("# B")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [
                        {"path": md_a, "view_mode": "edit"},
                        {"path": md_b, "view_mode": "edit"},
                    ],
                    "active_tab": md_b,
                    "mru": [md_b, md_a],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._tab_bar.add_tab(md_a)
        self._tab_bar.add_tab(md_b)

        pushed = []
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: None,
            mru_push_fn=lambda fp: pushed.append(fp),
        )
        self.assertEqual(pushed, [md_a, md_b])

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_mru_fallback_to_tab_order(self, mock_session):
        """restore_vault_session falls back to tab order when no MRU."""
        md_a = os.path.join(self._tmp, "a.md")
        md_b = os.path.join(self._tmp, "b.md")
        Path(md_a).write_text("# A")
        Path(md_b).write_text("# B")
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [
                        {"path": md_a, "view_mode": "edit"},
                        {"path": md_b, "view_mode": "edit"},
                    ],
                    "active_tab": md_b,
                    "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.side_effect = lambda d: d
        self._tab_bar.add_tab(md_a)
        self._tab_bar.add_tab(md_b)

        pushed = []
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: None,
            mru_push_fn=lambda fp: pushed.append(fp),
        )
        self.assertIn(md_a, pushed)
        self.assertIn(md_b, pushed)

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_empty_vault(self, mock_session):
        """restore_vault_session handles empty vault data."""
        mock_session.load_session.return_value = {"vault_sessions": {}}
        mock_session.prune_vault_session.side_effect = lambda d: d

        opened = []
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: opened.append(fp),
            mru_push_fn=lambda fp: None,
        )
        self.assertEqual(opened, [])

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_no_longer_takes_history_callbacks(self, mock_session):
        """History is the caller's business now: the vault switch and startup
        wrap the whole restore in one suppress clamp, and the "here I landed"
        entry is pushed by the caller, bound to whether a file is opened after.
        The method must not silently accept the old callbacks again."""
        mock_session.load_session.return_value = {"vault_sessions": {}}
        mock_session.prune_vault_session.side_effect = lambda d: d
        for callback in ("push_history_fn", "suppress_nav_fn"):
            with self.assertRaises(TypeError, msg=callback):
                self._mgr.restore_vault_session(
                    "/vault",
                    open_file_fn=lambda fp, **kw: None,
                    mru_push_fn=lambda fp: None,
                    **{callback: lambda *a: None},
                )

    @unittest.mock.patch("markdown_vault.app.session_manager.session")
    def test_restore_skips_nonexistent_files(self, mock_session):
        """restore_vault_session skips tabs whose files don't exist."""
        mock_session.load_session.return_value = {
            "vault_sessions": {
                "/vault": {
                    "tabs": [
                        {"path": "/nonexistent/file.md", "view_mode": "edit"},
                    ],
                    "active_tab": None,
                    "mru": [],
                }
            }
        }
        mock_session.prune_vault_session.return_value = {"tabs": [], "active_tab": None, "mru": []}

        opened = []
        self._mgr.restore_vault_session(
            "/vault",
            open_file_fn=lambda fp, **kw: opened.append(fp),
            mru_push_fn=lambda fp: None,
        )
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
