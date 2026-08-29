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

from gi.repository import Gtk, GtkSource, GObject, GLib, Gio, Adw, Gdk, Pango

from markdown_vault.core.i18n import _

logger = logging.getLogger(__name__)

# Source-mark category / text-tag name for broken wikilinks.
_BROKEN_CATEGORY = "broken-wikilink"
# Image-link gutter categories: a broken target, and a local image outside the
# attachments tree that can be adopted (double-click) into it.
_IMG_BROKEN_CATEGORY = "broken-image"
_IMG_ADOPT_CATEGORY = "adoptable-image"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
                   ".ico", ".avif"}


def _clamp_scroll(value: float, upper: float, page_size: float) -> float:
    """The largest in-range scroll offset: never past the last page, never below
    zero. This is the clamp reload_editor relies on — it catches a return into a
    note that has since become *shorter*, where the raw value would scroll into
    the void."""
    return min(value, max(0.0, upper - page_size))


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
        # An image was saved into the note's attachments dir — refresh the tree.
        "attachment-added": (GObject.SignalFlags.RUN_LAST, None, ()),
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
        self._setup_image_link_markers()

        self.update_color_scheme()

        self.set_child(self._view)

        self._setup_image_input()

    # ------------------------------------------------------------------
    # Image input — paste / drag-drop an image into the note
    # ------------------------------------------------------------------

    def _setup_image_input(self) -> None:
        """Accept an image via Ctrl+V (clipboard) or a file/texture drop, saving it
        into the note's attachments dir and inserting a link — so the user never
        has to touch the app-managed attachments tree."""
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key_pressed_for_paste)
        self._view.add_controller(key)

        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.Texture, Gdk.FileList])
        drop.connect("drop", self._on_image_drop)
        self._view.add_controller(drop)

        # Right-click menu: a working image paste (the built-in "Paste" greys out
        # when the clipboard holds an image, not text) plus the file picker.
        extra = Gio.Menu()
        extra.append(_("Paste Image"), "win.paste-image")
        extra.append(_("Insert Image…"), "win.insert-image")
        self._view.set_extra_menu(extra)

    def _on_key_pressed_for_paste(self, _ctrl, keyval, _keycode, state):
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK:
            if self.paste_image_from_clipboard():
                return True     # consume: we handled the image, not a text paste
        return False            # anything else falls through to the normal paste

    def paste_image_from_clipboard(self) -> bool:
        """Paste an image from the clipboard into the note if one is present.
        Returns ``True`` if an image was found and handled (async)."""
        clipboard = self._view.get_clipboard()
        if clipboard.get_formats().contain_gtype(Gdk.Texture):
            clipboard.read_texture_async(None, self._on_clipboard_texture)
            return True
        return False

    def _on_clipboard_texture(self, clipboard, result) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as exc:
            logger.warning("paste image: %s", exc)
            return
        if texture is not None:
            self.insert_image(texture.save_to_png_bytes().get_data(), "pasted.png")

    def _on_image_drop(self, _target, value, _x, _y) -> bool:
        if isinstance(value, Gdk.Texture):
            self.insert_image(value.save_to_png_bytes().get_data(), "dropped.png")
            return True
        if isinstance(value, Gdk.FileList):
            for gfile in value.get_files():
                path = gfile.get_path()
                if path and Path(path).suffix.lower() in _IMAGE_SUFFIXES:
                    try:
                        data = Path(path).read_bytes()
                    except OSError as exc:
                        logger.warning("drop image: %s", exc)
                        continue
                    self.insert_image(data, Path(path).name)
                    return True
        return False

    def insert_image(self, data: bytes, name: str) -> None:
        """Save *data* into this note's attachments dir and insert a link at the
        cursor. A no-op (with a warning) if the note has never been saved."""
        if not self._file_path:
            logger.warning("insert_image: note has no path yet; save it first")
            return
        from markdown_vault.core import attachments, path_utils, vault_fs
        note_dir = str(Path(self._file_path).parent)
        vault = path_utils.find_vault_for_dir(note_dir) or note_dir
        try:
            link = attachments.store_image(vault, self._file_path, data, name)
        except (OSError, vault_fs.VaultWriteError) as exc:
            # VaultWriteError (not an OSError): a note outside every vault cannot store a
            # managed image. Refuse gracefully — log and insert nothing, do not crash.
            logger.warning("insert_image: could not store %s: %s", name, exc, exc_info=True)
            return
        alt = Path(name).stem or "image"
        self._buffer.insert_at_cursor(f"![{alt}]({link})")
        # The image is a non-.md file, so the vault monitor won't see it — ask the
        # window to refresh the tree so the new attachment shows up.
        self.emit("attachment-added")

    def _setup_broken_link_markers(self) -> None:
        """Configure gutter warning marks + red underline for broken links."""
        attrs = GtkSource.MarkAttributes()
        attrs.set_icon_name("dialog-warning-symbolic")
        attrs.connect("query-tooltip-text", lambda _a, _m: _("Broken wikilink"))
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

    def _setup_image_link_markers(self) -> None:
        """Gutter marks for image links: a warning for a broken target (red
        underline too), and an info hint for a local image outside the attachments
        tree that a double-click adopts into it."""
        self._adopt_sources: dict[int, str] = {}   # line -> external file to adopt
        self._img_ranges: list[tuple[int, int, str]] = []   # (start, end, tooltip)
        warn = GtkSource.MarkAttributes()
        warn.set_icon_name("dialog-warning-symbolic")
        self._view.set_mark_attributes(_IMG_BROKEN_CATEGORY, warn, 11)
        hint = GtkSource.MarkAttributes()
        hint.set_icon_name("dialog-information-symbolic")
        self._view.set_mark_attributes(_IMG_ADOPT_CATEGORY, hint, 9)
        color = Gdk.RGBA()
        color.parse("rgb(255,64,64)")
        self._img_broken_tag = self._buffer.create_tag(
            _IMG_BROKEN_CATEGORY, underline=Pango.Underline.LOW, underline_rgba=color)
        self._view.connect("line-mark-activated", self._on_line_mark_activated)
        # Gutter-mark tooltips are unreliable in GtkSourceView 5, so show the hint
        # as a text-hover tooltip over the link span instead.
        self._view.set_has_tooltip(True)
        self._view.connect("query-tooltip", self._on_image_link_tooltip)

    _BROKEN_TOOLTIP = _("Broken image link — the file does not exist")
    _ADOPT_TOOLTIP = _("Not in the attachments folder — double-click the gutter icon "
                       "to adopt it for auto-management")

    def _on_image_link_tooltip(self, view, x, y, _keyboard, tooltip) -> bool:
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, x, y)
        over, it = view.get_iter_at_location(bx, by)
        if not over:
            return False
        offset = it.get_offset()
        for start, end, text in self._img_ranges:
            if start <= offset < end:
                tooltip.set_text(text)
                return True
        return False

    def _refresh_image_marks(self) -> bool:
        """Reclassify the note's image links and repaint the gutter marks."""
        if not self._file_path:
            self._set_image_link_marks([])
            return False
        from markdown_vault.core import attachments, path_utils
        note_dir = str(Path(self._file_path).parent)
        vault = path_utils.find_vault_for_dir(note_dir) or note_dir
        self._set_image_link_marks(
            attachments.classify_image_links(self.get_text(), note_dir, vault))
        return False

    def _set_image_link_marks(self, marks) -> None:
        start, end = self._buffer.get_bounds()
        self._buffer.remove_source_marks(start, end, _IMG_BROKEN_CATEGORY)
        self._buffer.remove_source_marks(start, end, _IMG_ADOPT_CATEGORY)
        self._buffer.remove_tag(self._img_broken_tag, start, end)
        self._adopt_sources = {}
        self._img_ranges = []
        seen: set[tuple[str, int]] = set()
        for offset_start, offset_end, line, kind, source in marks:
            si = self._buffer.get_iter_at_offset(offset_start)
            if kind == "broken":
                ei = self._buffer.get_iter_at_offset(offset_end)
                self._buffer.apply_tag(self._img_broken_tag, si, ei)
                category = _IMG_BROKEN_CATEGORY
                self._img_ranges.append((offset_start, offset_end, self._BROKEN_TOOLTIP))
            else:
                self._adopt_sources[line] = source
                category = _IMG_ADOPT_CATEGORY
                self._img_ranges.append((offset_start, offset_end, self._ADOPT_TOOLTIP))
            # One gutter mark per line (its icon only renders at column 0).
            if (category, line) not in seen:
                seen.add((category, line))
                line_start = si.copy()
                line_start.set_line_offset(0)
                self._buffer.create_source_mark(None, category, line_start)

    def _on_line_mark_activated(self, _view, it, _button, _state, n_press) -> None:
        if n_press >= 2 and it.get_line() in self._adopt_sources:
            self._adopt_image_on_line(it.get_line())

    def _adopt_image_on_line(self, line: int) -> None:
        """Copy the external image referenced on *line* into the attachments tree
        and repoint the link at it."""
        source = self._adopt_sources.get(line)
        if not source or not self._file_path:
            return
        try:
            data = Path(source).read_bytes()
        except OSError as exc:
            logger.warning("adopt image: cannot read %s: %s", source, exc)
            return
        from markdown_vault.core import attachments, path_utils, vault_fs
        note_dir = str(Path(self._file_path).parent)
        vault = path_utils.find_vault_for_dir(note_dir) or note_dir
        try:
            link = attachments.store_image(vault, self._file_path, data, Path(source).name)
        except (OSError, vault_fs.VaultWriteError) as exc:
            # VaultWriteError (not an OSError): a note outside every vault cannot adopt an
            # image into a managed tree. Refuse gracefully — log and return, do not crash.
            logger.warning("adopt image: cannot store %s: %s", source, exc, exc_info=True)
            return
        ok, line_start = self._buffer.get_iter_at_line(line)
        if not ok:
            return
        line_end = line_start.copy()
        line_end.forward_to_line_end()
        line_text = self._buffer.get_text(line_start, line_end, False)
        new_line = attachments.retarget_image(line_text, note_dir, source, link)
        if new_line != line_text:
            self._buffer.begin_user_action()
            self._buffer.delete(line_start, line_end)
            self._buffer.insert(line_start, new_line)
            self._buffer.end_user_action()
        self.emit("attachment-added")   # new file in attachments → refresh tree
        self._refresh_image_marks()

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
        self._refresh_image_marks()

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
            # invalid regex / no valid match range → nothing replaced; caller handles False
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
            # invalid regex → zero replacements; caller handles the 0 count
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

    # ── Scroll / caret position (navigation history) ────────────────

    def capture_scroll_position(self) -> tuple[float, int]:
        """Read the current vertical scroll offset and caret character offset.

        Synchronous: an editor is a :class:`Gtk.ScrolledWindow`, so the value is
        available at once — unlike the preview, whose scroll has to be reported
        asynchronously from JavaScript.
        """
        vadj = self.get_vadjustment()
        scroll = vadj.get_value() if vadj is not None else 0.0
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert()).get_offset()
        return scroll, cursor

    def restore_scroll_position(self, scroll: float | None = None,
                                cursor: int | None = None,
                                smooth: bool = False) -> None:
        """Restore a caret + scroll captured earlier, each clamped to the current
        buffer so returning into a now-shorter note lands in range, not the void.

        The caret is placed at once (clamped to the character count). The scroll
        is applied only after the view re-lays out (via ``idle_add``): right
        after a buffer fill the adjustment's ``upper`` is still 0 and would
        silently clamp the value to 0 (the failure the pattern in
        ``Tab.reload_editor`` exists to avoid). Fields left ``None`` are skipped.

        With ``smooth`` (in-page back/forward, same note) the view *animates* to
        the saved offset via ``scroll_to_iter`` — the same method the outline
        click and search use, so the editor glides like the preview. The target
        line is looked up **from the saved offset**, never from the caret: the
        caret is routinely nowhere near the viewport (a freshly opened note keeps
        it at 0 while the reader scrolls down), and aligning on it would land at
        the caret instead of the saved spot. Without ``smooth`` (a note switch)
        the offset is written straight onto the adjustment — an instant jump,
        because the target tab may not even be visible.
        """
        buf = self._buffer
        if cursor is not None:
            buf.place_cursor(buf.get_iter_at_offset(min(cursor, buf.get_char_count())))
        if scroll is None:
            return
        vadj = self.get_vadjustment()
        if vadj is None:
            return
        if smooth:
            def _animate(value=scroll):
                # The bool is "is this position over text"; x=0 lies in the left
                # margin, so it is False even for a perfectly good line — the
                # iter is the right line either way. A value past the end of a
                # now-shorter note yields its last line, and scroll_to_iter
                # cannot scroll beyond it: the _clamp_scroll safety is kept.
                _ok, it = self._view.get_iter_at_location(0, int(value))
                self._view.scroll_to_iter(it, 0.0, True, 0.0, 0.0)
                return False
            GLib.idle_add(_animate)
        else:
            def _apply(adj=vadj, value=scroll):
                adj.set_value(
                    _clamp_scroll(value, adj.get_upper(), adj.get_page_size()))
                return False
            GLib.idle_add(_apply)

    def grab_editor_focus(self) -> None:
        """Focus the text view so a restored caret is visible and ready to type."""
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
        self._refresh_image_marks()
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
