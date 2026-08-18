"""MainWindow, constructed for real — the base every App_Window characterisation
test builds on (ticket A, step 1).

The ticket's decisive question was whether the window can be built headless at
all. An abandoned attempt sat in `test_dirty_close.py:239`: written with three
patches, never called; the same file then asserts method existence by grepping
its own source, and two other files borrow unbound methods onto a fake `self`.
All three are what people write when the object will not build.

**It builds.** The attempt failed for two reasons that have nothing to do with the
window: `Adw.ApplicationWindow` type-checks `application=` and refuses a Mock, and
its own `Gio.SimpleAction.new` patch fed a MagicMock to `TabBar`. With a real
`Adw.Application` and without that patch, the whole window constructs — Preview
and WebKit included — in a third of a second.

Two things the fixture must do, both learned the hard way:

* **Register the application.** Adding a window to an unstarted `GApplication` is
  a `Gtk-CRITICAL`; a suite that ends silent is what makes a *new* warning
  noticeable.
* **Cancel the autosave timer.** `__init__` starts a 30 s `GLib.timeout`
  (`app_window.py:557`). Left running it outlives the test and fires whenever a
  later test spins a main loop — the same shape as the leaked timer that once
  overwrote the real settings file.
"""
import unittest
import unittest.mock

_APP = None


def _application(aw):
    """One registered Adw.Application for the whole module.

    Registering exports the object on the session bus, so a second registration
    of the same id fails ("already exported") — one instance, reused by every
    test, and several windows on one application is normal anyway. Registration
    failure (no session bus) is tolerated: it only costs the Gtk-CRITICAL back.
    """
    global _APP
    if _APP is None:
        _APP = aw.Adw.Application(application_id="de.hannemann.markdown-vault.test")
        try:
            _APP.register(None)
        except Exception as exc:                      # no bus in this environment
            print(f"note: application not registered ({exc})")
    return _APP


class AppWindowTest(unittest.TestCase):
    """Base fixture: a real MainWindow, cleaned up after."""

    def setUp(self):
        import markdown_vault.app.app_window as aw
        from markdown_vault.core import config
        self.aw = aw
        self._config = config
        # The window's settings ARE the process-wide object (config.settings()).
        # A test that flips a setting would hand it to the next test — and a
        # leftover "semantic_search_enabled" makes the following window build a
        # real ONNX index. Reload from the pinned test config on both ends.
        config.reload_settings()
        self._app = _application(aw)
        self._patchers = [
            unittest.mock.patch.object(aw.Adw.StyleManager, "get_default"),
            unittest.mock.patch.object(aw, "_load_gtk_css"),
            # No embedder, no model file, no background build: these tests are
            # about the window's wiring, not about the index.
            unittest.mock.patch.object(aw.MainWindow, "_setup_semantic_index"),
        ]
        style = self._patchers[0].start()
        style.return_value.set_color_scheme = unittest.mock.Mock()
        for patcher in self._patchers[1:]:
            patcher.start()
        self.win = aw.MainWindow(self._app)

    def activate(self, action_name):
        """Drive the window the way the UI does — through its action.

        Preferred over calling the private method: an action name is the contract
        with menus and accelerators and survives the coming split, while
        `_toggle_zen` may move into a manager. Use `lookup_action(...).activate()`
        and **not** `Gtk.Widget.activate_action()`: the latter needs a realized,
        rooted widget and headless does nothing at all — silently, so the test
        would stay green while testing nothing.
        """
        action = self.win.lookup_action(action_name)
        self.assertIsNotNone(action, f"no such action: {action_name}")
        action.activate(None)

    def tearDown(self):
        autosave = getattr(self.win, "_autosave", None)
        if autosave is not None:
            autosave.cancel()          # a 30 s timer would outlive this test
        # getattr, not a bare call: a test may have parked a stand-in here, and a
        # teardown that explodes hides the failure the test was about.
        shutdown = getattr(getattr(self.win, "_semantic_index", None), "shutdown", None)
        if callable(shutdown):
            shutdown()
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._config.reload_settings()   # hand no mutated setting to the next test


class TestHeadlessConstruction(AppWindowTest):

    def test_the_window_builds(self):
        self.assertIsNotNone(self.win)

    def test_the_three_panels_and_the_tab_bar_exist(self):
        for attr in ("_vault_tree", "_sidebar", "_tab_bar", "_content_stack"):
            self.assertIsNotNone(getattr(self.win, attr, None), attr)

    def test_the_abandoned_attempt_fails_on_its_first_line(self):
        """Why it was abandoned, pinned so nobody repeats it: GTK type-checks
        `application=` and refuses a Mock before a single widget is built."""
        with self.assertRaises(TypeError):
            self.aw.MainWindow(unittest.mock.Mock())

    def test_construction_leaves_no_autosave_timer_behind_after_cleanup(self):
        # The fixture's contract, asserted rather than trusted: the timer exists
        # while the window lives and is gone once it is cleaned up.
        self.assertIsNotNone(self.win._autosave._timer_id)
        self.win._autosave.cancel()
        self.assertIsNone(self.win._autosave._timer_id)


if __name__ == "__main__":
    unittest.main()
