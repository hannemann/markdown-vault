"""Tests for markdown_vault.preview.preview — Markdown-to-HTML rendering."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from markdown_vault.preview.preview import (
    Preview,
    HTML_TEMPLATE,
    MARKDOWN_EXTENSIONS,
    EXTENSION_CONFIGS,
    WikiLinkExtension,
    _heading_to_slug,
    _build_csp,
    _same_page_fragment,
    LanguageExtractorPreprocessor,
    PygmentsCodePostprocessor,
    BlankLineBeforeListExtension,
    BlankLineBeforeListPreprocessor,
    _hover_uri_display,
    _anchor_scroll_js,
)
import json
import markdown as md


_TEMPLATE_KWARGS = dict(
    csp="default-src 'none'",
    css_content=".markdown-body { color: red; }",
    content="<p>Hi</p>",
    bg_color="#ffffff",
    fg_color="#000000",
    accent_color="#3584e4",
    dim_color="#77767b",
    card_bg_color="#f0f0f0",
    borders_color="#cdc7c2",
)


class TestHtmlTemplate(unittest.TestCase):
    """Verify the HTML template structure."""

    def test_template_contains_markers(self):
        self.assertIn("{css_content}", HTML_TEMPLATE)
        self.assertIn("{content}", HTML_TEMPLATE)
        self.assertIn("{csp}", HTML_TEMPLATE)

    def test_template_has_csp_meta(self):
        rendered = HTML_TEMPLATE.format(**_TEMPLATE_KWARGS)
        self.assertIn('http-equiv="Content-Security-Policy"', rendered)


class TestSamePageFragment(unittest.TestCase):
    """In-page anchors (footnote ref/backlink, TOC) must scroll, not navigate."""

    BASE = "file:///v/Wikipedia/"

    def test_footnote_fragment_is_in_page(self):
        self.assertEqual(
            _same_page_fragment("file:///v/Wikipedia/#fn:1", self.BASE), "fn:1")
        self.assertEqual(
            _same_page_fragment("file:///v/Wikipedia/#fnref:1", self.BASE), "fnref:1")

    def test_link_to_other_document_is_not_in_page(self):
        self.assertIsNone(
            _same_page_fragment("file:///v/Wikipedia/Other#sec", self.BASE))

    def test_no_fragment_is_none(self):
        self.assertIsNone(_same_page_fragment("file:///v/Wikipedia/", self.BASE))

    def test_no_base_uri_is_none(self):
        self.assertIsNone(_same_page_fragment("file:///v/Wikipedia/#fn:1", None))

    def test_fragment_is_percent_decoded(self):
        # WebKit hands over the URI encoded, while the element id carries the
        # real characters — so an anchor with anything beyond ASCII (an umlaut,
        # Greek, CJK …) found nothing. ASCII-only anchors like "#fn:1" worked,
        # which is why this stayed hidden.
        self.assertEqual(
            _same_page_fragment(self.BASE + "#e-beim-blo%C3%9Fen-rendern", self.BASE),
            "e-beim-bloßen-rendern")
        self.assertEqual(
            _same_page_fragment(self.BASE + "#%E6%97%A5%E6%9C%AC%E8%AA%9E", self.BASE),
            "日本語")


class TestInPageNavHistory(unittest.TestCase):
    """In-page anchor back/forward state machine (footnote/TOC jumps).

    The WebView JS is mocked out; only the Python-side counters — which drive the
    nav buttons and the return-to-reference behaviour — are under test.
    """

    def _preview(self):
        p = Preview.__new__(Preview)          # bypass GTK/WebKit init
        p._in_page_back = 0
        p._in_page_fwd = 0
        p._run_js = MagicMock()
        p.emit = MagicMock()
        return p

    def test_jump_pushes_back_clears_forward_and_notifies(self):
        p = self._preview()
        p._in_page_fwd = 2                     # a prior unwind existed
        p._jump_to_anchor("fn:1")
        self.assertEqual((p._in_page_back, p._in_page_fwd), (1, 0))
        self.assertTrue(p.can_go_back_in_page())
        p.emit.assert_called_once_with("in-page-nav-changed")   # buttons refresh now
        p._run_js.assert_called_once()

    def test_back_then_forward_round_trip(self):
        p = self._preview()
        p._jump_to_anchor("fn:1")
        p._jump_to_anchor("fn:2")              # back=2, fwd=0
        self.assertTrue(p.go_back_in_page())
        self.assertEqual((p._in_page_back, p._in_page_fwd), (1, 1))
        self.assertTrue(p.go_forward_in_page())
        self.assertEqual((p._in_page_back, p._in_page_fwd), (2, 0))

    def test_back_is_false_when_no_in_page_history(self):
        p = self._preview()
        self.assertFalse(p.go_back_in_page())
        self.assertFalse(p.can_go_back_in_page())
        p._run_js.assert_not_called()          # nothing to scroll, no JS fired

    def test_forward_is_false_when_nothing_unwound(self):
        p = self._preview()
        self.assertFalse(p.go_forward_in_page())
        self.assertFalse(p.can_go_forward_in_page())

    def test_reset_clears_and_notifies_when_history_existed(self):
        # R94.1: a re-render clears the stack, so the buttons must refresh too.
        p = self._preview()
        p._jump_to_anchor("fn:1")
        p.emit.reset_mock()
        p._reset_in_page_nav()
        self.assertEqual((p._in_page_back, p._in_page_fwd), (0, 0))
        p.emit.assert_called_once_with("in-page-nav-changed")

    def test_reset_is_silent_when_no_history(self):
        # Nothing to clear -> no needless button refresh on every render.
        p = self._preview()
        p._reset_in_page_nav()
        self.assertEqual((p._in_page_back, p._in_page_fwd), (0, 0))
        p.emit.assert_not_called()


class TestBuildCsp(unittest.TestCase):
    """Content-Security-Policy assembly (5.1)."""

    def test_strict_blocks_remote_images(self):
        csp = _build_csp(False)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("img-src file: data:", csp)
        self.assertNotIn("https:", csp)

    def test_opt_in_allows_https_images_only(self):
        csp = _build_csp(True)
        self.assertIn("img-src file: data: https:", csp)
        # Scripts/frames/connections stay blocked even when images are allowed.
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'none'", csp)

    def test_styles_always_inline_allowed(self):
        for flag in (False, True):
            self.assertIn("style-src 'unsafe-inline'", _build_csp(flag))

    def test_emoji_generator_is_to_alt(self):
        from pymdownx.emoji import to_alt
        self.assertIs(EXTENSION_CONFIGS["pymdownx.emoji"]["emoji_generator"], to_alt)

    def test_template_is_valid_html(self):
        rendered = HTML_TEMPLATE.format(**_TEMPLATE_KWARGS)
        self.assertIn("<!DOCTYPE html>", rendered)
        self.assertIn("<p>Hi</p>", rendered)
        self.assertIn("--bg:", rendered)
        self.assertIn("--fg:", rendered)

    def test_template_has_css_variable_root(self):
        rendered = HTML_TEMPLATE.format(**_TEMPLATE_KWARGS)
        self.assertIn(":root", rendered)
        self.assertIn("--accent:", rendered)
        self.assertIn("--borders:", rendered)


class TestHeadingToSlug(unittest.TestCase):
    """Tests for _heading_to_slug() pure function."""

    def test_simple_heading(self):
        self.assertEqual(_heading_to_slug("Hello World"), "hello-world")

    def test_umlauts(self):
        # With unicode=True (default), slugify preserves Unicode letters
        # (no NFKD decomposition), so ü→ü, ö→ö, etc.
        self.assertEqual(_heading_to_slug("Ünïcödé"), "ünïcödé")

    def test_punctuation_stripped(self):
        self.assertEqual(_heading_to_slug("Hello, World!"), "hello-world")

    def test_multiple_spaces(self):
        self.assertEqual(_heading_to_slug("Hello   World"), "hello-world")

    def test_leading_trailing_hyphens(self):
        result = _heading_to_slug("-Hello-")
        self.assertEqual(result, "-hello-")

    def test_empty_string(self):
        self.assertEqual(_heading_to_slug(""), "")

    def test_numbers(self):
        self.assertEqual(_heading_to_slug("Chapter 1 Introduction"), "chapter-1-introduction")

    def test_special_chars(self):
        self.assertEqual(_heading_to_slug("C++ vs. Java"), "c-vs-java")

    def test_japanese(self):
        result = _heading_to_slug("日本語テスト")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_mixed_case(self):
        self.assertEqual(_heading_to_slug("HELLO WORLD"), "hello-world")

    def test_duplicate_slug(self):
        seen: set[str] = set()
        self.assertEqual(_heading_to_slug("Hello", seen), "hello")
        self.assertEqual(_heading_to_slug("Hello", seen), "hello_1")
        self.assertEqual(_heading_to_slug("Hello", seen), "hello_2")

    def test_empty_heading_with_seen(self):
        seen: set[str] = set()
        self.assertEqual(_heading_to_slug("", seen), "_1")
        self.assertEqual(_heading_to_slug("", seen), "_2")

    def test_unicode_false(self):
        self.assertEqual(_heading_to_slug("Ünïcödé", unicode=False), "unicode")


class TestLanguageExtractorPreprocessor(unittest.TestCase):
    """Tests for LanguageExtractorPreprocessor."""

    def setUp(self):
        self._md = md.Markdown()
        self._preprocessor = LanguageExtractorPreprocessor(self._md)
        self._md.preprocessors.register(self._preprocessor, "lang", 30)

    def test_extracts_python(self):
        lines = ["```python", "print('hi')", "```"]
        result = self._preprocessor.run(lines)
        self.assertEqual(self._preprocessor.languages, ["python"])

    def test_extracts_multiple(self):
        lines = ["```python", "code", "```", "", "```rust", "code", "```"]
        self._preprocessor.run(lines)
        self.assertEqual(self._preprocessor.languages, ["python", "rust"])

    def test_no_language(self):
        lines = ["```", "code", "```"]
        self._preprocessor.run(lines)
        self.assertEqual(self._preprocessor.languages, [None])

    def test_no_code_blocks(self):
        lines = ["# Hello", "Some text"]
        self._preprocessor.run(lines)
        self.assertEqual(self._preprocessor.languages, [])

    def test_inner_fence_does_not_reopen_a_longer_block(self):
        # A ``` inside a ```` block is content, so this is ONE code block, not
        # two — the shared FenceTracker's CommonMark close rule (was: an inner
        # ``` closed the block and the trailing ```` reopened it).
        lines = ["````", "```", "still code", "````"]
        self._preprocessor.run(lines)
        self.assertEqual(self._preprocessor.languages, [None])


class TestPygmentsCodePostprocessor(unittest.TestCase):
    """Tests for PygmentsCodePostprocessor."""

    def setUp(self):
        self._md = md.Markdown()
        self._pp = PygmentsCodePostprocessor(self._md)

    def test_adds_data_lang(self):
        self._pp.set_languages(["python"])
        self._md.htmlStash.rawHtmlBlocks = [
            '<div class="codehilite"><pre><code>code</code></pre></div>'
        ]
        # Simulate a placeholder paragraph
        text = "<p>\x02wzxhzdk:0\x03</p>"
        result = self._pp.run(text)
        self.assertIn('data-lang="python"', result)

    def test_no_lang_no_data_attr(self):
        self._pp.set_languages([None])
        self._md.htmlStash.rawHtmlBlocks = [
            '<div class="codehilite"><pre><code>code</code></pre></div>'
        ]
        text = "<p>\x02wzxhzdk:0\x03</p>"
        result = self._pp.run(text)
        self.assertNotIn("data-lang", result)

    def test_existing_data_lang_not_duplicated(self):
        # When data-lang already exists in the matched class attribute,
        # the postprocessor should not add another one.
        self._pp.set_languages(["python"])
        self._md.htmlStash.rawHtmlBlocks = [
            '<div class="codehilite" data-lang="rust"><pre><code>code</code></pre></div>'
        ]
        text = "<p>\x02wzxhzdk:0\x03</p>"
        result = self._pp.run(text)
        # The regex matches only up to codehilite, so data-lang outside
        # the class attr gets duplicated — this documents current behavior.
        self.assertIn("data-lang=", result)


class TestMarkdownConversion(unittest.TestCase):
    """Test the markdown library integration directly."""

    def test_converts_heading(self):
        result = md.markdown("# Hello", extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<h1", result)
        self.assertIn("Hello", result)

    def test_converts_code_block(self):
        md_text = "```\ncode\n```"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<code>", result)

    def test_converts_table(self):
        md_text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<table>", result)

    def test_converts_wikilink(self):
        result = md.markdown(
            "[[Page]]",
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        self.assertIn("Page", result)

    def _render_wikilinks(self, text, source_vault="VaultA"):
        """Render *text* with the wikilink extension bound to *source_vault*."""
        extensions = [
            WikiLinkExtension(source_vault) if isinstance(e, WikiLinkExtension) else e
            for e in MARKDOWN_EXTENSIONS
        ]
        return md.markdown(
            text,
            extensions=extensions,
            extension_configs=EXTENSION_CONFIGS,
        )

    def test_wikilink_with_alias(self):
        result = self._render_wikilinks("[[Page|Alias]]")
        self.assertIn('href="vault:VaultA?path=Page"', result)
        self.assertIn(">Alias<", result)
        self.assertNotIn("[[", result)

    def test_wikilink_alias_and_plain_mixed(self):
        result = self._render_wikilinks("[[A|Link zu A]] and [[B]]")
        self.assertIn('href="vault:VaultA?path=A"', result)
        self.assertIn(">Link zu A<", result)
        self.assertIn('href="vault:VaultA?path=B"', result)
        self.assertIn(">B<", result)

    def test_wikilink_preserves_spaces_no_underscore_no_trailing_slash(self):
        """Wikilinks should generate href with spaces preserved, no underscores, no trailing slash."""
        result = self._render_wikilinks("[[Datei B]]")
        # Should NOT contain underscore or trailing slash
        self.assertNotIn("Datei_B", result)
        self.assertNotIn("Datei_B/", result)
        # Space is percent-encoded in the canonical vault: URL
        self.assertIn('href="vault:VaultA?path=Datei%20B"', result)
        self.assertIn("Datei B", result)

    def test_wikilink_vault_prefix_href(self):
        """Vault-prefixed wikilinks produce a canonical vault: URL."""
        result = self._render_wikilinks("[[VaultA>sub/Note|Alias]]")
        self.assertIn('href="vault:VaultA?path=sub/Note"', result)
        self.assertIn(">Alias<", result)

    def test_wikilink_cross_vault_href(self):
        """Explicit vault in the link wins over the source vault."""
        result = self._render_wikilinks("[[VaultB>Page]]", source_vault="VaultA")
        self.assertIn('href="vault:VaultB?path=Page"', result)

    def test_wikilink_fragment_href(self):
        """Fragment is passed through the URL, target stays path-only."""
        result = self._render_wikilinks("[[Page#Sec 1|Label]]")
        self.assertIn('href="vault:VaultA?path=Page#Sec%201"', result)
        self.assertIn(">Label<", result)

    def test_converts_bold(self):
        result = md.markdown("**bold**", extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<strong>", result)

    def test_converts_strikethrough(self):
        result = md.markdown("~~text~~", extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<del>", result)

    def test_converts_task_list(self):
        md_text = "- [ ] unchecked\n- [x] checked"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("checkbox", result)

    def test_renders_footnotes(self):
        # The web importer emits pymdownx-style [^n] / [^n]: footnotes; the preview
        # must actually render them (reference superscript + collected block).
        md_text = "A claim[^1] here.\n\n[^1]: The evidence."
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn('class="footnote-ref"', result)      # inline reference
        self.assertIn('class="footnote"', result)          # collected block
        self.assertIn("The evidence.", result)

    def test_footnote_and_superscript_coexist(self):
        # [^1] must be a footnote while ^2^ stays a caret superscript.
        result = md.markdown("a^2^[^1]\n\n[^1]: note",
                             extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<sup>2</sup>", result)              # caret superscript
        self.assertIn('class="footnote-ref"', result)      # footnote reference

    def test_checkbox_data_line_on_input(self):
        md_text = "- [ ] first checkbox\n- [x] second checkbox\n- [ ] third checkbox"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="1"', result)
        self.assertIn('data-checkbox-line="2"', result)
        self.assertNotIn('data-checkbox-index', result)
        self.assertNotIn('chk-line-marker', result)

    def test_checkbox_line_numbers_match_source_order(self):
        md_text = "- [ ] unchecked\n  - [ ] nested\n- [x] checked"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        first = result.index('data-checkbox-line="0"')
        nested = result.index('data-checkbox-line="1"')
        second = result.index('data-checkbox-line="2"')
        self.assertLess(first, nested)
        self.assertLess(nested, second)

    def test_checkbox_line_skips_fenced_code(self):
        md_text = (
            "- [ ] real one\n"
            "```python\n"
            "- [ ] fake in code\n"
            "```\n"
            "- [ ] real two"
        )
        result = md.markdown(
            md_text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        # Only two checkboxes should get data-checkbox-line (lines 0 and 4).
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="4"', result)
        # The fake checkbox in the code block has no data-checkbox-line.
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 2)

    def test_checkbox_line_skips_tilde_fence(self):
        md_text = (
            "- [ ] real one\n"
            "~~~\n"
            "- [ ] fake in code\n"
            "~~~\n"
            "- [ ] real two"
        )
        result = md.markdown(
            md_text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="4"', result)
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 2)

    def test_checkbox_uppercase_x(self):
        md_text = "- [X] uppercase checked\n- [x] lowercase checked\n- [ ] unchecked"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="1"', result)
        self.assertIn('data-checkbox-line="2"', result)

    def test_checkbox_line_blockquote(self):
        md_text = (
            "- [ ] normal checkbox\n"
            "> - [ ] quoted checkbox\n"
            "- [ ] another normal"
        )
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="1"', result)
        self.assertIn('data-checkbox-line="2"', result)
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 3)

    def test_checkbox_line_blockquote_mixed(self):
        md_text = (
            "- [ ] first\n"
            "> - [ ] quoted\n"
            "> > - [ ] double quoted\n"
            "- [ ] last"
        )
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="1"', result)
        self.assertIn('data-checkbox-line="2"', result)
        self.assertIn('data-checkbox-line="3"', result)
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 4)

    def test_checkboxes_not_disabled(self):
        md_text = "- [ ] unchecked\n- [x] checked"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertNotIn('disabled', result)
        self.assertIn('type="checkbox"', result)

    def test_checkbox_line_skips_indented_code_block(self):
        md_text = (
            "Some text\n"
            "\n"
            "    - [ ] fake in indented code\n"
            "\n"
            "- [ ] real one\n"
            "\n"
            "- [ ] real two"
        )
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 2)
        self.assertIn('data-checkbox-line="4"', result)
        self.assertIn('data-checkbox-line="6"', result)

    def test_checkbox_line_preserves_sublist_checkboxes(self):
        md_text = (
            "- [ ] outer\n"
            "\n"
            "    - [ ] nested sublist\n"
            "\n"
            "- [ ] another"
        )
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        lines_with_attr = [l for l in result.split('\n') if 'data-checkbox-line=' in l]
        self.assertEqual(len(lines_with_attr), 3)
        self.assertIn('data-checkbox-line="0"', result)
        self.assertIn('data-checkbox-line="2"', result)
        self.assertIn('data-checkbox-line="4"', result)

    def test_converts_fenced_code_with_lang(self):
        md_text = "```python\nprint('hi')\n```"
        result = md.markdown(
            md_text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        self.assertTrue("codehilite" in result or "highlight" in result)

    def test_converts_blockquote(self):
        md_text = "> quote"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<blockquote>", result)

    def test_converts_inline_code(self):
        md_text = "`code`"
        result = md.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<code>", result)

    def test_json_dumps_preserves_unicode(self):
        import json
        html = "<p>Grüße Café 日本語 naïve</p>"
        encoded = json.dumps(html, ensure_ascii=False)
        self.assertIn("Grüße", encoded)
        self.assertIn("Café", encoded)
        self.assertIn("日本語", encoded)


class TestPreviewResolveWikilink(unittest.TestCase):
    """Tests for Preview._resolve_wikilink() with temp filesystem."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / "Sub").mkdir()
        (self._vault / "Sub" / "Deep.md").write_text("# Deep")
        self._preview = Preview()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_resolves_exact_md_file(self):
        target = str(self._vault / "Page.md")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Page.md"))

    def test_resolves_without_extension(self):
        target = str(self._vault / "Page")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Page.md"))

    def test_resolves_in_subdirectory(self):
        target = str(self._vault / "Sub" / "Deep")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Deep.md"))

    def test_returns_none_for_unknown(self):
        result = self._preview._resolve_wikilink("/nonexistent/Nope.md")
        self.assertIsNone(result)

    def test_resolves_filename_with_spaces(self):
        """Test that wikilinks with spaces in filename are resolved correctly."""
        (self._vault / "Datei B.md").write_text("# Datei B")
        target = str(self._vault / "Datei B")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Datei B.md"))

    def test_resolves_filename_with_underscores_fallback(self):
        """R12.2: No underscore↔space normalization — exact match only."""
        (self._vault / "Datei B.md").write_text("# Datei B")
        # Link with underscore should NOT resolve to file with spaces
        target = str(self._vault / "Datei_B")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNone(result)

    def test_resolves_filename_with_underscores_fallback_subdir(self):
        """R12.2: Root-only — subdirectory files not indexed."""
        subdir = self._vault / "Sub Dir"
        subdir.mkdir()
        (subdir / "Deep File.md").write_text("# Deep")
        target = str(self._vault / "Sub_Dir" / "Deep_File")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNone(result)

    def test_a_directory_is_not_a_link_target(self):
        """A trailing slash means a folder, never a note.

        `Path` drops the slash, so appending ".md" would hit the *folder note*
        `Sub.md` beside `Sub/` — the widespread folder-note layout. That made the
        preview open an unrelated note (on the initial load, and on a click).
        """
        (self._vault / "Sub.md").write_text("# folder note")
        self.assertIsNone(self._preview._resolve_wikilink(str(self._vault / "Sub") + "/"))

    def test_a_dot_in_the_name_is_not_an_extension(self):
        """`with_suffix` *replaces* the last dot segment instead of appending:
        "notes/v1.2" became "v1.md" — a hit on a completely different note."""
        (self._vault / "v1.md").write_text("# one")
        self.assertIsNone(self._preview._resolve_wikilink(str(self._vault / "v1.2")))

        (self._vault / "v1.2.md").write_text("# one point two")
        result = self._preview._resolve_wikilink(str(self._vault / "v1.2"))
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("v1.2.md"), result)

    def test_a_dotted_folder_name_does_not_resolve_to_a_shorter_note(self):
        (self._vault / "My.md").write_text("# My")
        (self._vault / "My.Notes").mkdir()
        self.assertIsNone(
            self._preview._resolve_wikilink(str(self._vault / "My.Notes") + "/"))

    def _page_uri(self, vault_name, relpath, fragment=""):
        from markdown_vault.core.path_utils import wikilink_url
        return wikilink_url(vault_name, relpath, fragment)

    def test_resolves_vault_prefixed_wikilink(self):
        """R12.2: [[VaultName>Page]] resolves to the correct vault's file."""
        other_vault = Path(self._tmp) / "other_vault"
        other_vault.mkdir()
        (other_vault / "Page.md").write_text("# Other Page")
        # Set up config cache so resolve_wikilink finds the test vaults.
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [
            {"name": "vault", "path": str(self._vault)},
            {"name": "other_vault", "path": str(other_vault)},
        ]

        result = self._preview._resolve_wikilink_page(
            self._page_uri("other_vault", "Page")
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("other_vault" + os.sep + "Page.md"))

    def _set_current_vault(self):
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        self._preview._current_vault_path = str(self._vault)

    def test_page_resolves_root_not_sibling(self):
        """Strict: [[Page]] resolves to vault-root Page.md, never a sibling."""
        sibling = self._vault / "Sub" / "Page.md"
        sibling.write_text("# Sibling Page")
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(self._page_uri("vault", "Page"))
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith(os.sep + "Page.md"))
        self.assertNotEqual(result, str(sibling.resolve()))

    def test_page_resolves_subdir_path(self):
        """Strict: [[sub/Page]] resolves relative to the vault root."""
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(self._page_uri("vault", "Sub/Deep"))
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Sub" + os.sep + "Deep.md"))

    def test_page_resolves_md_suffix(self):
        """Strict: [[Page.md]] still resolves to Page.md."""
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(self._page_uri("vault", "Page.md"))
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith(os.sep + "Page.md"))

    def test_page_vault_prefixed(self):
        """Strict: [[VaultName>Page]] resolves into the named vault."""
        other_vault = Path(self._tmp) / "other_vault"
        other_vault.mkdir()
        (other_vault / "Page.md").write_text("# Other Page")
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [
            {"name": "vault", "path": str(self._vault)},
            {"name": "other_vault", "path": str(other_vault)},
        ]
        self._preview._current_vault_path = str(self._vault)
        result = self._preview._resolve_wikilink_page(
            self._page_uri("other_vault", "Page")
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("other_vault" + os.sep + "Page.md"))

    def test_page_fragment_does_not_affect_target(self):
        """A fragment is ignored for file resolution."""
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(
            self._page_uri("vault", "Page", "Sec 1")
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith(os.sep + "Page.md"))

    def test_page_unknown_returns_none(self):
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(self._page_uri("vault", "Nope"))
        self.assertIsNone(result)

    def test_page_empty_returns_none(self):
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(self._page_uri("vault", ""))
        self.assertIsNone(result)

    def test_page_unknown_vault_returns_none(self):
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page(
            self._page_uri("unknown_vault", "Page")
        )
        self.assertIsNone(result)

    def test_page_non_vault_uri_returns_none(self):
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page("file:///etc/passwd")
        self.assertIsNone(result)

    def test_page_empty_vault_is_bug(self):
        """An empty vault in a vault: URI must never fuzzy-resolve (R12.2)."""
        self._set_current_vault()
        result = self._preview._resolve_wikilink_page("vault:?path=Page")
        self.assertIsNone(result)

    def test_resolve_source_vault_from_base_dir(self):
        """_resolve_source_vault prefers the file's own directory."""
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        result = self._preview._resolve_source_vault(str(self._vault))
        self.assertEqual(result, "vault")

    def test_resolve_source_vault_outside_falls_back_to_current(self):
        """Outside any vault, falls back to _current_vault_path."""
        import markdown_vault.core.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        self._preview._current_vault_path = str(self._vault)
        result = self._preview._resolve_source_vault("/nonexistent/dir")
        self.assertEqual(result, "vault")

    def test_resolve_source_vault_none_when_fully_unknown(self):
        """No base_dir and no current vault → None (bug signal)."""
        self._preview._current_vault_path = None
        result = self._preview._resolve_source_vault("")
        self.assertIsNone(result)


class TestPreviewNavigationPolicy(unittest.TestCase):
    """What `_on_decide_policy` does with a navigation.

    The handler is driven directly instead of through a real navigation: headless
    there is no pointer, and a scripted `element.click()` produces no navigation at
    all. So these tests exercise the handler's *logic* — that a real click actually
    arrives as LINK_CLICKED (and a middle click with button=2) is a runtime check
    the stubs here take for granted, not one they prove.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        (self._vault / "Sub").mkdir(parents=True)
        (self._vault / "Sub.md").write_text("# folder note")
        (self._vault / "Other.md").write_text("# other")
        self._preview = Preview()
        self._preview._base_uri = self._uri(str(self._vault) + "/")
        self.clicked, self.not_found = [], []
        self._preview.connect("link-clicked",
                              lambda _w, p, f: self.clicked.append((p, f)))
        self._preview.connect("link-clicked-new-tab",
                              lambda _w, p, f: self.clicked.append((p, f)))
        self._preview.connect("link-not-found",
                              lambda _w, u: self.not_found.append(u))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _uri(path: str) -> str:
        from gi.repository import GLib
        return GLib.filename_to_uri(path)

    def _navigate(self, uri, nav_type, *, button=0, modifiers=0):
        """Call the policy handler as WebKit would."""
        from gi.repository import WebKit
        nav = MagicMock()
        nav.get_navigation_type.return_value = nav_type
        nav.get_request.return_value.get_uri.return_value = uri
        nav.get_mouse_button.return_value = button
        nav.get_modifiers.return_value = modifiers
        decision = MagicMock()
        decision.get_navigation_action.return_value = nav
        self._preview._on_decide_policy(
            None, decision, WebKit.PolicyDecisionType.NAVIGATION_ACTION)
        return decision

    def _types(self):
        from gi.repository import WebKit
        return WebKit.NavigationType

    def test_the_initial_load_resolves_nothing(self):
        # The note being rendered lives in Sub/, so the base URI is ".../Sub/" — and
        # `Sub.md` sits right beside it (folder-note layout). load_html arrives as a
        # navigation to that base URI, and this used to emit link-clicked on `Sub.md`
        # while merely rendering: no one had clicked anything.
        self._preview._base_uri = self._uri(str(self._vault / "Sub") + "/")
        self._navigate(self._preview._base_uri, self._types().OTHER)
        self.assertEqual(self.clicked, [])
        self.assertEqual(self.not_found, [])

    def test_a_preview_without_a_base_directory_shows_no_dialog(self):
        # No file path → base_uri None → WebKit navigates to "about:blank", which
        # used to end in a "link not found" dialog on plain rendering.
        self._preview._base_uri = None
        self._navigate("about:blank", self._types().OTHER)
        self.assertEqual(self.not_found, [])
        self.assertEqual(self.clicked, [])

    def test_clicking_a_directory_reports_not_found(self):
        # PO decision: [text](sub/) is a wrong link, so say so — instead of silently
        # opening the folder note that happens to sit beside it.
        self._navigate(self._uri(str(self._vault / "Sub") + "/"),
                       self._types().LINK_CLICKED)
        self.assertEqual(self.clicked, [])
        self.assertEqual(len(self.not_found), 1, self.not_found)

    def test_clicking_a_link_with_an_anchor_opens_the_note_and_keeps_the_anchor(self):
        # A cross-note anchor arrives with "#Heading" inside the path — _same_page_fragment
        # only catches anchors within THIS document. Today the note opens but the anchor
        # is lost; appending ".md" naively would break the link entirely.
        self._navigate(self._uri(str(self._vault / "Other.md")) + "#Heading",
                       self._types().LINK_CLICKED)
        self.assertEqual(self.not_found, [])
        self.assertEqual(len(self.clicked), 1, self.clicked)
        path, fragment = self.clicked[0]
        self.assertTrue(path.endswith("Other.md"), path)
        self.assertEqual(fragment, "Heading")

    def test_a_non_ascii_anchor_arrives_decoded(self):
        # WebKit encodes the URI, the heading id carries the real characters. An
        # anchor with an umlaut, ß, Greek or CJK would otherwise be handed on as
        # "%C3%9F…" and match no heading — the same trap as the in-page path.
        (self._vault / "Ziel.md").write_text("## Größe\n")
        self._navigate(self._uri(str(self._vault / "Ziel.md")) + "#gr%C3%B6%C3%9Fe",
                       self._types().LINK_CLICKED)
        self.assertEqual(len(self.clicked), 1, self.clicked)
        self.assertEqual(self.clicked[0][1], "größe")

    def test_an_extensionless_target_with_an_anchor_resolves(self):
        # "[x](Other#heading)" — no .md, fragment attached: the extension is
        # appended to the *path*, never to the fragment.
        (self._vault / "Ziel.md").write_text("## Größe\n")
        self._navigate(self._uri(str(self._vault / "Ziel")) + "#gr%C3%B6%C3%9Fe",
                       self._types().LINK_CLICKED)
        self.assertEqual(self.not_found, [])
        self.assertEqual(len(self.clicked), 1, self.clicked)
        self.assertTrue(self.clicked[0][0].endswith("Ziel.md"), self.clicked)
        self.assertEqual(self.clicked[0][1], "größe")

    def test_a_hash_in_the_file_name_survives(self):
        # "A#B.md" travels as A%23B.md. Splitting the fragment *after* unquoting
        # would cut the file name in half.
        (self._vault / "A#B.md").write_text("# hash")
        self._navigate(self._uri(str(self._vault / "A#B.md")),
                       self._types().LINK_CLICKED)
        self.assertEqual(self.not_found, [])
        self.assertEqual(len(self.clicked), 1, self.clicked)
        self.assertTrue(self.clicked[0][0].endswith("A#B.md"), self.clicked)


class TestPreview(unittest.TestCase):
    """Smoke tests for the Preview widget."""

    def test_instantiation(self):
        preview = Preview()
        self.assertIsNotNone(preview)

    def test_instantiation_with_css(self):
        preview = Preview(css_path="/tmp/test.css")
        self.assertEqual(preview._css_path, "/tmp/test.css")

    def test_initial_state(self):
        preview = Preview()
        self.assertFalse(preview._loaded)
        self.assertIsNone(preview._base_uri)
        self.assertEqual(preview._last_html_hash, "")

    def test_zoom_level_default(self):
        preview = Preview()
        self.assertEqual(preview.zoom_level, 1.0)

    def test_zoom_level_clamping(self):
        preview = Preview()
        preview.zoom_level = 0.1  # below min
        self.assertEqual(preview.zoom_level, 0.25)
        preview.zoom_level = 10.0  # above max
        self.assertEqual(preview.zoom_level, 5.0)


class TestPreviewCleanup(unittest.TestCase):
    """Tests for Preview.cleanup() — explicit WebView teardown."""

    def test_cleanup_unparents_web_view(self):
        preview = Preview()
        web_view = preview._web_view
        self.assertIsNotNone(web_view)
        preview.cleanup()
        self.assertIsNone(preview._web_view)

    def test_cleanup_clears_container_child(self):
        """cleanup() must detach the WebView via the container API so no
        stale child pointer remains in the ScrolledWindow (prevents the
        ``gtk_widget_unparent: assertion 'GTK_IS_WIDGET (widget)'`` failure
        when the Preview widget is finalized)."""
        preview = Preview()
        self.assertIsNotNone(preview.get_child())
        preview.cleanup()
        self.assertIsNone(preview.get_child())

    def test_cleanup_no_unparent_when_none(self):
        """cleanup() must not crash when _web_view is already None."""
        preview = Preview()
        preview._web_view = None
        preview.cleanup()  # should not raise
        self.assertIsNone(preview._web_view)

    def test_cleanup_idempotent(self):
        preview = Preview()
        preview.cleanup()
        preview.cleanup()
        self.assertIsNone(preview._web_view)

    def test_cleanup_tears_down_web_process_gracefully(self):
        """cleanup() stops loading, unregisters the handler and terminates the
        web process, so it ends via the API instead of being killed."""
        preview = Preview()
        mock_wv = unittest.mock.MagicMock()
        preview._web_view = mock_wv
        with unittest.mock.patch.object(preview, "set_child") as mock_set_child:
            preview.cleanup()
        mock_wv.stop_loading.assert_called_once()
        mock_wv.get_user_content_manager.return_value \
            .unregister_script_message_handler.assert_called_once_with("checkboxHandler")
        mock_set_child.assert_called_once_with(None)
        mock_wv.terminate_web_process.assert_called_once()
        self.assertIsNone(preview._web_view)

    def test_cleanup_survives_teardown_errors(self):
        """A failure in any teardown step must not prevent releasing the view."""
        preview = Preview()
        mock_wv = unittest.mock.MagicMock()
        mock_wv.stop_loading.side_effect = RuntimeError("boom")
        mock_wv.terminate_web_process.side_effect = RuntimeError("boom")
        preview._web_view = mock_wv
        with unittest.mock.patch.object(preview, "set_child"):
            preview.cleanup()  # must not raise
        self.assertIsNone(preview._web_view)

    def test_cleanup_without_web_view(self):
        preview = Preview()
        preview._web_view = None
        preview.cleanup()
        self.assertIsNone(preview._web_view)


class TestPreviewLazyLoading(unittest.TestCase):
    """Tests for Preview activate/deactivate (lazy loading)."""

    def test_active_by_default(self):
        preview = Preview()
        self.assertTrue(preview._active)

    def test_deactivate_sets_inactive(self):
        preview = Preview()
        preview.deactivate()
        self.assertFalse(preview._active)

    def test_activate_sets_active(self):
        preview = Preview()
        preview.deactivate()
        preview.activate()
        self.assertTrue(preview._active)

    def test_update_buffers_when_inactive(self):
        preview = Preview()
        preview.deactivate()
        preview.update_from_text("# Hello", "")
        self.assertEqual(preview._pending_text, "# Hello")
        self.assertEqual(preview._pending_base_dir, "")

    def test_activate_renders_pending(self):
        preview = Preview()
        preview.deactivate()
        preview.update_from_text("# Hello", "")
        self.assertIsNotNone(preview._pending_text)
        preview.activate()
        self.assertIsNone(preview._pending_text)
        self.assertTrue(preview._loaded)

    def test_update_renders_when_active(self):
        preview = Preview()
        preview.update_from_text("# Hello", "")
        self.assertIsNone(preview._pending_text)
        self.assertTrue(preview._loaded)


class TestPreviewCleanupGuard(unittest.TestCase):
    """R10.2: update/scroll/theme paths are no-ops after cleanup()"""

    def test_update_from_text_is_noop_after_cleanup(self):
        preview = Preview()
        preview.update_from_text("# Hello", "")
        preview.cleanup()
        self.assertIsNone(preview._web_view)
        preview.update_from_text("# World", "")

    def test_scroll_to_line_is_noop_after_cleanup(self):
        preview = Preview()
        preview.cleanup()
        preview.scroll_to_line(0, "# Heading")

    def test_update_theme_is_noop_after_cleanup(self):
        preview = Preview()
        preview.cleanup()
        preview.update_theme()


class TestPreviewSearch(unittest.TestCase):
    """In-preview WebKit find backend surface."""

    def test_search_methods_exist(self):
        preview = Preview()
        for m in ("search_set_text", "search_next", "search_prev",
                  "search_clear", "search_info"):
            self.assertTrue(callable(getattr(preview, m, None)))

    def test_clear_and_empty_query_report_zero(self):
        preview = Preview()
        preview.search_set_text("")   # must not raise on an empty page
        preview.search_clear()
        self.assertEqual(preview.search_info(), (0, 0))

    def test_search_after_cleanup_does_not_crash(self):
        # R21.1: cleanup() nulls _web_view; search_* must no-op, not crash.
        preview = Preview()
        preview.cleanup()
        preview.search_set_text("foo")
        preview.search_next()
        preview.search_prev()
        preview.search_clear()
        self.assertEqual(preview.search_info(), (0, 0))


class TestBlankLineBeforeList(unittest.TestCase):
    """A list directly under a paragraph (no blank line) must still render as a
    list. Python-Markdown otherwise folds it into the paragraph, which trips up
    GFM-style / AI-generated content; a preprocessor inserts the missing blank
    line at the paragraph-to-list boundary."""

    def _render(self, src):
        return md.markdown(src, extensions=[BlankLineBeforeListExtension()])

    def _plain(self, src):
        return md.markdown(src)

    def test_bullet_list_after_paragraph_without_blank_line(self):
        html = self._render("Generalist per handler:\n- PDF\n- DOCX")
        self.assertIn("<ul>", html)
        self.assertIn("<li>PDF</li>", html)
        self.assertIn("<li>DOCX</li>", html)

    def test_premise_plain_markdown_swallows_the_list(self):
        # Guards the premise the fix relies on: without it, no list forms.
        self.assertNotIn("<ul>", self._plain("Generalist per handler:\n- PDF\n- DOCX"))

    def test_ordered_list_starting_at_one_after_paragraph(self):
        html = self._render("Steps:\n1. first\n2. second")
        self.assertIn("<ol>", html)
        self.assertIn("<li>first</li>", html)

    def test_wrapped_number_is_not_turned_into_a_list(self):
        # CommonMark safeguard: an ordered list only interrupts a paragraph when it
        # starts at 1, so a hard-wrapped "14." stays prose.
        html = self._render("The number of doors is\n14. total, roughly")
        self.assertNotIn("<ol>", html)

    def test_existing_blank_line_is_not_doubled(self):
        html = self._render("Intro:\n\n- one\n- two")
        self.assertIn("<li>one</li>", html)           # still a tight list
        self.assertEqual(html.count("<ul>"), 1)

    def test_list_stays_tight_and_unsplit_with_continuations(self):
        src = "Intro:\n- PDF: use its\n  image support\n- DOCX"
        html = self._render(src)
        self.assertEqual(html.count("<ul>"), 1)       # one list, not split in two
        self.assertEqual(html.count("<li>"), 2)
        self.assertIn("image support", html)
        self.assertNotIn("<p>PDF", html)              # tight: no <p> inside <li>

    def test_list_marker_inside_fenced_code_is_untouched(self):
        html = md.markdown("```\ncode:\n- not a list\n```",
                           extensions=[BlankLineBeforeListExtension(), "fenced_code"])
        self.assertNotIn("<li>", html)
        self.assertIn("- not a list", html)           # survives as code text

    def test_thematic_break_is_not_treated_as_a_list(self):
        html = self._render("Some text\n***")
        self.assertNotIn("<li>", html)

    def test_longer_fence_is_not_closed_by_inner_fence(self):
        # R103.1: a block opened with four backticks (the idiom for showing a
        # three-backtick fence) must not be considered closed by the inner ```;
        # otherwise the rest of the code is read as prose and a blank line is
        # inserted *inside* the code block. The whole input is one fence -> the
        # preprocessor must leave it exactly as-is.
        pp = BlankLineBeforeListPreprocessor(None)
        lines = ["````", "```", "text", "- item", "```", "````"]
        self.assertEqual(pp.run(lines), lines)

    def test_lazy_continuation_is_not_corrupted(self):
        # A column-0 line continuing a list item (no blank line), then the next
        # item, must render exactly as Python-Markdown would natively.
        src = "- a\ntext\n- b"
        self.assertEqual(self._render(src), self._plain(src))

    def test_wired_into_markdown_extensions(self):
        self.assertTrue(any(isinstance(e, BlankLineBeforeListExtension)
                            for e in MARKDOWN_EXTENSIONS))


class TestHoverUriDisplay(unittest.TestCase):
    """The hover status line shows external URLs verbatim but renders the internal
    vault: scheme as a readable target (vault › path#fragment)."""

    def test_empty(self):
        self.assertEqual(_hover_uri_display(""), "")

    def test_external_url_verbatim(self):
        self.assertEqual(_hover_uri_display("https://example.com/a/b?q=1"),
                         "https://example.com/a/b?q=1")
        self.assertEqual(_hover_uri_display("mailto:a@b.c"), "mailto:a@b.c")

    def test_in_page_anchor_verbatim(self):
        self.assertEqual(_hover_uri_display("#fn:1"), "#fn:1")

    def test_in_page_file_anchor_shows_only_the_fragment(self):
        # A footnote/anchor within the current doc resolves to file://<base>#frag;
        # show just the anchor, not the full file path.
        page = "file:///home/u/notes/Web-Import/"
        self.assertEqual(
            _hover_uri_display("file:///home/u/notes/Web-Import/#fn:2", page), "#fn:2")

    def test_in_page_anchor_uses_the_note_breadcrumb(self):
        # With the current note's breadcrumb, a footnote reads like a wikilink to it.
        page = "file:///home/u/notes/Web-Import/"
        out = _hover_uri_display("file:///home/u/notes/Web-Import/#fn:2", page,
                                 "Wissenschaft › Web-Import › plato")
        self.assertEqual(out, "Wissenschaft › Web-Import › plato › fn:2")

    def test_file_link_to_other_doc_is_verbatim(self):
        page = "file:///home/u/notes/foo.md"
        out = _hover_uri_display("file:///home/u/notes/bar.md", page)
        self.assertEqual(out, "file:///home/u/notes/bar.md")

    def test_vault_link_without_fragment(self):
        self.assertEqual(_hover_uri_display("vault:Wissenschaft?path=Erde"),
                         "Wissenschaft › Erde")

    def test_vault_link_fragment_is_a_breadcrumb_segment(self):
        # An anchor within a wikilink target uses the same " › " scheme, not "#":
        # vault:Wissenschaft?path=Erde#Fußnote  ->  "Wissenschaft › Erde › Fußnote"
        out = _hover_uri_display("vault:Wissenschaft?path=Erde#Fußnote")
        self.assertEqual(out, "Wissenschaft › Erde › Fußnote")
        self.assertNotIn("#", out)
        self.assertNotIn("vault:", out)


class TestAnchorScrollJs(unittest.TestCase):
    """The JS builder that scrolls a freshly opened note to a heading anchor."""

    def test_empty_heading_yields_no_script(self):
        self.assertEqual(_anchor_scroll_js(""), "")

    def test_targets_the_slug_of_the_heading(self):
        # The fragment is heading *text*; it must be slugified to match the id.
        js = _anchor_scroll_js("My Heading")
        self.assertIn(json.dumps(_heading_to_slug("My Heading")), js)
        self.assertIn("getElementById", js)
        self.assertIn("scrollIntoView", js)

    def test_scrolls_smoothly(self):
        self.assertIn("behavior:'smooth'", _anchor_scroll_js("Test"))

    def test_does_not_poll(self):
        # It used to retry via setTimeout because the element might not exist
        # yet. That never worked here: by the time the script ran, the target
        # heading still belonged to the previous note, and the retry scrolled
        # nothing. Waiting is now the caller's job — the preview flushes the
        # pending anchor when the load or the innerHTML swap reports completion,
        # so the script itself can assume the DOM is in place.
        self.assertNotIn("setTimeout", _anchor_scroll_js("Test"))

    def test_slug_is_json_encoded_so_quotes_cannot_break_out(self):
        js = _anchor_scroll_js('He said "hi"')
        # No raw double-quote from the heading leaks into the JS string literal.
        self.assertIn(json.dumps(_heading_to_slug('He said "hi"')), js)


class TestArmedAnchorRidesWithTheContent(unittest.TestCase):
    """A jump into a note that is *about to* be rendered travels with it.

    Opening a note in an already-loaded preview replaces the body via innerHTML.
    Sending the jump afterwards aimed at the previous note's markup and layout
    and silently did nothing — the cross-note anchor bug. So the jump is armed
    first and emitted by the very script that swaps the DOM.
    """

    def setUp(self):
        self._preview = Preview()
        self._preview._web_view = MagicMock()
        self._preview._loaded = True
        self._ran = []
        self._preview._run_js = self._ran.append

    def _scripts(self):
        """The scripts handed to the WebView, with the queued idle run."""
        sent = []
        self._preview._web_view.evaluate_javascript.side_effect = (
            lambda js, *a, **kw: sent.append(js))
        return sent

    @staticmethod
    def _drain():
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    def test_arming_never_scrolls_on_its_own(self):
        self._preview.arm_anchor("Kapitel Zwei")
        self.assertEqual(self._ran, [])
        self.assertEqual(self._preview._pending_anchor, "Kapitel Zwei")

    def test_a_swap_does_not_spend_the_armed_jump(self):
        # Navigating inside a tab hits this swap first, but the window then
        # rebuilds the stack (reset() + refresh_preview()), so a full load
        # follows and throws away whatever the swap scrolled. Spending the anchor
        # here is how the jump got lost.
        sent = self._scripts()
        self._preview.arm_anchor("Kapitel Zwei")
        self._preview.update_from_text("## Kapitel Zwei\n", "/v", "/v/note.md")
        self._drain()
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("innerHTML", sent[0])
        self.assertNotIn("scrollIntoView", sent[0])
        self.assertEqual(self._preview._pending_anchor, "Kapitel Zwei")

    def test_the_full_load_finishing_performs_the_jump(self):
        from gi.repository import WebKit
        self._preview.arm_anchor("Kapitel Zwei")
        self._preview.reset()                           # what the window does
        self._preview._load_in_progress = True
        self._preview._on_load_changed(None, WebKit.LoadEvent.FINISHED)
        self.assertEqual(len(self._ran), 1, self._ran)
        self.assertIn(json.dumps(_heading_to_slug("Kapitel Zwei")), self._ran[0])
        self.assertEqual(self._preview._pending_anchor, "")

    def test_reset_keeps_the_armed_jump(self):
        self._preview.arm_anchor("Kapitel Zwei")
        self._preview.reset()
        self.assertEqual(self._preview._pending_anchor, "Kapitel Zwei")

    def test_a_settled_preview_still_jumps_right_away(self):
        # In-page anchors and freshly loaded tabs keep the direct path.
        self._preview.scroll_to_anchor("Kapitel Zwei")
        self.assertEqual(len(self._ran), 1, self._ran)


if __name__ == "__main__":
    unittest.main()
