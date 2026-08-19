"""Session and navigation, pinned for the App_Window split.

Two areas whose damage is invisible when it breaks: a session key silently
dropped costs the user their layout on the next start, and the vault switch is
the single point seven different "open this file" paths converge on.

Built on the real window — see test_app_window_construction.
"""
import unittest
import unittest.mock

from test_app_window_construction import AppWindowTest


class TestSessionState(AppWindowTest):
    """`_get_window_state` is the contract between the window and the session
    file. Every key here is something the user gets back on the next start, and
    a split that carries eleven of twelve keys over loses one silently — the
    file just stops mentioning it."""

    #: What the session file is entitled to receive. Written out rather than
    #: derived, so *removing* a key has to be a deliberate edit here too.
    EXPECTED = {
        "width", "height", "sidebar_visible", "expanded_vaults", "search_visible",
        "search_paned_position", "sidebar_paned_position", "main_paned_position",
        "nav_history", "ask_last_question",
    }

    def test_window_state_carries_exactly_the_expected_keys(self):
        self.assertEqual(set(self.win._get_window_state()), self.EXPECTED)

    def test_geometry_and_layout_are_numbers_the_session_can_store(self):
        state = self.win._get_window_state()
        for key in ("width", "height", "search_paned_position",
                    "sidebar_paned_position", "main_paned_position"):
            self.assertIsInstance(state[key], int, key)
        self.assertIsInstance(state["sidebar_visible"], bool)
        self.assertIsInstance(state["expanded_vaults"], list)

    def test_saving_a_session_writes_those_keys_to_disk(self):
        from markdown_vault.core import session
        self.win._session_mgr.save_session(None, self.win._content_stack)
        stored = session.load_session()
        self.assertIn("window", stored)
        self.assertEqual(set(stored["window"]), {"width", "height"})
        for key in ("sidebar_visible", "expanded_vaults", "vault_sessions"):
            self.assertIn(key, stored)


class TestNavigation(AppWindowTest):
    """The vault switch is where seven entry points meet — the file tree, the
    sidebar, two preview-link paths, a search result, a newly added vault and the
    history. A split may move it; it may not give any of them their own copy."""

    def test_a_switch_is_not_committed_until_the_dirty_check_confirms(self):
        # Two-phase on purpose: cancelling the "unsaved changes" dialog has to
        # abort the switch completely, so phase 1 must not touch the active vault.
        with unittest.mock.patch.object(self.win, "_close_all_tabs_with_dirty_check") as check, \
                unittest.mock.patch.object(self.win, "_switch_vault_complete") as complete:
            self.win._switch_vault("/tmp/some-vault")
            self.assertIsNone(self.win._active_vault)      # nothing committed yet
            complete.assert_not_called()
            check.call_args.kwargs["on_confirm"]()          # user confirms
        complete.assert_called_once_with("/tmp/some-vault", None, None)

    def test_switching_to_the_active_vault_does_nothing(self):
        self.win._active_vault = "/tmp/same"
        with unittest.mock.patch.object(self.win, "_close_all_tabs_with_dirty_check") as check:
            self.win._switch_vault("/tmp/same")
        check.assert_not_called()

    def test_a_second_switch_while_one_is_pending_is_ignored(self):
        # Otherwise two dirty-check dialogs race and the second confirm lands in
        # a vault the first one already left.
        with unittest.mock.patch.object(self.win, "_close_all_tabs_with_dirty_check") as check:
            self.win._switch_vault("/tmp/first")
            self.win._switch_vault("/tmp/second")
        self.assertEqual(check.call_count, 1)

    def test_history_navigation_delegates_and_refreshes_the_buttons(self):
        # Back/forward live in InputManager; the window's job is to try the
        # in-page anchor first (footnotes, TOC) and only then hand over.
        with unittest.mock.patch.object(self.win, "_input_manager") as inputs, \
                unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_current_tab.return_value = None      # no in-page history
            self.activate("nav-back")
            self.activate("nav-forward")
        inputs.nav_back.assert_called_once()
        inputs.nav_forward.assert_called_once()

    def test_pushing_history_is_the_input_managers_job_not_the_windows(self):
        # The window used to carry a forwarder that every caller went through.
        # Collaborators now call the InputManager directly, so the forwarder is
        # gone — asserted, because "we removed a method" is exactly the kind of
        # thing that silently comes back.
        self.assertFalse(hasattr(self.win, "_push_history"))
        self.assertTrue(callable(self.win._input_manager.push_history))

    def test_an_in_page_anchor_is_unwound_before_the_note_history(self):
        # Ctrl+Alt+Left inside a long note with footnotes must first return to
        # where the anchor jump started, not leave the note entirely.
        tab = unittest.mock.Mock()
        tab.preview.go_back_in_page.return_value = True
        with unittest.mock.patch.object(self.win, "_input_manager") as inputs, \
                unittest.mock.patch.object(self.win, "_tab_bar") as tabs:
            tabs.get_current_tab.return_value = tab
            self.activate("nav-back")
        inputs.nav_back.assert_not_called()

    def test_opening_a_history_entry_from_another_vault_switches_first(self):
        # The history spans vaults, so an entry may point outside the active one.
        with unittest.mock.patch.object(self.win, "_find_vault_for_file",
                                        return_value="/tmp/other-vault"), \
                unittest.mock.patch.object(self.win, "_switch_vault") as switch:
            self.win._active_vault = "/tmp/this-vault"
            self.win._open_from_history("/tmp/other-vault/note.md")
        switch.assert_called_once_with("/tmp/other-vault",
                                       open_file_path="/tmp/other-vault/note.md")


class TestLinkNavigator(unittest.TestCase):
    """Following a link — the rule that used to be written out three times.

    A plain object, so it is tested as one: no window needed, which is itself the
    point of having pulled it out.
    """

    def _navigator(self, *, active_vault="/v", already_open=False):
        from markdown_vault.app.link_navigator import LinkNavigator
        self.calls = unittest.mock.Mock()
        self.tab = unittest.mock.Mock()
        return LinkNavigator(
            parent=None,
            get_current_tab=lambda: self.tab,
            get_active_vault=lambda: active_vault,
            open_in_place=self.calls.in_place,
            open_in_new_tab=self.calls.new_tab,
            switch_vault=self.calls.switch,
            is_open=lambda _path: already_open)

    def test_a_link_inside_the_active_vault_opens_in_place(self):
        nav = self._navigator()
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", new_tab=False)
        self.calls.in_place.assert_called_once_with("/v/note.md")
        self.calls.switch.assert_not_called()

    def test_ctrl_click_opens_a_new_tab_instead(self):
        nav = self._navigator()
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", new_tab=True)
        self.calls.new_tab.assert_called_once_with("/v/note.md")
        self.calls.in_place.assert_not_called()

    def test_a_target_in_another_vault_switches_first(self):
        # And the anchor jump is handed along rather than run now: there is
        # nothing to scroll to until the switch has opened and rendered the note.
        nav = self._navigator(active_vault="/v")
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/other"):
            nav.follow("/other/note.md", "Heading", new_tab=False)
        self.calls.switch.assert_called_once()
        args, kwargs = self.calls.switch.call_args
        self.assertEqual(args[0], "/other")
        self.assertEqual(kwargs["open_file_path"], "/other/note.md")
        self.assertTrue(callable(kwargs["post_open_fn"]))
        self.tab.preview.arm_anchor.assert_not_called()          # deferred
        kwargs["post_open_fn"]()                                 # …until run
        self.tab.preview.arm_anchor.assert_called_once_with("Heading")

    def test_an_anchor_is_armed_on_the_preview_that_ends_up_showing_the_note(self):
        # Which preview that is only becomes clear *after* opening: depending on
        # the view mode the link reuses the tab or opens a new one. Arming it
        # before would put the jump on the note being left — that is how it got
        # lost in split view. And arming rather than scrolling, because the
        # window reloads the preview right afterwards and would discard a scroll.
        nav = self._navigator()
        order = []
        self.tab.preview.arm_anchor.side_effect = lambda *_: order.append("arm")
        self.calls.in_place.side_effect = lambda *_: order.append("open")
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", "Heading", new_tab=False)
        self.tab.preview.arm_anchor.assert_called_once_with("Heading")
        self.tab.preview.scroll_to_anchor.assert_not_called()
        self.assertEqual(order, ["open", "arm"])

    def test_a_new_tab_gets_the_anchor_too(self):
        nav = self._navigator()
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", "Heading", new_tab=True)
        self.calls.new_tab.assert_called_once_with("/v/note.md")
        self.tab.preview.arm_anchor.assert_called_once_with("Heading")

    def test_an_already_open_note_is_scrolled_instead_of_armed(self):
        # Opening a note that is already in a tab only activates that tab —
        # nothing renders, so no FINISHED ever arrives and an armed jump would
        # sit unspent (and fire on some later, unrelated load). Its content is
        # on screen, so scroll straight away. This is the second click on the
        # same note, and it is the common case, not an edge one.
        nav = self._navigator(already_open=True)
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", "Heading", new_tab=False)
        self.tab.preview.scroll_to_anchor.assert_called_once_with("Heading")
        self.tab.preview.arm_anchor.assert_not_called()

    def test_the_cross_vault_case_uses_the_same_rule(self):
        # The switch opens the note itself, so its post-open callback goes
        # through the same decision instead of scrolling unconditionally.
        nav = self._navigator(active_vault="/other")
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", "Heading", new_tab=False)
        _args, kwargs = self.calls.switch.call_args
        self.tab.preview.arm_anchor.assert_not_called()
        kwargs["post_open_fn"]()
        self.tab.preview.arm_anchor.assert_called_once_with("Heading")

    def test_a_link_without_an_anchor_scrolls_nothing(self):
        nav = self._navigator()
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.find_vault_for_dir", return_value="/v"):
            nav.follow("/v/note.md", "", new_tab=False)
        self.tab.preview.scroll_to_anchor.assert_not_called()

    def test_the_not_found_dialog_gets_a_readable_name(self):
        from markdown_vault.app.link_navigator import LinkNavigator
        self.assertEqual(LinkNavigator.display_name("/plain/path.md"), "/plain/path.md")
        with unittest.mock.patch(
                "markdown_vault.core.path_utils.parse_wikilink_url",
                return_value=("Notes", "sub/Other.md", "")):
            self.assertEqual(LinkNavigator.display_name("vault:Notes/sub/Other.md"),
                             "Notes>sub/Other.md")


class TestVaultSwitchHistory(AppWindowTest):
    """The invariant: only a user action creates a history entry. Neither closing
    the old vault's tabs nor restoring the new vault's session may leave one.

    (`restore_vault_session` used to push and unsuppress itself; the switch now
    wraps the whole non-navigating part in one suppress clamp and pushes the
    "here I landed" entry itself, bound to whether a file is opened after.)
    """

    def setUp(self):
        import tempfile
        super().setUp()
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _md(self, name):
        import os
        from pathlib import Path
        p = os.path.join(self._tmp, name)
        Path(p).write_text(f"# {name}")
        return p

    def _switch(self, *, session_tabs, active_tab, open_file_path=None, old_tabs=()):
        """Open *old_tabs* in the current vault, clear the history, then run a
        vault switch whose target session holds *session_tabs* with *active_tab*.
        Returns exactly the entries the switch itself produced."""
        for fp in old_tabs:
            self.win._open_file(fp)
        hist = self.win._nav_history
        hist._history.clear()
        hist._pos = -1
        target = {
            "vault_sessions": {
                "/new": {
                    "tabs": [{"path": fp, "view_mode": "edit"} for fp in session_tabs],
                    "active_tab": active_tab,
                    "mru": [],
                }
            }
        }
        with unittest.mock.patch(
                "markdown_vault.app.session_manager.session") as ms:
            ms.load_session.return_value = target
            ms.prune_vault_session.side_effect = lambda d: d
            self.win._switch_vault_complete_phase3("/new", open_file_path=open_file_path)
        return list(hist.history)

    # active_tab is A, never the last-restored B — otherwise set_active_tab
    # short-circuits ("already active"), tab-changed never fires, and the test
    # would pass without ever entering the suppressed path.

    def test_switch_with_a_target_pushes_only_the_target(self):
        a, b, target = self._md("a.md"), self._md("b.md"), self._md("target.md")
        entries = self._switch(session_tabs=[a, b], active_tab=a, open_file_path=target)
        self.assertEqual(entries, [target])

    def test_switch_without_a_target_pushes_only_the_active_tab(self):
        a, b = self._md("a.md"), self._md("b.md")
        entries = self._switch(session_tabs=[a, b], active_tab=a)
        self.assertEqual(entries, [a])

    def test_closing_several_old_tabs_leaves_none_of_them(self):
        old = [self._md("o1.md"), self._md("o2.md"), self._md("o3.md")]
        a, b = self._md("a.md"), self._md("b.md")
        entries = self._switch(session_tabs=[a, b], active_tab=a, old_tabs=old)
        for fp in old:
            self.assertNotIn(fp, entries)
        self.assertEqual(entries, [a])          # only the deliberate one

    def test_switch_into_a_vault_without_tabs_pushes_nothing(self):
        entries = self._switch(session_tabs=[], active_tab=None)
        self.assertEqual(entries, [])

    def test_a_normal_navigation_after_a_switch_still_pushes(self):
        # A stuck suppression would silently swallow this — the guard against
        # the try/finally being dropped.
        a, b, later = self._md("a.md"), self._md("b.md"), self._md("later.md")
        self._switch(session_tabs=[a, b], active_tab=a)
        self.win._input_manager.push_history(later)
        self.assertIn(later, self.win._nav_history.history)


class TestStartupHistory(AppWindowTest):
    """Case 5 of the invariant: startup is not a navigation either. It calls
    `restore_vault_session` directly, not via phase 3, so it carries its own
    suppress clamp — and with no saved `nav_history` nothing overwrites the
    result afterwards, which is what makes a missing clamp visible here."""

    def test_startup_without_saved_nav_history_leaves_the_history_empty(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        a, b = os.path.join(tmp, "a.md"), os.path.join(tmp, "b.md")
        Path(a).write_text("# a")
        Path(b).write_text("# b")
        ses = {
            "window": {"width": 1200, "height": 800},
            "active_vault": tmp,
            "vault_sessions": {tmp: {
                "tabs": [{"path": a, "view_mode": "edit"},
                         {"path": b, "view_mode": "edit"}],
                "active_tab": a,          # not the last restored one: see the note
                "mru": [],                # in TestVaultSwitchHistory
            }},
            # deliberately no "nav_history" — nothing overrides what the restore left
        }
        with unittest.mock.patch.object(self.aw.session, "load_session",
                                        return_value=ses), \
             unittest.mock.patch.object(self.aw.session, "prune_vault_session",
                                        side_effect=lambda d: d), \
             unittest.mock.patch.object(self._config, "load_vaults",
                                        return_value=[{"name": "v", "path": tmp}]):
            win = self.aw.MainWindow(self._app)
        self.addCleanup(win._autosave.cancel)
        self.assertEqual(win._nav_history.history, [])


if __name__ == "__main__":
    unittest.main()
