"""Tests for markdown_vault.app.view_mode_manager — ViewModeManager."""

import unittest
import unittest.mock

from markdown_vault.app.view_mode_manager import ViewModeManager


class _MockTab:
    """Minimal tab stand-in for testing."""

    def __init__(self, file_path="/tmp/test.md", view_mode="edit"):
        self.file_path = file_path
        self.view_mode = view_mode
        self.editor = unittest.mock.MagicMock()
        self.editor.file_path = file_path
        self.editor.get_text.return_value = "# Test"
        self.preview = unittest.mock.MagicMock()
        self.preview.get_visible.return_value = True
        self.split = unittest.mock.MagicMock()
        self.split.get_width.return_value = 800
        self.split.get_position.return_value = 500
        self.split_ratio = None


class _MockTabBar:
    """Mock for TabBar."""

    def __init__(self, current_tab=None):
        self._current_tab = current_tab

    def get_current_tab(self):
        return self._current_tab


class TestViewModeManagerInit(unittest.TestCase):
    """ViewModeManager.__init__."""

    def test_creates_manager_with_dependencies(self):
        """__init__ stores all dependencies."""
        tab_bar = _MockTabBar()
        toggle_buttons = {"edit": unittest.mock.Mock()}
        sidebar = unittest.mock.Mock()
        backlink_index = unittest.mock.Mock()

        mgr = ViewModeManager(tab_bar, toggle_buttons, sidebar, backlink_index)

        self.assertEqual(mgr._tab_bar, tab_bar)
        self.assertEqual(mgr._view_toggle_buttons, toggle_buttons)
        self.assertEqual(mgr._sidebar, sidebar)
        self.assertEqual(mgr._backlink_index, backlink_index)
        self.assertIsNone(mgr._preview_debounce_id)


class TestSetViewMode(unittest.TestCase):
    """ViewModeManager.set_view_mode."""

    def setUp(self):
        self.tab = _MockTab()
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {
            "edit": unittest.mock.Mock(),
            "split": unittest.mock.Mock(),
            "render": unittest.mock.Mock(),
        }
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_valid_edit_mode(self):
        """set_view_mode('edit') sets tab.view_mode and applies."""
        self._mgr.set_view_mode("edit")
        self.assertEqual(self.tab.view_mode, "edit")
        self.toggle_buttons["edit"].set_active.assert_called_once()

    def test_valid_split_mode(self):
        """set_view_mode('split') sets tab.view_mode and applies."""
        self._mgr.set_view_mode("split")
        self.assertEqual(self.tab.view_mode, "split")

    def test_valid_render_mode(self):
        """set_view_mode('render') sets tab.view_mode and applies."""
        self._mgr.set_view_mode("render")
        self.assertEqual(self.tab.view_mode, "render")

    def test_invalid_mode_logged(self):
        """set_view_mode with invalid mode logs warning and does nothing."""
        with unittest.mock.patch("markdown_vault.app.view_mode_manager.logger") as mock_logger:
            self._mgr.set_view_mode("invalid")
            mock_logger.warning.assert_called_once()
            self.assertNotEqual(self.tab.view_mode, "invalid")


class TestSplitCentering(unittest.TestCase):
    """Activating split view balances the editor|preview paned 50/50."""

    def setUp(self):
        self.tab = _MockTab()
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {m: unittest.mock.Mock() for m in ("edit", "split", "render")}
        self._mgr = ViewModeManager(
            self.tab_bar, self.toggle_buttons,
            unittest.mock.Mock(), unittest.mock.Mock(),
        )

    def test_first_split_centers(self):
        self._mgr.set_view_mode("split")
        self.tab.split.set_position.assert_called_once_with(400)  # 800 // 2

    def test_edit_does_not_center(self):
        self._mgr.set_view_mode("edit")
        self.tab.split.set_position.assert_not_called()

    def test_leaving_split_captures_ratio(self):
        self.tab.view_mode = "split"
        self.tab.split.get_position.return_value = 200  # of width 800
        self._mgr.set_view_mode("render")
        self.assertAlmostEqual(self.tab.split_ratio, 0.25)

    def test_remembered_ratio_restored_at_new_width(self):
        # Leave split at 25%, window later wider → restored proportionally.
        self.tab.view_mode = "split"
        self.tab.split.get_position.return_value = 200  # 25% of 800
        self._mgr.set_view_mode("edit")                 # captures ratio 0.25
        self.assertAlmostEqual(self.tab.split_ratio, 0.25)
        self.tab.split.get_width.return_value = 1200     # window grew
        self._mgr.set_view_mode("split")                 # restores 0.25 * 1200
        self.tab.split.set_position.assert_called_with(300)

    def test_unallocated_paned_defers(self):
        self.tab.split.get_width.return_value = 0
        with unittest.mock.patch(
            "markdown_vault.app.view_mode_manager.GLib.idle_add"
        ) as mock_idle:
            self._mgr.set_view_mode("split")
            self.tab.split.set_position.assert_not_called()  # nothing yet
            mock_idle.assert_called_once()  # retried later

    def test_missing_split_is_noop(self):
        self.tab.split = None
        self._mgr.set_view_mode("split")  # must not raise
        self.assertEqual(self.tab.view_mode, "split")


class TestApplyViewMode(unittest.TestCase):
    """ViewModeManager.apply_view_mode."""

    def setUp(self):
        self.tab = _MockTab()
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {}
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_edit_mode_shows_editor_only(self):
        """View mode 'edit' shows only editor."""
        self.tab.view_mode = "edit"
        self._mgr.apply_view_mode()
        self.tab.editor.set_visible.assert_called_with(True)
        self.tab.preview.set_visible.assert_called_with(False)

    def test_render_mode_shows_preview_only(self):
        """View mode 'render' shows only preview."""
        self.tab.view_mode = "render"
        self._mgr.apply_view_mode()
        self.tab.editor.set_visible.assert_called_with(False)
        self.tab.preview.set_visible.assert_called_with(True)

    def test_split_mode_shows_both(self):
        """View mode 'split' shows both editor and preview."""
        self.tab.view_mode = "split"
        self._mgr.apply_view_mode()
        self.tab.editor.set_visible.assert_called_with(True)
        self.tab.preview.set_visible.assert_called_with(True)

    def test_render_mode_refreshes_preview(self):
        """View mode 'render' calls refresh_preview."""
        self.tab.view_mode = "render"
        self._mgr.refresh_preview = unittest.mock.Mock()
        self._mgr.apply_view_mode()
        self._mgr.refresh_preview.assert_called_once()

    def test_no_tab_is_noop(self):
        """No current tab → apply_view_mode does nothing."""
        self.tab_bar = _MockTabBar(current_tab=None)
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)
        self._mgr.apply_view_mode()  # Should not raise


class TestSyncViewToggle(unittest.TestCase):
    """ViewModeManager.sync_view_toggle."""

    def setUp(self):
        self.tab_bar = _MockTabBar()
        self.toggle_buttons = {
            "edit": unittest.mock.Mock(),
            "split": unittest.mock.Mock(),
            "render": unittest.mock.Mock(),
        }
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_sync_sets_active(self):
        """sync_view_toggle sets the toggle button to active."""
        self._mgr.sync_view_toggle("edit")
        self.toggle_buttons["edit"].set_active.assert_called_once()

    def test_sync_unknown_mode_is_noop(self):
        """sync_view_toggle with unknown mode does nothing."""
        self._mgr.sync_view_toggle("unknown")
        # No exception raised


class TestRefreshPreview(unittest.TestCase):
    """ViewModeManager.refresh_preview."""

    def setUp(self):
        self.tab = _MockTab(file_path="/tmp/test.md")
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {}
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_refresh_calls_update_from_text(self):
        """refresh_preview calls preview.update_from_text with text and base_dir."""
        self._mgr.refresh_preview()
        self.tab.preview.update_from_text.assert_called_once()
        args = self.tab.preview.update_from_text.call_args
        self.assertEqual(args[0][0], "# Test")  # text
        self.assertEqual(args[0][1], "/tmp")  # base_dir

    def test_refresh_no_file_path(self):
        """refresh_preview with no editor.file_path passes empty base_dir."""
        self.tab.editor.file_path = None
        self._mgr.refresh_preview()
        self.tab.preview.update_from_text.assert_called_once()
        args = self.tab.preview.update_from_text.call_args
        self.assertEqual(args[0][1], "")

    def test_refresh_no_tab_is_noop(self):
        """refresh_preview with no current tab does nothing."""
        self.tab_bar = _MockTabBar(current_tab=None)
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)
        self._mgr.refresh_preview()  # Should not raise


class TestOnEditorTextChanged(unittest.TestCase):
    """ViewModeManager.on_editor_text_changed."""

    def setUp(self):
        self.tab = _MockTab(file_path="/tmp/test.md")
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {}
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_updates_backlink_index(self):
        """on_editor_text_changed updates backlink_index for file_path."""
        editor = self.tab.editor
        self._mgr.on_editor_text_changed(editor)
        self.backlink_index.update_file.assert_called_once_with(
            "/tmp/test.md", "# Test"
        )

    def test_schedules_preview_when_preview_visible(self):
        """on_editor_text_changed schedules preview refresh when preview is visible."""
        editor = self.tab.editor
        self._mgr._schedule_preview_refresh = unittest.mock.Mock()
        self._mgr.on_editor_text_changed(editor)
        self._mgr._schedule_preview_refresh.assert_called_once()

    def test_updates_sidebar(self):
        """on_editor_text_changed updates sidebar."""
        editor = self.tab.editor
        self._mgr.on_editor_text_changed(editor)
        self.sidebar.update_text_only.assert_called_once_with(
            "/tmp/test.md", "# Test"
        )

    def test_wrong_editor_is_noop(self):
        """on_editor_text_changed with wrong editor does nothing."""
        other_editor = unittest.mock.Mock()
        self._mgr.on_editor_text_changed(other_editor)
        self.backlink_index.update_file.assert_not_called()
        self.sidebar.update_text_only.assert_not_called()

    def test_no_tab_is_noop(self):
        """on_editor_text_changed with no current tab does nothing."""
        self.tab_bar = _MockTabBar(current_tab=None)
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)
        editor = self.tab.editor
        self._mgr.on_editor_text_changed(editor)  # Should not raise


class TestPreviewDebounce(unittest.TestCase):
    """ViewModeManager preview debounce."""

    def setUp(self):
        self.tab = _MockTab(file_path="/tmp/test.md")
        self.tab_bar = _MockTabBar(current_tab=self.tab)
        self.toggle_buttons = {}
        self.sidebar = unittest.mock.Mock()
        self.backlink_index = unittest.mock.Mock()
        self._mgr = ViewModeManager(self.tab_bar, self.toggle_buttons, self.sidebar, self.backlink_index)

    def test_schedule_preview_refresh_starts_timer(self):
        """_schedule_preview_refresh starts a GLib timer."""
        with unittest.mock.patch(
            "markdown_vault.app.view_mode_manager.GLib.timeout_add"
        ) as mock_add:
            mock_add.return_value = 42
            self._mgr._schedule_preview_refresh()
            mock_add.assert_called_once_with(
                500, self._mgr._on_preview_debounce
            )
            self.assertEqual(self._mgr._preview_debounce_id, 42)

    def test_schedule_reschedules_existing(self):
        """_schedule_preview_refresh removes old timer before starting new one."""
        with unittest.mock.patch(
            "markdown_vault.app.view_mode_manager.GLib.timeout_add",
            return_value=42,
        ) as mock_add:
            with unittest.mock.patch(
                "markdown_vault.app.view_mode_manager.GLib.source_remove"
            ) as mock_remove:
                self._mgr._preview_debounce_id = 10
                self._mgr._schedule_preview_refresh()
                mock_remove.assert_called_once_with(10)
                self.assertEqual(self._mgr._preview_debounce_id, 42)

    def test_on_preview_debounce_refreshes_and_refreshes_backlinks(self):
        """_on_preview_debounce calls refresh_preview and sidebar.refresh_backlinks."""
        self._mgr.refresh_preview = unittest.mock.Mock()
        result = self._mgr._on_preview_debounce()
        self._mgr.refresh_preview.assert_called_once()
        self.sidebar.refresh_backlinks.assert_called_once_with("/tmp/test.md")
        self.assertFalse(result)  # GLib sources must return False
        self.assertIsNone(self._mgr._preview_debounce_id)

    def test_cancel_preview_debounce(self):
        """cancel_preview_debounce removes the timer and clears the id."""
        with unittest.mock.patch(
            "markdown_vault.app.view_mode_manager.GLib.source_remove"
        ) as mock_remove:
            self._mgr._preview_debounce_id = 99
            self._mgr.cancel_preview_debounce()
            mock_remove.assert_called_once_with(99)
            self.assertIsNone(self._mgr._preview_debounce_id)

    def test_cancel_preview_debounce_noop_when_none(self):
        """cancel_preview_debounce when debounce_id is None does nothing."""
        self._mgr._preview_debounce_id = None
        self._mgr.cancel_preview_debounce()  # Should not raise


if __name__ == "__main__":
    unittest.main()
