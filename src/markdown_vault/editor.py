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

from gi.repository import Gtk, GtkSource, GObject, GLib, Adw, Gdk, Pango

logger = logging.getLogger(__name__)

# Source-mark category / text-tag name for broken wikilinks.
_BROKEN_CATEGORY = "broken-wikilink"


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
        # Emitted when the in-editor search match count becomes available.
        "search-info-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
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

        # In-editor search (Ctrl+F find bar).
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_wrap_around(True)
        self._search_context = GtkSource.SearchContext.new(
            self._buffer, self._search_settings,
        )
        # Highlight matches ourselves (GtkSource's built-in highlight draws
        # above text tags, so a per-match style would be hidden).  All matches
        # get the yellow tag; the current match an orange tag on top (created
        # last → higher priority).  Tracked via marks so navigation / replace
        # never depend on the text selection.
        self._search_context.set_highlight(False)
        self._search_context.connect(
            "notify::occurrences-count",
            lambda *_: self.emit("search-info-changed"),
        )
        _fg = Gdk.RGBA(); _fg.parse("#000000")
        _match_bg = Gdk.RGBA(); _match_bg.parse("#ffe066")
        self._match_tag = self._buffer.create_tag(
            "search-match", background_rgba=_match_bg, foreground_rgba=_fg,
        )
        _cur_bg = Gdk.RGBA(); _cur_bg.parse("#ff9d3c")
        self._current_match_tag = self._buffer.create_tag(
            "current-search-match", background_rgba=_cur_bg, foreground_rgba=_fg,
        )
        self._match_start_mark = self._buffer.create_mark(
            None, self._buffer.get_start_iter(), True,
        )
        self._match_end_mark = self._buffer.create_mark(
            None, self._buffer.get_start_iter(), False,
        )

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

        self._setup_broken_link_markers()

        self.update_color_scheme()

        self.set_child(self._view)

    def _setup_broken_link_markers(self) -> None:
        """Configure gutter warning marks + red underline for broken links."""
        attrs = GtkSource.MarkAttributes()
        attrs.set_icon_name("dialog-warning-symbolic")
        attrs.connect("query-tooltip-text", lambda _a, _m: "Broken wikilink")
        self._view.set_mark_attributes(_BROKEN_CATEGORY, attrs, 10)
        # LOW sits the underline below the text's ink extents (a visible gap),
        # and unlike the themed ERROR squiggle its colour can be set reliably.
        underline_color = Gdk.RGBA()
        underline_color.parse("rgb(255,64,64)")
        self._broken_tag = self._buffer.create_tag(
            _BROKEN_CATEGORY,
            underline=Pango.Underline.LOW,
            underline_rgba=underline_color,
        )

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
        except (OSError, UnicodeDecodeError) as exc:
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
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to save %s: %s", self._file_path, exc)
            return False

    def set_broken_link_ranges(self, ranges: list[tuple[int, int]]) -> None:
        """Highlight broken wikilinks with gutter marks and a red underline.

        *ranges* is a list of ``(start_offset, end_offset)`` character pairs.
        Passing an empty list clears all existing markers.  Applying tags and
        marks does not modify the text, so the buffer's modified flag is
        untouched.
        """
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        self._buffer.remove_source_marks(start, end, _BROKEN_CATEGORY)
        self._buffer.remove_tag(self._broken_tag, start, end)
        marked_lines: set[int] = set()
        for offset_start, offset_end in ranges:
            si = self._buffer.get_iter_at_offset(offset_start)
            ei = self._buffer.get_iter_at_offset(offset_end)
            self._buffer.apply_tag(self._broken_tag, si, ei)
            # The red underline marks the exact link span; the gutter icon is a
            # per-line indicator.  GtkSourceView only renders a mark's icon when
            # the mark sits at the line start, so anchor it to column 0 (once per
            # line) — otherwise a link with any text before it shows no icon.
            line = si.get_line()
            if line not in marked_lines:
                marked_lines.add(line)
                line_start = si.copy()
                line_start.set_line_offset(0)
                self._buffer.create_source_mark(None, _BROKEN_CATEGORY, line_start)

    def apply_wikilink_fixes(self, fixes: list) -> None:
        """Apply offset-based text replacements to the buffer.

        Each fix carries ``start``/``end`` character offsets and ``new`` text.
        Replacements are applied right-to-left so earlier offsets stay valid,
        wrapped in a single user action so the whole autofix is one undo step.
        """
        if not fixes:
            return
        buf = self._buffer
        # Anchor the cursor on a mark so it rides along with the edits instead
        # of collapsing to a rewritten link's position (R21.18).
        cursor = buf.create_mark(None, buf.get_iter_at_mark(buf.get_insert()), True)
        buf.begin_user_action()
        for fix in sorted(fixes, key=lambda f: f.start, reverse=True):
            si = buf.get_iter_at_offset(fix.start)
            ei = buf.get_iter_at_offset(fix.end)
            buf.delete(si, ei)
            si = buf.get_iter_at_offset(fix.start)
            buf.insert(si, fix.new)
        buf.end_user_action()
        buf.place_cursor(buf.get_iter_at_mark(cursor))
        buf.delete_mark(cursor)

    # ── In-editor search ────────────────────────────────────────────

    def _selection_iters(self) -> tuple:
        """Return the current selection as ``(lo, hi)`` iters (cursor if none)."""
        buf = self._buffer
        a = buf.get_iter_at_mark(buf.get_insert())
        b = buf.get_iter_at_mark(buf.get_selection_bound())
        return (a, b) if a.compare(b) <= 0 else (b, a)

    def search_set_text(self, text: str) -> None:
        """Set the search term and mark the first match at/after the current
        match, so typing tightens the match in place instead of walking to the
        next one (R21.9).  Empty text clears highlighting."""
        self._search_settings.set_search_text(text or None)
        if text:
            lo, _hi = self._current_match_iters()
            found, ms, me, _wrapped = self._search_context.forward(lo)
            if found:
                self._select_match(ms, me)
            self._refresh_match_highlights()
        else:
            self._clear_current_match()

    def search_clear(self) -> None:
        """Clear the search term and its match highlighting."""
        self._search_settings.set_search_text(None)
        self._clear_current_match()

    def _current_match_iters(self) -> tuple:
        """Return the current match as ``(start, end)`` iters (both at the buffer
        start when there is no current match)."""
        buf = self._buffer
        return (
            buf.get_iter_at_mark(self._match_start_mark),
            buf.get_iter_at_mark(self._match_end_mark),
        )

    def _clear_current_match(self) -> None:
        buf = self._buffer
        lo, hi = buf.get_bounds()
        buf.remove_tag(self._match_tag, lo, hi)
        buf.remove_tag(self._current_match_tag, lo, hi)
        buf.move_mark(self._match_start_mark, buf.get_start_iter())
        buf.move_mark(self._match_end_mark, buf.get_start_iter())

    def _refresh_match_highlights(self) -> None:
        """Tag every occurrence, then re-apply the current-match tag on top."""
        buf = self._buffer
        lo, hi = buf.get_bounds()
        buf.remove_tag(self._match_tag, lo, hi)
        if not self._search_settings.get_search_text():
            return
        wrap = self._search_settings.get_wrap_around()
        self._search_settings.set_wrap_around(False)
        it = buf.get_start_iter()
        while True:
            found, ms, me, _w = self._search_context.forward(it)
            if not found or me.compare(it) <= 0:
                break
            buf.apply_tag(self._match_tag, ms, me)
            it = me
        self._search_settings.set_wrap_around(wrap)
        self._apply_current_highlight()

    def _apply_current_highlight(self) -> None:
        buf = self._buffer
        lo, hi = buf.get_bounds()
        buf.remove_tag(self._current_match_tag, lo, hi)
        s, e = self._current_match_iters()
        if s.compare(e) != 0:
            buf.apply_tag(self._current_match_tag, s, e)

    def _select_match(self, match_start, match_end) -> None:
        """Mark *match* as the current one and give it the strong highlight."""
        buf = self._buffer
        buf.move_mark(self._match_start_mark, match_start)
        buf.move_mark(self._match_end_mark, match_end)
        buf.place_cursor(match_end)  # no selection — tags mark the matches
        self._apply_current_highlight()
        self._view.scroll_to_iter(match_start, 0.2, False, 0.0, 0.5)

    def search_next(self) -> bool:
        """Mark the next match after the current one."""
        _lo, hi = self._current_match_iters()
        found, ms, me, _wrapped = self._search_context.forward(hi)
        if found:
            self._select_match(ms, me)
        return found

    def search_prev(self) -> bool:
        """Mark the previous match before the current one."""
        lo, _hi = self._current_match_iters()
        found, ms, me, _wrapped = self._search_context.backward(lo)
        if found:
            self._select_match(ms, me)
        return found

    def search_info(self) -> tuple[int, int]:
        """Return ``(current_1based, total)`` matches; ``current`` may be 0.

        ``total`` is ``-1`` while the count is still being computed
        (``search-info-changed`` fires when it is ready).
        """
        total = self._search_context.get_occurrences_count()
        lo, hi = self._current_match_iters()
        pos = self._search_context.get_occurrence_position(lo, hi)
        return (max(pos, 0), total)

    def set_search_options(self, case_sensitive: bool, whole_word: bool,
                           regex: bool) -> None:
        """Configure the in-editor search (case / whole-word / regex)."""
        self._search_settings.set_case_sensitive(case_sensitive)
        self._search_settings.set_at_word_boundaries(whole_word)
        self._search_settings.set_regex_enabled(regex)

    def replace_current(self, replacement: str) -> bool:
        """Replace the current match, then advance to the next."""
        start, end = self._current_match_iters()
        if start.compare(end) == 0:
            return self.search_next()  # no current match — go to one first
        try:
            replaced = self._search_context.replace(start, end, replacement, -1)
        except GLib.Error:
            return False
        if replaced:
            self.search_next()
            self._refresh_match_highlights()
        return replaced

    def replace_all(self, replacement: str) -> int:
        """Replace every match; returns the number replaced."""
        try:
            count = self._search_context.replace_all(replacement, -1)
        except GLib.Error:
            return 0
        self._refresh_match_highlights()
        return count

    def scroll_to_line(self, line: int, yalign: float = 0.5) -> None:
        """Scroll the view to *line* (0-based) and place the cursor there.

        *yalign* positions the line vertically: ``0.0`` puts it at the top
        (matching the preview's heading jump), ``0.5`` centres it (used for
        search matches so surrounding context stays visible).
        """
        _ok, iter = self._buffer.get_iter_at_line(line)
        if _ok:
            margin = 0.0 if yalign <= 0.0 else 0.25
            self._view.scroll_to_iter(iter, margin, True, 0.0, yalign)
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

    def cleanup(self) -> None:
        """Release pending debounce timer."""
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
