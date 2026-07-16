"""Markdown Vault — GtkSourceView-based Markdown editor.

Provides a self-contained editor widget with syntax highlighting,
line numbers, and file I/O.  Each tab gets its own ``Editor`` instance
so that unsaved buffer state is preserved when switching between files.
"""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GObject, GLib, Adw

logger = logging.getLogger(__name__)


class Editor(Gtk.ScrolledWindow):
    """A source-code editor widget specialised for Markdown files.

    Signals:
        file-changed(str): Emitted when a file is opened.
        modified-changed(bool): Emitted when the modified flag toggles.
    """

    __gsignals__ = {
        "file-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "modified-changed": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
        "text-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, base_font_size: int = 14, tab_width: int = 4,
                 wrap_text: bool = True) -> None:
        super().__init__()
        self._file_path: str | None = None
        self._debounce_id: int | None = None
        self._base_font_size: int = base_font_size
        self._zoom_factor: float = 1.0

        self._buffer = GtkSource.Buffer()
        self._buffer.connect("modified-changed", self._on_buffer_modified)
        self._buffer.connect("changed", self._on_buffer_changed)

        lang_manager = GtkSource.LanguageManager.get_default()
        md_lang = lang_manager.get_language("markdown")
        if md_lang:
            self._buffer.set_language(md_lang)

        self._view = GtkSource.View(buffer=self._buffer)
        self._view.set_monospace(True)
        self._view.set_show_line_numbers(True)
        self._view.set_show_line_marks(True)
        self._view.set_auto_indent(True)
        self._view.set_indent_on_tab(True)
        self._view.set_tab_width(tab_width)
        self._view.set_insert_spaces_instead_of_tabs(True)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD if wrap_text else Gtk.WrapMode.NONE)
        self._view.set_left_margin(12)
        self._view.set_right_margin(12)
        self._view.set_top_margin(8)
        self._view.set_bottom_margin(8)
        self._view.add_css_class("editor-view")
        self._css_provider: Gtk.CssProvider | None = None
        self._apply_font_size()

        self.update_color_scheme()

        self.set_child(self._view)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def file_path(self) -> str | None:
        """Absolute path of the currently loaded file, or ``None``."""
        return self._file_path

    def set_file_path(self, new_path: str) -> None:
        """Update the file path without reloading the buffer.

        Called after a file is renamed so that ``save()`` writes to
        the correct location.
        """
        self._file_path = new_path

    @property
    def is_modified(self) -> bool:
        """Whether the buffer has unsaved changes."""
        return self._buffer.get_modified()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_file(self, path: str) -> None:
        """Load *path* into the editor buffer.

        If the file cannot be read, the buffer is cleared and a warning
        is logged.
        """
        self._file_path = path
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to open %s: %s", path, exc)
            text = ""
        self._buffer.begin_irreversible_action()
        self._buffer.set_text(text)
        self._buffer.end_irreversible_action()
        self._buffer.set_modified(False)
        self.emit("file-changed", path)

    def get_text(self) -> str:
        """Return the full buffer content as a string."""
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        return self._buffer.get_text(start, end, True)

    def save(self) -> bool:
        """Write the buffer to ``file_path``.

        Returns ``True`` on success, ``False`` on failure or when no
        file path is set.
        """
        if not self._file_path:
            return False
        try:
            Path(self._file_path).write_text(self.get_text(), encoding="utf-8")
            self._buffer.set_modified(False)
            return True
        except OSError as exc:
            logger.warning("Failed to save %s: %s", self._file_path, exc)
            return False

    def scroll_to_line(self, line: int) -> None:
        """Scroll the view to *line* (0-based) and place the cursor there."""
        _ok, iter = self._buffer.get_iter_at_line(line)
        if _ok:
            self._view.scroll_to_iter(iter, 0.25, True, 0.0, 0.5)
            self._buffer.place_cursor(iter)
            self._view.grab_focus()

    # ── Zoom ────────────────────────────────────────────────────────

    @property
    def zoom_factor(self) -> float:
        return self._zoom_factor

    @zoom_factor.setter
    def zoom_factor(self, factor: float) -> None:
        factor = max(0.25, min(5.0, factor))
        self._zoom_factor = factor
        self._apply_font_size()

    @property
    def base_font_size(self) -> int:
        return self._base_font_size

    @base_font_size.setter
    def base_font_size(self, size: int) -> None:
        self._base_font_size = max(8, min(72, size))
        self._apply_font_size()

    def _apply_font_size(self) -> None:
        size = max(8, int(self._base_font_size * self._zoom_factor))
        css = f".editor-view {{ font-size: {size}px; }}"
        if self._css_provider is not None:
            self._view.get_style_context().remove_provider(self._css_provider)
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(css.encode(), -1)
        self._view.get_style_context().add_provider(
            self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def update_settings(self, font_size: int | None = None,
                        tab_width: int | None = None,
                        wrap_text: bool | None = None) -> None:
        """Apply changed preferences live."""
        if font_size is not None:
            self._base_font_size = font_size
            self._apply_font_size()
        if tab_width is not None:
            self._view.set_tab_width(tab_width)
        if wrap_text is not None:
            self._view.set_wrap_mode(
                Gtk.WrapMode.WORD if wrap_text else Gtk.WrapMode.NONE
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_buffer_modified(self, _buffer: GtkSource.Buffer) -> None:
        self.emit("modified-changed", self.is_modified)

    def _on_buffer_changed(self, _buffer: GtkSource.Buffer) -> None:
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(150, self._emit_text_changed)

    def _emit_text_changed(self) -> bool:
        self._debounce_id = None
        self.emit("text-changed")
        return False

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def update_color_scheme(self) -> None:
        """Switch between ``Adwaita`` and ``Adwaita-dark`` to match the
        current libadwaita colour scheme."""
        scheme_id = "Adwaita-dark" if Adw.StyleManager.get_default().get_dark() else "Adwaita"
        sm = GtkSource.StyleSchemeManager.get_default()
        scheme = sm.get_scheme(scheme_id)
        if scheme:
            self._buffer.set_style_scheme(scheme)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def do_dispose(self) -> None:
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        Gtk.ScrolledWindow.do_dispose(self)
