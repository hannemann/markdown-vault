"""Markdown Vault — WebKitGTK-based Markdown preview renderer.

Converts Markdown text to HTML and displays it inside a ``WebKit.WebView``.
The rendering respects system theme colours via GTK named CSS variables
(``@theme_text_color`` etc.) so that the preview automatically adapts
to light and dark mode.
"""

import hashlib
import json
import logging
import os
import markdown as md
import re
import unicodedata
from collections.abc import MutableSet
from pathlib import Path
from markdown.extensions import Extension
from markdown.extensions.toc import slugify, unique as toc_unique


def _toc_slugify(value: str, separator: str) -> str:
    """Slugify function for toc extension that preserves Unicode (CJK, Arabic, etc.)."""
    return slugify(value, separator, unicode=True)
from markdown.inlinepatterns import InlineProcessor
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor
import xml.etree.ElementTree as etree
from pygments.formatters import HtmlFormatter
from urllib.parse import unquote
from pymdownx.emoji import to_alt
from markdown_vault.core import config
from markdown_vault.markdown.latex_mathml import MathMLPostprocessor
from markdown_vault.markdown.md_fences import FenceTracker
from markdown_vault.core.path_utils import (
    HEADING_RE,
    find_vault_name_for_path,
    parse_wikilink_url,
    resolve_vault_path,
    resolve_wikilink,
    wikilink_url,
)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, WebKit, GObject, Gdk, GLib, Gio, Pango


import unicodedata

logger = logging.getLogger(__name__)


def _heading_to_slug(heading: str, seen: MutableSet[str] | None = None,
                     unicode: bool = True) -> str:
    """Convert a heading text to a slug matching the toc extension's output.

    Delegates to the toc extension's slugify and unique functions to ensure
    identical slug generation for TOC links and heading IDs.
    """
    base_slug = slugify(heading, "-", unicode=unicode)

    if seen is not None:
        return toc_unique(base_slug, seen)

    return base_slug


def _anchor_scroll_js(heading: str) -> str:
    """Build JS that smooth-scrolls to the heading anchor for *heading*.

    *heading* is heading **text** (as written after ``#`` in a wikilink), so it
    is slugified to the rendered element id. The script polls for a bounded
    window because a freshly opened note may still be rendering (full load or an
    innerHTML swap) when this runs, so the target may not be in the DOM yet.
    Returns ``""`` for an empty heading (nothing to scroll to).
    """
    if not heading:
        return ""
    target = json.dumps(_heading_to_slug(heading))
    # Appended to the script that puts the content in place, so the jump runs in
    # the same turn as the DOM it targets — no waiting, no polling, no guessing
    # when the layout is ready.
    return (
        f"var _t=document.getElementById({target});"
        "if(_t)_t.scrollIntoView({behavior:'smooth',block:'start'});"
    )


def _scroll_to_js(y: float) -> str:
    """Build JS that jumps the preview to vertical pixel offset *y*.

    No ``behavior:'smooth'``: a restored reading position should appear at once,
    not animate up from the top the way an in-page anchor jump does.
    """
    return f"window.scrollTo(0, {float(y)});"


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --bg: {bg_color}; --fg: {fg_color}; --accent: {accent_color}; --dim: {dim_color}; --card-bg: {card_bg_color}; --borders: {borders_color}; }}
{css_content}
</style>
</head>
<body>
<div class="markdown-body">
{content}
</div>
</body>
</html>"""


EXTENSION_CONFIGS = {
    "pymdownx.superfences": {
        "css_class": "codehilite",
    },
    "pymdownx.tasklist": {
        "clickable_checkbox": True,
    },
    "markdown.extensions.toc": {
        "slugify": _toc_slugify,
    },
    # Render :emoji: as the Unicode glyph (system emoji font) instead of the
    # default remote <img src="https://cdnjs…emojione…png"> — no network fetch.
    "pymdownx.emoji": {
        "emoji_generator": to_alt,
    },
}


def _build_csp(allow_remote_images: bool) -> str:
    """Return the preview document's Content-Security-Policy (5.1).

    ``default-src 'none'`` denies everything not explicitly listed.
    ``style-src 'unsafe-inline'`` is required — the whole stylesheet and the
    theme JS set inline styles (all first-party).  ``img-src`` permits local
    ``file:`` images and inline ``data:`` URIs; ``https:`` is added only when
    the user opts in to remote images, and even then scripts, frames and
    connections stay blocked so a note can only ever fetch an image, never
    beacon via fetch/XHR or run remote code.  Page scripts are already off
    (``enable_javascript_markup(False)``); the checkbox/theme JS runs through
    privileged WebKit APIs, not the page's ``script-src``.
    """
    img = "file: data: https:" if allow_remote_images else "file: data:"
    return (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "script-src 'none'; "
        f"img-src {img}"
    )


def _hover_uri_display(uri: str, page_uri: str = "", page_breadcrumb: str = "") -> str:
    """Text for the hover status line. External URLs are shown verbatim; the internal
    ``vault:`` scheme as a readable breadcrumb (``vault › path › fragment``); and an
    in-page anchor — same document as *page_uri*, only the ``#fragment`` differs — in
    the SAME breadcrumb scheme, appending the fragment to *page_breadcrumb* (the current
    note's ``vault › note``), so a footnote reads like a wikilink to the same note."""
    if not uri:
        return ""
    if uri.startswith("vault:"):
        # vault › path › fragment — a breadcrumb; an in-doc anchor is just another
        # segment, not a "#…" suffix.
        segments = [s for s in parse_wikilink_url(uri) if s]
        return " › ".join(segments) if segments else uri
    if "#" in uri:
        base, _, frag = uri.partition("#")
        if frag and page_uri and base.rstrip("/") == page_uri.partition("#")[0].rstrip("/"):
            frag = unquote(frag)                        # in-page anchor (e.g. footnote)
            return f"{page_breadcrumb} › {frag}" if page_breadcrumb else "#" + frag
    return uri


class LanguageExtractorPreprocessor(Preprocessor):
    """Extract language from fenced code blocks in markdown source."""

    def __init__(self, md):
        super().__init__(md)
        self.languages = []

    def run(self, lines):
        self.languages = []
        fences = FenceTracker()
        for line in lines:
            fences.feed(line)
            if fences.opened:
                # The language is the first word of the fence's info string.
                m = re.match(r'\w+', fences.info)
                self.languages.append(m.group(0) if m else None)

        # Pass languages to the postprocessor
        if hasattr(self.md, 'lang_postprocessor') and self.md.lang_postprocessor:
            self.md.lang_postprocessor.set_languages(self.languages)

        return lines


class PygmentsCodePostprocessor(Postprocessor):
    """Replace fenced code blocks with Pygments-highlighted HTML."""
    
    PLACEHOLDER_PATTERN = re.compile(r'\x02wzxhzdk:(\d+)\x03')
    LANG_PATTERN = re.compile(r'class="language-(\w+)"')
    # Pattern to match <p> with placeholder
    PARAGRAPH_PLACEHOLDER = re.compile(r'<p>\s*\x02wzxhzdk:(\d+)\x03\s*</p>')
    
    def __init__(self, md):
        super().__init__(md)
        self.formatter = HtmlFormatter(cssclass="codehilite", noclasses=False)
        self._languages = []
    
    def set_languages(self, languages: list[str]) -> None:
        """Store languages extracted by the preprocessor."""
        self._languages = languages
    
    def run(self, text):
        def replace_placeholder(match):
            index = int(match.group(1))
            try:
                stashed_html = self.md.htmlStash.rawHtmlBlocks[index]
            except (IndexError, AttributeError):
                return match.group(0)
            lang = self._languages[index] if index < len(self._languages) else None

            def _add_lang(m):
                if 'data-lang' in m.group(0) or not lang:
                    return m.group(0)
                return m.group(0) + f' data-lang="{lang}"'

            stashed_html = re.sub(
                r'(<div class="[^"]*codehilite[^"]*)"',
                _add_lang,
                stashed_html,
            )
            return stashed_html

        return self.PARAGRAPH_PLACEHOLDER.sub(replace_placeholder, text)


class LanguageExtension(Extension):
    """Extension that adds language extraction via preprocessor."""
    
    def extendMarkdown(self, md):
        lang_preprocessor = LanguageExtractorPreprocessor(md)
        md.preprocessors.register(lang_preprocessor, 'language_extractor', 30)
        md.lang_preprocessor = lang_preprocessor


class CheckboxLinePreprocessor(Preprocessor):
    """Record source-line numbers of checkbox lines before markdown processing.

    Scans the raw markdown source for task-list checkbox patterns while
    tracking fenced-code and 4-space-indented code boundaries so that
    checkbox-like lines inside code blocks are ignored.
    """

    CHECKBOX_RE = re.compile(r'^(>\s*)*(\s*)([-*+]|\d+\.)\s+\[[ xX]\]')
    LIST_MARKER_RE = re.compile(r'^(\s*)([-*+]|\d+\.)\s')

    def _prev_nonblank_is_list(self, lines: list[str], i: int) -> bool:
        j = i - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        return j >= 0 and bool(self.LIST_MARKER_RE.match(lines[j].lstrip()))

    def run(self, lines):
        fences = FenceTracker()
        in_indented_code = False
        checkbox_lines = []
        for i, line in enumerate(lines):
            if fences.feed(line):        # opener/content/closer of a fenced block
                continue

            stripped = line.lstrip()
            is_indented = len(line) - len(stripped) >= 4 and stripped != ''

            if in_indented_code:
                if is_indented or line == '':
                    continue
                in_indented_code = False
            elif is_indented and (i == 0 or lines[i - 1].rstrip() == ''):
                if not self._prev_nonblank_is_list(lines, i):
                    in_indented_code = True
                    continue

            if self.CHECKBOX_RE.match(line):
                checkbox_lines.append(i)
        self.md._checkbox_source_lines = checkbox_lines
        return lines


class CheckboxLinePostprocessor(Postprocessor):
    """Assign data-checkbox-line attributes to rendered checkbox inputs.

    Each ``<input type="checkbox">`` produced by pymdownx.tasklist gets a
    ``data-checkbox-line`` attribute carrying the 0-based source-line
    number recorded by ``CheckboxLinePreprocessor``.
    """

    CHECKBOX_INPUT_RE = re.compile(r'<input\s+type="checkbox"[^>]*/?>')
    TAG_CLOSE_RE = re.compile(r'(/?>)$')

    def run(self, text):
        source_lines = getattr(self.md, '_checkbox_source_lines', [])
        if not source_lines:
            return text

        result_parts = []
        last_end = 0
        idx = 0
        for match in self.CHECKBOX_INPUT_RE.finditer(text):
            tag = match.group(0)
            tag = re.sub(r'\s+disabled\b', '', tag)
            if idx < len(source_lines):
                line_num = source_lines[idx]
                tag = self.TAG_CLOSE_RE.sub(
                    f' data-checkbox-line="{line_num}"\\1', tag,
                )
            result_parts.append(text[last_end:match.start()])
            result_parts.append(tag)
            idx += 1
            last_end = match.end()

        result_parts.append(text[last_end:])
        return ''.join(result_parts)


class CheckboxExtension(Extension):
    """Extension that tracks checkbox source lines via pre+postprocessor."""

    def extendMarkdown(self, md):
        preprocessor = CheckboxLinePreprocessor(md)
        md.preprocessors.register(preprocessor, 'checkbox_line', 40)
        postprocessor = CheckboxLinePostprocessor(md)
        md.postprocessors.register(postprocessor, 'checkbox_line', 25)


class PygmentsCodeExtension(Extension):
    def extendMarkdown(self, md):
        # Run after all other postprocessors (priority > 0 = later)
        postprocessor = PygmentsCodePostprocessor(md)
        md.postprocessors.register(postprocessor, 'pygments_code', 50)
        # Store reference so preprocessor can pass languages
        md.lang_postprocessor = postprocessor
        # Register language extension
        LanguageExtension().extendMarkdown(md)
        # Register checkbox line tracking extension
        CheckboxExtension().extendMarkdown(md)


class BlankLineBeforeListPreprocessor(Preprocessor):
    """Insert the blank line Python-Markdown requires before a list.

    Python-Markdown follows the original Markdown rule that a list does not
    interrupt a paragraph: a list marker directly under a text line is folded
    into that paragraph instead of starting a list. GitHub-flavored Markdown —
    and therefore most AI-generated content — routinely omits that blank line, so
    such lists render as run-on text here. This normalizes the input by inserting
    a single blank line at a paragraph-to-list boundary, before the block parser
    runs.

    Scope is deliberately bounded to top-level lists (indent 0): it never fires
    inside fenced code, inside an existing list, or on a lazy paragraph
    continuation, and it mirrors CommonMark's safeguard that an ordered list only
    interrupts a paragraph when it starts at 1 (so a hard-wrapped "14." stays
    prose). Nested lists after a paragraph are left to the author.
    """

    LIST_MARKER_RE = re.compile(r'^([-*+]|(\d+)\.)\s')
    THEMATIC_BREAK_RE = re.compile(r'^ {0,3}([-*_])( *\1){2,} *$')

    def _interrupts(self, marker) -> bool:
        # Bullets always interrupt a paragraph; ordered lists only when they
        # start at 1 (group 2 is the number, None for bullets).
        number = marker.group(2)
        return number is None or number == '1'

    def run(self, lines):
        out: list[str] = []
        fences = FenceTracker()
        in_list = False
        prev_blank = True                              # start of doc == fresh block
        for line in lines:
            stripped = line.strip()
            if fences.feed(line):                      # opener/content/closer
                if fences.opened:
                    in_list = False
                out.append(line)
                prev_blank = False
                continue
            if stripped == '':
                out.append(line)
                prev_blank = True
                continue
            indent = len(line) - len(line.lstrip())
            marker = (self.LIST_MARKER_RE.match(line)
                      if indent == 0 and not self.THEMATIC_BREAK_RE.match(line)
                      else None)
            if marker:
                if self._interrupts(marker) and not in_list and not prev_blank and out:
                    out.append('')                     # the missing separator
                in_list = True
                out.append(line)
                prev_blank = False
                continue
            # Any other non-blank line. A fresh top-level block (one preceded by a
            # blank line) ends the list; a column-0 lazy continuation does not.
            if indent == 0 and prev_blank:
                in_list = False
            out.append(line)
            prev_blank = False
        return out


class BlankLineBeforeListExtension(Extension):
    """Register :class:`BlankLineBeforeListPreprocessor`."""

    def extendMarkdown(self, md):
        # Priority 35: after checkbox_line (40), which records editor source-line
        # numbers, so those indices still match the un-normalized source; before
        # fenced_code (25), so the preprocessor still sees literal ``` fences.
        md.preprocessors.register(
            BlankLineBeforeListPreprocessor(md), 'blank_line_before_list', 35)


WIKILINK_RE = r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"


class WikilinkInlineProcessor(InlineProcessor):
    """Render [[Page]] and [[Page|Alias]] as clickable links.

    Hrefs use the canonical ``vault:`` scheme
    (``vault:<vault>?path=<relative>#<fragment>``) so the click handler can
    resolve them strictly against vault roots instead of relative to the
    source file's directory.  The vault is always explicit: either taken
    from the link (``Vault>...``) or from *source_vault* (the vault of the
    file being rendered).
    """

    def __init__(self, pattern, md, source_vault: str | None = None):
        super().__init__(pattern, md)
        self._source_vault = source_vault

    def handleMatch(self, m, data):
        page = m.group(1).strip()
        alias = m.group(2)
        if alias:
            alias = alias.strip()
        fragment = ""
        if "#" in page:
            page, _, fragment = page.partition("#")
        if ">" in page:
            vault_name, _, relative = page.partition(">")
        else:
            vault_name = self._source_vault
            relative = page
        el = etree.Element("a")
        el.set("class", "wikilink")
        if vault_name:
            el.set("href", wikilink_url(vault_name, relative, fragment))
        else:
            # No vault available — this is a bug (never emit an empty vault
            # in the URL).  Render a plain link instead so no vault: URI is
            # produced.
            logger.error(
                "No vault context for wikilink %r — source file outside all vaults",
                m.group(0),
            )
            el.set("href", page)
        el.text = alias if alias else page
        return el, m.start(0), m.end(0)


class WikiLinkExtension(Extension):
    """Custom wikilink extension supporting [[Page|Alias]] syntax."""

    def __init__(self, source_vault: str | None = None):
        super().__init__()
        self.source_vault = source_vault

    def extendMarkdown(self, md):
        processor = WikilinkInlineProcessor(WIKILINK_RE, md, self.source_vault)
        md.inlinePatterns.register(processor, "wikilink", 75)


MARKDOWN_EXTENSIONS = [
    BlankLineBeforeListExtension(),
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.toc",
    "markdown.extensions.footnotes",
    WikiLinkExtension(),
    "pymdownx.tilde",
    "pymdownx.mark",
    "pymdownx.caret",
    "pymdownx.tasklist",
    "pymdownx.superfences",
    "pymdownx.magiclink",
    "pymdownx.keys",
    "pymdownx.smartsymbols",
    "pymdownx.emoji",
    "pymdownx.arithmatex",
    PygmentsCodeExtension(),
]


# In-preview find (Ctrl+F): custom highlighting by wrapping matches in <mark>
# elements. The CSS Custom Highlight API works but WebKit does not reliably
# repaint when highlights are removed/changed (stale highlights lingered after
# a 0-match query); DOM mutation always repaints. Driven through
# evaluate_javascript (bypasses the page CSP). Evaluating this returns
# window.__mvfind; append .search(q)/.step(d)/.clear(), each returning a JSON
# string {total, current}.
_FIND_JS = r"""
(function () {
  if (!window.__mvfind) {
    window.__mvfind = {
      marks: [], current: -1,
      clear: function () {
        for (var i = 0; i < this.marks.length; i++) {
          var m = this.marks[i], p = m.parentNode;
          if (p) { p.replaceChild(document.createTextNode(m.textContent), m); p.normalize(); }
        }
        this.marks = []; this.current = -1;
      },
      search: function (q) {
        this.clear();
        if (!q) return JSON.stringify({ total: 0, current: 0 });
        var root = document.querySelector('.markdown-body') || document.body;
        var ql = q.toLowerCase();
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
          acceptNode: function (n) {
            var pn = n.parentNode ? n.parentNode.nodeName : '';
            if (pn === 'SCRIPT' || pn === 'STYLE' || pn === 'MARK') return NodeFilter.FILTER_REJECT;
            return (n.nodeValue && n.nodeValue.toLowerCase().indexOf(ql) >= 0)
              ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
          }
        });
        var nodes = [], n;
        while ((n = walker.nextNode())) nodes.push(n);
        for (var i = 0; i < nodes.length; i++) {
          var node = nodes[i], text = node.nodeValue, lower = text.toLowerCase();
          var frag = document.createDocumentFragment(), last = 0, idx = lower.indexOf(ql);
          while (idx >= 0) {
            if (idx > last) frag.appendChild(document.createTextNode(text.slice(last, idx)));
            var mk = document.createElement('mark');
            mk.className = 'mv-find';
            mk.textContent = text.slice(idx, idx + q.length);
            frag.appendChild(mk); this.marks.push(mk);
            last = idx + q.length; idx = lower.indexOf(ql, last);
          }
          if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
          node.parentNode.replaceChild(frag, node);
        }
        if (this.marks.length) { this.current = 0; this._activate(); }
        return JSON.stringify({ total: this.marks.length, current: this.marks.length ? 1 : 0 });
      },
      _activate: function () {
        for (var i = 0; i < this.marks.length; i++) this.marks[i].classList.remove('mv-find-current');
        var m = this.marks[this.current];
        if (m) { m.classList.add('mv-find-current'); m.scrollIntoView({ block: 'center' }); }
      },
      step: function (d) {
        if (!this.marks.length) return JSON.stringify({ total: 0, current: 0 });
        this.current = (this.current + d + this.marks.length) % this.marks.length;
        this._activate();
        return JSON.stringify({ total: this.marks.length, current: this.current + 1 });
      }
    };
  }
  return window.__mvfind;
})()
"""


# Leading YAML frontmatter (--- … ---). It is shown in the sidebar's "Metadaten"
# tab, so in the preview we keep it in the DOM (hidden via CSS, still debuggable)
# but render the body with the block blanked to an equal number of newlines so
# source-line mapping (checkbox toggles, outline) stays aligned.
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(body_for_render, raw_frontmatter)``."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text, ""
    raw = m.group(0)
    return "\n" * raw.count("\n") + text[m.end():], raw


def _same_page_fragment(uri: str, base_uri: str | None) -> str | None:
    """The fragment of *uri* when it is an in-page anchor (its base equals the
    document's own ``base_uri``), else ``None``. Footnote references/backlinks and
    TOC links resolve against the loaded document's base, so they must scroll the
    view rather than be treated as file/wikilink navigation.

    The fragment is percent-decoded: WebKit hands over an encoded URI, while the
    element id in the document carries the real characters. Without this, every
    anchor beyond ASCII — an umlaut, Greek, CJK — silently found nothing, which
    ASCII-only footnote anchors hid.
    """
    if not base_uri or "#" not in uri:
        return None
    base, _, fragment = uri.partition("#")
    return unquote(fragment) if fragment and base == base_uri else None


class Preview(Gtk.ScrolledWindow):
    """Widget that renders Markdown as styled HTML.

    Signals:
        link-clicked(str, str): Emitted when a wikilink is clicked. The
            arguments are the resolved absolute path to the target ``.md`` file
            and the heading fragment to scroll to (``""`` when none).
    """

    __gsignals__ = {
        "link-clicked": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        # Middle-click / Ctrl+click on a wikilink → open in a NEW tab.
        "link-clicked-new-tab": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "link-not-found": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "checkbox-toggled": (GObject.SignalFlags.RUN_LAST, None, (int, bool)),
        # Right-click "Download Image" on a remote image → (image URL).
        "image-download-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # Emitted when the in-preview search match count becomes available.
        "search-info-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        # Emitted when an in-page anchor jump changes the in-page back/forward
        # availability, so the nav buttons refresh immediately (not just on the
        # next nav action).
        "in-page-nav-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, css_path: str = "") -> None:
        super().__init__()
        self._css_path = css_path
        self._zoom_level: float = 1.0
        self._current_vault_path: str | None = None
        self._current_file: str = ""    # the note being shown (for the hover breadcrumb)
        self._loaded: bool = False
        self._base_uri: str | None = None
        self._csp: str = _build_csp(False)
        self._last_html_hash: str = ""
        self._in_page_back: int = 0     # depth of the in-page anchor back stack
        self._in_page_fwd: int = 0      # depth of the in-page anchor forward stack
        self._last_html: str = ""
        self._active: bool = True
        self._pending_text: str | None = None
        self._pending_base_dir: str = ""
        self._pending_current_file: str = ""
        self._pending_anchor: str = ""  # heading to scroll to once the note has rendered
        self._pending_scroll: float | None = None  # pixel offset to restore on next render
        self._scroll_y: float = 0.0     # last reported scroll offset (kept live by JS)
        self._load_in_progress: bool = False  # full load_html in flight (DOM not ready yet)
        self._web_view = WebKit.WebView()
        self._setup_web_view(self._web_view)

        # Browser-style hover-URL status line: a small label pinned bottom-left, shown
        # only while the pointer is over a link (see _on_mouse_target_changed). Built
        # before the signals so the handler can reference it.
        self._hover_label = Gtk.Label(xalign=0, visible=False)
        self._hover_label.add_css_class("preview-hover-url")
        self._hover_label.set_halign(Gtk.Align.START)
        self._hover_label.set_valign(Gtk.Align.END)
        self._hover_label.set_max_width_chars(90)
        self._hover_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._hover_label.set_can_target(False)        # never intercept clicks / hover

        self._connect_preview_signals()

        overlay = Gtk.Overlay()
        overlay.set_child(self._web_view)
        overlay.add_overlay(self._hover_label)
        self.set_child(overlay)

        # In-preview search (Ctrl+F find bar) — see _FIND_JS. current/total are
        # reported by the injected JS, so the preview counter shows "n/m" too.
        self._search_text: str = ""
        self._search_matches: int = 0
        self._search_current: int = 0

    @staticmethod
    def _setup_web_view(wv: WebKit.WebView) -> None:
        """Configure a freshly created WebView (settings, scripts, handlers).

        Idempotent — subsequent calls on the same WebView are no-ops.
        """
        if getattr(wv, '_setup_done', False):
            return
        wv.set_vexpand(True)
        wv.set_hexpand(True)

        web_settings = wv.get_settings()
        web_settings.set_enable_javascript(True)
        web_settings.set_enable_javascript_markup(False)
        web_settings.set_allow_file_access_from_file_urls(False)

        content_controller = wv.get_user_content_manager()
        content_controller.register_script_message_handler("checkboxHandler")
        content_controller.register_script_message_handler("scrollHandler")

        # Report the scroll offset back to Python so it is always at hand when the
        # note is left (an evaluate_javascript on leave would answer too late —
        # after the content was already swapped). rAF-throttled: a scroll fires on
        # every wheel tick, but only the latest value before leaving is needed.
        scroll_script = WebKit.UserScript.new(
            """
            (function() {
                var ticking = false;
                window.addEventListener('scroll', function() {
                    if (ticking) return;
                    ticking = true;
                    window.requestAnimationFrame(function() {
                        ticking = false;
                        if (window.webkit && window.webkit.messageHandlers
                            && window.webkit.messageHandlers['scrollHandler']) {
                            window.webkit.messageHandlers['scrollHandler']
                                .postMessage({ y: window.scrollY });
                        }
                    });
                }, { passive: true });
            })();
            """,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.END,
        )
        content_controller.add_script(scroll_script)

        checkbox_script = WebKit.UserScript.new(
            """
            (function() {
                document.body.addEventListener('click', function(e) {
                    var el = e.target;
                    var cb = null;
                    if (el && el.type === 'checkbox' && el.dataset.checkboxLine !== undefined) {
                        cb = el;
                    } else if (el && el.closest) {
                        var li = el.closest('.task-list-item');
                        if (li) cb = li.querySelector('input[type="checkbox"]');
                    }
                    if (cb && cb.dataset.checkboxLine !== undefined) {
                        if (cb !== e.target) cb.checked = !cb.checked;
                        var line = parseInt(cb.dataset.checkboxLine);
                        var checked = cb.checked;
                        if (window.webkit && window.webkit.messageHandlers
                            && window.webkit.messageHandlers['checkboxHandler']) {
                            window.webkit.messageHandlers['checkboxHandler']
                                .postMessage({ line: line, checked: checked });
                        }
                    }
                });
            })();
            """,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.END,
        )
        content_controller.add_script(checkbox_script)

        wv._setup_done = True

    def _connect_preview_signals(self) -> None:
        """Connect per-acquire signal handlers and match background."""
        wv = self._web_view
        colors = self._get_theme_colors()
        bg = Gdk.RGBA()
        bg.parse(colors["bg_color"])
        wv.set_background_color(bg)

        wv.connect("decide-policy", self._on_decide_policy)
        wv.connect("context-menu", self._on_context_menu)
        wv.connect("mouse-target-changed", self._on_mouse_target_changed)
        wv.connect("load-changed", self._on_load_changed)

        ctrl = wv.get_user_content_manager()
        ctrl.connect(
            "script-message-received::checkboxHandler",
            self._on_checkbox_clicked,
        )
        ctrl.connect(
            "script-message-received::scrollHandler",
            self._on_scroll_reported,
        )

    def _on_mouse_target_changed(self, _wv, hit_test_result, _modifiers) -> None:
        """Show the hovered link's URL in the bottom-left status line, browser-style;
        hide it when the pointer leaves the link. Native WebKit signal — no JS, no CSP."""
        if hit_test_result.context_is_link():
            uri = hit_test_result.get_link_uri() or ""
            text = _hover_uri_display(uri, self._web_view.get_uri() or "",
                                      self._current_note_breadcrumb())
            self._hover_label.set_text(text)
            self._hover_label.set_visible(bool(text))
        else:
            self._hover_label.set_visible(False)

    def _current_note_breadcrumb(self) -> str:
        """``vault › folder › note`` for the note currently shown, so an in-page anchor
        (footnote) reads like a wikilink to the same note. Empty when the file is
        unknown or lives outside every configured vault."""
        path = self._current_file
        if not path:
            return ""
        vault = find_vault_name_for_path(path)
        root = resolve_vault_path(vault) if vault else None
        if not (vault and root):
            return ""
        try:
            rel = os.path.relpath(path, root)
        except ValueError:                             # different drive on Windows, etc.
            return ""
        stem = rel[:-3] if rel.lower().endswith(".md") else rel
        segments = [p for p in stem.split(os.sep) if p and p != os.curdir]
        return " › ".join([vault, *segments])

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    @zoom_level.setter
    def zoom_level(self, level: float) -> None:
        self._zoom_level = max(0.25, min(5.0, level))
        self._web_view.set_zoom_level(self._zoom_level)

    def reset(self) -> None:
        """Force a full HTML reload on the next ``update_from_text`` call."""
        self._loaded = False
        self._last_html_hash = ""

    def activate(self) -> None:
        """Tab became active -- render pending content if any."""
        self._active = True
        if self._pending_text is not None:
            self.update_from_text(self._pending_text, self._pending_base_dir,
                                  self._pending_current_file)
            self._pending_text = None
            self._pending_base_dir = ""

    def deactivate(self) -> None:
        """Tab became inactive."""
        self._active = False

    def _on_checkbox_clicked(self, _content_manager, jsc_value) -> None:
        """Handle checkbox click from JavaScript via postMessage."""
        try:
            data = json.loads(jsc_value.to_json(0))
            line = data.get("line", -1)
            checked = data.get("checked", False)
            logger.debug("Checkbox toggled: line=%s checked=%s", line, checked)
            GLib.idle_add(self.emit, "checkbox-toggled", line, checked)
        except Exception:
            logger.error("Failed to handle checkbox message from preview", exc_info=True)

    def _on_scroll_reported(self, _content_manager, jsc_value) -> None:
        """Record the preview's live scroll offset (rAF-throttled in JS), so it is
        available synchronously when the note is left — no async query needed."""
        try:
            data = json.loads(jsc_value.to_json(0))
            y = data.get("y")
            if isinstance(y, (int, float)) and not isinstance(y, bool):
                self._scroll_y = float(y)
        except Exception:
            logger.error("Failed to handle scroll message from preview", exc_info=True)

    def preview_scroll_position(self) -> float:
        """The last reported vertical scroll offset — kept current by the scroll
        handler, so it is the reader's position at the moment the note is left."""
        return self._scroll_y

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _emit_link(self, resolved: str, new_tab: bool, fragment: str = "") -> None:
        logger.debug("emit link: %s (fragment=%r, new_tab=%s)",
                     resolved, fragment, new_tab)
        self.emit("link-clicked-new-tab" if new_tab else "link-clicked",
                  resolved, fragment)

    def _run_js(self, js: str) -> None:
        if self._web_view is None:
            return
        GLib.idle_add(
            self._web_view.evaluate_javascript, js, -1, None, None, None, None)

    def _jump_to_anchor(self, fragment: str) -> None:
        """Smooth-scroll to *fragment*, pushing the current position onto the in-page
        back stack (and clearing the forward stack) so the nav buttons can return."""
        frag = json.dumps(fragment)
        self._run_js(
            "window._mvBack=window._mvBack||[];window._mvFwd=[];"
            "window._mvBack.push(window.scrollY);"
            f"var _t=document.getElementById({frag});"
            "if(_t)_t.scrollIntoView({behavior:'smooth',block:'start'});")
        self._in_page_back += 1
        self._in_page_fwd = 0
        self.emit("in-page-nav-changed")

    def scroll_to_anchor(self, heading: str) -> None:
        """Scroll to the *heading* anchor once the note has rendered.

        Used when a cross-note wikilink carries a fragment (``[[Other#Heading]]``):
        the target note is opened in this preview and then scrolled to the heading.
        The jump is deferred until the content is in the DOM — applied now if the
        page is already loaded (the JS itself briefly polls for an in-progress
        render), otherwise flushed on the next ``load-changed`` FINISHED.
        """
        self._pending_anchor = heading
        # Flush now only if the page is fully rendered. While a load_html is in
        # flight the DOM is not ready and the navigation will discard any script
        # we run, so defer to the load-changed FINISHED handler instead. For a
        # jump into content that is only about to be rendered, use
        # :meth:`arm_anchor` — this method would scroll the *previous* note.
        if heading and self._loaded and not self._load_in_progress:
            self._flush_pending_anchor()

    def _flush_pending_anchor(self) -> None:
        """Run the pending anchor scroll (if any) and clear it — one-shot."""
        if self._web_view is None or not self._pending_anchor:
            return
        js = _anchor_scroll_js(self._pending_anchor)
        self._pending_anchor = ""
        if js:
            self._run_js(js)

    def scroll_to_position(self, y: float) -> None:
        """Jump to pixel offset *y* — the position counterpart of
        :meth:`scroll_to_anchor`. Applied now if the page is already rendered
        (returning into an already-open tab), otherwise deferred to the next
        full load's FINISHED. For content only about to be rendered, use
        :meth:`arm_scroll`.
        """
        self._pending_scroll = y
        if self._loaded and not self._load_in_progress:
            self._flush_pending_scroll()

    def _flush_pending_scroll(self) -> None:
        """Run the pending scroll restore (if any) and clear it — one-shot."""
        if self._web_view is None or self._pending_scroll is None:
            return
        y = self._pending_scroll
        self._pending_scroll = None
        self._run_js(_scroll_to_js(y))

    def arm_scroll(self, y: float) -> None:
        """Remember pixel offset *y* for the content **about to** be rendered —
        the position counterpart of :meth:`arm_anchor`. Applied on the next full
        load's FINISHED, when the note it belongs to is actually in the DOM.
        Used when returning (back/forward) into a note that reloads.
        """
        self._pending_scroll = y

    def arm_anchor(self, heading: str) -> None:
        """Remember *heading* for the content that is **about to** be rendered.

        Unlike :meth:`scroll_to_anchor` this never scrolls right away: the target
        does not exist yet. The jump runs when the next **full** load reports
        ``load-changed FINISHED`` — the render that comes last and would discard
        anything scrolled before it. Only arm a jump when a load actually
        follows; where nothing renders (an already-open tab is merely activated)
        use :meth:`scroll_to_anchor`, or the jump is never spent.
        """
        logger.debug("preview: anchor armed %r on %s", heading, self._current_file)
        self._pending_anchor = heading

    def _on_load_changed(self, _web_view, event) -> None:
        """Apply a pending anchor jump once a full page load finishes.

        A newly opened note loads asynchronously via ``load_html``; the target
        heading only exists after FINISHED, so the deferred jump lands here.
        """
        if event == WebKit.LoadEvent.FINISHED:
            self._load_in_progress = False
            self._flush_pending_anchor()
            # A saved pixel position takes precedence over a fragment (it is run
            # last): a reader who entered at #heading and then scrolled on wants
            # to return to where they were, not back to the heading.
            self._flush_pending_scroll()

    def can_go_back_in_page(self) -> bool:
        """True if there is in-page anchor history (footnote/TOC jumps) to unwind."""
        return self._in_page_back > 0

    def can_go_forward_in_page(self) -> bool:
        """True if an unwound in-page anchor jump can be re-applied."""
        return self._in_page_fwd > 0

    def go_back_in_page(self) -> bool:
        """Smooth-scroll back to the position before the last in-page jump. Returns
        True if a step was taken, False if there is no in-page history to unwind."""
        if self._in_page_back <= 0:
            return False
        self._run_js(
            "if(window._mvBack&&window._mvBack.length){"
            "window._mvFwd=window._mvFwd||[];window._mvFwd.push(window.scrollY);"
            "window.scrollTo({top:window._mvBack.pop(),behavior:'smooth'});}")
        self._in_page_back -= 1
        self._in_page_fwd += 1
        return True

    def go_forward_in_page(self) -> bool:
        """Re-apply an unwound in-page anchor jump; True if a step was taken."""
        if self._in_page_fwd <= 0:
            return False
        self._run_js(
            "if(window._mvFwd&&window._mvFwd.length){"
            "window._mvBack=window._mvBack||[];window._mvBack.push(window.scrollY);"
            "window.scrollTo({top:window._mvFwd.pop(),behavior:'smooth'});}")
        self._in_page_fwd -= 1
        self._in_page_back += 1
        return True

    def _reset_in_page_nav(self) -> None:
        """Clear the in-page anchor history — a re-render or note switch makes the
        recorded scroll positions meaningless — and refresh the nav buttons if any
        history actually went away, so they never advertise a stack that is gone."""
        changed = self._in_page_back or self._in_page_fwd
        self._in_page_back = 0
        self._in_page_fwd = 0
        if changed:
            self.emit("in-page-nav-changed")

    def _open_external(self, uri: str) -> None:
        GLib.idle_add(Gtk.show_uri, self.get_root(), uri, Gdk.CURRENT_TIME)

    def _copy_to_clipboard(self, text: str) -> None:
        self.get_clipboard().set(text)

    def _resolve_internal_link(self, uri: str) -> str | None:
        """Resolve a link URI to an existing .md file, or ``None``.

        Mirrors the resolution logic of :meth:`_on_decide_policy` but never
        touches navigation — used to decide what a context-menu entry acts on.
        External links (http/mailto) and unresolvable targets return ``None``.
        """
        if not uri or uri.startswith(("http://", "https://", "mailto:")):
            return None
        if uri.startswith("vault:"):
            return self._resolve_wikilink_page(uri)
        path_str = uri[7:] if uri.startswith("file://") else uri
        return self._resolve_wikilink(unquote(path_str))

    def _ctx_item(self, name: str, label: str, callback) -> "WebKit.ContextMenuItem":
        """Build a context-menu item backed by a throwaway ``Gio.SimpleAction``.

        The action is retained in ``_ctx_actions`` for the lifetime of the menu
        so it is not garbage-collected before the user activates it.
        """
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_: callback())
        self._ctx_actions.append(action)
        return WebKit.ContextMenuItem.new_from_gaction(action, label, None)

    def _on_context_menu(self, _web_view, context_menu, hit_test_result):
        """Replace WebKit's default menu with a curated, app-specific one.

        Browser stock entries (Back/Forward/Reload/Download …) are meaningless
        here, so the menu is rebuilt from scratch per context: internal link,
        external link, text selection, or empty (no menu).
        """
        context_menu.remove_all()
        self._ctx_actions = []  # keep gactions alive while the menu is shown

        if hit_test_result.context_is_link():
            uri = hit_test_result.get_link_uri()
            resolved = self._resolve_internal_link(uri)
            if resolved:
                context_menu.append(
                    self._ctx_item("open-link", "Open",
                                   lambda: self._emit_link(resolved, False)))
                context_menu.append(
                    self._ctx_item("open-link-new-tab", "Open in New Tab",
                                   lambda: self._emit_link(resolved, True)))
                context_menu.append(WebKit.ContextMenuItem.new_separator())
                context_menu.append(
                    self._ctx_item("copy-link", "Copy Link",
                                   lambda: self._copy_to_clipboard(resolved)))
                return False
            if uri and uri.startswith(("http://", "https://", "mailto:")):
                context_menu.append(
                    self._ctx_item("open-browser", "Open in Browser",
                                   lambda: self._open_external(uri)))
                context_menu.append(WebKit.ContextMenuItem.new_separator())
                context_menu.append(
                    self._ctx_item("copy-link", "Copy Link",
                                   lambda: self._copy_to_clipboard(uri)))
                return False
            # A link that resolves to nothing internal — show no menu.
            return True

        if hit_test_result.context_is_image():
            uri = hit_test_result.get_image_uri()
            if uri and uri.startswith(("http://", "https://")):
                context_menu.append(
                    self._ctx_item("download-image", "Download Image",
                                   lambda: self.emit("image-download-requested", uri)))
                return False
            # A local (already downloaded) or data: image — nothing to offer.
            return True

        if hit_test_result.context_is_selection():
            context_menu.append(
                WebKit.ContextMenuItem.new_from_stock_action(
                    WebKit.ContextMenuAction.COPY))
            return False

        # Empty area / plain content — suppress the menu entirely.
        return True

    def _on_decide_policy(self, _web_view, decision, decision_type):
        """Intercept link clicks and resolve wikilinks to .md files.

        External links (http(s)://, mailto:) are opened in the default browser.
        """
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False

        nav_action = decision.get_navigation_action()
        request = nav_action.get_request()
        uri = request.get_uri()
        # Browser-style "open in new tab": middle mouse button or Ctrl+click.
        new_tab = (nav_action.get_mouse_button() == 2
                   or bool(nav_action.get_modifiers() & Gdk.ModifierType.CONTROL_MASK))

        if not uri:
            logger.debug("No URI in navigation request")
            return False

        # Explicitly handle external links - open in default browser
        if uri.startswith(("http://", "https://", "mailto:")):
            decision.ignore()
            GLib.idle_add(Gtk.show_uri, self.get_root(), uri, Gdk.CURRENT_TIME)
            return True

        # Wikilinks use the canonical "vault:" scheme so they are never
        # resolved relative to the source file — only against vault roots.
        if uri.startswith("vault:"):
            logger.debug("Wikilink click: %r", uri)
            resolved = self._resolve_wikilink_page(uri)
            _, _, fragment = parse_wikilink_url(uri)
            # A wikilink to THIS note with a heading anchor behaves like a footnote:
            # smooth-scroll to the anchor and record an in-page nav entry, instead of
            # "reopening" the note (which drops the fragment and does nothing). The
            # anchor is the heading text, so slugify it to match the rendered id.
            if (resolved and fragment and self._current_file
                    and os.path.realpath(resolved) == os.path.realpath(self._current_file)):
                decision.ignore()
                self._jump_to_anchor(_heading_to_slug(fragment))
                return True
            if resolved:
                logger.debug("Wikilink resolved to: %s", resolved)
                decision.ignore()
                # Cross-note link with a heading anchor: open the note and, once
                # it has rendered, scroll to the heading (fragment carried along).
                self._emit_link(resolved, new_tab, fragment)
                return True
            logger.warning("Wikilink NOT resolved: %r", uri)
            decision.ignore()
            self.emit("link-not-found", uri)
            return True

        # In-page anchor (footnote ref/backlink, TOC): smooth-scroll to it and record
        # the departure position on our own in-page history, which the nav buttons
        # unwind before the note history. (WebKit's own load_html history neither
        # scrolls smoothly nor records fragment jumps, so we manage it ourselves.)
        fragment = _same_page_fragment(uri, self._base_uri)
        if fragment:
            decision.ignore()
            self._jump_to_anchor(fragment)
            return True

        # The initial load_html arrives here as a navigation to the document's own
        # base URI (measured: type `other`, button 0) — or to "about:blank" when the
        # note has no directory yet (base_dir "" -> base_uri None). Nothing to
        # resolve in either case, and nothing to log: it was never a link. A real
        # click never matches both halves.
        if (nav_action.get_navigation_type() != WebKit.NavigationType.LINK_CLICKED
                and uri in (self._base_uri, "about:blank")):
            return False

        # Split the fragment off the *URI*, not off the unquoted path: a file whose
        # name really contains "#" arrives as %23 and must survive unquoting.
        # `_same_page_fragment` above only catches anchors within THIS document; a
        # cross-note link keeps its "#Heading" and would otherwise be resolved as
        # part of the file name.
        uri_path, _, link_fragment = uri.partition("#")
        link_fragment = unquote(link_fragment)   # id carries the real characters
        if uri_path.startswith("file://"):
            path_str = uri_path[7:]
        else:
            # Non-file:// URIs — could be a plain relative link.
            path_str = uri_path

        path_str = unquote(path_str)

        logger.debug("Attempting link resolve for: %r", path_str)
        resolved = self._resolve_wikilink(path_str)
        if resolved:
            logger.debug("Link resolved to: %s", resolved)
            decision.ignore()
            # honour middle/Ctrl like the vault: branch, and carry the anchor along
            self._emit_link(resolved, new_tab, link_fragment)
            return True

        logger.warning("Link NOT resolved: %r", path_str)
        decision.ignore()
        self.emit("link-not-found", path_str)
        return True

    def _resolve_wikilink(self, path_str: str) -> str | None:
        """Resolve a plain relative/absolute link target to an existing .md file.

        Only used for non-wikilink links (e.g. ``[text](sub/Page)``), which
        are resolved against the file system.  Wikilinks use the ``vault:``
        scheme and are handled by ``_resolve_wikilink_page`` instead.
        """
        # A directory is not a link target. Without this, `Path` drops the trailing
        # slash and ".md" lands on the *folder* name — so `[text](sub/)` would open
        # the folder note `sub.md` where one exists, instead of reporting a broken
        # link.
        if path_str.endswith("/"):
            return None

        target = Path(path_str)
        name = target.name

        if name.endswith(".md") and target.exists():
            return str(target.resolve())
        # Append, don't replace: `with_suffix` would turn "v1.2" into "v1.md" and
        # "My.Notes" into "My.md" — a hit on an unrelated note.
        with_md = target.parent / (name + ".md")
        if with_md.exists():
            return str(with_md.resolve())
        return None

    def _resolve_wikilink_page(self, uri: str) -> str | None:
        """Strictly resolve a canonical ``vault:`` URI to an existing .md file.

        Root-only semantics — the relative path is always interpreted
        relative to the vault root given in the URL, never relative to the
        source file:

        - ``vault:Vault?path=Page`` → ``<Vault>/Page.md``
        - ``vault:Vault?path=sub/Page`` → ``<Vault>/sub/Page.md``

        The vault in the URL must never be empty — an empty vault is a bug
        and is never fuzzy-resolved.  The fragment (heading anchor) is
        ignored for file resolution.
        """
        if not uri.startswith("vault:"):
            return None
        vault_name, relative, _fragment = parse_wikilink_url(uri)
        if not vault_name:
            logger.error("Empty vault in vault: URI — internal bug: %r", uri)
            return None
        if relative.endswith(".md"):
            relative = relative[:-3]
        if not relative:
            return None
        result = resolve_wikilink(vault_name, relative)
        if result and Path(result).exists():
            return str(Path(result).resolve())
        return None

    def _resolve_source_vault(self, base_dir: str) -> str | None:
        """Return the vault name of the file currently being rendered.

        Prefers the file's own directory (*base_dir*); falls back to
        ``_current_vault_path``.  ``None`` means the file is outside all
        vaults — callers must treat that as a bug (never an empty vault).
        """
        if base_dir:
            vault = find_vault_name_for_path(base_dir)
            if vault:
                return vault
        if self._current_vault_path:
            return find_vault_name_for_path(self._current_vault_path)
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from_text(self, text: str, base_dir: str = "",
                         current_file: str = "") -> None:
        """Render *text* as Markdown and display the result.

        On first call, loads the full HTML template.  On subsequent
        calls, updates only the ``.markdown-body`` innerHTML via JS
        to avoid a full document reload (preserving scroll position
        natively without any capture/restore dance).

        If the tab is inactive, content is buffered and rendered later
        when ``activate()`` is called.
        """
        if self._web_view is None:
            return
        if not self._active:
            self._pending_text = text
            self._pending_base_dir = base_dir
            self._pending_current_file = current_file
            return

        if current_file and current_file != self._current_file:
            # A different note is being shown — the previous note's scroll offset
            # no longer applies. Reset so leaving immediately (before any scroll
            # event) records 0, not the old position; an armed restore updates it
            # again once it scrolls.
            self._scroll_y = 0.0
        self._current_file = current_file
        source_vault = self._resolve_source_vault(base_dir)
        extensions = [
            WikiLinkExtension(source_vault) if isinstance(e, WikiLinkExtension) else e
            for e in MARKDOWN_EXTENSIONS
        ]
        body, frontmatter = _split_frontmatter(text)
        html_content = md.markdown(
            body,
            extensions=extensions,
            extension_configs=EXTENSION_CONFIGS,
        )
        # Convert <script type="math/tex"> tags to native MathML
        mathml_pp = MathMLPostprocessor()
        html_content = mathml_pp.run(html_content)
        # Keep the frontmatter in the DOM (hidden via CSS) so it stays
        # debuggable, without rendering it as literal text.
        if frontmatter:
            escaped = (frontmatter.replace("&", "&amp;")
                       .replace("<", "&lt;").replace(">", "&gt;"))
            html_content = (
                f'<pre class="mv-frontmatter">{escaped}</pre>' + html_content
            )

        html_hash = hashlib.md5(html_content.encode()).hexdigest()
        if html_hash == self._last_html_hash:
            return
        self._last_html_hash = html_hash
        # Content changed — old in-page anchor positions are meaningless now; this
        # also refreshes the nav buttons so they don't keep advertising the stack.
        self._reset_in_page_nav()

        base_uri = GLib.filename_to_uri(base_dir + "/") if base_dir else None

        if not self._loaded:
            # CSP is fixed at load_html (a <meta> in <head>); read the opt-in
            # only here, on full load. Toggling the setting calls reset() so
            # the next full load rebuilds with the new policy.
            self._csp = _build_csp(
                config.settings().get("preview_allow_remote_images", False)
            )
            css_content = self._load_css_content()
            colors = self._get_theme_colors()
            full_html = HTML_TEMPLATE.format(
                csp=self._csp,
                css_content=css_content,
                content=html_content,
                **colors,
            )
            self._last_html = full_html
            self._base_uri = base_uri
            logger.debug("preview: full load (pending anchor=%r)",
                         self._pending_anchor)
            # A pending anchor must wait for load-changed FINISHED, not the
            # synchronous _loaded flag below (the load itself is still async).
            self._load_in_progress = True
            self._web_view.load_html(full_html, base_uri)
            self._loaded = True
        else:
            css_content = self._load_css_content()
            colors = self._get_theme_colors()
            self._last_html = HTML_TEMPLATE.format(
                csp=self._csp, css_content=css_content, content=html_content,
                **colors,
            )
            html_json = json.dumps(html_content, ensure_ascii=False)
            js = (
                'window._mvBack=[];window._mvFwd=[];'   # drop stale anchor positions
                'document.querySelector(".markdown-body").innerHTML '
                f'= {html_json}'
            )
            # An armed anchor is deliberately NOT spent here. Navigating inside a
            # tab reaches this swap first, but the window then rebuilds the stack
            # and calls reset() + refresh_preview(), so a full load follows and
            # discards whatever this scrolled. The jump therefore waits for that
            # load's FINISHED — see _on_load_changed.
            GLib.idle_add(
                self._web_view.evaluate_javascript,
                js, -1, None, None, None, None,
            )

    # ── In-preview search (custom Highlight-API find, see _FIND_JS) ──

    def _run_find(self, call: str, store: bool) -> None:
        """Evaluate ``_FIND_JS`` + *call* in the page; *store* routes the JSON
        result ({total, current}) back into the counter."""
        if self._web_view is None:  # torn down (e.g. tab closed)
            return
        cb = self._on_find_result if store else None
        self._web_view.evaluate_javascript(
            _FIND_JS + call, -1, None, None, None, cb, None,
        )

    def _on_find_result(self, web_view, result, _data) -> None:
        try:
            value = web_view.evaluate_javascript_finish(result)
            data = json.loads(value.to_string()) if value is not None else {}
        except Exception:
            data = {}
        self._search_matches = int(data.get("total", 0))
        self._search_current = int(data.get("current", 0))
        self.emit("search-info-changed")

    def search_set_text(self, text: str) -> None:
        """Search *text* in the rendered page (empty string clears)."""
        self._search_text = text or ""
        if not text:
            self._run_find(".clear()", store=False)
            self._search_matches = 0
            self._search_current = 0
            self.emit("search-info-changed")
            return
        self._run_find(".search(" + json.dumps(text) + ")", store=True)

    def search_next(self) -> None:
        if self._search_text:
            self._run_find(".step(1)", store=True)

    def search_prev(self) -> None:
        if self._search_text:
            self._run_find(".step(-1)", store=True)

    def search_clear(self) -> None:
        self._run_find(".clear()", store=False)
        self._search_text = ""
        self._search_matches = 0
        self._search_current = 0

    def search_info(self) -> tuple[int, int]:
        """Return ``(current, total)`` matches."""
        return (self._search_current, self._search_matches)

    def set_current_vault_path(self, vault_path: str) -> None:
        """Set the current vault path for wikilink resolution."""
        self._current_vault_path = vault_path

    def scroll_to_line(self, line: int, text: str) -> None:
        """Scroll the preview to the heading at the given 0-based *line*.

        Extracts the heading slug from the source *text* and uses
        JavaScript to scroll the matching element into view.
        """
        if self._web_view is None:
            return
        # Find the nearest heading at or before the target line.
        # Track seen slugs to match toc extension's duplicate handling.
        seen: set[str] = set()
        target_slug = None
        for m in HEADING_RE.finditer(text):
            heading_line = text[:m.start()].count("\n")
            heading_text = m.group(2)
            # Compute slug and update counter (matches toc behavior)
            slug = _heading_to_slug(heading_text, seen, unicode=True)
            if heading_line <= line:
                target_slug = slug
            else:
                break
        if not target_slug:
            return
        js = f'document.getElementById("{target_slug}")?.scrollIntoView({{behavior:"smooth",block:"start"}});'
        GLib.idle_add(
            self._web_view.evaluate_javascript,
            js, -1, None, None, None, None,
        )

    @staticmethod
    def _get_theme_colors() -> dict[str, str]:
        """Read current GTK theme colours and return them as CSS colour strings."""
        probe = Gtk.Label()
        ctx = probe.get_style_context()

        ok, fg = ctx.lookup_color("theme_fg_color")
        if not ok:
            fg = Gdk.RGBA()
            fg.parse("#000000")
        ok, bg = ctx.lookup_color("theme_bg_color")
        if not ok:
            bg = Gdk.RGBA()
            bg.parse("#ffffff")

        def _named(name: str, fallback: Gdk.RGBA) -> str:
            ok, c = ctx.lookup_color(name)
            return c.to_string() if ok else fallback.to_string()

        return {
            "bg_color": bg.to_string(),
            "fg_color": fg.to_string(),
            "accent_color": _named("accent_bg_color", fg),
            "dim_color": _named("dim_label_color", fg),
            "card_bg_color": _named("card_bg_color", bg),
            "borders_color": _named("borders_color", fg),
        }

    def update_theme(self) -> None:
        """Update the WebView background and CSS variables to match the current GTK theme."""
        if self._web_view is None:
            return
        colors = self._get_theme_colors()
        bg = Gdk.RGBA()
        bg.parse(colors["bg_color"])
        self._web_view.set_background_color(bg)
        if not self._loaded:
            return
        js = (
            "var s=document.documentElement.style;"
            f's.setProperty("--bg","{colors["bg_color"]}");'
            f's.setProperty("--fg","{colors["fg_color"]}");'
            f's.setProperty("--accent","{colors["accent_color"]}");'
            f's.setProperty("--dim","{colors["dim_color"]}");'
            f's.setProperty("--card-bg","{colors["card_bg_color"]}");'
            f's.setProperty("--borders","{colors["borders_color"]}");'
        )
        GLib.idle_add(
            self._web_view.evaluate_javascript,
            js, -1, None, None, None, None,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Detach and release the WebView to free WebKitGTK child processes.

        Tears the WebView down gracefully so its web process ends via the API
        rather than being killed when the window surface is destroyed — which
        the OS otherwise reports as a WebKitGTK crash. Steps:

        1. ``stop_loading()`` — cancel any in-flight load/JS.
        2. unregister the ``checkboxHandler`` / ``scrollHandler`` message handlers.
        3. detach via the container API (``set_child(None)``) — not
           ``unparent()`` on the child, which leaves a stale child pointer in
           the ScrolledWindow's internal Viewport and triggers
           ``gtk_widget_unparent`` assertions on finalization.
        4. ``terminate_web_process()`` — end the web process deterministically.
        """
        wv = self._web_view
        if wv is None:
            return
        try:
            wv.stop_loading()
        except Exception:
            logger.debug("stop_loading failed during cleanup", exc_info=True)
        try:
            ucm = wv.get_user_content_manager()
            ucm.unregister_script_message_handler("checkboxHandler")
            ucm.unregister_script_message_handler("scrollHandler")
        except Exception:
            logger.debug("unregister handler failed during cleanup", exc_info=True)
        self.set_child(None)
        try:
            wv.terminate_web_process()
        except Exception:
            logger.debug("terminate_web_process failed during cleanup", exc_info=True)
        self._web_view = None

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_html(self, path: str | Path) -> None:
        """Write the last rendered HTML to *path* (overwrites)."""
        try:
            Path(path).write_text(
                self._last_html or "<!-- no content -->",
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to dump preview HTML to %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_css_content(self) -> str:
        """Load the CSS file content for inline embedding."""
        try:
            import importlib.resources

            css_file = importlib.resources.files("markdown_vault").joinpath("css", "style.css")
            return css_file.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Could not load CSS from package", exc_info=True)
            return ""
