"""Smoke E2E tests — the async/integration flows that unit tests can't reach:
the app boots, a file opens and becomes active, full-text search returns the
right notes, path confinement holds, and tabs open/close. Deliberately thin.
"""

import os
import unittest

from harness import AppSession


class TestSmoke(AppSession):
    def test_app_boots_with_the_test_vault(self):
        # If the debug interface answered in setUp, the app is up; check it is the
        # isolated fixture vault, not a real one.
        self.assertEqual(self.active_file(), "")           # nothing open yet
        self.assertEqual(self.list_tabs(), [])

    def test_open_file_becomes_active(self):
        self.assertTrue(self.open_file("erde.md"))
        self.assertEqual(self.active_file(), self.path("erde.md"))
        self.assertIn(self.path("erde.md"), self.list_tabs())

    def test_search_returns_matching_notes(self):
        self.search("planet")
        results = self.search_results()
        self.assertIn(self.path("erde.md"), results)
        self.assertIn(self.path("mars.md"), results)
        self.assertNotIn(self.path("notes.md"), results)   # no "planet" in it

    def test_open_outside_vault_is_rejected(self):
        # Confinement: the debug interface must not open arbitrary files.
        rejected = self._call("OpenFile", _s("/etc/passwd"), "(b)")[0]
        self.assertFalse(rejected)

    def test_close_tab(self):
        self.open_file("mars.md")
        self.open_file("notes.md")
        self.assertIn(self.path("mars.md"), self.list_tabs())
        self.assertTrue(self.close_tab("mars.md"))
        self.assertNotIn(self.path("mars.md"), self.list_tabs())


def _s(value):
    from gi.repository import GLib
    return GLib.Variant("(s)", (value,))


if __name__ == "__main__":
    unittest.main()
