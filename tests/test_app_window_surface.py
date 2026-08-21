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

    #: Every action the window registers on itself, as a **closed** set. A split
    #: may move where they are registered; it may neither lose nor rename one,
    #: and adding one has to be a conscious edit here.
    EXPECTED = {
        "about", "add-vault", "close-tab", "find-in-view", "insert-image",
        "mru-switcher-next", "mru-switcher-prev", "nav-back", "nav-forward",
        "new-file", "next-tab", "paste-image", "preferences", "prev-tab",
        "quick-open", "replace-in-view", "save", "theme-dark", "theme-light",
        "theme-system", "toggle-help", "toggle-search", "toggle-sidebar",
        "toggle-zen", "toggle-zen-total", "view-edit", "view-graph",
        "view-render", "view-split", "zoom-in", "zoom-out", "zoom-reset",
    }

    def test_the_registered_actions_are_exactly_the_expected_set(self):
        # Equality, not subset: a lost action breaks a menu entry silently, and a
        # renamed one breaks the accelerator still pointing at the old name —
        # neither shows up in a subset check.
        self.assertEqual(set(self.win.list_actions()), self.EXPECTED)

    def test_every_expected_action_can_be_looked_up(self):
        # lookup_action is also how the tests below drive the window: it survives
        # the split, a private method name does not.
        for name in sorted(self.EXPECTED):
            self.assertIsNotNone(self.win.lookup_action(name), name)


class TestAskWiring(AppWindowTest):
    """The Ask surface the palette is handed. These decide whether the user may
    ask at all, so every answer has to survive being moved — they now live on the
    controller (`win._ask`), reached the same way the palette reaches them."""

    def test_no_reason_to_block_when_everything_is_configured(self):
        self.win._settings["semantic_search_enabled"] = True
        self.win._settings["ask_engine"] = "auto"
        self.win._semantic_index = unittest.mock.Mock()
        self.assertEqual(self.win._ask.unavailable_reason(), "")
        self.assertTrue(self.win._ask.can_ask())

    def test_semantic_search_off_is_named_first(self):
        self.win._settings["semantic_search_enabled"] = False
        reason = self.win._ask.unavailable_reason()
        self.assertIn("Semantic search", reason)
        self.assertIn("Preferences", reason)          # says where to fix it

    def test_a_missing_index_and_a_disabled_engine_each_give_their_own_reason(self):
        self.win._settings["semantic_search_enabled"] = True
        self.win._semantic_index = None
        self.assertIn("index", self.win._ask.unavailable_reason().lower())

        self.win._semantic_index = unittest.mock.Mock()
        self.win._settings["ask_engine"] = "off"
        self.assertIn("engine", self.win._ask.unavailable_reason().lower())

    def test_the_index_is_read_live_not_captured(self):
        # The controller gets a getter, not the index itself: Preferences drops
        # and rebuilds it at runtime, and a captured reference would keep
        # answering from an index the app has already discarded.
        self.win._settings["semantic_search_enabled"] = True
        self.win._settings["ask_engine"] = "auto"
        self.win._semantic_index = None
        self.assertFalse(self.win._ask.can_ask())
        self.win._semantic_index = unittest.mock.Mock()
        self.assertTrue(self.win._ask.can_ask())

    def test_endpoint_status_none_for_local_with_a_usable_model(self):
        # None is the palette's signal for "nothing to check" — a healthy local
        # model has no endpoint that could be unreachable.
        self.win._settings["ask_engine"] = "auto"        # auto is always local
        with unittest.mock.patch(
                "markdown_vault.search.llama_runtime.availability",
                return_value=None):
            self.assertIsNone(self.win._ask.endpoint_status())

    def test_endpoint_status_blocks_local_when_the_model_cannot_load(self):
        # A local backend now carries its own verdict: a chosen GGUF that cannot
        # load blocks and banners exactly like a dead server.
        self.win._settings["ask_engine"] = "auto"
        with unittest.mock.patch(
                "markdown_vault.search.llama_runtime.availability",
                return_value="No local model file at /x/m.gguf. Download one …"):
            st = self.win._ask.endpoint_status()
        self.assertIsNotNone(st)
        self.assertFalse(st.can_ask)

    def test_palette_is_wired_to_open_ask_settings(self):
        # The banner's "Settings" button is useless without this callback — the
        # palette would fall back to re-probing a server that isn't involved.
        with unittest.mock.patch.object(self.win, "_open_preferences") as prefs:
            self.win._quick_open._open_ask_settings()
        prefs.assert_called_once_with(target="ask")

    def test_endpoint_status_is_reported_for_a_server_backend(self):
        self.win._settings["ask_engine"] = "manual"
        self.win._settings["ask_backend"] = "openai"
        self.win._settings["ask_ollama_url"] = "http://localhost:8080"
        status = self.win._ask.endpoint_status()
        self.assertIsNotNone(status)
        self.assertTrue(hasattr(status, "can_ask"))


class TestZoom(AppWindowTest):
    """Ctrl+plus/minus/0 zoom whichever half the pointer is over — the editor or
    the preview, never both. Untested until now, and the pointer branch is the
    kind of thing a split silently drops (both halves would still "work", just
    always zooming the same one)."""

    def _tab(self):
        tab = unittest.mock.Mock()
        tab.preview.zoom_level = 1.0
        tab.editor.zoom_factor = 1.0
        return tab

    def _pointing_at(self, tab, *, preview):
        """Put *tab* under the pointer, on the given half.

        Both collaborators live on the controller now: it captured
        `get_current_tab` at construction and owns the pointer position.
        """
        self.win._zoom._get_current_tab = lambda: tab
        return unittest.mock.patch.object(self.win._zoom, "pointer_over_preview",
                                          return_value=preview)

    def test_zooms_the_preview_when_the_pointer_is_over_it(self):
        tab = self._tab()
        with self._pointing_at(tab, preview=True):
            self.activate("zoom-in")
        self.assertGreater(tab.preview.zoom_level, 1.0)
        self.assertEqual(tab.editor.zoom_factor, 1.0)      # the other half untouched

    def test_zooms_the_editor_otherwise(self):
        tab = self._tab()
        with self._pointing_at(tab, preview=False):
            self.activate("zoom-out")
        self.assertLess(tab.editor.zoom_factor, 1.0)
        self.assertEqual(tab.preview.zoom_level, 1.0)

    def test_reset_returns_the_hovered_half_to_100_percent(self):
        tab = self._tab()
        tab.editor.zoom_factor = 1.6
        with self._pointing_at(tab, preview=False):
            self.activate("zoom-reset")
        self.assertEqual(tab.editor.zoom_factor, 1.0)

    def test_zooming_without_an_open_tab_does_nothing(self):
        self.win._zoom._get_current_tab = lambda: None
        self.activate("zoom-in")              # must not raise
        self.activate("zoom-reset")


class TestViewMode(AppWindowTest):
    """Edit / Render / Split. The window delegates the mode itself, but keeps one
    rule of its own — and that rule is the kind a split drops, because it looks
    like an unrelated line in the middle of a delegation."""

    def test_switching_the_mode_closes_the_find_bar_first(self):
        # The find bar targets and dims one specific view; after a mode switch it
        # would point at a hidden one (R21.5).
        self.win._find_bar.set_visible(True)
        with unittest.mock.patch.object(self.win, "_view_mode_manager"):
            self.activate("view-render")
        self.assertFalse(self.win._find_bar.get_visible())

    def test_each_action_carries_its_own_mode(self):
        # The three view actions are built in a loop over `mode`, so the binding
        # `m=mode` is the one place a copy-paste would point all three at the same
        # view — invisible from the outside, and nothing else checks it.
        for action, mode in (("view-edit", "edit"), ("view-render", "render"),
                             ("view-split", "split")):
            with unittest.mock.patch.object(self.win, "_view_mode_manager") as manager:
                self.activate(action)
            manager.set_view_mode.assert_called_once_with(mode)


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
