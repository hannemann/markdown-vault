"""Phase 4 — Integration: Banner bei externen Änderungen.

Tests die Banner-Integration über ContentChangeHandler:
- Datei offen in Tab → Banner wird angezeigt
- Datei nicht offen → kein Banner
- Reload → Tab-Inhalt wird aktualisiert + Banner verschwindet
- Dismiss → Banner verschwindet ohne Reload
"""

import unittest
from unittest.mock import MagicMock

from markdown_vault.editor.content_changes import ContentChangeHandler


class TestExternalChangesBanner(unittest.TestCase):
    """Phase 4: ContentChangeHandler bei externen Änderungen."""

    def _make_handler(self, paths):
        """Create a handler with a mocked tab_bar containing the given paths."""
        tabs = {}
        for p in paths:
            tab = MagicMock(file_path=p)
            tabs[p] = tab
        tab_bar = MagicMock()
        tab_bar.get_all_paths.return_value = list(paths)
        tab_bar.get_tab = lambda p: tabs.get(p)
        # Stub explicitly: a bare MagicMock would auto-answer this with a truthy Mock, so a file
        # that is NOT open would look open and the "no banner" tests would pass for the wrong
        # reason. These paths do not exist on disk, so plain equality is the whole comparison.
        tab_bar.find_tab_for_file = lambda p: tabs.get(p)
        handler = ContentChangeHandler(tab_bar=tab_bar)
        return handler, tab_bar, tabs

    def test_external_change_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        handler.handle_external_change(tab_file)

        tab_bar.show_warning_banner.assert_called_once()

    def test_external_change_not_open_no_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler(["/tmp/vault/other.md"])

        handler.handle_external_change(tab_file)

        tab_bar.show_warning_banner.assert_not_called()

    def test_banner_reload_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        handler.reload_content(tab_file)

        tabs[tab_file].reload_editor.assert_called_once_with(tab_file)
        tab_bar.hide_warning_banner.assert_called_once_with(tab_file)

    def test_banner_dismiss_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        handler.dismiss_content(tab_file)

        tab_bar.hide_warning_banner.assert_called_once_with(tab_file)
        tabs[tab_file].reload_editor.assert_not_called()

    def test_content_changed_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])
        tabs[tab_file].editor.is_modified = True

        handler.handle_external_change(tab_file)

        tab_bar.show_warning_banner.assert_called_once()

    def test_clean_tab_reloads_silently_without_banner(self):
        """Clean tab + external change → silent reload, no banner, no flag."""
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])
        tabs[tab_file].editor.is_modified = False

        handler.handle_external_change(tab_file)

        tabs[tab_file].reload_editor.assert_called_once_with(tab_file)
        tab_bar.show_warning_banner.assert_not_called()
        self.assertFalse(tabs[tab_file].external_change_pending)

    def test_dirty_tab_shows_banner_and_sets_pending_flag(self):
        """Dirty tab + external change → banner + conflict flag set."""
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])
        tabs[tab_file].editor.is_modified = True

        handler.handle_external_change(tab_file)

        tab_bar.show_warning_banner.assert_called_once()
        tabs[tab_file].reload_editor.assert_not_called()
        self.assertTrue(tabs[tab_file].external_change_pending)

    def test_reload_clears_pending_flag(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])
        tabs[tab_file].external_change_pending = True

        handler.reload_content(tab_file)

        self.assertFalse(tabs[tab_file].external_change_pending)

    def test_dismiss_clears_pending_flag(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])
        tabs[tab_file].external_change_pending = True

        handler.dismiss_content(tab_file)

        self.assertFalse(tabs[tab_file].external_change_pending)

    def test_multiple_changes_updates_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        handler.handle_external_change(tab_file)
        handler.handle_external_change(tab_file)

        self.assertEqual(tab_bar.show_warning_banner.call_count, 2)

    def test_banner_reload_failure_keeps_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        tabs[tab_file].reload_editor.return_value = False

        handler.reload_content(tab_file)

        tab_bar.hide_warning_banner.assert_not_called()
        tabs[tab_file].preview.update_from_text.assert_not_called()

    @unittest.mock.patch("markdown_vault.editor.content_changes.show_error")
    def test_banner_reload_failure_shows_error_dialog(self, mock_show_error):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        parent = MagicMock()
        from markdown_vault.editor.content_changes import ContentChangeHandler
        handler = ContentChangeHandler(tab_bar=tab_bar, parent=parent)

        tabs[tab_file].reload_editor.return_value = False

        handler.reload_content(tab_file)

        tab_bar.hide_warning_banner.assert_not_called()
        mock_show_error.assert_called_once_with(
            parent, "Reload Failed", 'Could not read "test.md" from disk.'
        )

    @unittest.mock.patch("markdown_vault.editor.content_changes.show_error")
    def test_reload_content_missing_file_shows_dialog(self, mock_show_error):
        """Real integration: missing file → reload fails → dialog shown."""
        tab_file = "/tmp/vault_nonexistent_12345/test.md"

        handler, tab_bar, tabs = self._make_handler([tab_file])

        parent = MagicMock()
        from markdown_vault.editor.content_changes import ContentChangeHandler
        handler = ContentChangeHandler(tab_bar=tab_bar, parent=parent)

        tabs[tab_file].reload_editor.return_value = False

        handler.reload_content(tab_file)

        tab_bar.hide_warning_banner.assert_not_called()
        tabs[tab_file].preview.update_from_text.assert_not_called()
        mock_show_error.assert_called_once_with(
            parent, "Reload Failed", 'Could not read "test.md" from disk.'
        )


class TestEventOnTheTargetPathFindsTheLinkedTab(unittest.TestCase):
    """A monitor event names the path the FILESYSTEM saw, which for a symlinked note is the
    target — never the link, since the link's directory entry is untouched by a write through it.

    The tab is keyed by the link (its identity), so matching the event to a tab has to resolve.
    Before this, the two matched only by accident, when the tab happened to be keyed by the
    target as well; keying tabs by the link without fixing this would silently switch off
    external-change detection for exactly those notes.
    """

    def setUp(self):
        import os
        import shutil
        import tempfile
        from unittest.mock import MagicMock
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.target = os.path.join(self._tmp, "target.md")
        with open(self.target, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.link = os.path.join(self._tmp, "link.md")
        os.symlink(self.target, self.link)

        self.tab = MagicMock(file_path=self.link)
        self.tab.editor.is_modified = True          # a real conflict, so the banner path runs
        tabs = {self.link: self.tab}
        self.tab_bar = MagicMock()
        self.tab_bar.get_all_paths.return_value = [self.link]
        self.tab_bar.get_tab = lambda p: tabs.get(p)
        self.tab_bar.find_tab_for_file = lambda p: next(
            (t for k, t in tabs.items() if os.path.realpath(k) == os.path.realpath(p)), None)
        self.handler = ContentChangeHandler(tab_bar=self.tab_bar)

    def test_an_event_on_the_target_reaches_the_tab_keyed_by_the_link(self):
        self.handler.handle_external_change(self.target)
        self.tab_bar.show_warning_banner.assert_called_once()
        self.assertTrue(self.tab.external_change_pending)

    def test_the_banner_is_attached_to_the_tabs_own_key(self):
        # The banner is addressed by tab key elsewhere in the tab bar, so passing the event's
        # spelling would attach it to a tab that does not exist.
        self.handler.handle_external_change(self.target)
        self.assertEqual(self.tab_bar.show_warning_banner.call_args[0][0], self.link)

    def test_an_event_for_a_file_that_is_not_open_still_does_nothing(self):
        import os
        other = os.path.join(self._tmp, "other.md")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("y")
        self.handler.handle_external_change(other)
        self.tab_bar.show_warning_banner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
