"""Tests for the gettext runtime binding (core.i18n)."""
import unittest

from markdown_vault.core import i18n


class TestI18n(unittest.TestCase):
    def test_singular_is_identity_without_catalog(self):
        # Under the pinned test locale (LC_ALL=C, see Makefile TEST_ENV) no catalog
        # matches, so _() returns the English msgid unchanged. This is also the guard
        # for ZB3: an unpinned locale + a shipped de.mo would translate here and the
        # assertion would fail on a German machine.
        self.assertEqual(i18n._("Open File"), "Open File")

    def test_ngettext_selects_by_count(self):
        self.assertEqual(i18n.ngettext("%d note", "%d notes", 1), "%d note")
        self.assertEqual(i18n.ngettext("%d note", "%d notes", 2), "%d notes")
        self.assertEqual(i18n.ngettext("%d note", "%d notes", 0), "%d notes")

    def test_localedir_ends_at_locale(self):
        self.assertTrue(i18n._localedir().endswith("locale"))


if __name__ == "__main__":
    unittest.main()
