"""Tests for latex_mathml — LaTeX-to-MathML converter."""

import unittest
from unittest.mock import patch

from src.latex_mathml import (
    tokenize,
    Token,
    TokenType,
    parse,
    ASTNode,
    NodeType,
    latex_to_mathml,
    MathMLPostprocessor,
)


# ---------------------------------------------------------------------------
# Tokenizer tests
# ---------------------------------------------------------------------------

class TestTokenizer(unittest.TestCase):
    """Verify the LaTeX tokenizer produces correct token streams."""

    def test_simple_variables(self):
        tokens = tokenize("E = mc^2")
        types = [t.type for t in tokens]
        self.assertEqual(types, [
            TokenType.VARIABLE,
            TokenType.SPACE,
            TokenType.OPERATOR,
            TokenType.SPACE,
            TokenType.VARIABLE,
            TokenType.VARIABLE,
            TokenType.SUPERSCRIPT,
            TokenType.NUMBER,
        ])

    def test_group_braces(self):
        tokens = tokenize("\\frac{a}{b}")
        types = [t.type for t in tokens]
        self.assertIn(TokenType.COMMAND, types)
        self.assertIn(TokenType.GROUP_OPEN, types)

    def test_subscript(self):
        tokens = tokenize("x_{1}")
        types = [t.type for t in tokens]
        self.assertIn(TokenType.SUBSCRIPT, types)
        self.assertIn(TokenType.GROUP_OPEN, types)

    def test_command_recognition(self):
        tokens = tokenize("\\sum \\frac \\sqrt \\pi")
        commands = [t.value for t in tokens if t.type == TokenType.COMMAND]
        symbols = [t.value for t in tokens if t.type == TokenType.SYMBOL]
        self.assertEqual(commands, ["sum", "frac", "sqrt"])
        self.assertEqual(symbols, ["\\pi"])

    def test_empty_input(self):
        tokens = tokenize("")
        self.assertEqual(tokens, [])

    def test_special_operators(self):
        tokens = tokenize("\\cdot \\times \\leq \\geq")
        values = [(t.type, t.value) for t in tokens]
        self.assertIn((TokenType.SYMBOL, "\\cdot"), values)
        self.assertIn((TokenType.SYMBOL, "\\times"), values)
        self.assertIn((TokenType.SYMBOL, "\\leq"), values)
        self.assertIn((TokenType.SYMBOL, "\\geq"), values)

    def test_backslash_infinity(self):
        tokens = tokenize("\\infty")
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].type, TokenType.SYMBOL)
        self.assertEqual(tokens[0].value, "\\infty")

    def test_alignment_ampersand(self):
        tokens = tokenize("a &= b")
        types = [t.type for t in tokens]
        self.assertIn(TokenType.ALIGN, types)

    def test_line_break(self):
        tokens = tokenize("a \\\\ b")
        types = [t.type for t in tokens]
        self.assertIn(TokenType.LINE_BREAK, types)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser(unittest.TestCase):
    """Verify the parser builds correct ASTs from token streams."""

    def test_simple_expression(self):
        node = parse("E = mc^2")
        self.assertEqual(node.type, NodeType.ROOT)
        self.assertTrue(len(node.children) > 0)

    def test_fraction(self):
        node = parse("\\frac{a}{b}")
        # Should find a FRAC node somewhere
        frac_nodes = _find_nodes(node, NodeType.FRAC)
        self.assertEqual(len(frac_nodes), 1)
        frac = frac_nodes[0]
        self.assertIsNotNone(frac.children[0])  # numerator
        self.assertIsNotNone(frac.children[1])  # denominator

    def test_sum_with_limits(self):
        node = parse("\\sum_{i=1}^{n}")
        sum_nodes = _find_nodes(node, NodeType.SUM)
        self.assertEqual(len(sum_nodes), 1)

    def test_integral(self):
        node = parse("\\int_{-\\infty}^{\\infty}")
        int_nodes = _find_nodes(node, NodeType.INTEGRAL)
        self.assertEqual(len(int_nodes), 1)

    def test_sqrt(self):
        node = parse("\\sqrt{\\pi}")
        sqrt_nodes = _find_nodes(node, NodeType.SQRT)
        self.assertEqual(len(sqrt_nodes), 1)

    def test_sqrt_with_optional(self):
        node = parse("\\sqrt[n]{x}")
        sqrt_n_nodes = _find_nodes(node, NodeType.SQRT_N)
        self.assertEqual(len(sqrt_n_nodes), 1)

    def test_bold(self):
        node = parse("\\mathbf{E}")
        bold_nodes = _find_nodes(node, NodeType.BOLD)
        self.assertEqual(len(bold_nodes), 1)

    def test_aligned_environment(self):
        latex = "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}"
        node = parse(latex)
        aligned_nodes = _find_nodes(node, NodeType.ALIGNED)
        self.assertEqual(len(aligned_nodes), 1)

    def test_greek_letter_pi(self):
        node = parse("\\pi")
        symbol_nodes = _find_nodes(node, NodeType.SYMBOL)
        self.assertTrue(any(n.value == "\\pi" for n in symbol_nodes))

    def test_partial_derivative(self):
        node = parse("\\partial")
        symbol_nodes = _find_nodes(node, NodeType.SYMBOL)
        self.assertTrue(any(n.value == "\\partial" for n in symbol_nodes))


# ---------------------------------------------------------------------------
# MathML rendering tests
# ---------------------------------------------------------------------------

class TestMathMLRendering(unittest.TestCase):
    """Verify LaTeX→MathML conversion produces valid MathML."""

    def test_simple_inline(self):
        result = latex_to_mathml("E = mc^2", inline=True)
        self.assertIn("<math", result)
        self.assertIn(">", result)
        self.assertIn("<mi>E</mi>", result)
        self.assertIn("<mi>m</mi>", result)
        self.assertIn("<mi>c</mi>", result)
        self.assertNotIn('display="block"', result)

    def test_simple_block(self):
        result = latex_to_mathml("E = mc^2", inline=False)
        self.assertIn('display="block"', result)

    def test_fraction(self):
        result = latex_to_mathml("\\frac{a}{b}", inline=True)
        self.assertIn("<mfrac>", result)
        self.assertIn("<mi>a</mi>", result)
        self.assertIn("<mi>b</mi>", result)

    def test_sum(self):
        result = latex_to_mathml("\\sum_{i=1}^{n}", inline=True)
        self.assertIn("<msubsup>", result)

    def test_integral(self):
        result = latex_to_mathml("\\int_{-\\infty}^{\\infty}", inline=True)
        self.assertIn("<msubsup>", result)
        self.assertIn("\u222B", result)  # ∫ character

    def test_sqrt(self):
        result = latex_to_mathml("\\sqrt{\\pi}", inline=True)
        self.assertIn("<msqrt>", result)

    def test_sqrt_nth_root(self):
        result = latex_to_mathml("\\sqrt[n]{x}", inline=True)
        self.assertIn("<mroot>", result)
        self.assertNotIn("]", result)

    def test_bold(self):
        result = latex_to_mathml("\\mathbf{E}", inline=True)
        self.assertIn('mathvariant="bold"', result)

    def test_pi_symbol(self):
        result = latex_to_mathml("\\pi", inline=True)
        self.assertIn("\u03C0", result)  # π character

    def test_infinity_symbol(self):
        result = latex_to_mathml("\\infty", inline=True)
        self.assertIn("\u221E", result)  # ∞ character

    def test_aligned(self):
        latex = "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}"
        result = latex_to_mathml(latex, inline=False)
        self.assertIn("<mtable", result)
        self.assertIn("<mtr>", result)
        self.assertIn("<mtd>", result)

    def test_partial(self):
        result = latex_to_mathml("\\partial", inline=True)
        self.assertIn("\u2202", result)  # ∂ character

    def test_empty_input(self):
        result = latex_to_mathml("", inline=True)
        self.assertIn("<math", result)

    def test_display_block_attribute(self):
        result = latex_to_mathml("x", inline=False)
        self.assertIn('display="block"', result)
        self.assertIn('displaystyle="true"', result)

    def test_display_inline_no_attribute(self):
        result = latex_to_mathml("x", inline=True)
        self.assertNotIn('display="block"', result)

    def test_nested_frac_in_sqrt(self):
        result = latex_to_mathml("\\sqrt{\\frac{a}{b}}", inline=True)
        self.assertIn("<msqrt>", result)
        self.assertIn("<mfrac>", result)


# ---------------------------------------------------------------------------
# Postprocessor tests
# ---------------------------------------------------------------------------

class TestMathMLPostprocessor(unittest.TestCase):
    """Verify the HTML postprocessor replaces <script> tags with <math>."""

    def setUp(self):
        self.pp = MathMLPostprocessor()

    def test_replaces_block_script(self):
        html = '<p><script type="math/tex">E = mc^2</script></p>'
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)

    def test_replaces_inline_script(self):
        html = '<p><script type="math/tex; mode=inline">E = mc^2</script></p>'
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)

    def test_block_script_gets_display_block(self):
        html = '<script type="math/tex">x^2</script>'
        result = self.pp.run(html)
        self.assertIn('display="block"', result)

    def test_inline_script_no_display_block(self):
        html = '<script type="math/tex; mode=inline">x^2</script>'
        result = self.pp.run(html)
        self.assertNotIn('display="block"', result)

    def test_no_script_tags_unchanged(self):
        html = "<p>Hello world</p>"
        result = self.pp.run(html)
        self.assertEqual(result, html)

    def test_multiple_scripts(self):
        html = (
            '<script type="math/tex">a</script>'
            '<script type="math/tex; mode=inline">b</script>'
            '<script type="math/tex">c</script>'
        )
        result = self.pp.run(html)
        self.assertNotIn("<script", result)
        self.assertEqual(result.count("<math"), 3)

    def test_script_with_fraction(self):
        html = '<script type="math/tex">\\frac{a}{b}</script>'
        result = self.pp.run(html)
        self.assertIn("<mfrac>", result)

    def test_removes_arithmatex_wrapper(self):
        html = (
            '<div class="arithmatex">\n'
            '<div class="MathJax_Preview">\n'
            '\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\n'
            '</div>\n'
            '<script type="math/tex; mode=display">\n'
            '\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\n'
            '</script>\n'
            '</div>'
        )
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("MathJax_Preview", result)
        self.assertNotIn("<script", result)
        self.assertNotIn("arithmatex", result)

    def test_removes_arithmatex_wrapper_inline(self):
        html = (
            '<p>Die Formel <span class="arithmatex">'
            '<script type="math/tex; mode=inline">E = mc^2</script>'
            '</span> ist berühmt.</p>'
        )
        result = self.pp.run(html)
        self.assertIn("<math", result)
        self.assertNotIn("<script", result)
        self.assertIn("ist berühmt", result)


# ---------------------------------------------------------------------------
# Integration: full Markdown → HTML with math
# ---------------------------------------------------------------------------

class TestMathIntegration(unittest.TestCase):
    """Test math rendering through the full Markdown pipeline."""

    def test_inline_math_in_markdown(self):
        import markdown
        from src.preview import MARKDOWN_EXTENSIONS, EXTENSION_CONFIGS
        text = "Die Formel $E = mc^2$ ist berühmt."
        result = markdown.markdown(
            text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        self.assertIn("script", result)
        pp = MathMLPostprocessor()
        result = pp.run(result)
        self.assertIn("<math", result)

    def test_block_math_in_markdown(self):
        import markdown
        from src.preview import MARKDOWN_EXTENSIONS, EXTENSION_CONFIGS
        text = "$$\n\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\n$$"
        result = markdown.markdown(
            text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        pp = MathMLPostprocessor()
        result = pp.run(result)
        self.assertIn("<math", result)
        self.assertIn("<mfrac>", result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_nodes(node: ASTNode, node_type: NodeType) -> list:
    """Recursively find all nodes of a given type in the AST."""
    found = []
    if node.type == node_type:
        found.append(node)
    for child in node.children:
        if isinstance(child, ASTNode):
            found.extend(_find_nodes(child, node_type))
    return found


if __name__ == "__main__":
    unittest.main()
