"""Phase 4 — Integration: Banner bei externen Änderungen.

Tests die Banner-Integration über ContentChangeHandler:
- Datei offen in Tab → Banner wird angezeigt
- Datei nicht offen → kein Banner
- Reload → Tab-Inhalt wird aktualisiert + Banner verschwindet
- Dismiss → Banner verschwindet ohne Reload
"""

import unittest
from unittest.mock import MagicMock

from markdown_vault.content_changes import ContentChangeHandler


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

        handler.handle_external_change(tab_file)

        tab_bar.show_warning_banner.assert_called_once()

    def test_multiple_changes_updates_banner(self):
        tab_file = "/tmp/vault/test.md"
        handler, tab_bar, tabs = self._make_handler([tab_file])

        handler.handle_external_change(tab_file)
        handler.handle_external_change(tab_file)

        self.assertEqual(tab_bar.show_warning_banner.call_count, 2)


if __name__ == '__main__':
    unittest.main()
