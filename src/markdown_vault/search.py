"""Markdown Vault — bottom-bar full-text search.

A toggleable search bar at the bottom of the window that searches across all
configured vault directories via :mod:`search_backend` (ripgrep with a Python
fallback).

- Live search as you type (debounced); a superseded search is discarded, never
  blocks the next one.
- Matches are highlighted in the result preview.
- Keyboard: ``Down`` from the entry moves into the results; ``Up``/``Down``
  navigate, ``Enter`` opens, ``Esc`` closes.

The disk scan runs in a background thread; results are delivered back to the
main thread via ``GLib.idle_add`` and gated by a generation counter so only the
newest search populates the list.
"""

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject, GLib, Gdk

from . import search_backend

logger = logging.getLogger(__name__)


class SearchBar(Gtk.Box):
    """Bottom search bar with a ``Gtk.SearchEntry`` and a result list.

    Signals:
        file-selected(str, int): Emitted with (path, line) when a result is
            activated.
        close-requested(): Emitted when the bar should close.
    """

    __gsignals__ = {
        "file-selected": (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        "close-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    MAX_RESULTS = 50
    _DEBOUNCE_MS = 150

    def __init__(self, get_vault_paths=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._get_vault_paths = get_vault_paths
        self.set_visible(False)
        self.set_vexpand(True)

        self._generation = 0          # newest search id; older results discarded
        self._debounce_id = None      # pending debounce timeout

        # --- Input row ---
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(6)
        input_box.set_margin_start(8)
        input_box.set_margin_end(8)

        self._entry = Gtk.SearchEntry()
        self._entry.set_hexpand(False)
        self._entry.set_width_chars(40)
        self._entry.set_max_width_chars(40)
        self._entry.set_placeholder_text("Search across all vaults…")
        self._entry.connect("search-changed", self._on_search_changed)
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("stop-search", lambda _e: self.emit("close-requested"))
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._on_entry_key)
        self._entry.add_controller(entry_keys)
        input_box.append(self._entry)

        # Query modifiers.
        self._case_btn = self._make_toggle("Aa", "Case sensitive")
        self._word_btn = self._make_toggle("W", "Whole word")
        self._regex_btn = self._make_toggle(".*", "Regular expression")
        for btn in (self._case_btn, self._word_btn, self._regex_btn):
            btn.connect("toggled", lambda *_: self._run_search())
            input_box.append(btn)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        input_box.append(self._spinner)

        spacer = Gtk.Box(hexpand=True)  # push the close button to the right
        input_box.append(spacer)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Close (Esc)")
        close_btn.connect("clicked", lambda *_: self.emit("close-requested"))
        input_box.append(close_btn)

        self.append(input_box)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Results ---
        self._results = Gtk.ListBox()
        self._results.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._results.add_css_class("search-results")
        self._results.connect("row-activated", self._on_row_activated)
        results_keys = Gtk.EventControllerKey()
        results_keys.connect("key-pressed", self._on_results_key)
        self._results.add_controller(results_keys)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._results)
        scrolled.set_vexpand(True)
        self.append(scrolled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def focus(self) -> None:
        """Show the search bar and move focus to the entry."""
        self.set_visible(True)
        self._entry.grab_focus()

    @staticmethod
    def _make_toggle(label: str, tooltip: str) -> Gtk.ToggleButton:
        btn = Gtk.ToggleButton(label=label)
        btn.set_tooltip_text(tooltip)
        btn.add_css_class("flat")
        return btn

    def _current_options(self) -> search_backend.SearchOptions:
        return search_backend.SearchOptions(
            case_sensitive=self._case_btn.get_active(),
            whole_word=self._word_btn.get_active(),
            regex=self._regex_btn.get_active(),
        )

    # ------------------------------------------------------------------
    # Search lifecycle
    # ------------------------------------------------------------------

    def _on_search_changed(self, _entry) -> None:
        """Debounce live search on every keystroke."""
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(self._DEBOUNCE_MS, self._run_search)

    def _run_search(self) -> bool:
        self._debounce_id = None
        query = self._entry.get_text().strip()
        self._clear_results()
        vault_paths = self._get_vault_paths() if self._get_vault_paths else []
        if not query or not vault_paths:
            self._stop_spinner()
            return False

        self._generation += 1
        generation = self._generation
        self._spinner.set_visible(True)
        self._spinner.start()
        threading.Thread(
            target=self._worker,
            args=(generation, query, list(vault_paths), self._current_options()),
            daemon=True,
        ).start()
        return False  # one-shot

    def _worker(self, generation, query, vault_paths, options) -> None:
        matches = search_backend.search(query, vault_paths, self.MAX_RESULTS, options)
        GLib.idle_add(self._on_complete, generation, matches)

    def _on_complete(self, generation: int, matches: list) -> bool:
        if generation != self._generation:
            return False  # superseded by a newer search
        self._stop_spinner()
        self._clear_results()
        if not matches:
            self._results.append(self._message_row("No results found"))
            return False
        for match in matches:
            self._results.append(self._build_result_row(match))
        return False

    # ------------------------------------------------------------------
    # Result rows
    # ------------------------------------------------------------------

    def _build_result_row(self, match: search_backend.Match) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._mv_match = match  # stash for activation
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("search-result")

        preview = Gtk.Label()
        preview.set_xalign(0)
        preview.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        preview.set_hexpand(True)
        preview.set_markup(_highlight_markup(match.text, match.spans))
        box.append(preview)

        location = Gtk.Label(label=f"{Path(match.path).name}:{match.line}")
        location.add_css_class("dim-label")
        location.add_css_class("mono")
        location.set_xalign(1)
        location.set_halign(Gtk.Align.END)
        box.append(location)

        row.set_child(box)
        return row

    def _message_row(self, text: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.set_margin_start(8)
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        label.add_css_class("dim-label")
        row.set_child(label)
        return row

    def _clear_results(self) -> None:
        child = self._results.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._results.remove(child)
            child = nxt

    def _stop_spinner(self) -> None:
        self._spinner.stop()
        self._spinner.set_visible(False)

    # ------------------------------------------------------------------
    # Activation & keyboard navigation
    # ------------------------------------------------------------------

    def _on_row_activated(self, _list_box, row) -> None:
        match = getattr(row, "_mv_match", None)
        if match is not None:
            self.emit("file-selected", match.path, match.line)

    def _on_entry_activate(self, _entry) -> None:
        """Enter in the entry opens the selected (or first) result."""
        row = self._results.get_selected_row() or self._first_result_row()
        if row is not None and getattr(row, "_mv_match", None) is not None:
            self.emit("file-selected", row._mv_match.path, row._mv_match.line)

    def _on_entry_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        """Down moves into the result list; Up stays put (never escape upward)."""
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            row = self._first_result_row()
            if row is not None:
                self._results.select_row(row)
                row.grab_focus()
                return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            return True  # keep focus in the search entry
        return False

    def _on_results_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.emit("close-requested")
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            # At the top row, go back to the entry instead of escaping the list.
            if self._results.get_selected_row() is self._first_result_row():
                self._entry.grab_focus()
                return True
        return False

    def _first_result_row(self):
        row = self._results.get_row_at_index(0)
        if row is not None and getattr(row, "_mv_match", None) is None:
            return None  # the "no results" message row
        return row


def _highlight_markup(text: str, spans: list, max_len: int = 240) -> str:
    """Return Pango markup for *text* with *spans* bolded.

    Leading whitespace is trimmed (spans shifted to match) so previews line up.
    """
    stripped = text.lstrip()
    shift = len(text) - len(stripped)
    text = stripped[:max_len]
    shifted = [(max(0, s - shift), max(0, e - shift)) for s, e in spans]

    out: list[str] = []
    pos = 0
    for s, e in sorted(shifted):
        s = min(s, len(text))
        e = min(e, len(text))
        if s >= e or s < pos:
            continue
        out.append(GLib.markup_escape_text(text[pos:s]))
        out.append("<b>" + GLib.markup_escape_text(text[s:e]) + "</b>")
        pos = e
    out.append(GLib.markup_escape_text(text[pos:]))
    return "".join(out)
