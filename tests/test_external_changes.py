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
        with patch('gi.repository.Adw.Application') as mock_app:
            from src.app_window import MainWindow
            win = MagicMock()
            win._on_external_content_changed = MainWindow._on_external_content_changed.__get__(win, type(win))
            win._on_banner_reload = MainWindow._on_banner_reload.__get__(win, type(win))
            win._on_banner_dismiss = MainWindow._on_banner_dismiss.__get__(win, type(win))
            return win

    def _make_tab_bar(self, paths):
        tab_bar = MagicMock()
        tabs = {}
        for p in paths:
            banner = MagicMock()
            label = MagicMock()
            tab = MagicMock(file_path=p, banner=banner, _banner_label=label)
            tabs[p] = tab
        tab_bar.get_all_paths.return_value = list(paths)
        tab_bar.get_tab = lambda p: tabs.get(p)
        return tab_bar, tabs

    def test_external_change_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)

        tabs[tab_file].banner.set_reveal_child.assert_called_once_with(True)

    def test_external_change_not_open_no_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar(["/tmp/vault/other.md"])

        win._on_external_content_changed(tab_file)

        for t in tabs.values():
            t.banner.set_reveal_child.assert_not_called()

    def test_banner_reload_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_banner_reload(tab_file)

        tabs[tab_file].reload_editor.assert_called_once_with(tab_file)
        tabs[tab_file].banner.set_reveal_child.assert_called_once_with(False)

    def test_banner_dismiss_hides_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_banner_dismiss(tab_file)

        tabs[tab_file].banner.set_reveal_child.assert_called_once_with(False)
        tabs[tab_file].reload_editor.assert_not_called()

    def test_content_changed_shows_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)

        tabs[tab_file].banner.set_reveal_child.assert_called_once_with(True)

    def test_multiple_changes_updates_banner(self):
        tab_file = "/tmp/vault/test.md"
        win = self._make_app_window()
        win._tab_bar, tabs = self._make_tab_bar([tab_file])

        win._on_external_content_changed(tab_file)
        win._on_external_content_changed(tab_file)

        self.assertEqual(tabs[tab_file].banner.set_reveal_child.call_count, 2)
        self.assertEqual(tabs[tab_file]._banner_label.set_text.call_count, 2)


if __name__ == '__main__':
    unittest.main()
