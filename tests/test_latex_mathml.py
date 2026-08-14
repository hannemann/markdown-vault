"""Tests for the LaTeX->MathML wrapper (latex2mathml) and the arithmatex glue.

The conversion itself is latex2mathml's job; these tests cover our thin wrapper
(display mode, empty input, graceful fallback) and the MathMLPostprocessor that
turns pymdownx.arithmatex's <script type="math/tex"> tags into <math> elements.
"""

import unittest
from unittest import mock

from markdown_vault.latex_mathml import latex_to_mathml, MathMLPostprocessor


class TestLatexToMathml(unittest.TestCase):
    def test_returns_math_element(self):
        self.assertIn("<math", latex_to_mathml("E = mc^2"))

    def test_inline_display_attribute(self):
        out = latex_to_mathml("x", inline=True)
        self.assertIn('display="inline"', out)
        self.assertNotIn('display="block"', out)

    def test_block_display_attribute(self):
        self.assertIn('display="block"', latex_to_mathml("x", inline=False))

    def test_library_is_wired(self):
        # Sanity that the library actually converts (structure, not exact markup).
        self.assertIn("<mfrac>", latex_to_mathml("\\frac{a}{b}"))
        self.assertIn("<msqrt>", latex_to_mathml("\\sqrt{2}"))

    def test_empty_input_is_valid_empty_math(self):
        out = latex_to_mathml("", inline=True)
        self.assertIn("<math", out)
        self.assertIn("</math>", out)

    def test_malformed_input_does_not_raise(self):
        # Whatever the library does with a broken formula, we return valid <math>.
        self.assertIn("<math", latex_to_mathml("\\frac{a}{", inline=True))

    def test_output_is_well_formed_xml(self):
        # The \text{} input contains raw <, >, & (now harvested from arbitrary web
        # pages' MathML annotations); our wrapper must escape them so the OUTPUT we
        # inject into the preview stays well-formed XML.
        from lxml import etree
        for tex in (r"\text{a < b & c}", r"\text{<b>x</b>}", r"\text{R&D; and AT&T;}",
                    r"\frac{a}{b}", "E = mc^2"):
            etree.fromstring(latex_to_mathml(tex, inline=True))   # raises if bad

    def test_text_special_chars_are_escaped(self):
        out = latex_to_mathml(r"\text{a < b & c}", inline=True)
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)

    def test_smuggled_tag_is_neutralised(self):
        out = latex_to_mathml(r"\text{<b>x</b>}", inline=True)
        self.assertNotIn("<b>", out)          # not kept as real markup
        self.assertIn("&lt;b&gt;", out)

    def test_numeric_entities_are_preserved(self):
        # latex2mathml uses &#x…; for spacing — those must not be double-escaped.
        out = latex_to_mathml(r"a \quad b", inline=True)
        self.assertNotIn("&amp;#x", out)

    def test_converter_error_falls_back_to_escaped_text(self):
        # A converter exception must degrade to the source as escaped text inside
        # valid MathML — one bad formula can't break the whole preview.
        with mock.patch("markdown_vault.latex_mathml.convert",
                        side_effect=ValueError("boom")):
            out = latex_to_mathml("x < y & z", inline=False)
        self.assertIn("<math", out)
        self.assertIn('display="block"', out)
        self.assertIn("<mtext>", out)
        self.assertIn("&lt;", out)          # escaped, not raw
        self.assertIn("&amp;", out)


class TestMathMLPostprocessor(unittest.TestCase):
    """The postprocessor replaces arithmatex <script> tags with <math>."""

    def setUp(self):
        self.pp = MathMLPostprocessor()

    def test_replaces_block_script(self):
        result = self.pp.run('<p><script type="math/tex">E = mc^2</script></p>')
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)

    def test_replaces_inline_script(self):
        result = self.pp.run(
            '<p><script type="math/tex; mode=inline">E = mc^2</script></p>')
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)

    def test_block_script_gets_display_block(self):
        self.assertIn('display="block"',
                      self.pp.run('<script type="math/tex">x^2</script>'))

    def test_inline_script_no_display_block(self):
        self.assertNotIn(
            'display="block"',
            self.pp.run('<script type="math/tex; mode=inline">x^2</script>'))

    def test_no_script_tags_unchanged(self):
        html = "<p>Hello world</p>"
        self.assertEqual(self.pp.run(html), html)

    def test_multiple_scripts(self):
        html = ('<script type="math/tex">a</script>'
                '<script type="math/tex; mode=inline">b</script>'
                '<script type="math/tex">c</script>')
        result = self.pp.run(html)
        self.assertNotIn("<script", result)
        self.assertEqual(result.count("<math"), 3)

    def test_script_with_fraction(self):
        self.assertIn("<mfrac>",
                      self.pp.run('<script type="math/tex">\\frac{a}{b}</script>'))

    def test_removes_arithmatex_block_wrapper(self):
        html = ('<div class="arithmatex">\n'
                '<div class="MathJax_Preview">\n\\frac{a}{b}\n</div>\n'
                '<script type="math/tex; mode=display">\n\\frac{a}{b}\n</script>\n'
                '</div>')
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("MathJax_Preview", result)
        self.assertNotIn("<script", result)
        self.assertNotIn("arithmatex", result)

    def test_removes_arithmatex_inline_wrapper(self):
        html = ('<p>Die Formel <span class="arithmatex">'
                '<script type="math/tex; mode=inline">E = mc^2</script>'
                '</span> ist berühmt.</p>')
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)
        self.assertIn("ist berühmt", result)


class TestMathIntegration(unittest.TestCase):
    """Math rendering through the full Markdown pipeline."""

    def _render(self, text):
        import markdown
        from markdown_vault.preview import MARKDOWN_EXTENSIONS, EXTENSION_CONFIGS
        html = markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS,
                                 extension_configs=EXTENSION_CONFIGS)
        return MathMLPostprocessor().run(html)

    def test_inline_math_in_markdown(self):
        self.assertIn("<math", self._render("Die Formel $E = mc^2$ ist berühmt."))

    def test_block_math_in_markdown(self):
        out = self._render("$$\n\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\n$$")
        self.assertIn("<math", out)
        self.assertIn("<mfrac>", out)


if __name__ == "__main__":
    unittest.main()
