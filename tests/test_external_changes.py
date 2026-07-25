"""Phase 4 — Integration: Banner bei externen Änderungen.

Tests die Banner-Integration in MainWindow:
- Datei offen in Tab → Banner wird angezeigt
- Datei nicht offen → kein Banner
- Reload → Tab-Inhalt wird aktualisiert + Banner verschwindet
- Dismiss → Banner verschwindet ohne Reload
"""

import unittest
from unittest.mock import MagicMock, patch


class TestExternalChangesBanner(unittest.TestCase):
    """Phase 4: AppWindow Banner bei externen Änderungen."""

    def _make_app_window(self):
        from markdown_vault.app_window import MainWindow
        win = MagicMock()
        win._on_external_content_changed = MainWindow._on_external_content_changed.__get__(win, type(win))
        win._on_banner_reload = MainWindow._on_banner_reload.__get__(win, type(win))
        win._on_banner_dismiss = MainWindow._on_banner_dismiss.__get__(win, type(win))
        return win

    def _make_tab_bar(self, paths):
        tab_bar = MagicMock()
        tabs = {}
        for p in paths:
            tab = MagicMock(file_path=p)
            tabs[p] = tab
        tab_bar.get_all_paths.return_value = list(paths)
        tab_bar.get_tab = lambda p: tabs.get(p)
        return tab_bar, tabs

    def test_external_change_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)

        win._tab_bar.show_warning_banner.assert_called_once()

    def test_external_change_not_open_no_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar(["/tmp/vault/other.md"])

        win._on_external_content_changed(tab_file)

        win._tab_bar.show_warning_banner.assert_not_called()

    def test_banner_reload_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_banner_reload(tab_file)

        tabs[tab_file].reload_editor.assert_called_once_with(tab_file)
        win._tab_bar.hide_warning_banner.assert_called_once_with(tab_file)

    def test_banner_dismiss_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_banner_dismiss(tab_file)

        win._tab_bar.hide_warning_banner.assert_called_once_with(tab_file)
        tabs[tab_file].reload_editor.assert_not_called()

    def test_content_changed_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)

        win._tab_bar.show_warning_banner.assert_called_once()

    def test_multiple_changes_updates_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)
        win._on_external_content_changed(tab_file)

        self.assertEqual(win._tab_bar.show_warning_banner.call_count, 2)


if __name__ == '__main__':
    unittest.main()
