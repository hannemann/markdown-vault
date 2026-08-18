"""Shared Markdown text helpers.

Small, deliberately non-parsing utilities shared across the app: reducing
Markdown to readable plain text for single-line labels (tooltips, the outline),
and light Markdown->Markdown cleanups on imported notes. Kept neutral (no GTK,
no frontmatter knowledge) so every caller shares one implementation instead of
reaching across modules for it.
"""

import re

from markdown_vault.markdown.md_fences import FenceTracker

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_MDLINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

_HEADING_RE = re.compile(r"^(#{1,6}\s+)(.*)$")
# A heading whose text is a single bold span covering the whole line (** or __),
# tolerating trailing whitespace. The inner text must not contain the same
# marker, so "**A** and **B**" is not treated as one span.
_HEADING_BOLD_RE = re.compile(r"^(\*\*|__)((?:(?!\1).)+)\1\s*$")


def strip_markdown(text: str) -> str:
    """Reduce Markdown to plain text: drop heading/quote/list markers and
    emphasis, keep the visible text of links. Not a full parser — just enough
    that a line reads cleanly as a label."""
    text = re.sub(r"`+", "", text)                                   # code marks
    text = _WIKILINK_RE.sub(lambda m: m.group(1).split("|")[-1], text)  # [[a|b]]→b
    text = _MDLINK_RE.sub(r"\1", text)                               # [t](u)→t
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)        # headings
    text = re.sub(r"^\s{0,3}>+\s?", "", text, flags=re.M)            # blockquotes
    text = re.sub(r"^\s{0,3}(?:[-*+]|\d+\.)\s+", "", text, flags=re.M)  # list marks
    text = re.sub(r"[*_~]{1,3}", "", text)                           # emphasis
    return text


def unwrap_bold_headings(md: str) -> str:
    """Remove redundant whole-heading bold from imported Markdown.

    Some importers (e.g. pymupdf4llm) mirror a bold heading *font* by wrapping
    the heading text in ``**…**`` / ``__…__`` — ``# **Title**``. The bold adds
    nothing on a heading and shows up raw in non-rendering contexts, so unwrap it
    when the whole heading text is a single bold span. Italic is left as authored
    (importers never emphasise headings that way), and bold inside fenced code is
    untouched.
    """
    fences = FenceTracker()
    out = []
    for line in md.split("\n"):
        if fences.feed(line):
            out.append(line)
            continue
        m = _HEADING_RE.match(line)
        if m:
            bold = _HEADING_BOLD_RE.match(m.group(2))
            if bold:
                line = m.group(1) + bold.group(2)
        out.append(line)
    return "\n".join(out)


# A task-list checkbox on a list line; group 4 is the state (space or x/X).
# Quotes and ordered lists count, so a checkbox inside a blockquote or a
# numbered list can be ticked in the preview like any other.
_CHECKBOX_RE = re.compile(r'^(>\s*)*(\s*)([-*+]|\d+\.)\s+\[([ xX])\]')


def set_checkbox_state(line: str, checked: bool) -> str | None:
    """Return *line* with its checkbox set to *checked*, or ``None``.

    ``None`` means "nothing to do": the line carries no checkbox, or it already
    has the requested state. Returning that instead of the unchanged line lets
    the caller skip an undo step for a click that changes nothing.
    """
    match = _CHECKBOX_RE.match(line)
    if not match:
        return None
    new_state = "x" if checked else " "
    if match.group(4).lower() == new_state:
        return None
    return line[:match.start(4)] + new_state + line[match.end(4):]
