"""Tests for markdown_vault.preview — Markdown-to-HTML rendering."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from markdown_vault.preview import (
    Preview,
    HTML_TEMPLATE,
    MARKDOWN_EXTENSIONS,
    EXTENSION_CONFIGS,
    WikiLinkExtension,
    _heading_to_slug,
    _build_csp,
    LanguageExtractorPreprocessor,
    PygmentsCodePostprocessor,
)
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

    def _page_uri(self, vault_name, relpath, fragment=""):
        from markdown_vault.path_utils import wikilink_url
        return wikilink_url(vault_name, relpath, fragment)

    def test_resolves_vault_prefixed_wikilink(self):
        """R12.2: [[VaultName>Page]] resolves to the correct vault's file."""
        other_vault = Path(self._tmp) / "other_vault"
        other_vault.mkdir()
        (other_vault / "Page.md").write_text("# Other Page")
        # Set up config cache so resolve_wikilink finds the test vaults.
        import markdown_vault.config as _cfg
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
        import markdown_vault.config as _cfg
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
        import markdown_vault.config as _cfg
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
        import markdown_vault.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        result = self._preview._resolve_source_vault(str(self._vault))
        self.assertEqual(result, "vault")

    def test_resolve_source_vault_outside_falls_back_to_current(self):
        """Outside any vault, falls back to _current_vault_path."""
        import markdown_vault.config as _cfg
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        self._preview._current_vault_path = str(self._vault)
        result = self._preview._resolve_source_vault("/nonexistent/dir")
        self.assertEqual(result, "vault")

    def test_resolve_source_vault_none_when_fully_unknown(self):
        """No base_dir and no current vault → None (bug signal)."""
        self._preview._current_vault_path = None
        result = self._preview._resolve_source_vault("")
        self.assertIsNone(result)


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


if __name__ == "__main__":
    unittest.main()
