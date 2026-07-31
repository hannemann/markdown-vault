"""Markdown Vault — WebKitGTK-based Markdown preview renderer.

Converts Markdown text to HTML and displays it inside a ``WebKit.WebView``.
The rendering respects system theme colours via GTK named CSS variables
(``@theme_text_color`` etc.) so that the preview automatically adapts
to light and dark mode.
"""

import os
from pathlib import Path

import hashlib
import json
import logging
import markdown as md
import re
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor
import xml.etree.ElementTree as etree
from pygments.formatters import HtmlFormatter
from urllib.parse import unquote
from markdown_vault.latex_mathml import MathMLPostprocessor
from markdown_vault.path_utils import (
    HEADING_RE,
    find_vault_name_for_path,
    resolve_vault_path,
    resolve_wikilink,
)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, WebKit, GObject, Gdk, GLib


import unicodedata

logger = logging.getLogger(__name__)


def _heading_to_slug(heading: str, seen: dict[str, int] | None = None,
                     unicode: bool = True) -> str:
    """Convert a heading text to a slug matching the toc extension's output.

    The toc extension lowercases, removes punctuation, replaces spaces with
    hyphens, and appends _1, _2 for duplicates.
    """
    value = unicodedata.normalize("NFKD", heading)
    if not unicode:
        value = re.sub(r"[^\x00-\x7F]", "", value)
    value = re.sub(r"[^\w\s-]", "", value).strip()
    value = re.sub(r"[-\s]+", "-", value)
    base_slug = value.lower()

    if seen is not None:
        count = seen.get(base_slug, 0)
        if count > 0:
            slug = f"{base_slug}_{count}"
        else:
            slug = base_slug
        seen[base_slug] = count + 1
        return slug

    return base_slug


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
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
}


class LanguageExtractorPreprocessor(Preprocessor):
    """Extract language from fenced code blocks in markdown source."""
    
    FENCE_RE = re.compile(r'^(\s*)(`{3,}|~{3,})\s*(\w+)?')
    
    def __init__(self, md):
        super().__init__(md)
        self.languages = []
    
    def run(self, lines):
        self.languages = []
        new_lines = []
        in_code_block = False
        fence_chars = None
        for line in lines:
            match = self.FENCE_RE.match(line)
            if match:
                # Check if this is an opening or closing fence
                if not in_code_block:
                    # Opening fence
                    lang = match.group(3) or None
                    self.languages.append(lang)
                    in_code_block = True
                    fence_chars = match.group(2)[0]  # ` or ~
                elif line.strip().startswith(fence_chars * 3):
                    # Closing fence (same char, at least 3)
                    in_code_block = False
                    fence_chars = None
            new_lines.append(line)
        
        # Pass languages to the postprocessor
        if hasattr(self.md, 'lang_postprocessor') and self.md.lang_postprocessor:
            self.md.lang_postprocessor.set_languages(self.languages)
        
        return new_lines


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
    FENCE_OPEN_RE = re.compile(r'^(`{3,}|~{3,})(.*)$')
    LIST_MARKER_RE = re.compile(r'^(\s*)([-*+]|\d+\.)\s')

    def _prev_nonblank_is_list(self, lines: list[str], i: int) -> bool:
        j = i - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        return j >= 0 and bool(self.LIST_MARKER_RE.match(lines[j].lstrip()))

    def run(self, lines):
        in_fence = False
        fence_char = None
        in_indented_code = False
        checkbox_lines = []
        for i, line in enumerate(lines):
            if in_fence:
                if line.rstrip().startswith(fence_char):
                    in_fence = False
                continue
            m = self.FENCE_OPEN_RE.match(line.rstrip())
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
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


WIKILINK_RE = r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"


class WikilinkInlineProcessor(InlineProcessor):
    """Render [[Page]] and [[Page|Alias]] as clickable links."""

    def handleMatch(self, m, data):
        page = m.group(1).strip()
        alias = m.group(2)
        if alias:
            alias = alias.strip()
        el = etree.Element("a")
        el.set("class", "wikilink")
        el.set("href", page)
        el.text = alias if alias else page
        return el, m.start(0), m.end(0)


class WikiLinkExtension(Extension):
    """Custom wikilink extension supporting [[Page|Alias]] syntax."""

    def extendMarkdown(self, md):
        processor = WikilinkInlineProcessor(WIKILINK_RE, md)
        md.inlinePatterns.register(processor, "wikilink", 75)


MARKDOWN_EXTENSIONS = [
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.toc",
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


class Preview(Gtk.ScrolledWindow):
    """Widget that renders Markdown as styled HTML.

    Signals:
        link-clicked(str): Emitted when a wikilink is clicked. The argument
            is the resolved absolute path to the target ``.md`` file.
    """

    __gsignals__ = {
        "link-clicked": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "link-not-found": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "checkbox-toggled": (GObject.SignalFlags.RUN_LAST, None, (int, bool)),
    }

    def __init__(self, css_path: str = "") -> None:
        super().__init__()
        self._css_path = css_path
        self._zoom_level: float = 1.0
        self._vault_paths: list[str] = []
        self._vault_names: list[str] = []
        self._current_vault_path: str | None = None
        self._loaded: bool = False
        self._base_uri: str | None = None
        self._last_html_hash: str = ""
        self._last_html: str = ""
        self._active: bool = True
        self._pending_text: str | None = None
        self._pending_base_dir: str = ""
        self._web_view = WebKit.WebView()
        self._setup_web_view(self._web_view)
        self._connect_preview_signals()
        self.set_child(self._web_view)

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

        ctrl = wv.get_user_content_manager()
        ctrl.connect(
            "script-message-received::checkboxHandler",
            self._on_checkbox_clicked,
        )

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
            self.update_from_text(self._pending_text, self._pending_base_dir)
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

    # ------------------------------------------------------------------
    # Vault paths (for wikilink resolution)
    # ------------------------------------------------------------------

    def set_vault_paths(self, paths: list[str]) -> None:
        """Set the vault root directories used to resolve wikilinks."""
        self._vault_paths = list(paths)

    def set_vault_names(self, names: list[str]) -> None:
        """Set the vault names (from vaults.yaml) for resolution."""
        self._vault_names = list(names)

    def _resolve_vault_name(self, vault_name: str) -> str | None:
        """Try to match a vault name to a vault path."""
        if not vault_name:
            return None
        # Try exact match first (vault name == vault path)
        if vault_name in self._vault_paths:
            return vault_name
        # Try matching by vault name
        if self._vault_names:
            try:
                idx = self._vault_names.index(vault_name)
                if idx < len(self._vault_paths):
                    return self._vault_paths[idx]
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_decide_policy(self, _web_view, decision, decision_type):
        """Intercept link clicks and resolve wikilinks to .md files.

        External links (http(s)://, mailto:) are opened in the default browser.
        """
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False

        nav_action = decision.get_navigation_action()
        request = nav_action.get_request()
        uri = request.get_uri()

        if not uri:
            logger.debug("No URI in navigation request")
            return False

        logger.debug("Navigation request URI (raw): %r", uri)
        logger.debug("Navigation request URI (hex): %r", uri.encode().hex())

        # Explicitly handle external links - open in default browser
        if uri.startswith(("http://", "https://", "mailto:")):
            decision.ignore()
            GLib.idle_add(Gtk.show_uri, self.get_root(), uri, Gdk.CURRENT_TIME)
            return True

        # Extract the path string from the URI
        if uri.startswith("file://"):
            path_str = uri[7:]
        else:
            # Non-file:// URIs — could be a wikilink (e.g., "vault::Page")
            path_str = uri

        path_str = unquote(path_str)

        logger.debug("Attempting wikilink resolve for: %r", path_str)
        resolved = self._resolve_wikilink(path_str)
        if resolved:
            logger.debug("Wikilink resolved to: %s", resolved)
            decision.ignore()
            self.emit("link-clicked", resolved)
            return True

        # Only show error dialog if this looks like a wikilink click
        # (not a directory URI like the initial load)
        if not path_str.endswith("/"):
            logger.warning("Wikilink NOT resolved: %r", path_str)
            decision.ignore()
            self.emit("link-not-found", path_str)
            return True

        return False

    def _resolve_wikilink(self, path_str: str) -> str | None:
        """Resolve a link target to an existing .md file.

        Handles three cases:
        1. Absolute file paths that exist on disk
        2. Vault-prefixed wikilinks (``VaultName>sub/Page``) — resolved via
           the cached vaults config (SSOT: vaults.yaml)
        3. Same-vault wikilinks (``sub/Page``) — resolved relative to the
           vault that contains the source file
        """
        logger.debug("_resolve_wikilink path_str=%r", path_str)
        target = Path(path_str)
        name = target.name

        # Case 1: Direct file path (with or without .md extension)
        if name.endswith(".md") and target.exists():
            return str(target.resolve())
        with_md = target.with_suffix(".md")
        if with_md.exists():
            return str(with_md.resolve())

        # Determine the raw wikilink stem from the URI.
        raw = name if not name.endswith(".md") else target.stem
        if ">" in raw or ">" in str(target.parent):
            # Case 2: Vault-prefixed wikilink.
            # The '>' may be in the filename (raw) or in a parent directory
            # because the WebView resolved the relative URL against the
            # current file's directory.
            if ">" in raw:
                vault_name, _, stem = raw.partition(">")
            else:
                # Extract vault+stem from the parent path.
                # parent is like "…/Vault-B/Vault-A>sub"
                parent_stem = target.parent.name  # "Vault-A>sub"
                if ">" in parent_stem:
                    vault_name, _, parent_suffix = parent_stem.partition(">")
                    stem = parent_suffix + os.sep + name
                else:
                    return None
            result = resolve_wikilink(vault_name, stem)
            if result and Path(result).exists():
                return str(Path(result).resolve())
            return None

        # Case 3: Same-vault wikilink (no vault prefix)
        if self._current_vault_path:
            vault_name = find_vault_name_for_path(self._current_vault_path)
            if vault_name is None:
                return None
            result = resolve_wikilink(vault_name, raw)
            if result and Path(result).exists():
                return str(Path(result).resolve())
            return None

        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from_text(self, text: str, base_dir: str = "") -> None:
        """Render *text* as Markdown and display the result.

        On first call, loads the full HTML template.  On subsequent
        calls, updates only the ``.markdown-body`` innerHTML via JS
        to avoid a full document reload (preserving scroll position
        natively without any capture/restore dance).

        If the tab is inactive, content is buffered and rendered later
        when ``activate()`` is called.
        """
        if not self._active:
            self._pending_text = text
            self._pending_base_dir = base_dir
            return

        html_content = md.markdown(
            text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        # Convert <script type="math/tex"> tags to native MathML
        mathml_pp = MathMLPostprocessor()
        html_content = mathml_pp.run(html_content)

        html_hash = hashlib.md5(html_content.encode()).hexdigest()
        if html_hash == self._last_html_hash:
            return
        self._last_html_hash = html_hash

        base_uri = GLib.filename_to_uri(base_dir + "/") if base_dir else None

        if not self._loaded:
            css_content = self._load_css_content()
            colors = self._get_theme_colors()
            full_html = HTML_TEMPLATE.format(
                css_content=css_content,
                content=html_content,
                **colors,
            )
            self._last_html = full_html
            self._base_uri = base_uri
            self._web_view.load_html(full_html, base_uri)
            self._loaded = True
        else:
            css_content = self._load_css_content()
            colors = self._get_theme_colors()
            self._last_html = HTML_TEMPLATE.format(
                css_content=css_content, content=html_content, **colors,
            )
            html_json = json.dumps(html_content, ensure_ascii=False)
            js = (
                'document.querySelector(".markdown-body").innerHTML '
                f'= {html_json}'
            )
            GLib.idle_add(
                self._web_view.evaluate_javascript,
                js, -1, None, None, None, None,
            )

    def set_current_vault_path(self, vault_path: str) -> None:
        """Set the current vault path for wikilink resolution."""
        self._current_vault_path = vault_path

    def scroll_to_line(self, line: int, text: str) -> None:
        """Scroll the preview to the heading at the given 0-based *line*.

        Extracts the heading slug from the source *text* and uses
        JavaScript to scroll the matching element into view.
        """
        # Find the nearest heading at or before the target line.
        # Track seen slugs to match toc extension's duplicate handling.
        seen: dict[str, int] = {}
        target_slug = None
        for m in HEADING_RE.finditer(text):
            heading_line = text[:m.start()].count("\n")
            heading_text = m.group(2)
            # Compute slug and update counter (matches toc behavior)
            slug = _heading_to_slug(heading_text, seen, unicode=False)
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
        """Explicitly unparent WebView to release WebKitGTK child processes."""
        if self._web_view:
            self._web_view.unparent()
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
