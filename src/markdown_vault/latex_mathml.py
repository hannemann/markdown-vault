"""LaTeX-to-MathML converter — converts LaTeX math strings to MathML XML.

Pure Python implementation, no external dependencies required beyond the
standard library.  Designed for use with Markdown preview rendering where
LaTeX math formulas (inline ``$...$`` and block ``$$...$$``) are converted
to native MathML that WebKitGTK renders without JavaScript.
"""

from __future__ import annotations

import html
import re
from enum import Enum, auto
from typing import NamedTuple


# ── Token types ──────────────────────────────────────────────────────

class TokenType(Enum):
    VARIABLE = auto()
    NUMBER = auto()
    OPERATOR = auto()
    SYMBOL = auto()
    COMMAND = auto()
    GROUP_OPEN = auto()
    GROUP_CLOSE = auto()
    BRACKET_OPEN = auto()
    BRACKET_CLOSE = auto()
    SUBSCRIPT = auto()
    SUPERSCRIPT = auto()
    SPACE = auto()
    ALIGN = auto()
    LINE_BREAK = auto()
    BEGIN_ENV = auto()
    END_ENV = auto()
    TEXT = auto()


class Token(NamedTuple):
    type: TokenType
    value: str


# ── AST node types ──────────────────────────────────────────────────

class NodeType(Enum):
    EXPRESSION = auto()
    VARIABLE = auto()
    NUMBER = auto()
    OPERATOR = auto()
    SYMBOL = auto()
    FRAC = auto()
    BINOM = auto()
    SUM = auto()
    INTEGRAL = auto()
    SQRT = auto()
    SQRT_N = auto()
    BOLD = auto()
    ALIGNED = auto()
    SUBSCRIPT = auto()
    SUPERSCRIPT = auto()
    SUBSUPERSCRIPT = auto()
    GROUP = auto()
    TEXT = auto()
    LINE = auto()
    ROOT = auto()
    STRETCHY = auto()  # stretchy delimiter (\left / \right)


class ASTNode(NamedTuple):
    type: NodeType
    children: list
    value: str = ""


# ── Tokenizer ───────────────────────────────────────────────────────

_SYMBOL_MAP: dict[str, str] = {
    # Lowercase Greek
    "\\pi": "\u03C0",
    "\\alpha": "\u03B1",
    "\\beta": "\u03B2",
    "\\gamma": "\u03B3",
    "\\delta": "\u03B4",
    "\\epsilon": "\u03B5",
    "\\zeta": "\u03B6",
    "\\eta": "\u03B7",
    "\\theta": "\u03B8",
    "\\iota": "\u03B9",
    "\\kappa": "\u03BA",
    "\\lambda": "\u03BB",
    "\\mu": "\u03BC",
    "\\nu": "\u03BD",
    "\\xi": "\u03BE",
    "\\omicron": "\u03BF",
    "\\rho": "\u03C1",
    "\\sigma": "\u03C3",
    "\\tau": "\u03C4",
    "\\upsilon": "\u03C5",
    "\\phi": "\u03C6",
    "\\chi": "\u03C7",
    "\\psi": "\u03C8",
    "\\omega": "\u03C9",
    # Uppercase Greek
    "\\Gamma": "\u0393",
    "\\Delta": "\u0394",
    "\\Theta": "\u0398",
    "\\Lambda": "\u039B",
    "\\Xi": "\u039E",
    "\\Pi": "\u03A0",
    "\\Sigma": "\u03A3",
    "\\Upsilon": "\u03A5",
    "\\Phi": "\u03A6",
    "\\Psi": "\u03A8",
    "\\Omega": "\u03A9",
    # Math operators / relations
    "\\infty": "\u221E",
    "\\partial": "\u2202",
    "\\nabla": "\u2207",
    "\\pm": "\u00B1",
    "\\mp": "\u2213",
    "\\cdot": "\u22C5",
    "\\times": "\u00D7",
    "\\leq": "\u2264",
    "\\geq": "\u2265",
    "\\neq": "\u2260",
    "\\approx": "\u2248",
    "\\equiv": "\u2261",
    "\\rightarrow": "\u2192",
    "\\leftarrow": "\u2190",
    "\\Rightarrow": "\u21D2",
    "\\Leftarrow": "\u21D0",
    "\\ldots": "\u2026",
    "\\cdots": "\u22EF",
    "\\forall": "\u2200",
    "\\exists": "\u2203",
    "\\neg": "\u00AC",
    "\\in": "\u2208",
    "\\notin": "\u2209",
    "\\subset": "\u2282",
    "\\supset": "\u2283",
    "\\cup": "\u222A",
    "\\cap": "\u2229",
    "\\wedge": "\u2227",
    "\\vee": "\u2228",
    "\\emptyset": "\u2205",
    "\\angle": "\u2220",
    "\\ell": "\u2113",
    "\\hbar": "\u210F",
    "\\aleph": "\u2135",
    "\\langle": "\u27E8",
    "\\rangle": "\u27E9",
    "\\prime": "\u2032",
    "\\dagger": "\u2020",
    "\\ddagger": "\u2021",
    # Spacing (render as wide mo elements)
    "\\quad": "\u2003",
    "\\qquad": "\u2003\u2003",
}

# Large operators: COMMAND tokens (not SYMBOL) because the parser
# needs to attach sub/superscript limits to them.
_LARGE_OPERATORS = {"sum", "prod", "int", "iint", "iiint", "oint"}

# Symbols that should render as <mo> (operator) instead of <mi> (identifier)
_OPERATOR_SYMBOLS = {
    "\\cdots", "\\ldots", "\\cdot", "\\times", "\\pm", "\\mp",
    "\\leq", "\\geq", "\\neq", "\\approx", "\\equiv",
    "\\rightarrow", "\\leftarrow", "\\Rightarrow", "\\Leftarrow",
    "\\forall", "\\exists", "\\neg", "\\in", "\\notin",
    "\\cup", "\\cap", "\\wedge", "\\vee",
    "\\langle", "\\rangle", "\\dagger", "\\ddagger",
    "\\quad", "\\qquad",
}


# Commands that take exactly 1 argument and render as upright operator name
_OPERATOR_NAMES = {
    "exp", "log", "ln", "lg",
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "coth",
    "det", "dim", "ker", "hom", "deg", "arg",
    "min", "max", "sup", "inf", "lim", "limsup", "liminf",
    "gcd", "lcm", "Pr",
}

_BEGIN_ENV_RE = re.compile(r"begin\{(\w+)\}")
_END_ENV_RE = re.compile(r"end\{(\w+)\}")


def tokenize(latex: str) -> list[Token]:
    """Tokenize a LaTeX math string into a list of tokens."""
    tokens: list[Token] = []
    i = 0
    n = len(latex)

    while i < n:
        ch = latex[i]

        # Whitespace
        if ch in (" ", "\t", "\n"):
            tokens.append(Token(TokenType.SPACE, " "))
            i += 1
            # Skip consecutive whitespace
            while i < n and latex[i] in (" ", "\t", "\n"):
                i += 1
            continue

        # Brace groups
        if ch == "{":
            tokens.append(Token(TokenType.GROUP_OPEN, "{"))
            i += 1
            continue
        if ch == "}":
            tokens.append(Token(TokenType.GROUP_CLOSE, "}"))
            i += 1
            continue

        # Optional argument brackets
        if ch == "[":
            tokens.append(Token(TokenType.BRACKET_OPEN, "["))
            i += 1
            continue
        if ch == "]":
            tokens.append(Token(TokenType.BRACKET_CLOSE, "]"))
            i += 1
            continue

        # Subscript / superscript
        if ch == "_":
            tokens.append(Token(TokenType.SUBSCRIPT, "_"))
            i += 1
            continue
        if ch == "^":
            tokens.append(Token(TokenType.SUPERSCRIPT, "^"))
            i += 1
            continue

        # Alignment & line break
        if ch == "&":
            tokens.append(Token(TokenType.ALIGN, "&"))
            i += 1
            continue
        if ch == "\\" and i + 1 < n and latex[i + 1] == "\\":
            tokens.append(Token(TokenType.LINE_BREAK, "\\\\"))
            i += 2
            continue

        # Backslash commands
        if ch == "\\":
            i += 1
            if i >= n:
                break
            # \begin{env} / \end{env}
            rest = latex[i:]
            m_begin = _BEGIN_ENV_RE.match(rest)
            if m_begin:
                tokens.append(Token(TokenType.BEGIN_ENV, m_begin.group(1)))
                i += m_begin.end()
                continue
            m_end = _END_ENV_RE.match(rest)
            if m_end:
                tokens.append(Token(TokenType.END_ENV, m_end.group(1)))
                i += m_end.end()
                continue
            # Named command or symbol
            cmd_match = re.match(r"[a-zA-Z]+", rest)
            if cmd_match:
                cmd = cmd_match.group(0)
                full = "\\" + cmd
                i += cmd_match.end()
                if cmd in _LARGE_OPERATORS:
                    tokens.append(Token(TokenType.COMMAND, cmd))
                elif full in _SYMBOL_MAP:
                    tokens.append(Token(TokenType.SYMBOL, full))
                else:
                    tokens.append(Token(TokenType.COMMAND, cmd))
                continue
            # Escaped character like \, or \; or \! or \{ or \}
            next_ch = latex[i]
            if next_ch in ("{", "}"):
                tokens.append(Token(TokenType.OPERATOR, next_ch))
            else:
                tokens.append(Token(TokenType.TEXT, next_ch))
            i += 1
            continue

        # Operators
        if ch in "=+-><:!":
            tokens.append(Token(TokenType.OPERATOR, ch))
            i += 1
            continue

        # Numbers
        if ch.isdigit():
            start = i
            while i < n and (latex[i].isdigit() or latex[i] == "."):
                i += 1
            tokens.append(Token(TokenType.NUMBER, latex[start:i]))
            continue

        # Single-letter variables (a-z, A-Z)
        if ch.isalpha():
            tokens.append(Token(TokenType.VARIABLE, ch))
            i += 1
            continue

        # Parentheses, brackets, etc. → operator (renders as <mo>)
        if ch in "()[]|":
            tokens.append(Token(TokenType.OPERATOR, ch))
            i += 1
            continue

        # Anything else
        tokens.append(Token(TokenType.TEXT, ch))
        i += 1

    return tokens


# ── Parser ──────────────────────────────────────────────────────────

class _Parser:
    """Recursive-descent parser for LaTeX math tokens → AST."""

    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0
        self._token_buffer: list[Token] = []

    def _peek(self) -> Token | None:
        if self._token_buffer:
            return self._token_buffer[-1]
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> Token | None:
        if self._token_buffer:
            return self._token_buffer.pop()
        tok = self._peek()
        if tok is not None:
            self._pos += 1
        return tok

    def _skip_spaces(self):
        while self._peek() and self._peek().type == TokenType.SPACE:
            self._pos += 1

    def _parse_group(self) -> ASTNode:
        """Parse a {…} group into a GROUP node, or a single token if no brace."""
        self._skip_spaces()
        if self._peek() and self._peek().type == TokenType.GROUP_OPEN:
            self._advance()  # consume {
            children = []
            while self._peek() and self._peek().type != TokenType.GROUP_CLOSE:
                children.append(self._parse_element())
            self._advance()  # consume }
            return ASTNode(NodeType.GROUP, children)
        # Single token argument (e.g., \frac12 -> numerator=1)
        return self._parse_single_token_argument()

    def _parse_single_token_argument(self) -> ASTNode:
        """Parse a single token as argument, splitting multi-digit numbers."""
        self._skip_spaces()
        tok = self._peek()
        if tok is None:
            return ASTNode(NodeType.EXPRESSION, [])
        
        if tok.type == TokenType.GROUP_OPEN:
            return self._parse_group()
        
        self._advance()
        
        if tok.type == TokenType.NUMBER and len(tok.value) > 1:
            # LaTeX \frac12 -> two single-digit args; split "12" -> "1", "2"
            first_digit = tok.value[0]
            rest = tok.value[1:]
            # Push rest back as a new NUMBER token
            self._token_buffer.insert(0, Token(TokenType.NUMBER, rest))
            return ASTNode(NodeType.NUMBER, [], first_digit)
        
        if tok.type == TokenType.VARIABLE:
            return ASTNode(NodeType.VARIABLE, [], tok.value)
        
        if tok.type == TokenType.NUMBER:
            return ASTNode(NodeType.NUMBER, [], tok.value)
        
        if tok.type == TokenType.OPERATOR:
            return ASTNode(NodeType.OPERATOR, [], tok.value)

    def _parse_optional_group(self) -> ASTNode | None:
        """Parse an optional [...] argument, or return None if not present."""
        self._skip_spaces()
        if self._peek() and self._peek().type == TokenType.BRACKET_OPEN:
            self._advance()  # consume [
            children = []
            while self._peek() and self._peek().type != TokenType.BRACKET_CLOSE:
                children.append(self._parse_element())
            self._advance()  # consume ]
            return ASTNode(NodeType.GROUP, children)
        return None

    def _parse_primary(self) -> ASTNode:
        """Parse a single primary token or construct."""
        self._skip_spaces()
        tok = self._peek()
        if tok is None:
            return ASTNode(NodeType.EXPRESSION, [])

        if tok.type == TokenType.GROUP_OPEN:
            return self._parse_group()

        self._advance()

        if tok.type == TokenType.VARIABLE:
            return ASTNode(NodeType.VARIABLE, [], tok.value)

        if tok.type == TokenType.NUMBER:
            return ASTNode(NodeType.NUMBER, [], tok.value)

        if tok.type == TokenType.OPERATOR:
            return ASTNode(NodeType.OPERATOR, [], tok.value)

        if tok.type == TokenType.SYMBOL:
            return ASTNode(NodeType.SYMBOL, [], tok.value)

        if tok.type == TokenType.TEXT:
            return ASTNode(NodeType.TEXT, [], tok.value)

        if tok.type == TokenType.COMMAND:
            return self._parse_command(tok.value)

        if tok.type == TokenType.SUBSCRIPT:
            return self._parse_subscript()

        if tok.type == TokenType.SUPERSCRIPT:
            return self._parse_superscript()

        # Fallback
        return ASTNode(NodeType.TEXT, [], tok.value)

    def _parse_command(self, cmd: str) -> ASTNode:
        """Parse a \\command, consuming its arguments as needed."""
        # Binary operators (2 arguments)
        if cmd in ("frac", "cfrac", "dfrac", "tfrac"):
            num = self._parse_group()
            den = self._parse_group()
            return ASTNode(NodeType.FRAC, [num, den])
        if cmd in ("binom", "dbinom", "tbinom"):
            top = self._parse_group()
            bot = self._parse_group()
            return ASTNode(NodeType.BINOM, [top, bot])
        # Square root
        if cmd == "sqrt":
            opt = self._parse_optional_group()
            body = self._parse_group()
            if opt:
                return ASTNode(NodeType.SQRT_N, [opt, body])
            return ASTNode(NodeType.SQRT, [body])
        # Style / font commands (1 argument)
        if cmd == "mathcal":
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body], "script")
        if cmd == "mathbb":
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body], "double-struck")
        if cmd == "mathfrak":
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body], "fraktur")
        if cmd in ("mathbf",):
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body], "bold")
        if cmd in ("mathrm",):
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body], "normal")
        if cmd in ("hat", "bar", "vec", "dot", "overline", "underline"):
            body = self._parse_group()
            return ASTNode(NodeType.BOLD, [body])  # fallback bold for accent commands
        # Text / operator names
        if cmd == "text":
            body = self._parse_group()
            return ASTNode(NodeType.TEXT, [], _extract_text(body))
        if cmd == "operatorname":
            body = self._parse_group()
            return ASTNode(NodeType.TEXT, [], _extract_text(body))
        # Named operators (sin, cos, log, exp, etc.)
        if cmd in _OPERATOR_NAMES:
            return ASTNode(NodeType.TEXT, [], cmd)
        # Large operators
        if cmd == "sum":
            return ASTNode(NodeType.SUM, [])
        if cmd == "int":
            return ASTNode(NodeType.INTEGRAL, [])
        if cmd == "prod":
            return ASTNode(NodeType.SUM, [], "prod")
        # \left / \right — consume delimiter, render as stretchy delimiter
        if cmd == "left":
            self._skip_spaces()
            delim = self._advance()
            if delim:
                return ASTNode(NodeType.STRETCHY, [], delim.value)
            return ASTNode(NodeType.EXPRESSION, [])
        if cmd == "right":
            self._skip_spaces()
            delim = self._advance()
            if delim:
                return ASTNode(NodeType.STRETCHY, [], delim.value)
            return ASTNode(NodeType.EXPRESSION, [])
        # Spacing commands
        if cmd == "quad":
            return ASTNode(NodeType.SYMBOL, [], "\\quad")
        if cmd == "qquad":
            return ASTNode(NodeType.SYMBOL, [], "\\qquad")
        # Unknown command — render as text (without backslash)
        return ASTNode(NodeType.TEXT, [], f"\\{cmd}")

    def _parse_subscript(self) -> ASTNode:
        self._skip_spaces()
        base = ASTNode(NodeType.EXPRESSION, [])  # empty base
        sub = self._parse_primary()
        return ASTNode(NodeType.SUBSCRIPT, [base, sub])

    def _parse_superscript(self) -> ASTNode:
        self._skip_spaces()
        base = ASTNode(NodeType.EXPRESSION, [])
        sup = self._parse_primary()
        return ASTNode(NodeType.SUPERSCRIPT, [base, sup])

    def parse_expression(self) -> ASTNode:
        """Parse a full expression until end of tokens."""
        children = []
        while self._peek() is not None:
            self._skip_spaces()
            if self._peek() is None:
                break
            if self._peek().type == TokenType.GROUP_CLOSE:
                break
            if self._peek().type == TokenType.END_ENV:
                break
            children.append(self._parse_element())
        return ASTNode(NodeType.EXPRESSION, children)

    def _parse_element(self) -> ASTNode:
        """Parse an element, handling sub/superscript applied to preceding node."""
        self._skip_spaces()
        tok = self._peek()
        if tok is None:
            return ASTNode(NodeType.EXPRESSION, [])

        node = self._parse_primary()

        # Check for subscript/superscript attached to this element
        self._skip_spaces()
        tok = self._peek()
        if tok and tok.type == TokenType.SUBSCRIPT:
            self._advance()
            self._skip_spaces()
            sub = self._parse_primary()
            self._skip_spaces()
            tok2 = self._peek()
            if tok2 and tok2.type == TokenType.SUPERSCRIPT:
                self._advance()
                self._skip_spaces()
                sup = self._parse_primary()
                return ASTNode(NodeType.SUBSUPERSCRIPT, [node, sub, sup])
            return ASTNode(NodeType.SUBSCRIPT, [node, sub])
        if tok and tok.type == TokenType.SUPERSCRIPT:
            self._advance()
            self._skip_spaces()
            sup = self._parse_primary()
            return ASTNode(NodeType.SUPERSCRIPT, [node, sup])

        return node

    def _parse_element_stop_at(self, stop_types: set) -> ASTNode | None:
        """Parse an element like _parse_element, but return None if the
        next token is in *stop_types* (without consuming it)."""
        self._skip_spaces()
        tok = self._peek()
        if tok is None or tok.type in stop_types:
            return None
        return self._parse_element()

    def parse_aligned(self) -> ASTNode:
        """Parse \\begin{aligned}...\\end{aligned} content.

        Produces LINE nodes, each containing EXPRESSION children (one per column).
        ``&`` separates columns, ``\\\\`` separates rows.
        """
        lines: list[ASTNode] = []
        columns: list[ASTNode] = []
        col_elements: list[ASTNode] = []

        def _flush_col():
            nonlocal col_elements
            if col_elements:
                columns.append(ASTNode(NodeType.EXPRESSION, col_elements))
                col_elements = []

        def _flush_row():
            nonlocal columns, col_elements
            _flush_col()
            if columns:
                lines.append(ASTNode(NodeType.LINE, columns))
                columns = []

        _ALIGNED_STOP = {TokenType.ALIGN, TokenType.LINE_BREAK, TokenType.END_ENV,
                         TokenType.GROUP_CLOSE}

        while self._peek() is not None:
            tok = self._peek()
            if tok.type == TokenType.END_ENV:
                self._advance()
                break
            if tok.type == TokenType.LINE_BREAK:
                self._advance()
                _flush_row()
                continue
            if tok.type == TokenType.ALIGN:
                self._advance()
                _flush_col()
                continue
            # Parse one element, but stop before ALIGN / LINE_BREAK / END_ENV
            node = self._parse_element_stop_at(_ALIGNED_STOP)
            if node is not None:
                col_elements.append(node)

        _flush_row()
        return ASTNode(NodeType.ALIGNED, lines)


def _extract_text(node: ASTNode) -> str:
    """Recursively extract plain text from an AST node."""
    if node.value:
        return node.value
    parts = []
    for child in node.children:
        if isinstance(child, ASTNode):
            parts.append(_extract_text(child))
    return "".join(parts)


def parse(latex: str) -> ASTNode:
    """Parse a LaTeX math string into an AST."""
    tokens = tokenize(latex)
    parser = _Parser(tokens)

    # Check for \begin{aligned}...\end{aligned}
    for i, tok in enumerate(tokens):
        if tok.type == TokenType.BEGIN_ENV and tok.value == "aligned":
            # Re-parse with aligned support
            parser._pos = 0
            # Skip to \begin{aligned}
            while parser._peek() and not (parser._peek().type == TokenType.BEGIN_ENV
                                          and parser._peek().value == "aligned"):
                parser._pos += 1
            parser._advance()  # consume \begin{aligned}
            aligned = parser.parse_aligned()
            return ASTNode(NodeType.ROOT, [aligned])

    return ASTNode(NodeType.ROOT, [parser.parse_expression()])


# ── MathML renderer ────────────────────────────────────────────────

def _symbol_to_char(sym: str) -> str:
    """Convert a LaTeX symbol command to its Unicode character."""
    return _SYMBOL_MAP.get(sym, sym)


def _render_node(node: ASTNode, inline: bool = True) -> str:
    """Render an AST node to a MathML string."""
    if node.type == NodeType.ROOT:
        inner = "".join(_render_node(c, inline) for c in node.children)
        if inline:
            return f"<math xmlns=\"http://www.w3.org/1998/Math/MathML\">{inner}</math>"
        # Block mode: add displaystyle so sum/prod/int show limits above/below
        return (
            f'<math xmlns="http://www.w3.org/1998/Math/MathML"'
            f' display="block">'
            f'<mstyle displaystyle="true">{inner}</mstyle>'
            f'</math>'
        )

    if node.type == NodeType.EXPRESSION:
        return "".join(_render_node(c, inline) for c in node.children)

    if node.type == NodeType.VARIABLE:
        return f"<mi>{html.escape(node.value)}</mi>"

    if node.type == NodeType.NUMBER:
        return f"<mn>{html.escape(node.value)}</mn>"

    if node.type == NodeType.OPERATOR:
        if node.value in ("(", ")", "[", "]"):
            return f'<mo stretchy="false">{html.escape(node.value)}</mo>'
        return f"<mo>{html.escape(node.value)}</mo>"

    if node.type == NodeType.STRETCHY:
        return f'<mo stretchy="true">{html.escape(node.value)}</mo>'

    if node.type == NodeType.SYMBOL:
        char = _symbol_to_char(node.value)
        # Operators/specials render as <mo>, identifiers as <mi>
        if node.value in _OPERATOR_SYMBOLS:
            return f"<mo>{html.escape(char)}</mo>"
        return f"<mi>{html.escape(char)}</mi>"

    if node.type == NodeType.TEXT:
        # Operator names (exp, sin, cos, …) → upright <mi>
        if node.value in _OPERATOR_NAMES:
            return f'<mi mathvariant="normal">{html.escape(node.value)}</mi>'
        return f"<mtext>{html.escape(node.value)}</mtext>"

    if node.type == NodeType.GROUP:
        return "".join(_render_node(c, inline) for c in node.children)

    if node.type == NodeType.FRAC:
        num = _render_node(node.children[0], inline)
        den = _render_node(node.children[1], inline)
        return f"<mfrac><mrow>{num}</mrow><mrow>{den}</mrow></mfrac>"

    if node.type == NodeType.BINOM:
        # Binomial coefficient: use mfrac with linethickness="0" and fence parentheses
        top = _render_node(node.children[0], inline)
        bot = _render_node(node.children[1], inline)
        return f'<mrow><mo>(</mo><mfrac linethickness="0"><mrow>{top}</mrow><mrow>{bot}</mrow></mfrac><mo>)</mo></mrow>'

    if node.type == NodeType.SQRT:
        body = _render_node(node.children[0], inline)
        return f"<msqrt><mrow>{body}</mrow></msqrt>"

    if node.type == NodeType.SQRT_N:
        # \sqrt[n]{x} → <mroot><mrow>x</mrow><mrow>n</mrow></mroot>
        body = _render_node(node.children[1], inline)
        index = _render_node(node.children[0], inline)
        return f"<mroot><mrow>{body}</mrow><mrow>{index}</mrow></mroot>"

    if node.type == NodeType.BOLD:
        variant = node.value if node.value else "bold"
        body = _render_node(node.children[0], inline)
        return f'<mstyle mathvariant="{variant}"><mrow>{body}</mrow></mstyle>'

    if node.type == NodeType.SUM:
        char = {"sum": "\u2211", "prod": "\u220F"}.get(node.value, "\u2211")
        return f"<mo>{char}</mo>"

    if node.type == NodeType.INTEGRAL:
        return f"<mo>\u222B</mo>"

    if node.type == NodeType.SUBSCRIPT:
        base = _render_node(node.children[0], inline)
        sub = _render_node(node.children[1], inline)
        return f"<msub><mrow>{base}</mrow><mrow>{sub}</mrow></msub>"

    if node.type == NodeType.SUPERSCRIPT:
        base = _render_node(node.children[0], inline)
        sup = _render_node(node.children[1], inline)
        return f"<msup><mrow>{base}</mrow><mrow>{sup}</mrow></msup>"

    if node.type == NodeType.SUBSUPERSCRIPT:
        base = _render_node(node.children[0], inline)
        sub = _render_node(node.children[1], inline)
        sup = _render_node(node.children[2], inline)
        # In block mode, use <munderover> for large operators (sum/int/prod)
        # so limits appear above/below instead of beside
        if not inline and node.children[0].type in (NodeType.SUM, NodeType.INTEGRAL):
            return f"<munderover><mrow>{base}</mrow><mrow>{sub}</mrow><mrow>{sup}</mrow></munderover>"
        return f"<msubsup><mrow>{base}</mrow><mrow>{sub}</mrow><mrow>{sup}</mrow></msubsup>"

    if node.type == NodeType.ALIGNED:
        rows = []
        for line in node.children:
            cells = []
            for cell in line.children:
                cells.append(f"<mtd>{_render_node(cell, inline)}</mtd>")
            rows.append(f"<mtr>{''.join(cells)}</mtr>")
        return f"<mtable displaystyle=\"true\">{''.join(rows)}</mtable>"

    if node.type == NodeType.LINE:
        return "".join(_render_node(c, inline) for c in node.children)

    return ""


def latex_to_mathml(latex: str, inline: bool = True) -> str:
    """Convert a LaTeX math string to a MathML string.

    Args:
        latex: The LaTeX math source (e.g. ``"E = mc^2"``).
        inline: If True, render as inline math; if False, render as
            block-level math with ``display="block"``.

    Returns:
        A MathML XML string.
    """
    if not latex.strip():
        if inline:
            return '<math xmlns="http://www.w3.org/1998/Math/MathML"></math>'
        return (
            '<math xmlns="http://www.w3.org/1998/Math/MathML"'
            ' display="block"><mstyle displaystyle="true">'
            '</mstyle></math>'
        )

    ast = parse(latex)
    return _render_node(ast, inline)


# ── HTML postprocessor ─────────────────────────────────────────────

class MathMLPostprocessor:
    """Replace ``<script type="math/tex">`` tags with ``<math>`` elements.

    This postprocessor is designed to work with the ``pymdownx.arithmatex``
    Markdown extension which wraps LaTeX math in ``<script>`` tags inside
    ``<div class="arithmatex">`` (block) or ``<span class="arithmatex">``
    (inline) containers that may also include a preview element.

    Both the preview element and the script tag are replaced by a single
    ``<math>`` element.
    """

    # Match arithmatex wrapper with <div> (block math)
    _BLOCK_RE = re.compile(
        r'<div\s+class="arithmatex">\s*'
        r'(?:<div\s+class="MathJax_Preview">.*?</div>\s*)?'
        r'<script\s+type="math/tex(?:;\s*mode=display)?"[^>]*>'
        r'(.*?)'
        r'</script>\s*'
        r'</div>',
        re.DOTALL,
    )

    # Match arithmatex wrapper with <span> (inline math)
    _INLINE_RE = re.compile(
        r'<span\s+class="arithmatex">\s*'
        r'(?:<span\s+class="MathJax_Preview">.*?</span>\s*)?'
        r'<script\s+type="math/tex(?:;\s*mode=inline)?"[^>]*>'
        r'(.*?)'
        r'</script>\s*'
        r'</span>',
        re.DOTALL,
    )

    # Fallback: bare script tags (no wrapper)
    _SCRIPT_RE = re.compile(
        r'<script\s+type="math/tex(?:;\s*mode=(?:inline|display))?"[^>]*>'
        r'(.*?)'
        r'</script>',
        re.DOTALL,
    )

    def run(self, html: str) -> str:
        # Pass 1: replace block arithmatex divs
        html = self._BLOCK_RE.sub(
            lambda m: latex_to_mathml(m.group(1), inline=False),
            html,
        )
        # Pass 2: replace inline arithmatex spans
        html = self._INLINE_RE.sub(
            lambda m: latex_to_mathml(m.group(1), inline=True),
            html,
        )
        # Pass 3: replace any remaining bare script tags
        def _replace_bare(match: re.Match) -> str:
            tag = match.group(0)
            is_inline = "mode=inline" in tag
            is_display = "mode=display" in tag
            inline = is_inline and not is_display
            return latex_to_mathml(match.group(1), inline=inline)

        html = self._SCRIPT_RE.sub(_replace_bare, html)
        return html
