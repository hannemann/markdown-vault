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


if __name__ == "__main__":
    unittest.main()
