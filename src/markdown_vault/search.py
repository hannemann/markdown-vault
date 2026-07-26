"""Markdown Vault — bottom-bar full-text search.

Provides a toggleable search bar at the bottom of the window that
searches across all configured vault directories.  Results are shown
as clickable entries that open the matching file.

Search runs in a background thread to keep the UI responsive on large
vaults.  Results are delivered back to the main thread via
``GLib.idle_add``.
"""

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject, GLib

from . import search_logic

logger = logging.getLogger(__name__)


class SearchBar(Gtk.Box):
    """Bottom search bar with a ``Gtk.SearchEntry`` and result list.

    Signals:
        file-selected(str): Emitted when a search result is clicked.
    """

    __gsignals__ = {
        "file-selected": (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        "close-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    MAX_RESULTS = 50

    def __init__(self, get_vault_paths=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._get_vault_paths = get_vault_paths
        self.set_visible(False)
        self.set_vexpand(True)

        self._search_thread: threading.Thread | None = None
        self._pending_vault_paths: list[str] = []

        # --- Input row ---
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(6)
        input_box.set_margin_start(8)
        input_box.set_margin_end(8)

        self._entry = Gtk.SearchEntry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Search across all vaults\u2026")
        self._entry.connect("activate", self._on_search)
        self._entry.connect("stop-search", lambda _e: self.emit("close-requested"))
        input_box.append(self._entry)

        search_btn = Gtk.Button(label="Search")
        search_btn.connect("clicked", self._on_search)
        input_box.append(search_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        input_box.append(self._spinner)

        self.append(input_box)

        # --- Separator ---
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.append(sep)

        # --- Results ---
        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._results_box)
        scrolled.set_vexpand(True)
        self.append(scrolled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def focus(self) -> None:
        """Show the search bar and move focus to the entry."""
        self.set_visible(True)
        self._entry.grab_focus()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_search(self, _widget=None) -> None:
        """Execute the search and populate the results list."""
        self._clear_results()
        query = self._entry.get_text().strip()
        if not query or not self._get_vault_paths:
            return

        # Wait for any in-flight search to finish (should be fast).
        if self._search_thread and self._search_thread.is_alive():
            return

        vault_paths = self._get_vault_paths()
        if not vault_paths:
            return

        self._spinner.set_visible(True)
        self._spinner.start()

        self._search_thread = threading.Thread(
            target=self._search_worker,
            args=(query, vault_paths),
            daemon=True,
        )
        self._search_thread.start()

    def _search_worker(self, query: str, vault_paths: list[str]) -> None:
        """Run the search in a background thread."""
        results = search_logic.search_vaults(query, vault_paths, self.MAX_RESULTS)
        GLib.idle_add(self._on_search_complete, results)

    def _on_search_complete(self, results: list) -> bool:
        """Populate results on the main thread (called via idle_add)."""
        self._spinner.stop()
        self._spinner.set_visible(False)
        if not results:
            self._results_box.append(
                self._empty_label("No results found")
            )
            return False
        for filepath, line_num, line_text in results[: self.MAX_RESULTS]:
            row = self._build_result_row(filepath, line_num, line_text)
            self._results_box.append(row)
        return False  # remove idle handler

    def _build_result_row(
        self, filepath: str, line_num: int, line_text: str
    ) -> Gtk.Box:
        """Create a clickable widget for a single search result."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("search-result")

        location = Gtk.Label(label=f"{Path(filepath).name}:{line_num}")
        location.add_css_class("dim-label")
        location.add_css_class("mono")
        location.set_xalign(0)
        row.append(location)

        preview = Gtk.Label(label=line_text.strip()[:120])
        preview.set_xalign(0)
        preview.set_ellipsize(3)
        preview.set_hexpand(True)
        row.append(preview)

        gesture = Gtk.GestureClick()
        gesture.connect(
            "released",
            lambda _g, _n, _x, _y, fp=filepath, ln=line_num: self.emit("file-selected", fp, ln),
        )
        row.add_controller(gesture)
        return row

    def _clear_results(self) -> None:
        """Remove all result widgets."""
        for child in list(self._results_box):
            self._results_box.remove(child)

    @staticmethod
    def _empty_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.set_margin_start(8)
        lbl.set_margin_top(4)
        lbl.add_css_class("dim-label")
        return lbl
