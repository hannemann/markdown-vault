"""Quick-open palette — the Ctrl+Space overlay.

A modal :class:`Adw.Dialog` with a search entry and a live-filtered result
list.  All matching/ranking lives in :mod:`quick_open`; this widget only builds
a fresh engine on open, renders results and handles keyboard navigation.
"""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GObject, GLib, Gdk

logger = logging.getLogger(__name__)


class QuickOpenPalette(Adw.Dialog):
    """Fuzzy file switcher.

    Signals:
        file-selected(str, int): (path, line) when a result is activated.
    """

    __gsignals__ = {
        "file-selected": (GObject.SignalFlags.RUN_LAST, None, (str, int)),
    }

    MAX_RESULTS = 40

    def __init__(self, make_engine) -> None:
        super().__init__()
        self._make_engine = make_engine
        self._engine = None
        self.set_title("Quick Open")
        self.set_content_width(640)
        self.set_content_height(480)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(box)

        self._entry = Gtk.SearchEntry()
        self._entry.set_placeholder_text("Go to file…")
        self._entry.set_margin_top(8)
        self._entry.set_margin_bottom(8)
        self._entry.set_margin_start(8)
        self._entry.set_margin_end(8)
        self._entry.connect("search-changed", lambda _e: self._refresh())
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("stop-search", lambda _e: self.close())
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._on_entry_key)
        self._entry.add_controller(entry_keys)
        box.append(self._entry)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._results = Gtk.ListBox()
        self._results.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._results.add_css_class("quick-open-results")
        self._results.connect("row-activated", self._on_row_activated)
        results_keys = Gtk.EventControllerKey()
        results_keys.connect("key-pressed", self._on_results_key)
        self._results.add_controller(results_keys)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._results)
        scrolled.set_vexpand(True)
        box.append(scrolled)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self, parent: Gtk.Widget) -> None:
        """Build a fresh index, show recent files and present over *parent*."""
        self._engine = self._make_engine()
        self._entry.set_text("")
        self._refresh()
        self.present(parent)
        self._entry.grab_focus()

    def _refresh(self) -> None:
        query = self._entry.get_text().strip()
        self._clear()
        if self._engine is None:
            return
        results = self._engine.search(query, limit=self.MAX_RESULTS)
        if not results:
            self._results.append(self._message_row("No files"))
            return
        for r in results:
            self._results.append(self._build_row(r))
        first = self._results.get_row_at_index(0)
        if first is not None:
            self._results.select_row(first)

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _build_row(self, result) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._mv_open = (result.path, 1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("quick-open-result")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)

        name = Gtk.Label()
        name.set_xalign(0)
        name.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name.set_markup(_highlight_positions(result.name, result.positions))
        text.append(name)

        folder = Gtk.Label(label=result.folder)
        folder.set_xalign(0)
        folder.add_css_class("dim-label")
        folder.add_css_class("mono")
        folder.set_ellipsize(1)  # PANGO_ELLIPSIZE_START — keep the tail
        text.append(folder)

        box.append(text)
        row.set_child(box)
        return row

    def _message_row(self, text: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        label.set_margin_start(8)
        row.set_child(label)
        return row

    def _clear(self) -> None:
        child = self._results.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._results.remove(child)
            child = nxt

    # ------------------------------------------------------------------
    # Activation & keyboard
    # ------------------------------------------------------------------

    def _on_row_activated(self, _list_box, row) -> None:
        self._activate(row)

    def _on_entry_activate(self, _entry) -> None:
        row = self._results.get_selected_row() or self._first_row()
        self._activate(row)

    def _activate(self, row) -> None:
        target = getattr(row, "_mv_open", None) if row is not None else None
        if target is not None:
            self.close()
            self.emit("file-selected", target[0], target[1])

    def _on_entry_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            row = self._first_row()
            if row is not None:
                self._results.select_row(row)
                row.grab_focus()
                return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            return True  # keep focus in the entry
        return False

    def _on_results_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            if self._results.get_selected_row() is self._first_row():
                self._entry.grab_focus()
                return True
        return False

    def _first_row(self):
        row = self._results.get_row_at_index(0)
        if row is not None and getattr(row, "_mv_open", None) is None:
            return None  # the "no files" message row
        return row


def _highlight_positions(name: str, positions: list) -> str:
    """Pango markup for *name* with the matched *positions* bolded."""
    marked = set(positions)
    out: list[str] = []
    for i, ch in enumerate(name):
        esc = GLib.markup_escape_text(ch)
        out.append(f"<b>{esc}</b>" if i in marked else esc)
    return "".join(out)
