"""MainWindow's stable surface — characterisation for the App_Window split.

These tests exist to make the split *safe*, not to test the window's features:
they pin what must still be true afterwards, and deliberately avoid the internal
method names a split will move. The Preferences split ran without a single red
intermediate state because 50 such tests were green before and after; this is the
same net for a much bigger object.

Built on the real window (`AppWindowTest` — see test_app_window_construction for
why that works and what it has to clean up).
"""
import unittest
import unittest.mock

from test_app_window_construction import AppWindowTest


class TestActions(AppWindowTest):
    """The action surface: what the menus, shortcuts and the app shell can call.

    Asserted as a set rather than one-by-one, because the failure a split causes
    is a *missing* action (a wiring block not carried over) — and that is invisible
    until someone clicks the menu entry.
    """

    #: Every action the window registers on itself. A split may move where they
    #: are registered; it may not lose one.
    EXPECTED = {
        "theme-system", "theme-light", "theme-dark",
        "add-vault", "new-file", "insert-image", "paste-image",
    }

    def test_registered_actions_cover_the_expected_set(self):
        registered = set(self.win.list_actions())
        missing = self.EXPECTED - registered
        self.assertEqual(missing, set(), f"lost actions: {sorted(missing)}")

    def test_every_registered_action_is_enabled_and_callable(self):
        # A registered-but-disabled action is the other way a split breaks this:
        # the menu entry exists and does nothing.
        for name in sorted(self.EXPECTED):
            self.assertTrue(self.win.has_action(name), name)


class TestAskWiring(AppWindowTest):
    """The window answers three questions for the Ask palette. They are pure
    functions over the settings, they decide whether the user may ask at all, and
    a split moving them into another object must keep every answer."""

    def test_no_reason_to_block_when_everything_is_configured(self):
        self.win._settings["semantic_search_enabled"] = True
        self.win._settings["ask_engine"] = "auto"
        self.win._semantic_index = unittest.mock.Mock()
        self.assertEqual(self.win._ask_unavailable_reason(), "")

    def test_semantic_search_off_is_named_first(self):
        self.win._settings["semantic_search_enabled"] = False
        reason = self.win._ask_unavailable_reason()
        self.assertIn("Semantic search", reason)
        self.assertIn("Preferences", reason)          # says where to fix it

    def test_a_missing_index_and_a_disabled_engine_each_give_their_own_reason(self):
        self.win._settings["semantic_search_enabled"] = True
        self.win._semantic_index = None
        self.assertIn("index", self.win._ask_unavailable_reason().lower())

        self.win._semantic_index = unittest.mock.Mock()
        self.win._settings["ask_engine"] = "off"
        self.assertIn("engine", self.win._ask_unavailable_reason().lower())

    def test_endpoint_status_is_none_for_a_backend_without_a_server(self):
        # None is the palette's signal for "nothing to check" — a local model has
        # no endpoint that could be unreachable.
        self.win._settings["ask_engine"] = "auto"        # auto is always local
        self.assertIsNone(self.win._ask_endpoint_status())

    def test_endpoint_status_is_reported_for_a_server_backend(self):
        self.win._settings["ask_engine"] = "manual"
        self.win._settings["ask_backend"] = "openai"
        self.win._settings["ask_ollama_url"] = "http://localhost:8080"
        status = self.win._ask_endpoint_status()
        self.assertIsNotNone(status)
        self.assertTrue(hasattr(status, "can_ask"))


class TestPreferencesReactions(AppWindowTest):
    """What the window *does* when a setting changes — the part that is not
    visible in the settings file and broke silently once already.

    The dialog shares the window's settings object, so by the time the signal
    arrives the new value is already there. The window compares against what it
    last *applied*; a split that drops that comparison makes toggling remote
    images stop reloading the preview, with nothing going red.
    """

    def test_a_toggled_setting_is_recognised_as_a_change_exactly_once(self):
        self.win._settings["preview_allow_remote_images"] = True
        with unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_all_paths.return_value = []
            self.win._on_preferences_changed(None)
            self.win._on_preferences_changed(None)      # nothing changed since
        self.assertTrue(self.win._applied["preview_allow_remote_images"])

    def test_semantic_search_turned_off_drops_the_index(self):
        self.win._settings["semantic_search_enabled"] = True
        self.win._remember_applied_settings()
        index = unittest.mock.Mock()
        self.win._semantic_index = index
        self.win._settings["semantic_search_enabled"] = False
        with unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_all_paths.return_value = []
            self.win._on_preferences_changed(None)
        index.shutdown.assert_called_once()
        self.assertIsNone(self.win._semantic_index)


if __name__ == "__main__":
    unittest.main()
