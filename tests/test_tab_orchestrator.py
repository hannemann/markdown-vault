"""Tests for markdown_vault.app.tab_manager — TabOrchestrator."""

import unittest
import unittest.mock

from markdown_vault.app.tab_manager import TabOrchestrator


class _Tab:
    """Minimal tab stand-in for testing."""
    def __init__(self, file_path, view_mode="edit"):
        self.file_path = file_path
        self.view_mode = view_mode
        self.editor = unittest.mock.MagicMock()
        self.editor.file_path = file_path
        self.editor.get_text.return_value = ""


class _TabBarMock:
    """Mock for TabBar."""
    def __init__(self, paths=None, current_path=None):
        self._paths = paths or []
        self._current_path = current_path
        self._active = None
        self._callbacks = {}

    def get_all_paths(self):
        return self._paths[:]

    def get_current_path(self):
        return self._current_path

    def get_current_tab(self):
        if self._current_path and self._current_path in self._paths:
            return _Tab(self._current_path)
        return None

    def set_active_tab(self, path):
        self._active = path
        self._current_path = path

    def add_tab(self, file_path, editor, preview, **kwargs):
        tab = _Tab(file_path)
        self._paths.append(file_path)
        self._current_path = file_path
        return tab

    def _set_tab_unmodified(self, path, dirty):
        pass


class _MRUMock:
    """Mock for MRUManager."""
    def __init__(self):
        self._tabs = []
        self.pushed = []
        self.removed = []

    @property
    def tabs(self):
        return self._tabs[:]

    def push(self, fp):
        self.pushed.append(fp)
        if fp in self._tabs:
            self._tabs.remove(fp)
        self._tabs.insert(0, fp)

    def remove(self, fp):
        self.removed.append(fp)
        if fp in self._tabs:
            self._tabs.remove(fp)

    def next(self):
        if len(self._tabs) > 1:
            return self._tabs[1]
        return None

    def prev(self):
        if len(self._tabs) > 1:
            return self._tabs[-1]
        return None


class _SidebarMock:
    def __init__(self):
        self.updates = []

    def update_for_file(self, file_path, text=""):
        self.updates.append((file_path, text))


class _ContentStackMock:
    def __init__(self):
        self.visible = None

    def set_visible_child_name(self, name):
        self.visible = name


class _VaultTreeMock:
    def get_vault_paths(self):
        return ["/vault"]


def _make_orchestrator(
    paths=None,
    current_path=None,
    tab_switch_mode="linear",
):
    """Helper to build a TabOrchestrator with mocks."""
    bar = _TabBarMock(paths or [], current_path)
    mru = _MRUMock()
    sidebar = _SidebarMock()
    settings = {"tabs": {"switch_mode": tab_switch_mode}}
    stack = _ContentStackMock()
    file_index = unittest.mock.MagicMock()
    backlink_index = unittest.mock.MagicMock()
    vault_tree = _VaultTreeMock()

    # Track callback invocations.
    cb = {
        "on_preview_link_clicked": unittest.mock.MagicMock(),
        "on_preview_link_new_tab": unittest.mock.MagicMock(),
        "on_preview_link_not_found": unittest.mock.MagicMock(),
        "on_preview_checkbox_toggled": unittest.mock.MagicMock(),
        "on_editor_text_changed": unittest.mock.MagicMock(),
        "on_editor_modified": unittest.mock.MagicMock(),
        "apply_view_mode": unittest.mock.MagicMock(),
        "sync_view_toggle": unittest.mock.MagicMock(),
        "refresh_preview": unittest.mock.MagicMock(),
        "push_history": unittest.mock.MagicMock(),
        "on_banner_reload": unittest.mock.MagicMock(),
        "on_banner_dismiss": unittest.mock.MagicMock(),
        "dump_debug": unittest.mock.MagicMock(),
    }

    orch = TabOrchestrator(
        tab_bar=bar,
        mru_manager=mru,
        sidebar=sidebar,
        settings=settings,
        content_stack=stack,
        file_index=file_index,
        backlink_index=backlink_index,
        vault_tree=vault_tree,
        callbacks=cb,
    )
    return orch, bar, mru, sidebar, stack, cb


class TestOnTabChanged(unittest.TestCase):
    """Tests for TabOrchestrator.on_tab_changed."""

    def test_pushes_mru(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md"], current_path="/a.md",
        )
        orch.on_tab_changed("/a.md")
        self.assertIn("/a.md", mru.pushed)

    def test_refreshes_sidebar(self):
        orch, bar, mru, sidebar, *_ = _make_orchestrator(
            paths=["/a.md"], current_path="/a.md",
        )
        orch.on_tab_changed("/a.md")
        self.assertEqual(len(sidebar.updates), 1)
        self.assertEqual(sidebar.updates[0][0], "/a.md")

    def test_sets_content_stack(self):
        orch, bar, mru, sidebar, stack, *_ = _make_orchestrator(
            paths=["/a.md"], current_path="/a.md",
        )
        orch.on_tab_changed("/a.md")
        self.assertEqual(stack.visible, "/a.md")

    def test_calls_apply_view_mode(self):
        orch, bar, mru, sidebar, stack, cb = _make_orchestrator(
            paths=["/a.md"], current_path="/a.md",
        )
        orch.on_tab_changed("/a.md")
        cb["apply_view_mode"].assert_called_once()

    def test_calls_refresh_preview(self):
        orch, bar, mru, sidebar, stack, cb = _make_orchestrator(
            paths=["/a.md"], current_path="/a.md",
        )
        orch.on_tab_changed("/a.md")
        cb["refresh_preview"].assert_called_once()

    def test_noop_when_no_tab(self):
        orch, bar, mru, sidebar, stack, cb = _make_orchestrator()
        orch.on_tab_changed("/missing.md")
        sidebar.updates.clear()
        # No crash, no sidebar update.
        self.assertEqual(len(sidebar.updates), 0)


class TestNextPrevTab(unittest.TestCase):
    """Tests for TabOrchestrator.next_tab / prev_tab in linear mode."""

    def test_next_tab_linear(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md", "/c.md"],
            current_path="/a.md",
            tab_switch_mode="linear",
        )
        orch.next_tab()
        self.assertEqual(bar._active, "/b.md")

    def test_prev_tab_linear(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md", "/c.md"],
            current_path="/b.md",
            tab_switch_mode="linear",
        )
        orch.prev_tab()
        self.assertEqual(bar._active, "/a.md")

    def test_next_tab_wraps_around(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/b.md",
            tab_switch_mode="linear",
        )
        orch.next_tab()
        self.assertEqual(bar._active, "/a.md")

    def test_prev_tab_wraps_around(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/a.md",
            tab_switch_mode="linear",
        )
        orch.prev_tab()
        self.assertEqual(bar._active, "/b.md")

    def test_noop_when_single_tab(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md"],
            current_path="/a.md",
            tab_switch_mode="linear",
        )
        orch.next_tab()
        # No crash, no change.
        self.assertIsNone(bar._active)

    def test_next_tab_mru(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/a.md",
            tab_switch_mode="mru",
        )
        mru._tabs = ["/a.md", "/b.md"]
        orch.next_tab()
        # MRU mode: next() returns /b.md → open_tab activates it.
        self.assertEqual(bar._active, "/b.md")

    def test_prev_tab_mru(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/a.md",
            tab_switch_mode="mru",
        )
        mru._tabs = ["/a.md", "/b.md"]
        orch.prev_tab()
        # MRU mode: prev() returns /b.md → open_tab activates it.
        self.assertEqual(bar._active, "/b.md")


class TestCycleTab(unittest.TestCase):
    """Tests for TabOrchestrator.cycle_tab."""

    def test_cycle_forward(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md", "/c.md"],
            current_path="/a.md",
        )
        orch.cycle_tab(+1)
        self.assertEqual(bar._active, "/b.md")

    def test_cycle_backward(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md", "/c.md"],
            current_path="/b.md",
        )
        orch.cycle_tab(-1)
        self.assertEqual(bar._active, "/a.md")

    def test_cycle_wraps_forward(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/b.md",
        )
        orch.cycle_tab(+1)
        self.assertEqual(bar._active, "/a.md")

    def test_cycle_wraps_backward(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md", "/b.md"],
            current_path="/a.md",
        )
        orch.cycle_tab(-1)
        self.assertEqual(bar._active, "/b.md")

    def test_noop_when_fewer_than_two(self):
        orch, bar, mru, *_ = _make_orchestrator(
            paths=["/a.md"],
            current_path="/a.md",
        )
        orch.cycle_tab(+1)
        self.assertIsNone(bar._active)


class TestQueries(unittest.TestCase):
    """Tests for get_tab_count and is_single_tab."""

    def test_get_tab_count(self):
        orch, *_ = _make_orchestrator(paths=["/a.md", "/b.md", "/c.md"])
        self.assertEqual(orch.get_tab_count(), 3)

    def test_get_tab_count_empty(self):
        orch, *_ = _make_orchestrator()
        self.assertEqual(orch.get_tab_count(), 0)

    def test_is_single_tab_true(self):
        orch, *_ = _make_orchestrator(paths=["/a.md"])
        self.assertTrue(orch.is_single_tab())

    def test_is_single_tab_false(self):
        orch, *_ = _make_orchestrator(paths=["/a.md", "/b.md"])
        self.assertFalse(orch.is_single_tab())


class TestSignalForwarding(unittest.TestCase):
    """Tests that signal handler methods forward arguments correctly.

    These tests verify the exact argument signatures that GTK signals
    provide match what MainWindow callbacks expect.  A mismatch here
    means wikilink clicks or editor events silently fail.
    """

    def _make_with_spy(self):
        """Build orchestrator with spy callbacks that record calls."""
        orch, bar, mru, sidebar, stack, cb = _make_orchestrator()
        # Spy dicts to record all callback invocations.
        spy = {k: [] for k in cb}
        for name, fn in cb.items():
            def make_spy(n=name, orig=fn):
                def spy_fn(*args, **kwargs):
                    spy[n].append((args, kwargs))
                    return orig(*args, **kwargs)
                return spy_fn
            cb[name] = make_spy()
        return orch, spy

    def test_preview_link_clicked_forwards_widget_path_and_fragment(self):
        orch, spy = self._make_with_spy()
        mock_widget = unittest.mock.MagicMock()
        orch._on_preview_link_clicked(mock_widget, "/doc.md", "Some Heading")
        self.assertEqual(len(spy["on_preview_link_clicked"]), 1)
        args = spy["on_preview_link_clicked"][0][0]
        self.assertIs(args[0], mock_widget)
        self.assertEqual(args[1], "/doc.md")
        self.assertEqual(args[2], "Some Heading")

    def test_preview_link_new_tab_forwards_widget_path_and_fragment(self):
        orch, spy = self._make_with_spy()
        mock_widget = unittest.mock.MagicMock()
        orch._on_preview_link_new_tab(mock_widget, "/doc.md", "H")
        self.assertEqual(len(spy["on_preview_link_new_tab"]), 1)
        args = spy["on_preview_link_new_tab"][0][0]
        self.assertIs(args[0], mock_widget)
        self.assertEqual(args[1], "/doc.md")
        self.assertEqual(args[2], "H")

    def test_preview_link_not_found_forwards_widget_and_path(self):
        orch, spy = self._make_with_spy()
        mock_widget = unittest.mock.MagicMock()
        orch._on_preview_link_not_found(mock_widget, "missing")
        self.assertEqual(len(spy["on_preview_link_not_found"]), 1)
        args = spy["on_preview_link_not_found"][0][0]
        self.assertIs(args[0], mock_widget)
        self.assertEqual(args[1], "missing")

    def test_preview_checkbox_toggled_forwards_widget_line_checked(self):
        orch, spy = self._make_with_spy()
        mock_widget = unittest.mock.MagicMock()
        orch._on_preview_checkbox_toggled(mock_widget, 5, True)
        self.assertEqual(len(spy["on_preview_checkbox_toggled"]), 1)
        args = spy["on_preview_checkbox_toggled"][0][0]
        self.assertIs(args[0], mock_widget)
        self.assertEqual(args[1], 5)
        self.assertTrue(args[2])

    def test_editor_text_changed_forwards_editor(self):
        orch, spy = self._make_with_spy()
        mock_editor = unittest.mock.MagicMock()
        orch._on_editor_text_changed(mock_editor)
        self.assertEqual(len(spy["on_editor_text_changed"]), 1)
        args = spy["on_editor_text_changed"][0][0]
        self.assertIs(args[0], mock_editor)

    def test_editor_modified_forwards_editor_and_dirty(self):
        orch, spy = self._make_with_spy()
        mock_editor = unittest.mock.MagicMock()
        orch._on_editor_modified(mock_editor, True)
        self.assertEqual(len(spy["on_editor_modified"]), 1)
        args = spy["on_editor_modified"][0][0]
        self.assertIs(args[0], mock_editor)
        self.assertTrue(args[1])

    def test_editor_modified_false_forwards_correctly(self):
        orch, spy = self._make_with_spy()
        mock_editor = unittest.mock.MagicMock()
        orch._on_editor_modified(mock_editor, False)
        args = spy["on_editor_modified"][0][0]
        self.assertFalse(args[1])


if __name__ == "__main__":
    unittest.main()
