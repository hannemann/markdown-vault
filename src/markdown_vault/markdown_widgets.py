"""Render a small Markdown subset to native GTK widgets.

The Ask answer is Markdown, but a single ``Gtk.Label`` shows a table as raw
pipes and a list as literal ``-``. This turns the common blocks a local model
emits — paragraphs, headings, bullet/numbered lists, fenced code, pipe tables,
block quotes, rules — into real widgets, with inline ``**bold**``, ``*italic*``,
``` `code` ``` and ``[links](url)`` converted to Pango markup.

Deliberately *not* a full Markdown/HTML pipeline (no WebView): the answer lives
in a keyboard-driven results list, and a lightweight widget tree fits that far
better.

Two copy paths, on purpose: the rendered labels are selectable, so a mouse
selection plus the label's context menu copies exactly the *visible* text a user
highlighted (a sentence, a table cell). The palette's dedicated copy button
copies the whole answer as its Markdown *source* instead (it holds the stored
raw text, not these widgets) — a distinct affordance with its own tooltip.
"""

import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .md_fences import FENCE_OPEN_RE, FenceTracker

# --- inline -----------------------------------------------------------------

_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# Only these schemes become clickable links (opened via gtk_show_uri by the
# label's default handler) — matching the preview's allowlist. A note is the
# user's own content, but it can be imported from anywhere, so a link with a
# file:, javascript: or other scheme must render as plain text, not a launcher.
_ALLOWED_LINK_SCHEMES = ("http://", "https://", "mailto:")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITAL_STAR = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_ITAL_US = re.compile(r"(?<![\w*])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w])")
# Sentinels wrapping a stashed-fragment index. Private-use codepoints (NUL
# can't be used — markup_escape_text is a C routine that truncates at an
# embedded NUL). They are stripped from the input first (see inline_to_pango),
# so a stray U+E000/U+E001 in the answer can't forge a sentinel.
_STASH_OPEN = chr(0xE000)
_STASH_CLOSE = chr(0xE001)
_STASH = _STASH_OPEN + "%d" + _STASH_CLOSE
_STASH_RE = re.compile(_STASH_OPEN + r"(\d+)" + _STASH_CLOSE)


def _emphasis(text: str) -> str:
    """Bold/italic Pango markup. Runs on already-escaped prose that has had code
    spans and links stashed out, so it never rewrites a URL or a code span."""
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITAL_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _ITAL_US.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return text


def _link_markup(text: str, url: str) -> str:
    """A ``[text](url)`` → a Pango ``<a>`` only for an allowlisted scheme. Any
    other scheme is not made clickable but keeps the URL visible beside the text
    (``text (url)``) so it doesn't silently vanish. *text* and *url* are already
    escaped; emphasis is applied to the link text, never to the URL."""
    if url.lower().startswith(_ALLOWED_LINK_SCHEMES):
        return f'<a href="{url}">{_emphasis(text)}</a>'
    return f"{_emphasis(text)} ({url})"


def inline_to_pango(md: str) -> str:
    """Inline Markdown → Pango markup, fully escaped so it is safe for
    :meth:`Gtk.Label.set_markup`.

    Code spans and links are rendered first and *stashed*, so the bold/italic
    passes run only on the surrounding prose — never inside a URL or a code span
    (which would corrupt an ``href`` and emit ``<`` inside an attribute)."""
    # Strip our own delimiters from the input so it can't forge a sentinel.
    md = md.replace(_STASH_OPEN, "").replace(_STASH_CLOSE, "")
    frags: list = []

    def keep(markup: str) -> str:
        frags.append(markup)
        return _STASH % (len(frags) - 1)

    text = _CODE.sub(
        lambda m: keep(f"<tt>{GLib.markup_escape_text(m.group(1))}</tt>"), md)
    text = GLib.markup_escape_text(text)            # escape the surrounding prose
    text = _LINK.sub(lambda m: keep(_link_markup(m.group(1), m.group(2))), text)
    text = _emphasis(text)
    # Restore stashed fragments, repeating so a link that wraps a code span
    # (its fragment holds an inner sentinel) is fully resolved. Depth is ≤2.
    for _ in range(8):
        if not _STASH_RE.search(text):
            break
        text = _STASH_RE.sub(lambda m: frags[int(m.group(1))], text)
    return text


# --- block parsing ----------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_ULIST = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_table_start(lines: list, i: int) -> bool:
    return ("|" in lines[i] and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1]) is not None)


def parse_blocks(text: str) -> list:
    """Group *text* into ``(kind, payload)`` blocks. Exposed for testing; the
    kinds are ``heading``, ``paragraph``, ``ulist``, ``olist``, ``code``,
    ``table``, ``quote`` and ``rule``."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = FenceTracker()
        if fence.feed(line):                        # fenced code block (``` or ~~~)
            i += 1
            body = []
            while i < n:
                fence.feed(lines[i])
                if not fence.in_fence:               # this line is the closing marker
                    i += 1
                    break
                body.append(lines[i])               # content (inc. inner fences)
                i += 1
            blocks.append(("code", "\n".join(body)))
            continue
        if _is_table_start(lines, i):               # pipe table
            header = _split_row(lines[i])
            i += 2                                    # header + separator
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(("table", (header, rows)))
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(("heading", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue
        if _HR.match(line):
            blocks.append(("rule", None))
            i += 1
            continue
        if _ULIST.match(line) or _OLIST.match(line):
            kind = "ulist" if _ULIST.match(line) else "olist"
            items = []
            while i < n and (_ULIST.match(lines[i]) or _OLIST.match(lines[i])):
                um, om = _ULIST.match(lines[i]), _OLIST.match(lines[i])
                items.append((om.group(1) if om else None,
                              (om.group(2) if om else um.group(1))))
                i += 1
            blocks.append((kind, items))
            continue
        if line.lstrip().startswith(">"):           # block quote
            body = []
            while i < n and lines[i].lstrip().startswith(">"):
                body.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            blocks.append(("quote", " ".join(body)))
            continue
        para = []                                    # paragraph: until a blank
        while i < n and lines[i].strip() and not _special(lines, i):
            para.append(lines[i].strip())
            i += 1
        blocks.append(("paragraph", " ".join(para)))
    return blocks


def _special(lines: list, i: int) -> bool:
    line = lines[i]
    return bool(FENCE_OPEN_RE.match(line) or _HEADING.match(line) or _HR.match(line)
                or _ULIST.match(line) or _OLIST.match(line)
                or line.lstrip().startswith(">") or _is_table_start(lines, i))


# --- widget building --------------------------------------------------------

_HEADING_CLASS = {1: "title-1", 2: "title-2", 3: "title-3",
                  4: "title-4", 5: "heading", 6: "heading"}


def _label(markup: str, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label()
    label.set_markup(markup)
    label.set_xalign(0)
    label.set_wrap(wrap)
    label.set_selectable(True)         # select + context-menu copies visible text
    label.set_focusable(False)         # …but not a Tab stop (mouse selection only)
    return label


def render_markdown(text: str) -> Gtk.Widget:
    """A vertical ``Gtk.Box`` of block widgets rendering *text* (a Markdown
    subset). Text is selectable (for a visible-text copy) but not focusable, so
    the answer adds no Tab stops."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for kind, payload in parse_blocks(text):
        box.append(_render_block(kind, payload))
    return box


def _render_block(kind: str, payload) -> Gtk.Widget:
    if kind == "heading":
        level, content = payload
        label = _label(inline_to_pango(content))
        label.add_css_class(_HEADING_CLASS.get(level, "heading"))
        return label
    if kind == "paragraph":
        return _label(inline_to_pango(payload))
    if kind in ("ulist", "olist"):
        return _render_list(payload)
    if kind == "code":
        label = _label(GLib.markup_escape_text(payload), wrap=False)
        label.add_css_class("monospace")
        label.add_css_class("mv-answer-code")   # subtle background + padding box
        return label
    if kind == "table":
        return _render_table(*payload)
    if kind == "quote":
        label = _label(inline_to_pango(payload))
        label.add_css_class("dim-label")
        label.set_margin_start(12)
        return label
    return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)  # rule


def _render_list(items: list) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_start(6)
    for number, content in items:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bullet = Gtk.Label(label=f"{number}." if number else "•")
        bullet.set_xalign(0)
        bullet.set_valign(Gtk.Align.START)
        row.append(bullet)
        item = _label(inline_to_pango(content))
        item.set_hexpand(True)
        row.append(item)
        box.append(row)
    return box


def _render_table(header: list, rows: list) -> Gtk.Widget:
    grid = Gtk.Grid(column_spacing=16, row_spacing=4)
    grid.add_css_class("mv-answer-table")
    for col, cell in enumerate(header):
        label = _label(f"<b>{inline_to_pango(cell)}</b>")
        grid.attach(label, col, 0, 1, 1)
    for r, cells in enumerate(rows, start=1):
        for col, cell in enumerate(cells):
            grid.attach(_label(inline_to_pango(cell)), col, r, 1, 1)
    return grid
