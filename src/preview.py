"""Markdown Vault — WebKitGTK-based Markdown preview renderer.

Converts Markdown text to HTML and displays it inside a ``WebKit.WebView``.
The rendering respects system theme colours via GTK named CSS variables
(``@theme_text_color`` etc.) so that the preview automatically adapts
to light and dark mode.
"""

from pathlib import Path

import markdown as md
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, WebKit, GObject


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="file://{css_path}">
</head>
<body>
<div class="markdown-body">
{content}
</div>
</body>
</html>"""

MARKDOWN_EXTENSIONS = [
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.toc",
    "markdown.extensions.wikilinks",
]

EXTENSION_CONFIGS = {
    "markdown.extensions.wikilinks": {"base_url": ""},
}


class Preview(Gtk.ScrolledWindow):
    """Widget that renders Markdown as styled HTML.

    Args:
        css_path: Filesystem path to the CSS file used for styling.
            When empty, a default location is resolved at render time.
    """

    def __init__(self, css_path: str = "") -> None:
        super().__init__()
        self._css_path = css_path

        self._web_view = WebKit.WebView()
        self._web_view.set_vexpand(True)
        self._web_view.set_hexpand(True)

        web_settings = self._web_view.get_settings()
        web_settings.set_enable_javascript(False)

        self.set_child(self._web_view)

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
        full_html = HTML_TEMPLATE.format(css_path=css_path, content=html_content)
        base_uri = f"file://{base_dir}/" if base_dir else None
        self._web_view.load_html(full_html, base_uri)

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
