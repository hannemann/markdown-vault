"""Markdown Vault — WebKitGTK-based Markdown preview renderer.

Converts Markdown text to HTML and displays it inside a ``WebKit.WebView``.
The rendering respects system theme colours via GTK named CSS variables
(``@theme_text_color`` etc.) so that the preview automatically adapts
to light and dark mode.
"""

from pathlib import Path

import markdown as md
import re
from markdown.extensions import Extension
from markdown.postprocessors import Postprocessor
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Adw, WebKit, GObject, Gdk


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
    --bg: {bg_color};
    --fg: {fg_color};
    --accent: {accent_color};
    --dim: {dim_color};
    --card-bg: {card_bg_color};
    --borders: {borders_color};
}}
</style>
<link rel="stylesheet" href="file://{css_path}">
</head>
<body>
<div class="markdown-body">
{content}
</div>
</body>
</html>"""

EXTENSION_CONFIGS = {
    "markdown.extensions.wikilinks": {"base_url": ""},
}


class PygmentsCodePostprocessor(Postprocessor):
    """Replace fenced code blocks with Pygments-highlighted HTML."""
    
    PLACEHOLDER_PATTERN = re.compile(r'\x02wzxhzdk:(\d+)\x03')
    LANG_PATTERN = re.compile(r'class="language-(\w+)"')
    # Pattern to match <p> with placeholder
    PARAGRAPH_PLACEHOLDER = re.compile(r'<p>\s*\x02wzxhzdk:(\d+)\x03\s*</p>')
    
    def __init__(self, md):
        super().__init__(md)
        self.formatter = HtmlFormatter(cssclass="codehilite", noclasses=False)
    
    def run(self, text):
        def replace_placeholder(match):
            index = int(match.group(1))
            # Look up the stashed HTML
            try:
                stashed_html = self.md.htmlStash.rawHtmlBlocks[index]
            except (IndexError, AttributeError):
                return match.group(0)
            
            # Extract language from the stashed HTML
            lang_match = self.LANG_PATTERN.search(stashed_html)
            if not lang_match:
                return match.group(0)
            
            lang = lang_match.group(1)
            
            # Extract code content from stashed HTML
            code_match = re.search(r'<pre><code class="language-\w+">(.*?)</code></pre>', stashed_html, re.DOTALL)
            if not code_match:
                return match.group(0)
            
            code = code_match.group(1)
            # Decode HTML entities
            code = code.replace('<', '<').replace('>', '>').replace('&', '&')
            
            try:
                lexer = get_lexer_by_name(lang, stripall=True)
            except ValueError:
                lexer = get_lexer_by_name('text', stripall=True)
            
            highlighted = highlight(code, lexer, self.formatter)
            # Add data-lang attribute to the div
            highlighted = highlighted.replace('<div class="codehilite">', f'<div class="codehilite" data-lang="{lang}">')
            
            return highlighted
        
        # Replace <p>placeholder</p> with highlighted code
        return self.PARAGRAPH_PLACEHOLDER.sub(replace_placeholder, text)


class PygmentsCodeExtension(Extension):
    def extendMarkdown(self, md):
        # Run after all other postprocessors (priority > 0 = later)
        md.postprocessors.register(PygmentsCodePostprocessor(md), 'pygments_code', 50)


MARKDOWN_EXTENSIONS = [
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.toc",
    "markdown.extensions.wikilinks",
    PygmentsCodeExtension(),
]


class Preview(Gtk.ScrolledWindow):
    """Widget that renders Markdown as styled HTML.

    Args:
        css_path: Filesystem path to the CSS file used for styling.
            When empty, a default location is resolved at render time.
    """

    def __init__(self, css_path: str = "") -> None:
        super().__init__()
        self._css_path = css_path
        self._zoom_level: float = 1.0

        self._web_view = WebKit.WebView()
        self._web_view.set_vexpand(True)
        self._web_view.set_hexpand(True)

        # Match WebView background to the GTK theme.
        colors = self._get_theme_colors()
        bg = Gdk.RGBA()
        bg.parse(colors["bg_color"])
        self._web_view.set_background_color(bg)

        web_settings = self._web_view.get_settings()
        web_settings.set_enable_javascript(True)

        self.set_child(self._web_view)

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_from_text(self, text: str, base_dir: str = "") -> None:
        """Render *text* as Markdown and display the result.

        *base_dir* is used as the base URI for resolving relative
        image paths referenced in the Markdown.
        """
        html_content = md.markdown(
            text,
            extensions=MARKDOWN_EXTENSIONS,
            extension_configs=EXTENSION_CONFIGS,
        )
        css_path = self._resolve_css_path()
        colors = self._get_theme_colors()
        full_html = HTML_TEMPLATE.format(
            css_path=css_path,
            content=html_content,
            **colors,
        )
        base_uri = f"file://{base_dir}/" if base_dir else None
        self._web_view.load_html(full_html, base_uri)

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
        """Update the WebView background to match the current GTK theme."""
        colors = self._get_theme_colors()
        bg = Gdk.RGBA()
        bg.parse(colors["bg_color"])
        self._web_view.set_background_color(bg)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_css_path(self) -> str:
        """Return the absolute path to the stylesheet."""
        if self._css_path:
            return self._css_path
        # Fall back to the installed data directory.
        try:
            import importlib.resources

            return str(importlib.resources.files("data").joinpath("css/style.css"))
        except Exception:
            return ""
