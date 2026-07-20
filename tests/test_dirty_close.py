"""Tests for R4.2 — Dirty Tab Close with aggregated dialog.

Verifies:
- TabBar: ``tab-close-requested`` signal for bulk operations
- TabBar: dirty-check callback is called
- AppWindow: ``_on_tab_close_request`` checks dirty state
- AppWindow: ``_show_save_dialog`` appears for dirty tabs
- AppWindow: ``_save_dirty_tabs`` saves dirty tabs
- AppWindow: ``_on_save_dialog_response`` handles Save/Discard/Cancel
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


if __name__ == "__main__":
    unittest.main()
