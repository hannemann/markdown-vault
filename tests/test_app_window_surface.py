"""MainWindow's stable surface — characterisation for the App_Window split.

These tests exist to make the split *safe*, not to test the window's features:
they pin what must still be true afterwards, and deliberately avoid the internal
method names a split will move. The Preferences split ran without a single red
intermediate state because 50 such tests were green before and after; this is the
same net for a much bigger object.

Built on the real window (`AppWindowTest` — see test_app_window_construction for
why that works and what it has to clean up).

Two kinds of test live here. Most are characterisation tests on the real window
(`AppWindowTest`), avoiding internal method names on purpose. A few — currently
`TestSemanticBuildWiring` — are seam guards: they pin that one collaborator is
called with the right argument, which needs a stub window and the internal name.
Keep them apart and say which kind a new test is.
"""
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk

from markdown_vault.app.app_window import MainWindow
from markdown_vault.core import config
from test_app_window_construction import AppWindowTest


class TestInsertImageDialogResult(unittest.TestCase):
    """Seam guard (stub window, internal name): the image file-dialog callback
    splits a user cancel from a real dialog failure — silent on cancel, log +
    toast on a real failure. It previously swallowed both."""

    def _dialog(self, code):
        err = GLib.Error.new_literal(Gtk.DialogError.quark(), "x", code)
        d = unittest.mock.Mock()
        d.open_finish.side_effect = err
        return d

    def test_cancel_is_silent(self):
        win = unittest.mock.Mock()
        with self.assertNoLogs("markdown_vault.app.app_window", level="WARNING"):
            MainWindow._on_insert_image_chosen(
                win, self._dialog(Gtk.DialogError.CANCELLED), None)
        win._toast.assert_not_called()

    def test_real_failure_logs_and_toasts(self):
        win = unittest.mock.Mock()
        with self.assertLogs("markdown_vault.app.app_window", level="WARNING"):
            MainWindow._on_insert_image_chosen(
                win, self._dialog(Gtk.DialogError.FAILED), None)
        win._toast.assert_called_once()


class TestOpenPreferencesConfigError(unittest.TestCase):
    """A config-access failure opening Preferences must log (the dialog is gone once
    dismissed, so the trace would otherwise vanish) as well as surface the error."""

    @unittest.mock.patch("markdown_vault.app.app_window.config")
    def test_logs_and_surfaces(self, mock_config):
        mock_config.check_config_access.side_effect = OSError("no access")
        win = unittest.mock.Mock()
        with self.assertLogs("markdown_vault.app.app_window", level="WARNING"):
            result = MainWindow._open_preferences(win)
        self.assertIsNone(result)
        win._show_error.assert_called_once()


class TestAttachmentsMovedStaleGuard(unittest.TestCase):
    """Seam guard (stub window, internal name): _on_attachments_moved must ignore a stale
    reverse move event whose new path no longer exists — the same guard
    monitor_handler.on_file_moved already has. An in-app move whose skip_next_event leaked
    fires a late reverse-direction event on the shared external-file-moved signal; applying it
    re-runs _sync_attachments_move backwards and reverts the mirror move + relink already done
    via the tree's file-renamed signal (the double-sync that broke the attachments E2E)."""

    def test_a_stale_reverse_event_with_a_missing_new_path_is_ignored(self):
        win = unittest.mock.Mock()
        win._is_attachment_path.return_value = False
        with unittest.mock.patch("markdown_vault.app.app_window.os.path.exists",
                                 return_value=False):
            MainWindow._on_attachments_moved(
                win, "vault", "/vault/gone.md", old_path="/vault/old.md")
        win._sync_attachments_move.assert_not_called()

    def test_a_genuine_move_with_an_existing_new_path_still_syncs(self):
        win = unittest.mock.Mock()
        win._is_attachment_path.return_value = False
        with unittest.mock.patch("markdown_vault.app.app_window.os.path.exists",
                                 return_value=True):
            MainWindow._on_attachments_moved(
                win, "vault", "/vault/there.md", old_path="/vault/old.md")
        win._sync_attachments_move.assert_called_once_with("/vault/old.md", "/vault/there.md")


class TestSaveSkipRegisteredOnlyOnSuccess(unittest.TestCase):
    """Seam guard (stub window, internal names): the three save paths must register the
    vault-monitor skip only AFTER a successful save. editor.save() returns False without
    producing an FS event (no file path, or an OSError/UnicodeDecodeError); a skip registered
    up-front then leaks and swallows the next genuine external change to the open note — exactly
    what feeds reload/conflict detection (BB1, same class as AV1). Both directions per site:
    success registers a skip, failure does not."""

    def _tab(self):
        tab = unittest.mock.Mock()
        tab.editor.file_path = "/vault/note.md"
        tab.file_path = "/vault/note.md"
        tab.editor.is_modified = True
        return tab

    def test_autosave_registers_skip_only_after_a_successful_save(self):
        win = unittest.mock.Mock()
        tab = self._tab()
        tab.editor.save.return_value = True
        MainWindow._autosave_save_tab(win, tab)
        win._vault_monitor.skip_next_event.assert_called_once_with("/vault/note.md")

    def test_autosave_registers_no_skip_when_the_save_fails(self):
        win = unittest.mock.Mock()
        tab = self._tab()
        tab.editor.save.return_value = False
        MainWindow._autosave_save_tab(win, tab)
        win._vault_monitor.skip_next_event.assert_not_called()

    def test_save_dirty_tabs_registers_skip_only_after_a_successful_save(self):
        win = unittest.mock.Mock()
        tab = self._tab()
        win._tab_bar.get_tab.return_value = tab
        win._apply_wikilink_autofix.return_value = []
        tab.editor.save.return_value = True
        MainWindow._save_dirty_tabs(win, ["/vault/note.md"])
        win._vault_monitor.skip_next_event.assert_called_once_with("/vault/note.md")

    def test_save_dirty_tabs_registers_no_skip_when_the_save_fails(self):
        win = unittest.mock.Mock()
        tab = self._tab()
        win._tab_bar.get_tab.return_value = tab
        win._apply_wikilink_autofix.return_value = []
        tab.editor.save.return_value = False
        failed = MainWindow._save_dirty_tabs(win, ["/vault/note.md"])
        win._vault_monitor.skip_next_event.assert_not_called()
        self.assertEqual(failed, ["/vault/note.md"])

    def test_save_current_registers_skip_only_after_a_successful_save(self):
        win = unittest.mock.Mock()
        tab = self._tab()
        win._tab_bar.get_current_tab.return_value = tab
        win._apply_wikilink_autofix.return_value = []
        tab.editor.save.return_value = True
        MainWindow._save_current(win)
        win._vault_monitor.skip_next_event.assert_called_once_with("/vault/note.md")

    @unittest.mock.patch("markdown_vault.app.app_window.dialogs")
    def test_save_current_registers_no_skip_when_the_save_fails(self, _dialogs):
        win = unittest.mock.Mock()
        tab = self._tab()
        win._tab_bar.get_current_tab.return_value = tab
        win._apply_wikilink_autofix.return_value = []
        tab.editor.save.return_value = False
        MainWindow._save_current(win)
        win._vault_monitor.skip_next_event.assert_not_called()


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
        config.set_setting(self.win._settings, "semantic.enabled", True)
        config.set_setting(self.win._settings, "ask.engine", "auto")
        self.win._semantic_index = unittest.mock.Mock()
        self.assertEqual(self.win._ask.unavailable_reason(), "")
        self.assertTrue(self.win._ask.can_ask())

    def test_semantic_search_off_is_named_first(self):
        config.set_setting(self.win._settings, "semantic.enabled", False)
        reason = self.win._ask.unavailable_reason()
        self.assertIn("Semantic search", reason)
        self.assertIn("Preferences", reason)          # says where to fix it

    def test_a_missing_index_and_a_disabled_engine_each_give_their_own_reason(self):
        config.set_setting(self.win._settings, "semantic.enabled", True)
        self.win._semantic_index = None
        self.assertIn("index", self.win._ask.unavailable_reason().lower())

        self.win._semantic_index = unittest.mock.Mock()
        config.set_setting(self.win._settings, "ask.engine", "off")
        self.assertIn("engine", self.win._ask.unavailable_reason().lower())

    def test_the_index_is_read_live_not_captured(self):
        # The controller gets a getter, not the index itself: Preferences drops
        # and rebuilds it at runtime, and a captured reference would keep
        # answering from an index the app has already discarded.
        config.set_setting(self.win._settings, "semantic.enabled", True)
        config.set_setting(self.win._settings, "ask.engine", "auto")
        self.win._semantic_index = None
        self.assertFalse(self.win._ask.can_ask())
        self.win._semantic_index = unittest.mock.Mock()
        self.assertTrue(self.win._ask.can_ask())

    def test_endpoint_status_none_for_local_with_a_usable_model(self):
        # None is the palette's signal for "nothing to check" — a healthy local
        # model has no endpoint that could be unreachable.
        config.set_setting(self.win._settings, "ask.engine", "auto")  # auto is always local
        with unittest.mock.patch(
                "markdown_vault.search.llama_runtime.availability",
                return_value=None):
            self.assertIsNone(self.win._ask.endpoint_status())

    def test_endpoint_status_blocks_local_when_the_model_cannot_load(self):
        # A local backend now carries its own verdict: a chosen GGUF that cannot
        # load blocks and banners exactly like a dead server.
        config.set_setting(self.win._settings, "ask.engine", "auto")
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
        prefs.assert_called_once_with(page="search", subpage="ask")

    def test_semantic_error_settings_button_opens_the_search_page(self):
        # The embedding-unavailable banner's "Settings" should land the user on the
        # Search page, not the dialog's default page, so the fix is one tap away.
        self.win._sem_available = False
        with unittest.mock.patch.object(self.win, "_status_bar") as bar, \
             unittest.mock.patch.object(self.win, "_open_preferences") as prefs:
            self.win._update_status_bar()
            actions = dict(bar.show_error.call_args.kwargs["actions"])
            actions["Settings"]()          # simulate the button click
        prefs.assert_called_once_with(page="search")

    def test_endpoint_status_is_reported_for_a_server_backend(self):
        config.set_setting(self.win._settings, "ask.engine", "manual")
        config.set_setting(self.win._settings, "ask.backend", "openai")
        config.set_setting(self.win._settings, "ask.server.url", "http://localhost:8080")
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
        config.set_setting(self.win._settings, "preview.allow_remote_images", True)
        with unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_all_paths.return_value = []
            self.win._on_preferences_changed(None)
            self.win._on_preferences_changed(None)      # nothing changed since
        self.assertTrue(self.win._applied["preview.allow_remote_images"])

    def test_semantic_search_turned_off_drops_the_index(self):
        config.set_setting(self.win._settings, "semantic.enabled", True)
        self.win._remember_applied_settings()
        index = unittest.mock.Mock()
        self.win._semantic_index = index
        config.set_setting(self.win._settings, "semantic.enabled", False)
        with unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_all_paths.return_value = []
            self.win._on_preferences_changed(None)
        index.shutdown.assert_called_once()
        self.assertIsNone(self.win._semantic_index)


class TestSemanticBuildWiring(unittest.TestCase):
    """The window must hand its OWN settings to the search-domain factory.

    Guards the call site, not the factory — TestBuildEmbedder in
    tests/test_semantic_search.py covers the receiver. A plain unittest.TestCase
    rather than an AppWindowTest: the real-window fixture patches
    _setup_semantic_index away in setUp, so it cannot cover this seam.
    """

    def test_build_embedder_gets_the_windows_settings(self):
        import threading
        from markdown_vault.app import app_window as aw
        settings = {"semantic": {"backend": "onnx", "onnx": {"dir": "/models/x"}}}
        win = unittest.mock.MagicMock()
        win._settings = settings
        win._semantic_build_lock = threading.Lock()
        win._semantic_index = None
        # Patch GLib.idle_add out: with a MagicMock window the scheduled
        # _set_semantic_available returns a truthy Mock, which GLib reads as
        # "call again" — a later suite test that spins the main loop would then
        # busy-loop the mock and balloon memory. The test doesn't need the
        # scheduling, so keep it out of the shared main context entirely.
        with unittest.mock.patch(
                "markdown_vault.search.semantic_search.build_embedder") as be, \
             unittest.mock.patch(
                "markdown_vault.search.semantic_index.SemanticIndexManager"), \
             unittest.mock.patch("markdown_vault.app.app_window.GLib.idle_add"):
            be.return_value = (unittest.mock.Mock(), "tag")
            aw.MainWindow._setup_semantic_index(win)
        be.assert_called_once()
        self.assertIs(be.call_args[0][0], settings)   # the window's own object

    def test_manager_is_wired_to_the_dim_mismatch_handler(self):
        # AGENTS.md:355 — a new collaborator argument needs a caller test: the
        # window must hand the manager its own on_dim_mismatch handler, or a
        # dimension change would signal into nothing and the crash would return.
        import threading
        from markdown_vault.app import app_window as aw
        win = unittest.mock.MagicMock()
        win._settings = {"semantic": {"backend": "onnx"}}
        win._semantic_build_lock = threading.Lock()
        win._semantic_index = None
        with unittest.mock.patch(
                "markdown_vault.search.semantic_search.build_embedder") as be, \
             unittest.mock.patch(
                "markdown_vault.search.semantic_index.SemanticIndexManager") as SIM, \
             unittest.mock.patch("markdown_vault.app.app_window.GLib.idle_add"):
            be.return_value = (unittest.mock.Mock(), "tag")
            aw.MainWindow._setup_semantic_index(win)
        self.assertIs(SIM.call_args.kwargs["on_dim_mismatch"],
                      win._on_semantic_dim_changed)

    def test_dim_changed_handler_triggers_a_rebuild(self):
        from markdown_vault.app import app_window as aw
        win = unittest.mock.MagicMock()
        aw.MainWindow._on_semantic_dim_changed(win)
        win.rebuild_semantic_index.assert_called_once_with()


class TestPreferencesReuse(unittest.TestCase):
    """A second _open_preferences reuses the open dialog and just navigates, so
    repeated calls don't stack duplicate dialogs."""

    def test_second_call_reuses_the_dialog_and_navigates(self):
        from markdown_vault.app import app_window as aw
        win = unittest.mock.MagicMock()
        win._prefs_dialog = None
        with unittest.mock.patch(
                "markdown_vault.app.app_window.PreferencesDialog") as PD, \
             unittest.mock.patch(
                "markdown_vault.app.app_window.config.check_config_access"):
            aw.MainWindow._open_preferences(win, page="search")
            aw.MainWindow._open_preferences(win, page="editor")
        PD.assert_called_once()                       # only ONE dialog built
        dlg = PD.return_value
        self.assertEqual(dlg.present.call_count, 2)   # raised on both calls
        dlg.open_page.assert_any_call("editor", None)  # navigated on reuse

    def test_closing_the_dialog_drops_the_reference(self):
        from markdown_vault.app import app_window as aw
        win = unittest.mock.MagicMock()
        win._prefs_dialog = "a-dialog"
        aw.MainWindow._on_preferences_closed(win, None)
        self.assertIsNone(win._prefs_dialog)


if __name__ == "__main__":
    unittest.main()
