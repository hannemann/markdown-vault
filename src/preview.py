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
from markdown.preprocessors import Preprocessor
from markdown.treeprocessors import Treeprocessor
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter
from src.latex_mathml import MathMLPostprocessor
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
:root {{ --bg: {bg_color}; --fg: {fg_color}; --accent: {accent_color}; --dim: {dim_color}; --card-bg: {card_bg_color}; --borders: {borders_color}; }}
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
    "pymdownx.superfences": {
        "css_class": "codehilite",
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
                    lang = match.group(3) or 'text'
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


class LanguageTreeprocessor(Treeprocessor):
    """Treeprocessor to add data-lang attribute to codehilite divs based on code element's language class."""
    
    def run(self, root):
        # Find all div elements with class containing "codehilite"
        for div in root.iter('div'):
            classes = div.get('class', '').split()
            if 'codehilite' in classes:
                print(f'  Found codehilite div: {div.attrib}')
                # Skip if already has data-lang
                if 'data-lang' in div.attrib:
                    continue
                # Find the code element inside pre/code
                code_elem = div.find('.//code')
                lang = None
                if code_elem is not None:
                    code_classes = code_elem.get('class', '').split()
                    for cls in code_classes:
                        if cls.startswith('language-'):
                            lang = cls[9:]  # Remove 'language-' prefix
                            break
                # Fallback: check div's own classes for language-* prefix
                if lang is None:
                    for cls in classes:
                        if cls.startswith('language-'):
                            lang = cls[9:]
                            break
                if lang:
                    div.set('data-lang', lang)
        return root


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
            # Look up the stashed HTML
            try:
                stashed_html = self.md.htmlStash.rawHtmlBlocks[index]
            except (IndexError, AttributeError):
                return match.group(0)
            
            # Get language from preprocessor's list (same index as stash)
            lang = self._languages[index] if index < len(self._languages) else 'text'
            
            # The stashed HTML already has Pygments-highlighted code with <div class="codehilite">
            # Just add data-lang attribute to the codehilite div
            import re
            stashed_html = re.sub(r'(<div class="[^"]*codehilite[^"]*)"',
                                  lambda m: m.group(0) + f' data-lang="{lang}"' if 'data-lang' not in m.group(0) else m.group(0),
                                  stashed_html)
            return stashed_html
        
        # Replace <p>placeholder</p> with highlighted code from stash
        return self.PARAGRAPH_PLACEHOLDER.sub(replace_placeholder, text)


class LanguageExtension(Extension):
    """Extension that adds language extraction and data-lang attribute."""
    
    def extendMarkdown(self, md):
        # Preprocessor to extract languages - run BEFORE superfences (priority 30 > 25)
        lang_preprocessor = LanguageExtractorPreprocessor(md)
        md.preprocessors.register(lang_preprocessor, 'language_extractor', 30)
        
        # Treeprocessor to add data-lang attributes (runs after HTML tree is built)
        md.treeprocessors.register(LanguageTreeprocessor(md), 'language_data', 10)
        
        # Pass languages from preprocessor to treeprocessor
        md.lang_preprocessor = lang_preprocessor


class PygmentsCodeExtension(Extension):
    def extendMarkdown(self, md):
        # Run after all other postprocessors (priority > 0 = later)
        postprocessor = PygmentsCodePostprocessor(md)
        md.postprocessors.register(postprocessor, 'pygments_code', 50)
        # Store reference so preprocessor can pass languages
        md.lang_postprocessor = postprocessor
        # Register language extension
        LanguageExtension().extendMarkdown(md)


MARKDOWN_EXTENSIONS = [
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.toc",
    "markdown.extensions.wikilinks",
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
    "pymdownx.tasklist",
    "pymdownx.superfences",
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
    }

    def __init__(self, css_path: str = "") -> None:
        super().__init__()
        self._css_path = css_path
        self._zoom_level: float = 1.0
        self._vault_paths: list[str] = []

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
        web_settings.set_allow_file_access_from_file_urls(True)

        self._web_view.connect("decide-policy", self._on_decide_policy)

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
    # Vault paths (for wikilink resolution)
    # ------------------------------------------------------------------

    def set_vault_paths(self, paths: list[str]) -> None:
        """Set the vault root directories used to resolve wikilinks."""
        self._vault_paths = list(paths)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_decide_policy(self, _web_view, decision, decision_type):
        """Intercept link clicks and resolve wikilinks to .md files."""
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False

        nav_action = decision.get_navigation_action()
        request = nav_action.get_request()
        uri = request.get_uri()

        # Only handle file:// and relative links
        if not uri:
            return False

        # Strip file:// prefix if present
        path_str = uri
        if path_str.startswith("file://"):
            path_str = path_str[7:]

        # Try to resolve as a wikilink (with or without .md)
        resolved = self._resolve_wikilink(path_str)
        if resolved:
            decision.ignore()
            self.emit("link-clicked", resolved)
            return True

        return False

    def _resolve_wikilink(self, path_str: str) -> str | None:
        """Resolve a link target to an existing .md file."""
        from pathlib import Path as _P

        target = _P(path_str)
        name = target.name

        # If it already has .md and exists, use it
        if name.endswith(".md") and target.exists():
            return str(target.resolve())

        # Try with .md extension in same directory
        with_md = target.with_suffix(".md")
        if with_md.exists():
            return str(with_md.resolve())

        # Search in vault roots
        stem = target.stem if name.endswith(".md") else name
        for vp in self._vault_paths:
            vault = _P(vp)
            candidate = vault / f"{stem}.md"
            if candidate.exists():
                return str(candidate.resolve())
            # Also check subdirectories
            for md_file in vault.rglob("*.md"):
                if md_file.stem == stem:
                    return str(md_file.resolve())

        return None

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
        # Convert <script type="math/tex"> tags to native MathML
        mathml_pp = MathMLPostprocessor()
        html_content = mathml_pp.run(html_content)

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
