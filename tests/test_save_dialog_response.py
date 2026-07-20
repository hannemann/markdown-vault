"""Unit tests for _on_save_dialog_response — critical Cancel-bug check.

Tests the dialog response handler directly (without GTK mocking).
IMPORTANT: Reveals that Cancel does NOT close tabs (data loss bug).
"""

import os
import tempfile
import shutil
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from markdown_vault.editor import Editor
from markdown_vault.tabs import Tab, TabBar


class TestSaveDialogResponse(unittest.TestCase):
    """Tests für _on_save_dialog_response — Cancel/Save/Discard."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._md1 = os.path.join(self._tmp, "note1.md")
        self._md2 = os.path.join(self._tmp, "note2.md")
        for p in (self._md1, self._md2):
            with open(p, "w") as f:
                f.write("# Note")

        # TabBar + Editor erstellen
        self._tab_bar = TabBar()
        self._editor1 = Editor(base_font_size=14)
        self._editor1.open_file(self._md1)
        self._editor2 = Editor(base_font_size=14)
        self._editor2.open_file(self._md2)
        self._tab_bar.add_tab(self._md1, self._editor1, unittest.mock.Mock())
        self._tab_bar.add_tab(self._md2, self._editor2, unittest.mock.Mock())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_window(self):
        """Minimaler AppWindow-Fragment mit _on_save_dialog_response."""
        import markdown_vault.app_window as aw

        # Nur die Methoden die wir testen, isoliert
        class FakeWindow:
            def __init__(self, tab_bar):
                self._tab_bar = tab_bar
                self._vault_monitor = unittest.mock.Mock()
                self._vault_monitor.skip_next_event = unittest.mock.Mock()

            # Kopiere die Methoden vom echten Window
            _on_save_dialog_response = aw.MainWindow._on_save_dialog_response
            _save_dirty_tabs = aw.MainWindow._save_dirty_tabs
            _do_close_paths = aw.MainWindow._do_close_paths

        fw = FakeWindow(self._tab_bar)
        fw._tab_bar = self._tab_bar
        return fw

    def test_cancel_does_not_call_on_confirm(self):
        """Cancel — on_confirm is NOT called, tabs remain open."""
        win = self._make_window()

        # on_confirm would close tabs
        on_confirm_called = []
        def fake_on_confirm():
            on_confirm_called.append(True)
            win._do_close_paths([self._md1])

        # Cancel — sollte on_confirm NICHT aufrufen
        win._on_save_dialog_response("cancel", [self._md1], fake_on_confirm)

        # on_confirm darf NICHT aufgerufen werden
        self.assertEqual(on_confirm_called, [],
                        "BUG: on_confirm wurde bei Cancel aufgerufen — Tabs würden geschlossen!")
        # Tab MUSS offen bleiben
        self.assertIn(self._md1, self._tab_bar.get_all_paths(),
                      "BUG: Cancel schließt Tabs — Datenverlust!")

    def test_cancel_does_not_save(self):
        """Cancel — Tabs werden NICHT gespeichert."""
        win = self._make_window()

        # Editor dirty machen
        self._editor1._buffer.set_text("# Dirty", -1)
        original_text = self._editor1.get_text()

        win._on_save_dialog_response("cancel", [self._md1], None)

        # Text sollte unverändert sein
        self.assertEqual(self._editor1.get_text(), original_text)
        # Editor sollte dirty bleiben
        self.assertTrue(self._editor1.is_modified)

    def test_save_calls_save_dirty_tabs(self):
        """Save — dirty tabs werden gespeichert."""
        win = self._make_window()

        # Editor dirty machen
        self._editor1._buffer.set_text("# Dirty", -1)
        saved_content = self._editor1.get_text()

        win._on_save_dialog_response("save", [self._md1], None)

        # Editor sollte clean sein
        self.assertFalse(self._editor1.is_modified)
        # Tab sollte geschlossen sein (via _do_close_paths in else-Zweig)
        self.assertNotIn(self._md1, self._tab_bar.get_all_paths())

    def test_discard_does_not_save_but_closes(self):
        """Discard — Tabs werden OHNE Speichern geschlossen."""
        win = self._make_window()

        # Editor dirty machen
        self._editor1._buffer.set_text("# Dirty", -1)
        original_text = self._editor1.get_text()

        win._on_save_dialog_response("discard", [self._md1], None)

        # Text sollte unverändert sein (nicht gespeichert)
        self.assertEqual(self._editor1.get_text(), original_text)
        # Tab sollte geschlossen sein
        self.assertNotIn(self._md1, self._tab_bar.get_all_paths())

    def test_save_with_on_confirm_calls_confirm(self):
        """Save + on_confirm — on_confirm wird aufgerufen."""
        win = self._make_window()

        self._editor1._buffer.set_text("# Dirty", -1)

        on_confirm_called = []
        def fake_on_confirm():
            on_confirm_called.append(True)

        win._on_save_dialog_response("save", [self._md1], fake_on_confirm)

        self.assertEqual(on_confirm_called, [True],
                        "on_confirm sollte bei Save aufgerufen werden")

    def test_discard_with_on_confirm_calls_confirm(self):
        """Discard + on_confirm — on_confirm wird aufgerufen."""
        win = self._make_window()

        on_confirm_called = []
        def fake_on_confirm():
            on_confirm_called.append(True)

        win._on_save_dialog_response("discard", [self._md1], fake_on_confirm)

        self.assertEqual(on_confirm_called, [True],
                        "on_confirm sollte bei Discard aufgerufen werden")

    def test_bulk_cancel_keeps_all_tabs(self):
        """Bulk Cancel (multiple paths) — ALLE Tabs bleiben offen."""
        win = self._make_window()

        # Beide dirty machen
        self._editor1._buffer.set_text("# Dirty1", -1)
        self._editor2._buffer.set_text("# Dirty2", -1)

        paths_before = set(self._tab_bar.get_all_paths())

        # Bulk cancel
        win._on_save_dialog_response("cancel", [self._md1, self._md2],
                                     lambda: win._do_close_paths([self._md1, self._md2]))

        # Alle Tabs müssen offen bleiben
        self.assertEqual(set(self._tab_bar.get_all_paths()), paths_before,
                        "BUG: Bulk Cancel schließt Tabs — Datenverlust!")

    def test_bulk_save_closes_all(self):
        """Bulk Save — ALLE Tabs werden gespeichert und geschlossen."""
        win = self._make_window()

        # Beide dirty machen
        self._editor1._buffer.set_text("# Dirty1", -1)
        self._editor2._buffer.set_text("# Dirty2", -1)

        win._on_save_dialog_response("save", [self._md1, self._md2], None)

        # Beide sollten clean und geschlossen sein
        self.assertFalse(self._editor1.is_modified)
        self.assertFalse(self._editor2.is_modified)
        self.assertNotIn(self._md1, self._tab_bar.get_all_paths())
        self.assertNotIn(self._md2, self._tab_bar.get_all_paths())


if __name__ == "__main__":
    unittest.main()
