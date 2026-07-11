"""Tests for markdown_vault.preview — Markdown-to-HTML rendering."""

import unittest

from src.preview import Preview, HTML_TEMPLATE, MARKDOWN_EXTENSIONS


class TestHtmlTemplate(unittest.TestCase):
    """Verify the HTML template structure."""

    def test_template_contains_markers(self):
        self.assertIn("{css_path}", HTML_TEMPLATE)
        self.assertIn("{content}", HTML_TEMPLATE)

    def test_template_is_valid_html(self):
        rendered = HTML_TEMPLATE.format(css_path="/style.css", content="<p>Hi</p>")
        self.assertIn("<!DOCTYPE html>", rendered)
        self.assertIn("<p>Hi</p>", rendered)


class TestMarkdownConversion(unittest.TestCase):
    """Test the markdown library integration directly."""

    def test_converts_heading(self):
        import markdown
        result = markdown.markdown("# Hello", extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<h1", result)
        self.assertIn("Hello", result)

    def test_converts_code_block(self):
        import markdown
        md_text = "```\ncode\n```"
        result = markdown.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<code>", result)

    def test_converts_table(self):
        import markdown
        md_text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = markdown.markdown(md_text, extensions=MARKDOWN_EXTENSIONS)
        self.assertIn("<table>", result)

    def test_converts_wikilink(self):
        import markdown
        result = markdown.markdown(
            "[[Page]]",
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs={"markdown.extensions.wikilinks": {"base_url": ""}},
        )
        self.assertIn("Page", result)


class TestPreview(unittest.TestCase):
    """Smoke tests for the Preview widget (no display required)."""

    def test_instantiation(self):
        """Verify that Preview can be constructed."""
        preview = Preview()
        self.assertIsNotNone(preview)

    def test_instantiation_with_css(self):
        preview = Preview(css_path="/tmp/test.css")
        self.assertEqual(preview._css_path, "/tmp/test.css")


if __name__ == "__main__":
    unittest.main()
