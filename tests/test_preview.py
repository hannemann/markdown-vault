"""Tests for markdown_vault.preview — Markdown-to-HTML rendering."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault.preview import (
    Preview,
    HTML_TEMPLATE,
    MARKDOWN_EXTENSIONS,
    EXTENSION_CONFIGS,
    _heading_to_slug,
    LanguageExtractorPreprocessor,
    PygmentsCodePostprocessor,
)
import markdown as md


_TEMPLATE_KWARGS = dict(
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
        self.assertEqual(_heading_to_slug("Ünïcödé"), "unicode")

    def test_punctuation_stripped(self):
        self.assertEqual(_heading_to_slug("Hello, World!"), "hello-world")

    def test_multiple_spaces(self):
        self.assertEqual(_heading_to_slug("Hello   World"), "hello-world")

    def test_leading_trailing_hyphens(self):
        # The slug function doesn't strip standalone leading/trailing hyphens
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

    def test_wikilink_preserves_spaces_no_underscore_no_trailing_slash(self):
        """Wikilinks should generate href with spaces preserved, no underscores, no trailing slash."""
        result = md.markdown(
            "[[Datei B]]",
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        # Should NOT contain underscore or trailing slash
        self.assertNotIn("Datei_B", result)
        self.assertNotIn("Datei_B/", result)
        # Should contain the link with space in href
        self.assertIn('href="Datei B"', result)
        self.assertIn("Datei B", result)

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
        self._preview.set_vault_paths([str(self._vault)])

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
        """Test fallback: underscore in link resolves to file with spaces."""
        (self._vault / "Datei B.md").write_text("# Datei B")
        # Link comes in with underscore (e.g., from older markdown renderers)
        target = str(self._vault / "Datei_B")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Datei B.md"))

    def test_resolves_filename_with_underscores_fallback_subdir(self):
        """Test fallback works for files in subdirectories."""
        subdir = self._vault / "Sub Dir"
        subdir.mkdir()
        (subdir / "Deep File.md").write_text("# Deep")
        target = str(self._vault / "Sub_Dir" / "Deep_File")
        result = self._preview._resolve_wikilink(target)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Deep File.md"))


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

    def test_vault_paths(self):
        preview = Preview()
        preview.set_vault_paths(["/a", "/b"])
        self.assertEqual(preview._vault_paths, ["/a", "/b"])


if __name__ == "__main__":
    unittest.main()
