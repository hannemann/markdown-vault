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


class TestFetchGuards(unittest.TestCase):
    """R71.4: a redirect must not leave the http(s) allowlist."""

    def _redirect(self, newurl):
        import email.message
        import urllib.request
        guard = wi._HttpRedirectGuard()
        req = urllib.request.Request("https://x.io/a")
        return guard.redirect_request(req, None, 302, "Found",
                                      email.message.Message(), newurl)

    def test_allows_http_and_https(self):
        self.assertIsNotNone(self._redirect("http://x.io/b"))
        self.assertIsNotNone(self._redirect("https://x.io/b"))

    def test_refuses_ftp_and_file(self):
        import urllib.error
        for bad in ("ftp://x.io/b", "file:///etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError):
                self._redirect(bad)


class _FakeResp:
    """A stand-in for the object opener.open() yields: a context manager with
    ``headers`` (an email.message.Message) and a ``read(n)`` that honours n."""

    def __init__(self, body=b"<html>ok</html>", content_type="text/html",
                 charset="utf-8", content_length=None):
        import email.message
        self._body = body
        self.read_arg = "unread"   # records the n passed to read()
        h = email.message.Message()
        ct = content_type + (f"; charset={charset}" if charset else "")
        h["Content-Type"] = ct
        if content_length is not None:
            h["Content-Length"] = str(content_length)
        self.headers = h

    def read(self, n=-1):
        self.read_arg = n
        return self._body[:n] if n is not None and n >= 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchBodyGuards(unittest.TestCase):
    """R72.1: the Content-Type check and the body cap (both branches) have tests."""

    def _fetch(self, resp):
        import unittest.mock as mock
        opener = mock.Mock()
        opener.open.return_value = resp
        with mock.patch("urllib.request.build_opener", return_value=opener):
            return wi.fetch_html("https://x.io/p")

    def test_reads_html_ok(self):
        self.assertEqual(self._fetch(_FakeResp(b"<html>hi</html>")), "<html>hi</html>")

    def test_read_is_bounded(self):
        # The memory protection IS the bounded read; the len() check only turns it
        # into an error. Simplifying to a plain resp.read() (unbounded) would slurp
        # the whole page first — pin the argument so that regression fails here.
        resp = _FakeResp(b"<html>hi</html>")
        self._fetch(resp)
        self.assertEqual(resp.read_arg, wi._MAX_BYTES + 1)

    def test_rejects_non_html_content_type(self):
        with self.assertRaisesRegex(ValueError, "Not an HTML page"):
            self._fetch(_FakeResp(content_type="application/pdf", charset=None))

    def test_rejects_oversize_content_length(self):
        import unittest.mock as mock
        with mock.patch.object(wi, "_MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "too large"):
                self._fetch(_FakeResp(b"<html>ok</html>", content_length=11))

    def test_rejects_oversize_body(self):
        # No Content-Length header, so only the post-read len() check can catch it.
        import unittest.mock as mock
        with mock.patch.object(wi, "_MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                self._fetch(_FakeResp(b"x" * 11))


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
    def _fm(self, title="A Page", **kw):
        from markdown_vault import frontmatter
        r = wi.ImportResult(url="https://x.io/p", title=title,
                            markdown="# Body\ntext", **kw)
        note = wi.to_note(r, today=datetime.date(2026, 8, 14))
        return frontmatter.parse(note), note

    def test_minimal_frontmatter(self):
        fm, note = self._fm()
        self.assertEqual(fm["title"], "A Page")
        self.assertEqual(fm["source"], "https://x.io/p")
        self.assertIn("# Body\ntext", note)
        self.assertNotIn("author", fm)

    def test_optional_metadata_included(self):
        fm, _ = self._fm(author="Jane", date="2025-01-02", sitename="X")
        self.assertEqual(fm["author"], "Jane")
        self.assertEqual(fm["published"], "2025-01-02")
        self.assertEqual(fm["site"], "X")

    def test_awkward_titles_still_round_trip(self):
        # R71.1: a leading YAML indicator, an inline '#', or a bool word must not
        # break the block (which would drop ALL frontmatter).
        for title in ("- leading dash", "? leading question", "Foo # not a comment",
                      "true", "Ratio 16: 9", "Doom: the Sequel", "[bracket]"):
            fm, _ = self._fm(title=title)
            self.assertEqual(fm.get("title"), title, f"{title!r} must round-trip")

    def test_title_newlines_collapsed_no_injection(self):
        # A crafted title cannot inject extra keys.
        fm, _ = self._fm(title="Evil\nauthor: attacker")
        self.assertEqual(fm["title"], "Evil author: attacker")
        self.assertNotIn("author", fm)


class TestAvailability(unittest.TestCase):
    def test_hint_names_the_install_command(self):
        self.assertIn("pip install trafilatura", wi._INSTALL_HINT)

    def test_availability_is_none_or_hint(self):
        av = wi.availability()
        self.assertTrue(av is None or "trafilatura" in av.lower())


def _has_table_deps():
    try:
        import bs4, lxml, markdownify, nh3  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestComplexity(unittest.TestCase):
    def _t(self, html):
        import bs4
        return bs4.BeautifulSoup(html, "html.parser").table

    def test_simple_is_not_complex(self):
        self.assertFalse(wi._is_complex_table(
            self._t("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")))

    def test_colspan_is_complex(self):
        self.assertTrue(wi._is_complex_table(self._t("<table><tr><td colspan='2'>x</td></tr></table>")))

    def test_list_in_cell_is_complex(self):
        self.assertTrue(wi._is_complex_table(
            self._t("<table><tr><td><ul><li>x</li></ul></td></tr></table>")))

    def test_caption_is_complex(self):
        self.assertTrue(wi._is_complex_table(
            self._t("<table><caption>C</caption><tr><td>x</td></tr></table>")))


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestTableToMarkdown(unittest.TestCase):
    def test_simple_becomes_pipe(self):
        md = wi._html_to_markdown(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
        self.assertIn("| A | B |", md)
        self.assertIn("---", md)
        self.assertNotIn("<table", md)

    def test_complex_kept_as_sanitised_html(self):
        md = wi._html_to_markdown(
            '<table><tr><td colspan="2" class="x" onclick="e()">y</td></tr></table>')
        self.assertIn("<table", md)
        self.assertIn('colspan="2"', md)
        self.assertNotIn("class=", md)         # sanitised away
        self.assertNotIn("onclick", md)


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestPlaceholders(unittest.TestCase):
    def test_keep_drops_navbox_keeps_data(self):
        from lxml import html as LH
        self.assertFalse(wi._keep_source_table(
            LH.fromstring('<table class="navbox"><tr><td>x</td></tr></table>')))
        self.assertTrue(wi._keep_source_table(
            LH.fromstring('<table class="wikitable"><tr><td>x</td></tr></table>')))

    def test_inject_then_restore_roundtrip(self):
        html = ("<div><p>before</p><table><tr><th>A</th></tr><tr><td>1</td></tr>"
                "</table><p>after</p></div>")
        modified, tables = wi._inject_placeholders(html)
        self.assertEqual(len(tables), 1)
        self.assertNotIn("<table", modified)          # replaced by a marker
        self.assertIn(wi._MARKER.format(0), modified)
        prose = "before\n\n" + wi._MARKER.format(0) + "\n\nafter"
        out = wi._restore_placeholders(prose, tables)
        self.assertIn("| A |", out)
        self.assertNotIn(wi._MARKER.format(0), out)

    def test_lost_marker_is_appended(self):
        out = wi._restore_placeholders("just prose", ["| A |\n| --- |\n| 1 |"])
        self.assertIn("## Tables", out)
        self.assertIn("| A |", out)


if __name__ == "__main__":
    unittest.main()
