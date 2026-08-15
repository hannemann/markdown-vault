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


class TestStripTracking(unittest.TestCase):
    def test_removes_utm_family(self):
        self.assertEqual(
            wi._strip_tracking("https://x.io/a?utm_source=s&utm_medium=m&utm_campaign=c"),
            "https://x.io/a")

    def test_keeps_functional_params(self):
        self.assertEqual(wi._strip_tracking("https://x.io/a?id=5&utm_source=s"),
                         "https://x.io/a?id=5")

    def test_keeps_youtube_video_id(self):
        self.assertEqual(wi._strip_tracking("https://youtube.com/watch?v=abc&utm_source=s"),
                         "https://youtube.com/watch?v=abc")

    def test_removes_click_ids(self):
        self.assertEqual(wi._strip_tracking("https://x.io/a?fbclid=1&gclid=2&msclkid=3"),
                         "https://x.io/a")

    def test_no_query_is_unchanged(self):
        self.assertEqual(wi._strip_tracking("https://x.io/a"), "https://x.io/a")

    def test_preserves_encoding_when_nothing_dropped(self):
        # R90.2: a presigned URL's signature covers the exact query string —
        # re-encoding it (e.g. / -> %2F) would break the signature. Return as-is.
        url = ("https://cdn.io/x.jpg?X-Amz-Credential=AKIA/20260815/eu/s3/aws4_request"
               "&X-Amz-Signature=abc123")
        self.assertEqual(wi._strip_tracking(url), url)

    def test_preserves_percent_encoding_when_nothing_dropped(self):
        url = "https://x.io/a?q=a%20b&r=x%2Fy"
        self.assertEqual(wi._strip_tracking(url), url)

    def test_works_on_relative_url(self):
        self.assertEqual(wi._strip_tracking("/page?utm_campaign=x"), "/page")

    def test_preserves_fragment(self):
        self.assertEqual(wi._strip_tracking("https://x.io/a?utm_source=s#sec"),
                         "https://x.io/a#sec")

    def test_stripped_from_link_image_and_media_in_extract(self):
        md = wi.extract(
            '<html><body><article><h1>T</h1><p>' + ('lorem ipsum ' * 20) + '</p>'
            '<p>See <a href="https://x.io/a?utm_source=s&id=5">this link</a> here.</p>'
            '<p><img src="https://x.io/pic.png?utm_medium=m" alt="p"></p>'
            '<video src="https://x.io/c.mp4?fbclid=9" controls></video>'
            '</article></body></html>', "https://ex.com/").markdown
        self.assertNotIn("utm_source", md)
        self.assertNotIn("utm_medium", md)
        self.assertNotIn("fbclid", md)
        self.assertIn("https://x.io/a?id=5", md)      # functional param kept
        self.assertIn("https://x.io/pic.png)", md)
        self.assertIn("https://x.io/c.mp4)", md)


class TestDedentAfterHeadings(unittest.TestCase):
    """Trafilatura indents the first content line after a heading (MDN section
    markup). An indented ``` opener stops being a fence and breaks the page."""

    def test_indented_fence_opener_is_dedented(self):
        md = wi._dedent_after_headings("## H\n\n    ```\ncode\n```\n\ntail")
        self.assertIn("\n```\ncode\n```", md)
        self.assertNotIn("    ```", md)

    def test_indented_paragraph_is_dedented(self):
        md = wi._dedent_after_headings("## H\n\n    Some prose here.\n\n- item")
        self.assertIn("\nSome prose here.", md)
        self.assertNotIn("    Some prose", md)

    def test_indentation_inside_fence_is_preserved(self):
        md = wi._dedent_after_headings("## H\n\n```\n    indented code\n```\n")
        self.assertIn("    indented code", md)

    def test_indented_heading_chains_dedent_to_its_body(self):
        # An indented heading is itself dedented, and still primes its own body.
        md = wi._dedent_after_headings("## A\n\n      ## B\n\n    body\n")
        self.assertIn("\n## B\n", md)
        self.assertIn("\nbody", md)
        self.assertNotIn("    body", md)

    def test_no_heading_leaves_text_untouched(self):
        src = "para\n\n- a\n  - nested list stays\n"
        self.assertEqual(wi._dedent_after_headings(src), src)


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


class TestMetadata(unittest.TestCase):
    """Title-suffix stripping and author sanitation."""

    def test_strip_suffix_matches_hostname_core(self):
        self.assertEqual(
            wi._strip_title_suffix("Markdown - Wikipedia",
                                   "https://en.wikipedia.org/wiki/Markdown", "Wikimedia"),
            "Markdown")
        self.assertEqual(
            wi._strip_title_suffix("Python's F-String – Real Python",
                                   "https://realpython.com/x/", "Realpython"),
            "Python's F-String")
        self.assertEqual(
            wi._strip_title_suffix("Wget - GNU Project",
                                   "https://www.gnu.org/software/wget/", "gnu.org"),
            "Wget")

    def test_strip_suffix_kept_when_tail_is_not_the_site(self):
        self.assertEqual(
            wi._strip_title_suffix("Cause - Effect", "https://example.com/", "Example"),
            "Cause - Effect")

    def test_strip_suffix_no_separator(self):
        self.assertEqual(
            wi._strip_title_suffix("Just A Title", "https://ex.com/", "Ex"),
            "Just A Title")

    def test_strip_suffix_title_is_site_only(self):
        self.assertEqual(
            wi._strip_title_suffix("Wikipedia", "https://en.wikipedia.org/", "Wikipedia"),
            "Wikipedia")

    def test_strip_suffix_not_fooled_by_short_core(self):
        # R90.1: a 1-2 char hostname core must not match arbitrary trailing segments.
        self.assertEqual(
            wi._strip_title_suffix("Rust - Linux", "https://x.com/", ""),
            "Rust - Linux")
        self.assertEqual(
            wi._strip_title_suffix("Alpha - Box", "https://x.co/", ""),
            "Alpha - Box")

    def test_strip_suffix_not_fooled_by_site_substring(self):
        # R90.1: tail being a substring of the site name is not evidence it IS the site.
        self.assertEqual(
            wi._strip_title_suffix("A - B", "https://example.org/", "AB"),
            "A - B")

    def test_clean_author_drops_boilerplate(self):
        self.assertEqual(wi._clean_author(
            "Authority control databases International FAST National United States "
            "Israel", ""), "")

    def test_clean_author_keeps_real_name(self):
        self.assertEqual(wi._clean_author("Real Python; Joanna Jablonski", "Realpython"),
                         "Real Python; Joanna Jablonski")

    def test_clean_author_drops_sitename(self):
        self.assertEqual(wi._clean_author("Example Site", "Example Site"), "")

    def test_clean_author_empty(self):
        self.assertEqual(wi._clean_author("", "X"), "")


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

    def test_image_in_cell_is_not_complex(self):
        # R76.1: the renderer shows an image in a pipe cell, so an image alone
        # does not force HTML — it stays a pipe table.
        self.assertFalse(wi._is_complex_table(
            self._t('<table><tr><td><img src="x.png"></td></tr></table>')))


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestMath(unittest.TestCase):
    """MathML -> $…$ / $$…$$ so formulas survive extraction and render via LaTeX."""

    def _convert(self, html):
        from lxml import html as LH
        tree = LH.fromstring(html)
        wi._convert_math_tree(tree)
        return LH.tostring(tree, encoding="unicode")

    def test_inline_from_annotation(self):
        out = self._convert(
            '<p>x <math><semantics>'
            '<annotation encoding="application/x-tex">a^2+b^2</annotation>'
            '</semantics></math> y</p>')
        self.assertIn("$a^2+b^2$", out)
        self.assertNotIn("<math", out)

    def test_block_math(self):
        out = self._convert('<p><math display="block" alttext="c^2=a^2+b^2"></math></p>')
        self.assertIn("$$c^2=a^2+b^2$$", out)

    def test_alttext_fallback(self):
        self.assertIn("$E=mc^2$", self._convert('<p><math alttext="E=mc^2"></math></p>'))

    def test_displaystyle_wrapper_stripped(self):
        out = self._convert(r'<p><math alttext="{\displaystyle a+b}"></math></p>')
        self.assertIn("$a+b$", out)
        self.assertNotIn("displaystyle", out)

    def test_math_without_tex_is_left_alone(self):
        out = self._convert('<p><math><mi>x</mi></math></p>')
        self.assertNotIn("$", out)      # nothing to convert → no spurious markers

    def test_math_in_table_cell_is_converted(self):
        # Math is converted before tables are extracted, so cell math becomes
        # $…$ (which then renders once the table is a pipe table).
        out = wi._clean_content_html(
            '<table><tr><td><math alttext="a^2"></math></td></tr></table>',
            "https://ex.com/")
        self.assertIn("$a^2$", out)

    def test_block_math_in_table_is_inline(self):
        # $$…$$ in a pipe cell mis-parses (a stray literal $ survives), so math in
        # a table cell must be inline $…$.
        out = self._convert(
            '<table><tr><td>'
            '<math display="block" alttext="a_{1}"></math></td></tr></table>')
        self.assertIn("$a_{1}$", out)
        self.assertNotIn("$$", out)

    def test_block_math_outside_table_stays_block(self):
        out = self._convert('<p><math display="block" alttext="a_{1}"></math></p>')
        self.assertIn("$$a_{1}$$", out)

    def test_noop_colrowspan_stripped(self):
        # colspan/rowspan="1" are no-ops but make _is_complex_table keep the table
        # as HTML instead of a pipe table — strip them; keep real spans.
        out = wi._clean_content_html(
            '<table><tr><td colspan="1" rowspan="1">x</td>'
            '<td colspan="2">y</td></tr></table>', "https://ex.com/")
        self.assertNotIn('colspan="1"', out)
        self.assertNotIn('rowspan="1"', out)
        self.assertIn('colspan="2"', out)


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestTableToMarkdown(unittest.TestCase):
    def test_simple_becomes_pipe(self):
        md = wi._html_to_markdown(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
        self.assertIn("| A | B |", md)
        self.assertIn("---", md)
        self.assertNotIn("<table", md)

    def test_image_kept_as_pipe_not_html(self):
        # R76.1: a simple table with an image stays a pipe table, image markdown
        # preserved (not reduced to alt text, not escalated to an HTML blob).
        md = wi._html_to_markdown(
            '<table><tr><th>Flag</th></tr>'
            '<tr><td><img src="https://ex.com/de.png" alt="DE"></td></tr></table>')
        self.assertIn("![DE](https://ex.com/de.png)", md)
        self.assertNotIn("<table", md)

    def test_underscores_not_escaped_in_cells(self):
        # markdownify escapes _ by default, which corrupts LaTeX ($…W_{1}…$) in a
        # cell — GFM doesn't treat intra-word _ as emphasis, so keep it verbatim.
        md = wi._html_to_markdown('<table><tr><td>$a_{1}+W_{2}$</td></tr></table>')
        self.assertNotIn("\\_", md)
        self.assertIn("a_{1}+W_{2}", md)

    def test_complex_kept_as_sanitised_html(self):
        md = wi._html_to_markdown(
            '<table><tr><td colspan="2" class="x" onclick="e()">y</td></tr></table>')
        self.assertIn("<table", md)
        self.assertIn('colspan="2"', md)
        self.assertNotIn("class=", md)         # sanitised away
        self.assertNotIn("onclick", md)

    def test_complex_table_unwraps_presentational_spans(self):
        # A kept table from any page must not leak <span> noise into the note.
        md = wi._html_to_markdown(
            '<table><tr><td colspan="2"><span class="hl">Cell</span> '
            '<span>text</span></td></tr></table>')
        self.assertIn("<table", md)
        self.assertNotIn("<span", md)
        self.assertIn("Cell", md)
        self.assertIn("text", md)


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


@unittest.skipUnless(_has_table_deps(), "web-import table deps not installed")
class TestImageNormalize(unittest.TestCase):
    """Ansatz C: clean up <img> in place so Trafilatura emits sound URLs."""

    def _img(self, html):
        from lxml import html as LH
        return LH.fromstring(html)

    def test_tracking_pixel_by_dimension(self):
        for dim in ('width="1"', 'height="1"', 'width="0"', 'height="0"'):
            self.assertTrue(wi._is_tracking_pixel(self._img(f'<img src="p.gif" {dim}>')),
                            dim)

    def test_real_image_is_not_tracking(self):
        self.assertFalse(wi._is_tracking_pixel(self._img('<img src="p.png" width="640">')))
        self.assertFalse(wi._is_tracking_pixel(self._img('<img src="p.png">')))

    def test_pick_src_plain_and_lazy_when_no_conflict(self):
        base = "https://ex.com/a/b.html"
        self.assertEqual(wi._pick_img_src(self._img('<img src="x.png">'), base),
                         "https://ex.com/a/x.png")          # no lazy attr -> plain src
        self.assertEqual(wi._pick_img_src(self._img('<img data-src="y.png">'), base),
                         "https://ex.com/a/y.png")          # no src -> data-src

    def test_pick_src_lazy_attr_wins_over_placeholder(self):
        # Lazy-load: a data-* source means the plain src is only a placeholder.
        base = "https://ex.com/a/b.html"
        for attr in ("data-src", "data-original", "data-lazy-src", "data-lazy"):
            self.assertEqual(
                wi._pick_img_src(self._img(f'<img src="ph.gif" {attr}="real.jpg">'), base),
                "https://ex.com/a/real.jpg", attr)

    def test_pick_src_lazy_srcset_wins_over_placeholder(self):
        base = "https://ex.com/a/"
        el = self._img('<img src="ph.gif" data-srcset="small.jpg 480w, big.jpg 1024w">')
        self.assertEqual(wi._pick_img_src(el, base), "https://ex.com/a/big.jpg")

    def test_lazy_image_with_pixel_placeholder_is_kept(self):
        # A 1x1 placeholder src with a real data-src is a lazy image, not a beacon.
        out = wi._normalize_images(
            '<img src="data:image/gif;base64,AAAA" width="1" height="1" '
            'data-src="/real.jpg">', "https://ex.com/")
        self.assertIn("https://ex.com/real.jpg", out)

    def test_pick_src_protocol_relative_and_srcset(self):
        base = "https://ex.com/a/"
        self.assertEqual(wi._pick_img_src(self._img('<img src="//cdn.io/z.png">'), base),
                         "https://cdn.io/z.png")
        el = self._img('<img srcset="s.png 480w, big.png 1024w">')
        self.assertEqual(wi._pick_img_src(el, base), "https://ex.com/a/big.png")

    def test_pick_src_none_when_empty(self):
        self.assertIsNone(wi._pick_img_src(self._img('<img alt="x">'), "https://ex.com/"))

    def test_pick_src_scheme_allowlist(self):
        # R75.1: only http/https and data:image survive normalisation.
        base = "https://ex.com/"
        self.assertIsNone(wi._pick_img_src(self._img('<img src="javascript:alert(1)">'), base))
        self.assertIsNone(wi._pick_img_src(self._img('<img src="data:text/html,<b>">'), base))
        self.assertEqual(
            wi._pick_img_src(self._img('<img src="data:image/png;base64,AAA">'), base),
            "data:image/png;base64,AAA")

    def test_normalize_strips_attrs_and_drops_noise(self):
        html = ('<div><img src="/a/pic.png" alt="Cat" class="lazy" width="640">'
                '<img src="beacon.gif" width="1" height="1">'
                '<img alt="broken"></div>')
        out = wi._normalize_images(html, "https://ex.com/post.html")
        node = self._img(out)
        imgs = node.xpath("//img")
        self.assertEqual(len(imgs), 1)                       # pixel + srcless gone
        self.assertEqual(imgs[0].get("src"), "https://ex.com/a/pic.png")
        self.assertEqual(imgs[0].get("alt"), "Cat")
        self.assertIsNone(imgs[0].get("class"))              # attrs stripped

    def test_clean_content_unwraps_spans_keeps_text(self):
        # Trafilatura leaks syntax-highlight <span>s into code blocks; unwrapping
        # them before extraction keeps the text but removes the tag noise.
        html = ('<pre><span class="k">![</span><span>Image</span>'
                '<span class="s">](Icon.png)</span></pre>')
        out = wi._clean_content_html(html, "https://ex.com/")
        self.assertNotIn("<span", out)
        self.assertIn("![Image](Icon.png)", out.replace("</pre>", "").replace("<pre>", ""))

    def test_title_used_as_alt_fallback(self):
        out = wi._normalize_images('<img src="/a.png" title="A Cat">', "https://ex.com/")
        self.assertEqual(self._img(out).xpath("//img")[0].get("alt"), "A Cat")

    def test_alt_preferred_over_title(self):
        out = wi._normalize_images('<img src="/a.png" alt="Real" title="T">',
                                   "https://ex.com/")
        self.assertEqual(self._img(out).xpath("//img")[0].get("alt"), "Real")

    def test_labels_empty_alt_images_sequentially(self):
        md = "![](a.png) text ![keep](b.png) ![](c.png)"
        out = wi._label_unlabeled_images(md)
        self.assertIn("![image-1](a.png)", out)
        self.assertIn("![keep](b.png)", out)       # a real alt is left alone
        self.assertIn("![image-2](c.png)", out)

    def test_clean_content_still_normalizes_images(self):
        out = wi._clean_content_html('<p><img src="/x.png" width="1" height="1">'
                                     '<img src="/y.png" alt="Y"></p>',
                                     "https://ex.com/")
        imgs = self._img(out).xpath("//img")
        self.assertEqual(len(imgs), 1)                       # tracking pixel gone
        self.assertEqual(imgs[0].get("src"), "https://ex.com/y.png")


class TestLocalizeImages(unittest.TestCase):
    """Optional download into attachments/<note>/: rewrite to relative, dedup,
    keep the remote URL when a fetch fails."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def _fetch_ok(self, url, timeout=20):
        return (b"\x89PNG\r\n\x1a\nDATA", "image/png")

    def test_downloads_and_rewrites_relative(self):
        from pathlib import Path
        md = "text\n\n![Cat](https://ex.com/a/cat.png)\n"
        dest = Path(self._tmp) / "attachments" / "note"
        out = wi._localize_images(md, dest, "attachments/note", fetch=self._fetch_ok)
        self.assertIn("![Cat](attachments/note/cat.png)", out)
        self.assertTrue((dest / "cat.png").exists())

    def test_dedup_same_url_downloads_once(self):
        from pathlib import Path
        calls = []

        def fetch(url, timeout=20):
            calls.append(url)
            return (b"DATA", "image/png")

        md = "![a](https://ex.com/x.png) and ![b](https://ex.com/x.png)"
        dest = Path(self._tmp) / "att"
        out = wi._localize_images(md, dest, "att", fetch=fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out.count("att/x.png"), 2)

    def test_failed_fetch_keeps_remote_url(self):
        from pathlib import Path
        md = "![x](https://ex.com/gone.png)"
        out = wi._localize_images(md, Path(self._tmp) / "att", "att",
                                  fetch=lambda url, timeout=20: None)
        self.assertIn("https://ex.com/gone.png", out)

    def test_non_http_left_untouched(self):
        from pathlib import Path
        md = "![x](attachments/old/y.png) ![d](data:image/png;base64,AAA)"
        out = wi._localize_images(md, Path(self._tmp) / "att", "att", fetch=self._fetch_ok)
        self.assertEqual(out, md)

    def test_downloads_image_in_html_table(self):
        # Images inside a complex table kept as HTML use <img src="…">, not
        # markdown, and must be downloaded and rewritten too.
        from pathlib import Path
        md = ('text\n\n<table><tr><td>'
              '<img src="https://ex.com/moon.png" alt="Moon"></td></tr></table>')
        dest = Path(self._tmp) / "att"
        out = wi._localize_images(md, dest, "att", fetch=self._fetch_ok)
        self.assertIn('src="att/moon.png"', out)
        self.assertNotIn("https://ex.com/moon.png", out)
        self.assertTrue((dest / "moon.png").exists())

    def test_dedup_across_markdown_and_html(self):
        from pathlib import Path
        calls = []

        def fetch(url, timeout=20):
            calls.append(url)
            return (b"D", "image/png")

        # The HTML src is entity-encoded (&amp;) but is the same image as the
        # markdown link (&); it must fetch the unescaped URL once and dedup.
        md = ('![a](https://ex.com/x.png?p=1&q=2) '
              '<img src="https://ex.com/x.png?p=1&amp;q=2">')
        out = wi._localize_images(md, Path(self._tmp) / "att", "att", fetch=fetch)
        self.assertEqual(calls, ["https://ex.com/x.png?p=1&q=2"])   # once, unescaped
        self.assertIn("![a](att/x.png)", out)
        self.assertIn('src="att/x.png"', out)

    def test_image_count_is_bounded(self):
        # R74.2: a hostile page cannot make the importer download without limit.
        import unittest.mock as mock
        from pathlib import Path
        md = "".join(f"![{i}](https://ex.com/{i}.png)" for i in range(5))
        calls = []

        def fetch(url, timeout=20):
            calls.append(url)
            return (b"D", "image/png")

        with mock.patch.object(wi, "_MAX_IMAGES", 2):
            out = wi._localize_images(md, Path(self._tmp) / "att", "att", fetch=fetch,
                                      sleep=lambda *_: None)
        self.assertEqual(len(calls), 2)                 # only 2 fetched
        self.assertIn("https://ex.com/4.png", out)      # the rest keep remote URLs

    def test_total_bytes_is_bounded(self):
        import unittest.mock as mock
        from pathlib import Path
        md = "".join(f"![{i}](https://ex.com/{i}.png)" for i in range(5))
        with mock.patch.object(wi, "_MAX_IMAGE_TOTAL", 3):
            out = wi._localize_images(md, Path(self._tmp) / "att", "att",
                                      fetch=lambda url, timeout=20: (b"XXXX", "image/png"),
                                      sleep=lambda *_: None)
        # first over-budget image already exceeds the cap; the rest stay remote
        self.assertIn("https://ex.com/4.png", out)

    def test_deadline_stops_downloading(self):
        # R78.1: once the wall-clock deadline is passed, remaining images keep
        # their remote URL rather than letting a slow/throttling host run for hours.
        import unittest.mock as mock
        from pathlib import Path
        md = "".join(f"![{i}](https://ex.com/{i}.png)" for i in range(3))
        calls = []

        def fetch(url, timeout=20):
            calls.append(url)
            return (b"D", "image/png")

        # start=0; first check 0 (ok, fetch), then jump past the deadline
        times = iter([0.0, 0.0, 1e9, 1e9])
        with mock.patch.object(wi, "_IMAGE_DEADLINE_SECONDS", 90.0):
            out = wi._localize_images(md, Path(self._tmp) / "att", "att", fetch=fetch,
                                      sleep=lambda *_: None, clock=lambda: next(times))
        self.assertEqual(len(calls), 1)                 # only the first, then deadline
        self.assertIn("https://ex.com/2.png", out)      # rest keep remote URLs

    def test_throttle_sleeps_between_fetches(self):
        from pathlib import Path
        md = "".join(f"![{i}](https://ex.com/{i}.png)" for i in range(3))
        slept = []
        wi._localize_images(md, Path(self._tmp) / "att", "att",
                            fetch=lambda url, timeout=20: (b"D", "image/png"),
                            sleep=slept.append)
        # a gap before each fetch except the first → 2 for 3 images
        self.assertEqual(slept, [wi._THROTTLE_SECONDS, wi._THROTTLE_SECONDS])


class TestSingleImage(unittest.TestCase):
    """Right-click download of one image: fetch it, then rewrite just its refs."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def _ok(self, url, timeout=20):
        return (b"\x89PNG DATA", "image/png")

    def test_save_one_image_returns_relative_path(self):
        from pathlib import Path
        dest = Path(self._tmp) / "attachments" / "note"
        rel = wi.save_one_image("https://ex.com/a.png", dest, "attachments/note",
                                fetch=self._ok)
        self.assertEqual(rel, "attachments/note/a.png")
        self.assertTrue((dest / "a.png").exists())

    def test_save_one_image_none_on_failure(self):
        from pathlib import Path
        rel = wi.save_one_image("https://ex.com/a.png", Path(self._tmp) / "att", "att",
                                fetch=lambda url, timeout=20: None)
        self.assertIsNone(rel)

    def test_rewrite_touches_only_the_target_url(self):
        text = ('![a](https://ex.com/a.png) ![b](https://ex.com/b.png) '
                '<img src="https://ex.com/a.png">')
        out = wi.rewrite_image_url(text, "https://ex.com/a.png", "attachments/note/a.png")
        self.assertIn("![a](attachments/note/a.png)", out)
        self.assertIn('src="attachments/note/a.png"', out)
        self.assertIn("![b](https://ex.com/b.png)", out)      # untouched

    def test_rewrite_matches_html_entity_form(self):
        # WebKit hands us the resolved '&' URL; the source may have '&amp;'.
        text = '<img src="https://ex.com/a.png?x=1&amp;y=2">'
        out = wi.rewrite_image_url(text, "https://ex.com/a.png?x=1&y=2", "att/a.png")
        self.assertIn('src="att/a.png"', out)


class TestSaveToVault(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def _result(self, title="Some Long Page Title"):
        return wi.ImportResult(url="https://x.io/p", title=title, markdown="# Body")

    def test_default_name_from_title(self):
        p = wi.save_to_vault(self._result(), self._tmp)
        self.assertEqual(p.name, "some-long-page-title.md")

    def test_optional_name_overrides_title(self):
        p = wi.save_to_vault(self._result(), self._tmp, name="My Custom Name")
        self.assertEqual(p.name, "my-custom-name.md")

    def test_blank_name_falls_back_to_title(self):
        p = wi.save_to_vault(self._result(), self._tmp, name="   ")
        self.assertEqual(p.name, "some-long-page-title.md")

    def test_collision_gets_numeric_suffix(self):
        self._result()
        p1 = wi.save_to_vault(self._result(), self._tmp, name="dup")
        p2 = wi.save_to_vault(self._result(), self._tmp, name="dup")
        self.assertEqual(p1.name, "dup.md")
        self.assertEqual(p2.name, "dup-2.md")


class TestSsrfGuard(unittest.TestCase):
    """R74.2: image URLs come from the page, so the fetch must refuse non-public
    hosts (localhost/LAN/cloud-metadata)."""

    def test_blocks_private_and_metadata_addresses(self):
        for addr in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                     "::1", "0.0.0.0", "not-an-ip"):
            self.assertTrue(wi._addr_blocked(addr), addr)

    def test_allows_public_addresses(self):
        for addr in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertFalse(wi._addr_blocked(addr), addr)

    def _img_redirect(self, newurl):
        import email.message
        import urllib.request
        guard = wi._ImageRedirectGuard()
        req = urllib.request.Request("https://ex.com/a.png")
        return guard.redirect_request(req, None, 302, "Found",
                                      email.message.Message(), newurl)

    def test_image_redirect_to_private_host_refused(self):
        # R74.2 core: a redirect must not smuggle the fetch to a private host.
        import urllib.error
        for bad in ("http://127.0.0.1:8080/admin",
                    "http://169.254.169.254/latest/meta-data/",
                    "ftp://ex.com/x"):
            with self.assertRaises(urllib.error.HTTPError):
                self._img_redirect(bad)

    def test_image_redirect_to_public_host_allowed(self):
        self.assertIsNotNone(self._img_redirect("http://8.8.8.8/x.png"))


@unittest.skipUnless(_has_table_deps() and wi.availability() is None,
                     "web-import extraction deps not installed")
class TestExtractImagesInTables(unittest.TestCase):
    """R74.1: an <img> inside a table must go through image normalisation too."""

    def _page(self, table):
        return (f"<html><body><article><h1>T</h1><p>{'lorem ipsum ' * 20}</p>"
                f"{table}</article></body></html>")

    def test_table_image_becomes_absolute_pixel_dropped(self):
        html = self._page(
            '<table><tr><th>Flag</th></tr>'
            '<tr><td><img src="/img/de.png" alt="DE">'
            '<img src="beacon.gif" width="1" height="1"></td></tr></table>')
        md = wi.extract(html, "https://ex.com/page.html").markdown
        self.assertIn("https://ex.com/img/de.png", md)   # relative -> absolute
        self.assertNotIn("beacon.gif", md)               # tracking pixel gone

    def test_complex_table_keeps_normalised_image(self):
        html = self._page(
            '<table><tr><td colspan="2"><img src="/x.png" alt="X"></td></tr></table>')
        md = wi.extract(html, "https://ex.com/page.html").markdown
        self.assertIn("<table", md)                      # kept as HTML (colspan)
        self.assertIn("https://ex.com/x.png", md)        # image survived, absolute

    def test_blockquote_preserved(self):
        md = wi.extract(self._page(
            '<blockquote><p>quoted line one</p><p>and two</p></blockquote>'),
            "https://ex.com/").markdown
        self.assertIn("> quoted line one", md)
        self.assertIn("> and two", md)

    def test_video_becomes_link(self):
        md = wi.extract(self._page('<video src="/clip.mp4" controls></video>'),
                        "https://ex.com/").markdown
        self.assertIn("(https://ex.com/clip.mp4)", md)
        self.assertIn("Video", md)

    def test_video_source_becomes_link(self):
        md = wi.extract(self._page(
            '<video controls><source src="/movie.webm"></video>'),
            "https://ex.com/").markdown
        self.assertIn("https://ex.com/movie.webm", md)

    def test_audio_becomes_link(self):
        md = wi.extract(self._page('<audio src="/song.mp3" controls></audio>'),
                        "https://ex.com/").markdown
        self.assertIn("https://ex.com/song.mp3", md)

    def test_video_in_code_example_not_linkified(self):
        # A media element that is itself sample code (inside <pre>/<code>) must be
        # left as source, never rewritten to a [▶ Video] link.
        md = wi.extract(self._page(
            '<pre><code><video src="/x.mp4" controls></video></code></pre>'),
            "https://ex.com/").markdown
        self.assertNotIn("[▶ Video]", md)

    def test_blockquote_in_code_example_not_converted(self):
        md = wi.extract(self._page(
            '<pre><code><blockquote>sample</blockquote></code></pre>'),
            "https://ex.com/").markdown
        self.assertNotIn("> sample", md)

    def test_math_in_table_survives_extraction(self):
        # R88.1: math in a table goes through markdownify (which escapes _) and the
        # $$ downgrade, a different path than prose. End to end: inline $…$ (no $$),
        # underscores intact.
        html = self._page(
            '<table><tr><th>Eq</th></tr><tr><td>'
            '<math display="block"><annotation encoding="application/x-tex">'
            'a_{1}+W_{2}</annotation></math></td></tr></table>')
        md = wi.extract(html, "https://ex.com/").markdown
        self.assertIn("$a_{1}+W_{2}$", md)
        self.assertNotIn("\\_", md)
        self.assertNotIn("$$", md)

    def test_math_survives_extraction_unescaped(self):
        # R82.1: the TeX is injected as tree text and passes through Trafilatura's
        # markdown emitter — assert backslashes/underscores are NOT escaped end to
        # end, which is the whole point of the feature.
        html = self._page(
            '<p>a <math><annotation encoding="application/x-tex">\\frac{a}{b}</annotation>'
            '</math> b <math><annotation encoding="application/x-tex">x_1 + y_2</annotation>'
            '</math> c.</p>')
        md = wi.extract(html, "https://ex.com/").markdown
        self.assertIn(r"$\frac{a}{b}$", md)
        self.assertIn("$x_1 + y_2$", md)


class TestFetchImageRetry(unittest.TestCase):
    """A transient 429/503 is retried (honouring Retry-After) so image-heavy pages
    don't lose images to a momentary rate limit."""

    def _run(self, open_side_effect):
        import unittest.mock as mock
        opener = mock.Mock()
        opener.open.side_effect = open_side_effect
        slept = []
        with mock.patch("urllib.request.build_opener", return_value=opener):
            got = wi._fetch_image("http://8.8.8.8/x.png", sleep=slept.append)
        return got, slept

    def test_retries_on_429_then_succeeds(self):
        import email.message
        import urllib.error
        hdr = email.message.Message()
        hdr["Retry-After"] = "1"
        seq = [urllib.error.HTTPError("http://8.8.8.8/x.png", 429, "slow", hdr, None),
               _FakeResp(b"IMG", content_type="image/png")]

        def openf(req, timeout=None):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        got, slept = self._run(openf)
        self.assertEqual(got, (b"IMG", "image/png"))
        self.assertEqual(slept, [1.0])                  # honoured Retry-After

    def test_gives_up_after_max_retries(self):
        import urllib.error

        def openf(req, timeout=None):
            raise urllib.error.HTTPError("http://8.8.8.8/x.png", 429, "slow", None, None)

        got, slept = self._run(openf)
        self.assertIsNone(got)
        self.assertEqual(len(slept), wi._MAX_RETRIES)   # waited before each retry

    def test_non_retryable_status_fails_fast(self):
        import urllib.error

        def openf(req, timeout=None):
            raise urllib.error.HTTPError("http://8.8.8.8/x.png", 404, "nope", None, None)

        got, slept = self._run(openf)
        self.assertIsNone(got)
        self.assertEqual(slept, [])                     # 404 is not retried


if __name__ == "__main__":
    unittest.main()
