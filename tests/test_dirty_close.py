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

import tempfile
import shutil
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # type: ignore[attr-defined]
from gi.repository import Adw  # type: ignore[attr-defined]

from markdown_vault.editor.tabs import Tab, TabBar
from markdown_vault.editor.editor import Editor


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

class TestTabBarSignals(unittest.TestCase):
    """The signals TabBar promises its users.

    Asked of GObject, not of the source text: this used to read tabs.py and
    assert that the string ``"tab-close-requested"`` appears somewhere in it —
    which stays true for a signal that was renamed in the registration but left
    in a comment, and for one that is never emitted.
    """

    EXPECTED = {"tab-changed", "tab-close-requested", "tab-closed",
                "tab-copy-path", "tab-renamed"}

    def test_the_registered_signals_are_exactly_the_expected_set(self):
        from gi.repository import GObject
        from markdown_vault.editor.tabs import TabBar
        self.assertEqual(set(GObject.signal_list_names(TabBar)), self.EXPECTED)


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
# AppWindow: closing tabs with unsaved changes
# ---------------------------------------------------------------------------
# Covered by behaviour in tests/test_app_window_close.py, on the real window.
# What stood here were four tests that read app_window.py as text and asserted
# that a method NAME exists — they would have kept passing after the dirty check
# itself was deleted. They also carried a _make_window() helper that was never
# called (it could not work: GTK refuses a Mock as `application`).


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
        import markdown_vault.app.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._vault_monitor = unittest.mock.Mock()
                self._close_window_pending = False
                self._switch_vault_pending = False
                self._semantic_index = None

            _save_dirty_tabs = aw.MainWindow._save_dirty_tabs
            _semantic_update = aw.MainWindow._semantic_update
            _on_save_dialog_response = aw.MainWindow._on_save_dialog_response
            _show_save_dialog = aw.MainWindow._show_save_dialog
            _do_close_paths = unittest.mock.Mock()
            _apply_wikilink_autofix = unittest.mock.Mock(return_value=[])
            _clear_external_conflict = unittest.mock.Mock()

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

        with unittest.mock.patch("markdown_vault.app.app_window.Adw.AlertDialog"):
            win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)
            win._do_close_paths.assert_not_called()

    def test_save_dialog_response_save_failure_resets_switch_vault_pending(self):
        """R13.3: after a failed save, _switch_vault_pending is reset so a
        later vault switch is not blocked."""
        win = self._make_fake_window()
        win._switch_vault_pending = True

        failing_editor = unittest.mock.Mock()
        failing_editor.is_modified = True
        failing_editor.file_path = "/tmp/fail.md"
        failing_editor.save.return_value = False

        tab = unittest.mock.Mock()
        tab.editor = failing_editor
        win._tab_bar.get_tab.return_value = tab

        with unittest.mock.patch("markdown_vault.app.app_window.Adw.AlertDialog"):
            win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)

        self.assertFalse(win._switch_vault_pending)

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

        with unittest.mock.patch("markdown_vault.app.app_window.Adw.AlertDialog") as MockDlg:
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
        import markdown_vault.app.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._vault_monitor = unittest.mock.Mock()
                self._view_mode_manager = unittest.mock.Mock()
                self._autosave = unittest.mock.Mock()
                self._close_window_pending = False
                self._switch_vault_pending = False
                self._rebuild_timeout = None
                self._surface = unittest.mock.Mock()
                self._restart_autosave = lambda: None
                self._active_vault = "/tmp/vault"
                self._content_stack = unittest.mock.Mock()

            _on_close_request = aw.MainWindow._on_close_request
            _on_close_request_confirmed = aw.MainWindow._on_close_request_confirmed
            _cleanup_all_previews = aw.MainWindow._cleanup_all_previews
            _save_dirty_tabs = aw.MainWindow._save_dirty_tabs
            _show_save_dialog = aw.MainWindow._show_save_dialog
            _on_save_dialog_response = aw.MainWindow._on_save_dialog_response
            _cancel_backlink_rebuild = aw.MainWindow._cancel_backlink_rebuild
            _apply_wikilink_autofix = unittest.mock.Mock(return_value=[])
            _clear_external_conflict = unittest.mock.Mock()
            _session_mgr = unittest.mock.Mock()

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

        with unittest.mock.patch("markdown_vault.app.app_window.GLib.idle_add"):
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

        with unittest.mock.patch("markdown_vault.app.app_window.GLib.idle_add"):
            win._on_close_request()
        self.assertTrue(win._close_window_pending)

    def test_close_request_confirmed_destroys_surface(self):
        """_on_close_request_confirmed cleans up and destroys the surface."""
        win = self._make_fake_window()
        win._tab_bar.get_all_paths.return_value = []
        win._on_close_request_confirmed()
        win._vault_monitor.cleanup.assert_called_once()
        win._session_mgr.save_session.assert_called_once()
        win._surface.destroy.assert_called_once()

    def test_close_request_cancels_autosave(self):
        """_on_close_request cancels autosave."""
        win = self._make_fake_window()
        win._tab_bar.get_all_paths.return_value = []

        win._on_close_request()
        win._autosave.cancel.assert_called_once()

    def test_close_request_cancels_backlink_rebuild(self):
        """R18.1: a pending debounced backlink rebuild is cancelled on close."""
        import unittest.mock
        win = self._make_fake_window()
        win._rebuild_timeout = 99
        win._tab_bar.get_all_paths.return_value = []
        with unittest.mock.patch("markdown_vault.app.app_window.GLib.source_remove") as source_remove:
            result = win._on_close_request()
        self.assertFalse(result)
        source_remove.assert_called_once_with(99)
        self.assertIsNone(win._rebuild_timeout)

    def test_restart_autosave_sets_up_new_timer(self):
        """_restart_autosave cancels old and sets up new timer."""
        win = self._make_fake_window()
        win._autosave.restart = unittest.mock.Mock()
        win._restart_autosave = lambda: win._autosave.restart()
        win._restart_autosave()
        win._autosave.restart.assert_called_once()

    def test_cancel_clears_close_window_pending_and_restarts_autosave(self):
        """Cancel response clears _close_window_pending and restarts autosave."""
        win = self._make_fake_window()
        win._close_window_pending = True

        win._on_save_dialog_response("cancel", ["/tmp/a.md"], on_confirm=None)
        self.assertFalse(win._close_window_pending)
        win._autosave.restart.assert_called_once()

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

        with unittest.mock.patch("markdown_vault.app.app_window.dialogs") as mock_dialogs:
            win._on_save_dialog_response("save", ["/tmp/fail.md"], on_confirm=None)
            # dialogs.show_error was called for the save failure
            mock_dialogs.show_error.assert_called_once()
            # _on_error_dismissed runs inline — pending cleared, autosave restarted
            self.assertFalse(win._close_window_pending)
            win._autosave.restart.assert_called_once()

    def test_close_request_no_tabs_returns_false(self):
        """Empty tab list returns False (no dirty tabs)."""
        win = self._make_fake_window()
        win._tab_bar.get_all_paths.return_value = []

        result = win._on_close_request()
        self.assertFalse(result)



# ---------------------------------------------------------------------------
# R4.2-b — Switch vault dirty dialog race fix
# ---------------------------------------------------------------------------

class TestSwitchVaultDirtyDialogRace(unittest.TestCase):
    """R4.2-b: vault switch waits for dirty-dialog confirmation."""

    def _make_fake_window(self):
        """Create a minimal FakeWindow with all needed methods from MainWindow."""
        import markdown_vault.app.app_window as aw

        class FakeWindow:
            _close_all_tabs_with_dirty_check = aw.MainWindow._close_all_tabs_with_dirty_check
            _do_close_paths = aw.MainWindow._do_close_paths
            _update_nav_buttons = lambda self: None
            _open_file = lambda self, *a, **k: None
            _switch_vault = aw.MainWindow._switch_vault

            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._vault_monitor = unittest.mock.Mock()
                self._vault_tree = unittest.mock.Mock()
                self._session_mgr = unittest.mock.Mock()
                self._nav_history = unittest.mock.Mock()
                self._autosave = unittest.mock.Mock()
                self._close_window_pending = False
                self._switch_vault_pending = False
                self._active_vault = "/tmp/vault-a"
                self._content_stack = unittest.mock.Mock()
                self.mru = unittest.mock.Mock()

            def _show_save_dialog(self, dirty_paths, on_confirm=None):
                if on_confirm is not None:
                    on_confirm()

            def _switch_vault_complete(self, new_vault, open_file_path=None, post_open_fn=None):
                # Full vault-switch logic (same as MainWindow)
                self._do_close_paths(self._tab_bar.get_all_paths())
                self.mru.clear()
                self._nav_history.clear()
                self._update_nav_buttons()
                self._active_vault = new_vault
                self._vault_tree.set_active_vault(new_vault)
                self._session_mgr.restore_vault_session(
                    new_vault,
                    open_file_fn=self._open_file,
                    push_history_fn=lambda *a, **k: None,
                    suppress_nav_fn=lambda s: setattr(self._nav_history, "suppress", s),
                    mru_push_fn=self.mru.push,
                )

        return FakeWindow()

    def test_close_all_tabs_clean_calls_on_confirm(self):
        """Clean tabs: on_confirm called immediately, no dialog shown."""
        win = self._make_fake_window()

        clean_editor = unittest.mock.Mock()
        clean_editor.is_modified = False
        tab = unittest.mock.Mock()
        tab.editor = clean_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/a.md", "/tmp/b.md"]

        confirmed = []
        def fake_on_confirm():
            confirmed.append(True)

        with unittest.mock.patch("markdown_vault.app.app_window.GLib.idle_add") as mock_idle:
            win._close_all_tabs_with_dirty_check(on_confirm=fake_on_confirm)

        self.assertEqual(confirmed, [True])
        mock_idle.assert_not_called()
        win._tab_bar.close_tab.assert_not_called()

    def test_close_all_dirty_defers_on_confirm(self):
        """Dirty tabs: on_confirm NOT called yet, dialog is scheduled."""
        import markdown_vault.app.app_window as aw

        class DirtyWindow:
            _close_all_tabs_with_dirty_check = aw.MainWindow._close_all_tabs_with_dirty_check

            def __init__(self):
                self._tab_bar = unittest.mock.Mock()
                self._show_save_dialog_called = False

            def _show_save_dialog(self, dirty_paths, on_confirm=None):
                self._show_save_dialog_called = True

        win = DirtyWindow()

        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/a.md"]

        confirmed = []
        def fake_on_confirm():
            confirmed.append(True)

        def fake_idle_add(func, *args, **kwargs):
            func(*args, **kwargs)

        with unittest.mock.patch(
            "markdown_vault.app.app_window.GLib.idle_add", fake_idle_add
        ):
            win._close_all_tabs_with_dirty_check(on_confirm=fake_on_confirm)

        self.assertEqual(confirmed, [])
        self.assertTrue(win._show_save_dialog_called)

    def test_switch_vault_no_dirty_switches_immediately(self):
        """No dirty tabs: vault switch happens synchronously."""
        win = self._make_fake_window()

        clean_editor = unittest.mock.Mock()
        clean_editor.is_modified = False
        tab = unittest.mock.Mock()
        tab.editor = clean_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/vault-a/note.md"]

        win._switch_vault("/tmp/vault-b")

        self.assertEqual(win._active_vault, "/tmp/vault-b")

    def test_switch_vault_cancelled_keeps_original(self):
        """Cancel: on_confirm is captured but NOT called."""
        win = self._make_fake_window()

        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/vault-a/note.md"]

        captured_kwargs = {}
        def capture_show(self, dirty_paths, on_confirm=None):
            captured_kwargs['on_confirm'] = on_confirm
        win.__class__._show_save_dialog = capture_show

        def fake_idle_add(func, *args, **kwargs):
            func(*args, **kwargs)

        with unittest.mock.patch(
            "markdown_vault.app.app_window.GLib.idle_add", fake_idle_add
        ):
            win._switch_vault("/tmp/vault-b")

        self.assertIn('on_confirm', captured_kwargs)

        # Cancel: on_confirm is NOT called
        self.assertEqual(win._active_vault, "/tmp/vault-a")

    def test_switch_vault_save_confirms_switch(self):
        """Save+Confirm: vault switch IS invoked."""
        win = self._make_fake_window()

        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/vault-a/note.md"]

        captured_kwargs = {}
        def capture_show(self, dirty_paths, on_confirm=None):
            captured_kwargs['on_confirm'] = on_confirm
        win.__class__._show_save_dialog = capture_show

        def fake_idle_add(func, *args, **kwargs):
            func(*args, **kwargs)

        with unittest.mock.patch(
            "markdown_vault.app.app_window.GLib.idle_add", fake_idle_add
        ):
            win._switch_vault("/tmp/vault-b")

        # Simulate Save/Discard: on_confirm IS called
        on_confirm = captured_kwargs.get('on_confirm')
        if on_confirm:
            on_confirm()

        self.assertEqual(win._active_vault, "/tmp/vault-b")

    def test_switch_vault_discard_confirms_switch(self):
        """Discard+Confirm: vault switch IS invoked (no save)."""
        win = self._make_fake_window()

        dirty_editor = unittest.mock.Mock()
        dirty_editor.is_modified = True
        tab = unittest.mock.Mock()
        tab.editor = dirty_editor
        win._tab_bar.get_tab.return_value = tab
        win._tab_bar.get_all_paths.return_value = ["/tmp/vault-a/note.md"]

        captured_kwargs = {}
        def capture_show(self, dirty_paths, on_confirm=None):
            captured_kwargs['on_confirm'] = on_confirm
        win.__class__._show_save_dialog = capture_show

        def fake_idle_add(func, *args, **kwargs):
            func(*args, **kwargs)

        with unittest.mock.patch(
            "markdown_vault.app.app_window.GLib.idle_add", fake_idle_add
        ):
            win._switch_vault("/tmp/vault-b")

        # Simulate Discard: on_confirm IS called
        on_confirm = captured_kwargs.get('on_confirm')
        if on_confirm:
            on_confirm()

        self.assertEqual(win._active_vault, "/tmp/vault-b")


if __name__ == "__main__":
    unittest.main()
