"""Tests for R4.2 — Dirty Tab Close with aggregated dialog.

Verifies:
- TabBar: ``tab-close-requested`` signal for bulk operations
- TabBar: dirty-check callback is called
- AppWindow: ``_on_tab_close_request`` checks dirty state
- AppWindow: ``_show_save_dialog`` appears for dirty tabs
- AppWindow: ``_save_dirty_tabs`` saves dirty tabs
- AppWindow: ``_on_save_dialog_response`` handles Save/Discard/Cancel
- R6.1: ``_save_dirty_tabs`` returns failed paths; failed tabs stay open
- R6.2: ``_on_close_request`` dirty-check prevents window close
"""

import os
import tempfile
import shutil
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from markdown_vault.tabs import Tab, TabBar
from markdown_vault.editor import Editor


# ---------------------------------------------------------------------------
# Helper: dirty-editor mock
# ---------------------------------------------------------------------------

class DirtyEditor:
    """Editor mock with configurable ``is_modified``."""

    def __init__(self, dirty=False):
        self.is_modified = dirty
        self.file_path = "/tmp/note.md"
        self._save_called = False

    def save(self):
        self._save_called = True

    @property
    def save_called(self):
        return self._save_called


# ---------------------------------------------------------------------------
# TabBar: tab-close-requested signal
# ---------------------------------------------------------------------------

class TestTabBarSignalExists(unittest.TestCase):
    """:code:`tab-close-requested` signal exists on TabBar."""

    def test_signal_defined_in_source(self):
        src = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "lib", "python3.13", "site-packages",
            "markdown_vault", "tabs.py",
        )
        source = Path(src).read_text()
        self.assertIn('"tab-close-requested"', source)


# ---------------------------------------------------------------------------
# TabBar: close_others mit dirty-check callback
# ---------------------------------------------------------------------------

class TestTabBarCloseOthersDirtyCheck(unittest.TestCase):
    """:code:`close_others` calls deferred callback for dirty tabs."""

    def setUp(self):
        self._calls = []

        def deferred(paths, on_confirm):
            self._calls.append(("deferred", paths))

        self._bar = TabBar()
        self._bar.set_close_request_callback(deferred)

    def test_clean_tabs_close_directly_no_callback(self):
        e1 = DirtyEditor(dirty=False)
        e2 = DirtyEditor(dirty=False)
        self._bar.add_tab("/tmp/a.md", editor=e1, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e2, preview=None)
        self._bar.close_others("/tmp/a.md")
        self.assertNotIn("/tmp/b.md", self._bar.get_all_paths())
        self.assertEqual(self._calls, [])

    def test_dirty_tabs_calls_deferred_callback(self):
        e1 = DirtyEditor(dirty=False)
        e2 = DirtyEditor(dirty=True)
        self._bar.add_tab("/tmp/a.md", editor=e1, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e2, preview=None)
        self._bar.close_others("/tmp/a.md")
        # deferred callback was called with the dirty tabs
        self.assertEqual(len(self._calls), 1)
        kind, paths = self._calls[0]
        self.assertEqual(kind, "deferred")
        self.assertEqual(paths, ["/tmp/b.md"])

    def test_all_dirty_calls_deferred_with_all_paths(self):
        e1 = DirtyEditor(dirty=True)
        e2 = DirtyEditor(dirty=True)
        self._bar.add_tab("/tmp/a.md", editor=e1, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e2, preview=None)
        self._bar.close_others("/tmp/a.md")
        self.assertEqual(self._calls[0][1], ["/tmp/b.md"])

    def test_no_other_tabs_no_callback(self):
        e1 = DirtyEditor(dirty=True)
        self._bar.add_tab("/tmp/a.md", editor=e1, preview=None)
        self._bar.close_others("/tmp/a.md")
        # Keine anderen tabs → kein callback, kein close
        self.assertEqual(self._bar.get_all_paths(), ["/tmp/a.md"])
        self.assertEqual(self._calls, [])


# ---------------------------------------------------------------------------
# TabBar: close_left / close_right mit dirty-check
# ---------------------------------------------------------------------------

class TestTabBarCloseLeftRightDirtyCheck(unittest.TestCase):
    """:code:`close_left` / :code:`close_right` dirty-check."""

    def setUp(self):
        self._calls = []

        def deferred(paths, on_confirm):
            self._calls.append(("deferred", paths))

        self._bar = TabBar()
        self._bar.set_close_request_callback(deferred)

    # -- close_left --

    def test_close_left_dirty_emits_deferred(self):
        e_left = DirtyEditor(dirty=True)
        e_mid = DirtyEditor(dirty=False)
        e_right = DirtyEditor(dirty=False)
        self._bar.add_tab("/tmp/a.md", editor=e_left, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e_mid, preview=None)
        self._bar.add_tab("/tmp/c.md", editor=e_right, preview=None)
        self._bar.close_left("/tmp/b.md")
        self.assertEqual(self._calls[0][1], ["/tmp/a.md"])

    def test_close_left_clean_closes_directly(self):
        e_left = DirtyEditor(dirty=False)
        e_mid = DirtyEditor(dirty=False)
        e_right = DirtyEditor(dirty=False)
        self._bar.add_tab("/tmp/a.md", editor=e_left, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e_mid, preview=None)
        self._bar.add_tab("/tmp/c.md", editor=e_right, preview=None)
        self._bar.close_left("/tmp/b.md")
        self.assertEqual(self._bar.get_all_paths(), ["/tmp/b.md", "/tmp/c.md"])
        self.assertEqual(self._calls, [])

    # -- close_right --

    def test_close_right_dirty_emits_deferred(self):
        e_left = DirtyEditor(dirty=False)
        e_mid = DirtyEditor(dirty=False)
        e_right = DirtyEditor(dirty=True)
        self._bar.add_tab("/tmp/a.md", editor=e_left, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e_mid, preview=None)
        self._bar.add_tab("/tmp/c.md", editor=e_right, preview=None)
        self._bar.close_right("/tmp/b.md")
        self.assertEqual(self._calls[0][1], ["/tmp/c.md"])

    def test_close_right_clean_closes_directly(self):
        e_left = DirtyEditor(dirty=False)
        e_mid = DirtyEditor(dirty=False)
        e_right = DirtyEditor(dirty=False)
        self._bar.add_tab("/tmp/a.md", editor=e_left, preview=None)
        self._bar.add_tab("/tmp/b.md", editor=e_mid, preview=None)
        self._bar.add_tab("/tmp/c.md", editor=e_right, preview=None)
        self._bar.close_right("/tmp/b.md")
        self.assertEqual(self._bar.get_all_paths(), ["/tmp/a.md", "/tmp/b.md"])
        self.assertEqual(self._calls, [])


# ---------------------------------------------------------------------------
# TabBar: close_tab emits tab-closed
# ---------------------------------------------------------------------------

class TestTabBarCloseTabSignal(unittest.TestCase):
    """:code:`close_tab` emits :code:`tab-closed`."""

    def test_close_tab_emits_tab_closed(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        received = []
        bar.connect("tab-closed", lambda _, fp: received.append(fp))
        bar.close_tab("/tmp/a.md")
        self.assertEqual(received, ["/tmp/a.md"])

    def test_close_tab_via_button_callback(self):
        """×-Button verwendet close_request_callback."""
        bar = TabBar()
        called_with = []

        def cb(fp):
            called_with.append(fp)
            bar.close_tab(fp)

        bar.set_close_request_callback(cb)

        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        # Button-Widget finden
        for child in bar._box:
            if getattr(child, "_file_path", None) == "/tmp/a.md":
                for grandchild in child:
                    if isinstance(grandchild, Gtk.Button) and grandchild.get_icon_name() == "window-close-symbolic":
                        grandchild.emit("clicked")
                        break
                break
        self.assertEqual(called_with, ["/tmp/a.md"])


# ---------------------------------------------------------------------------
# AppWindow: _on_tab_close_request dirty-check
# ---------------------------------------------------------------------------

class TestAppWindowTabCloseRequest(unittest.TestCase):
    """AppWindow: _on_tab_close_request checks dirty state."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._md = os.path.join(self._tmp, "note.md")
        with open(self._md, "w") as f:
            f.write("# Note")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_window(self):
        """Creates an AppWindow without display (structural)."""
        import markdown_vault.app_window as aw
        with unittest.mock.patch.object(aw.Gio.SimpleAction, 'new'):
            with unittest.mock.patch.object(aw.Adw.StyleManager, 'get_default') as sm:
                sm.return_value.set_color_scheme = unittest.mock.Mock()
                with unittest.mock.patch.object(aw, '_load_gtk_css'):
                    app = unittest.mock.Mock()
                    win = aw.MainWindow(app)
                    return win

    def test_source_has_on_tab_close_request(self):
        """_on_tab_close_request existiert in app_window.py."""
        src = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "lib", "python3.13", "site-packages",
            "markdown_vault", "app_window.py",
        )
        source = Path(src).read_text()
        self.assertIn("def _on_tab_close_request", source)

    def test_source_has_show_save_dialog(self):
        """_show_save_dialog existiert in app_window.py."""
        src = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "lib", "python3.13", "site-packages",
            "markdown_vault", "app_window.py",
        )
        source = Path(src).read_text()
        self.assertIn("def _show_save_dialog", source)

    def test_source_has_save_dialog_response_handler(self):
        """_on_save_dialog_response existiert in app_window.py."""
        src = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "lib", "python3.13", "site-packages",
            "markdown_vault", "app_window.py",
        )
        source = Path(src).read_text()
        self.assertIn("def _on_save_dialog_response", source)

    def test_source_has_save_dirty_tabs(self):
        """_save_dirty_tabs existiert in app_window.py."""
        src = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "lib", "python3.13", "site-packages",
            "markdown_vault", "app_window.py",
        )
        source = Path(src).read_text()
        self.assertIn("def _save_dirty_tabs", source)


from pathlib import Path


# ---------------------------------------------------------------------------
# TabBar: set_close_request_callback ist optional
# ---------------------------------------------------------------------------

class TestTabBarCallbackOptional(unittest.TestCase):
    """TabBar also works without close_request_callback."""

    def test_close_others_without_callback_closes_directly(self):
        bar = TabBar()
        bar.add_tab("/tmp/a.md", editor=None, preview=None)
        bar.add_tab("/tmp/b.md", editor=None, preview=None)
        bar.close_others("/tmp/a.md")
        self.assertEqual(bar.get_all_paths(), ["/tmp/a.md"])


# ---------------------------------------------------------------------------
# R6.1 — _save_dirty_tabs returns failed paths; failed tabs stay open
# ---------------------------------------------------------------------------

class TestSaveDirtyTabsFailure(unittest.TestCase):
    """R6.1: save failure must not close the tab."""

    def _make_fake_window(self):
        import markdown_vault.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._vault_monitor = unittest.mock.Mock()
                self._close_window_pending = False

            _save_dirty_tabs = aw.MainWindow._save_dirty_tabs
            _on_save_dialog_response = aw.MainWindow._on_save_dialog_response
            _show_save_dialog = aw.MainWindow._show_save_dialog
            _do_close_paths = unittest.mock.Mock()

        return FakeWindow()

    def test_save_dirty_tabs_returns_failed_paths(self):
        """_save_dirty_tabs returns paths whose save() returned False."""
        win = self._make_fake_window()

        failing_editor = unittest.mock.Mock()
        failing_editor.is_modified = True
        failing_editor.file_path = "/tmp/fail.md"
        failing_editor.save.return_value = False

        success_editor = unittest.mock.Mock()
        success_editor.is_modified = True
        success_editor.file_path = "/tmp/ok.md"
        success_editor.save.return_value = True

        tab_fail = unittest.mock.Mock()
        tab_fail.editor = failing_editor
        tab_ok = unittest.mock.Mock()
        tab_ok.editor = success_editor

        win._tab_bar.get_tab = lambda p: tab_fail if p == "/tmp/fail.md" else tab_ok

        failed = win._save_dirty_tabs(["/tmp/fail.md", "/tmp/ok.md"])
        self.assertEqual(failed, ["/tmp/fail.md"])

    def test_save_dirty_tabs_returns_empty_on_all_success(self):
        """_save_dirty_tabs returns [] when all saves succeed."""
        win = self._make_fake_window()

        editor = unittest.mock.Mock()
        editor.is_modified = True
        editor.file_path = "/tmp/ok.md"
        editor.save.return_value = True

        tab = unittest.mock.Mock()
        tab.editor = editor
        win._tab_bar.get_tab.return_value = tab

        failed = win._save_dirty_tabs(["/tmp/ok.md"])
        self.assertEqual(failed, [])

    def test_save_dialog_response_save_failure_keeps_tabs_open(self):
        """When save fails, _on_save_dialog_response does NOT close the tabs."""
        win = self._make_fake_window()

        failing_editor = unittest.mock.Mock()
        failing_editor.is_modified = True
        failing_editor.file_path = "/tmp/fail.md"
        failing_editor.save.return_value = False

        tab = unittest.mock.Mock()
        tab.editor = failing_editor
        win._tab_bar.get_tab.return_value = tab

        with unittest.mock.patch("markdown_vault.app_window.Adw.AlertDialog"):
            win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)
            win._do_close_paths.assert_not_called()

    def test_save_dialog_response_save_success_closes_tabs(self):
        """When save succeeds, _on_save_dialog_response closes the tabs."""
        win = self._make_fake_window()

        success_editor = unittest.mock.Mock()
        success_editor.is_modified = True
        success_editor.file_path = "/tmp/ok.md"
        success_editor.save.return_value = True

        tab = unittest.mock.Mock()
        tab.editor = success_editor
        win._tab_bar.get_tab.return_value = tab

        win._on_save_dialog_response("save", ["/tmp/ok.md"], on_confirm=None)
        win._do_close_paths.assert_called_once_with(["/tmp/ok.md"])

    def test_save_dialog_response_save_failure_shows_error_dialog(self):
        """When save fails, an error dialog is presented."""
        win = self._make_fake_window()

        failing_editor = unittest.mock.Mock()
        failing_editor.is_modified = True
        failing_editor.file_path = "/tmp/fail.md"
        failing_editor.save.return_value = False

        tab = unittest.mock.Mock()
        tab.editor = failing_editor
        win._tab_bar.get_tab.return_value = tab

        with unittest.mock.patch("markdown_vault.app_window.Adw.AlertDialog") as MockDlg:
            mock_instance = unittest.mock.Mock()
            MockDlg.return_value = mock_instance
            win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)
            MockDlg.assert_called_once()
            mock_instance.present.assert_called_once_with(win)

    def test_save_dialog_response_cancel_does_not_close(self):
        """Cancel never closes tabs."""
        win = self._make_fake_window()

        win._on_save_dialog_response("cancel", ["/tmp/a.md"], on_confirm=None)
        win._do_close_paths.assert_not_called()

    def test_save_dialog_response_discard_closes_tabs(self):
        """Discard closes tabs without saving."""
        win = self._make_fake_window()

        win._on_save_dialog_response("discard", ["/tmp/a.md"], on_confirm=None)
        win._do_close_paths.assert_called_once_with(["/tmp/a.md"])


# ---------------------------------------------------------------------------
# R6.2 — _on_close_request dirty-check
# ---------------------------------------------------------------------------

class TestOnCloseRequestDirtyCheck(unittest.TestCase):
    """R6.2: window close must check for dirty tabs."""

    def _make_fake_window(self):
        import markdown_vault.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._vault_monitor = unittest.mock.Mock()
                self._preview_debounce_id = None
                self._autosave_id = None
                self._close_window_pending = False
                self._surface = unittest.mock.Mock()

            _on_close_request = aw.MainWindow._on_close_request
            _on_close_request_confirmed = aw.MainWindow._on_close_request_confirmed
            _cancel_autosave = aw.MainWindow._cancel_autosave
            _restart_autosave = aw.MainWindow._restart_autosave
            _setup_autosave = aw.MainWindow._setup_autosave
            _save_dirty_tabs = aw.MainWindow._save_dirty_tabs
            _show_save_dialog = aw.MainWindow._show_save_dialog
            _on_save_dialog_response = aw.MainWindow._on_save_dialog_response
            _save_session = unittest.mock.Mock()

            def get_surface(self):
                return self._surface

        return FakeWindow()

    def test_close_request_returns_false_when_no_dirty_tabs(self):
        """Return False (allow close) when all tabs are clean."""
        win = self._make_fake_window()
        clean_editor = unittest.mock.Mock()
        clean_editor.is_modified = False
        tab = unittest.mock.Mock()
        tab.editor = clean_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/clean.md"]

        result = win._on_close_request()
        self.assertFalse(result)

    def test_close_request_returns_true_when_dirty_tabs_exist(self):
        """Return True (hold close) when dirty tabs exist."""
        win = self._make_fake_window()
        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/dirty.md"]

        with unittest.mock.patch("markdown_vault.app_window.GLib.idle_add"):
            result = win._on_close_request()
        self.assertTrue(result)

    def test_close_request_sets_close_window_pending(self):
        """_close_window_pending is set when dirty tabs exist."""
        win = self._make_fake_window()
        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/dirty.md"]

        with unittest.mock.patch("markdown_vault.app_window.GLib.idle_add"):
            win._on_close_request()
        self.assertTrue(win._close_window_pending)

    def test_close_request_confirmed_destroys_surface(self):
        """_on_close_request_confirmed cleans up and destroys the surface."""
        win = self._make_fake_window()
        win._on_close_request_confirmed()
        win._vault_monitor.cleanup.assert_called_once()
        win._save_session.assert_called_once()
        win._surface.destroy.assert_called_once()

    def test_close_request_cancels_autosave(self):
        """_on_close_request cancels autosave."""
        win = self._make_fake_window()
        win._autosave_id = 42
        win._tab_bar.get_all_paths.return_value = []

        with unittest.mock.patch("markdown_vault.app_window.GLib.source_remove") as mock_rm:
            win._on_close_request()
            mock_rm.assert_called()

    def test_restart_autosave_sets_up_new_timer(self):
        """_restart_autosave cancels old and sets up new timer."""
        win = self._make_fake_window()
        win._autosave_id = 42

        with unittest.mock.patch("markdown_vault.app_window.GLib.source_remove"):
            with unittest.mock.patch.object(win, '_setup_autosave') as mock_setup:
                win._restart_autosave()
                mock_setup.assert_called_once()

    def test_cancel_clears_close_window_pending_and_restarts_autosave(self):
        """Cancel response clears _close_window_pending and restarts autosave."""
        win = self._make_fake_window()
        win._close_window_pending = True

        with unittest.mock.patch.object(win, '_restart_autosave') as mock_restart:
            win._on_save_dialog_response("cancel", ["/tmp/a.md"], on_confirm=None)
            self.assertFalse(win._close_window_pending)
            mock_restart.assert_called_once()

    def test_save_failure_error_dismiss_clears_pending_and_restarts_autosave(self):
        """Dismissing the save-failure error clears pending and restarts autosave."""
        win = self._make_fake_window()
        win._close_window_pending = True

        failing_editor = unittest.mock.Mock()
        failing_editor.is_modified = True
        failing_editor.file_path = "/tmp/fail.md"
        failing_editor.save.return_value = False
        tab = unittest.mock.Mock()
        tab.editor = failing_editor
        win._tab_bar.get_tab.return_value = tab

        with unittest.mock.patch("markdown_vault.app_window.dialogs") as mock_dialogs:
            with unittest.mock.patch.object(win, '_restart_autosave') as mock_restart:
                win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)
                # dialogs.show_error was called for the save failure
                mock_dialogs.show_error.assert_called_once()
                # _on_error_dismissed runs inline — pending cleared, autosave restarted
                self.assertFalse(win._close_window_pending)
                mock_restart.assert_called_once()

    def test_close_request_no_tabs_returns_false(self):
        """Empty tab list returns False (no dirty tabs)."""
        win = self._make_fake_window()
        win._tab_bar.get_all_paths.return_value = []

        result = win._on_close_request()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
