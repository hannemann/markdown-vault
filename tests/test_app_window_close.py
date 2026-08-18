"""Closing tabs with unsaved changes — behaviour, on the real window.

`test_dirty_close.py` covers this today by reading `app_window.py` as text and
asserting `"def _on_tab_close_request" in source`: four tests that pass as long
as the *name* exists, and would keep passing if the method lost its dirty check
entirely. That is what people write when the object will not build — it does
build (see test_app_window_construction), so this asks what the code does.

The stake is the only place in the app where the user can lose work: a close path
that stops asking discards unsaved notes silently.
"""
import unittest
import unittest.mock

from test_app_window_construction import AppWindowTest


class CloseTest(AppWindowTest):

    def _tab(self, modified):
        tab = unittest.mock.Mock()
        tab.editor.is_modified = modified
        return tab

    def _tab_bar_with(self, tabs_by_path):
        bar = unittest.mock.patch.object(self.win, "_tab_bar").start()
        self.addCleanup(unittest.mock.patch.stopall)
        bar.get_tab.side_effect = tabs_by_path.get
        return bar


class TestCleanTabsCloseWithoutAsking(CloseTest):

    def test_a_clean_tab_closes_directly(self):
        self._tab_bar_with({"/v/a.md": self._tab(modified=False)})
        with unittest.mock.patch.object(self.win, "_show_save_dialog") as dialog, \
                unittest.mock.patch.object(self.win, "_do_close_paths") as close:
            self.win._on_tab_close_requested("/v/a.md")
        dialog.assert_not_called()
        close.assert_called_once_with(["/v/a.md"])

    def test_a_bulk_close_of_clean_tabs_runs_the_callers_callback(self):
        # Bulk close hands its own continuation in; the window must use it
        # instead of closing the paths itself, or the caller's follow-up work
        # (a vault switch, for instance) never happens.
        self._tab_bar_with({p: self._tab(modified=False)
                            for p in ("/v/a.md", "/v/b.md")})
        confirmed = []
        with unittest.mock.patch.object(self.win, "_show_save_dialog") as dialog, \
                unittest.mock.patch.object(self.win, "_do_close_paths") as close:
            self.win._on_tab_close_requested(["/v/a.md", "/v/b.md"],
                                             on_confirm=lambda: confirmed.append(True))
        dialog.assert_not_called()
        close.assert_not_called()
        self.assertEqual(confirmed, [True])


class TestDirtyTabsAreAlwaysAsked(CloseTest):

    def test_one_dirty_tab_among_clean_ones_still_raises_the_dialog(self):
        self._tab_bar_with({
            "/v/clean.md": self._tab(modified=False),
            "/v/dirty.md": self._tab(modified=True),
        })
        with unittest.mock.patch.object(self.win, "_show_save_dialog") as dialog, \
                unittest.mock.patch.object(self.win, "_do_close_paths") as close:
            self.win._on_tab_close_requested(["/v/clean.md", "/v/dirty.md"])
        close.assert_not_called()                       # nothing closed yet
        dirty_paths, _cb = dialog.call_args.args
        self.assertEqual(dirty_paths, ["/v/dirty.md"])  # only the dirty one is named

    def test_confirming_closes_every_requested_tab_not_just_the_dirty_one(self):
        self._tab_bar_with({
            "/v/clean.md": self._tab(modified=False),
            "/v/dirty.md": self._tab(modified=True),
        })
        with unittest.mock.patch.object(self.win, "_show_save_dialog") as dialog, \
                unittest.mock.patch.object(self.win, "_do_close_paths") as close:
            self.win._on_tab_close_requested(["/v/clean.md", "/v/dirty.md"])
            _paths, confirm = dialog.call_args.args
            confirm()
        close.assert_called_once_with(["/v/clean.md", "/v/dirty.md"])

    def test_a_tab_without_an_editor_is_not_treated_as_dirty(self):
        # get_tab returns None for a path that is no longer open (a race with an
        # external delete); treating that as dirty would block the close.
        self._tab_bar_with({})
        with unittest.mock.patch.object(self.win, "_show_save_dialog") as dialog, \
                unittest.mock.patch.object(self.win, "_do_close_paths") as close:
            self.win._on_tab_close_requested("/v/gone.md")
        dialog.assert_not_called()
        close.assert_called_once_with(["/v/gone.md"])


if __name__ == "__main__":
    unittest.main()
