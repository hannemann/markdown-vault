"""LaTeX-to-MathML conversion for Markdown preview rendering.

Converts LaTeX math (inline ``$...$`` and block ``$$...$$``) to native MathML,
which WebKitGTK renders without JavaScript. The conversion is done by the
``latex2mathml`` library (pure Python, offline); this module only wraps it with a
graceful fallback and provides :class:`MathMLPostprocessor`, which turns the
``<script type="math/tex">`` tags emitted by ``pymdownx.arithmatex`` into MathML.
"""

from __future__ import annotations

import html
import logging
import re

from latex2mathml.converter import convert

logger = logging.getLogger(__name__)

_MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# latex2mathml emits raw ``<``/``>``/``&`` in text nodes (e.g. ``\text{a < b & c}``),
# producing malformed markup we would inject straight into the preview. Its own
# tags are a known, closed set, so match those and escape everything between them —
# neutralising a smuggled ``<b>`` and fixing bare ``&`` while keeping the numeric
# entities (``&#x00A0;``) latex2mathml already uses.
_MML_TAG = re.compile(
    r"</?(?:math|mrow|mi|mo|mn|ms|mtext|mspace|mfrac|msqrt|mroot|msub|msup|"
    r"msubsup|munder|mover|munderover|mstyle|mtable|mtr|mtd|mlabeledtr|mpadded|"
    r"mphantom|menclose|merror|mmultiscripts|mprescripts|none|semantics|"
    r"annotation(?:-xml)?)\b[^>]*>", re.I)
# Escape every ``&`` except a numeric character reference or one of XML's five
# predefined entities — a named one like ``&D;`` (from ``\text{R&D;}``) is undefined
# in MathML and would be a parse error, not a character.
_BARE_AMP = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|(?:amp|lt|gt|quot|apos);)")


def _escape_text(text: str) -> str:
    return _BARE_AMP.sub("&amp;", text).replace("<", "&lt;").replace(">", "&gt;")


def _wellform(mathml: str) -> str:
    """Escape the text between MathML tags so the result is well-formed XML."""
    out: list[str] = []
    last = 0
    for m in _MML_TAG.finditer(mathml):
        out.append(_escape_text(mathml[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_escape_text(mathml[last:]))
    return "".join(out)


def latex_to_mathml(latex: str, inline: bool = True) -> str:
    """Convert a LaTeX math string to a MathML string.

    *inline* renders inline math; otherwise block-level (``display="block"``). On
    malformed LaTeX the source is shown as plain text inside valid MathML rather
    than raising, so a single bad formula can't break the whole preview.
    """
    latex = (latex or "").strip()
    display = "inline" if inline else "block"
    if not latex:
        return f'<math xmlns="{_MATHML_NS}" display="{display}"></math>'
    try:
        return _wellform(convert(latex, display=display))
    except Exception:
        logger.warning("latex2mathml failed for %r", latex, exc_info=True)
        return (f'<math xmlns="{_MATHML_NS}" display="{display}">'
                f'<mtext>{html.escape(latex)}</mtext></math>')


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
