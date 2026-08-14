"""Tests for web_import — the pure pieces (URL validation, slug, note assembly).
The Trafilatura extraction itself needs the optional dependency and a page, so it
isn't unit-tested here."""

import datetime
import unittest

from markdown_vault import web_import as wi


class TestValidateUrl(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(wi.validate_url("https://example.com/a"), "https://example.com/a")
        self.assertEqual(wi.validate_url("  http://x.io/p "), "http://x.io/p")

    def test_rejects_other_schemes_and_junk(self):
        for bad in ("ftp://x/y", "file:///etc/passwd", "example.com", "", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                wi.validate_url(bad)


class TestSlug(unittest.TestCase):
    def test_kebab_and_strip_punctuation(self):
        self.assertEqual(wi.slug("Hello, World! (2026)"), "hello-world-2026")

    def test_collapses_separators(self):
        self.assertEqual(wi.slug("a   b__c--d"), "a-b-c-d")

    def test_unicode_words_kept(self):
        self.assertEqual(wi.slug("Über Größe"), "über-größe")

    def test_length_cap(self):
        self.assertLessEqual(len(wi.slug("x" * 200)), 60)

    def test_empty_fallback(self):
        self.assertEqual(wi.slug("!!!"), "imported-page")
        self.assertEqual(wi.slug(""), "imported-page")


class TestToNote(unittest.TestCase):
    def _note(self, **kw):
        r = wi.ImportResult(url="https://x.io/p", title="A Page", markdown="# Body\ntext", **kw)
        return wi.to_note(r, today=datetime.date(2026, 8, 14))

    def test_minimal_frontmatter(self):
        note = self._note()
        self.assertIn("title: A Page", note)
        self.assertIn("source: https://x.io/p", note)
        self.assertIn("imported: 2026-08-14", note)
        self.assertIn("# Body\ntext", note)
        self.assertNotIn("author:", note)

    def test_optional_metadata_included(self):
        note = self._note(author="Jane", date="2025-01-02", sitename="X")
        self.assertIn("author: Jane", note)
        self.assertIn("published: 2025-01-02", note)
        self.assertIn("site: X", note)

    def test_title_with_colon_is_quoted(self):
        r = wi.ImportResult(url="https://x.io", title="Doom: the Sequel", markdown="b")
        note = wi.to_note(r, today=datetime.date(2026, 8, 14))
        self.assertIn('title: "Doom: the Sequel"', note)


class TestAvailability(unittest.TestCase):
    def test_hint_names_the_install_command(self):
        self.assertIn("pip install trafilatura", wi._INSTALL_HINT)

    def test_availability_is_none_or_hint(self):
        av = wi.availability()
        self.assertTrue(av is None or "trafilatura" in av.lower())


if __name__ == "__main__":
    unittest.main()
