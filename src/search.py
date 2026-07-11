"""Markdown Vault — bottom-bar full-text search.

Provides a toggleable search bar at the bottom of the window that
searches across all configured vault directories.  Results are shown
as clickable entries that open the matching file.
"""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject


class SearchBar(Gtk.Box):
    """Bottom search bar with a ``Gtk.SearchEntry`` and result list.

    Signals:
        file-selected(str): Emitted when a search result is clicked.
    """

    __gsignals__ = {
        "file-selected": (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    MAX_RESULTS = 50

    def __init__(self, get_vault_paths=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._get_vault_paths = get_vault_paths
        self.set_visible(False)

        # --- Input row ---
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(6)
        input_box.set_margin_start(8)
        input_box.set_margin_end(8)

        self._entry = Gtk.SearchEntry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Search across all vaults…")
        self._entry.connect("activate", self._on_search)
        self._entry.connect("stop-search", lambda _e: self.set_visible(False))
        input_box.append(self._entry)

        search_btn = Gtk.Button(label="Search")
        search_btn.connect("clicked", self._on_search)
        input_box.append(search_btn)

        self.append(input_box)

        # --- Results ---
        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._results_box)
        scrolled.set_max_content_height(300)
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
        results = self._search_vaults(query)
        if not results:
            self._results_box.append(
                self._empty_label("No results found")
            )
            return
        for filepath, line_num, line_text in results[: self.MAX_RESULTS]:
            row = self._build_result_row(filepath, line_num, line_text)
            self._results_box.append(row)

    def _search_vaults(self, query: str) -> list[tuple[str, int, str]]:
        """Return ``(filepath, line_number, line_text)`` matches."""
        results: list[tuple[str, int, str]] = []
        query_lower = query.lower()
        for vault_path in self._get_vault_paths():
            for root, _dirs, files in os.walk(vault_path):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            for i, line in enumerate(fh, 1):
                                if query_lower in line.lower():
                                    results.append((fpath, i, line))
                    except OSError:
                        continue
        return results

    def _build_result_row(
        self, filepath: str, line_num: int, line_text: str
    ) -> Gtk.Box:
        """Create a clickable widget for a single search result."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("search-result")
        row.set_margin_start(4)
        row.set_margin_end(4)

        location = Gtk.Label(label=f"{Path(filepath).name}:{line_num}")
        location.add_css_class("dim-label")
        location.set_xalign(0)
        row.append(location)

        preview = Gtk.Label(label=line_text.strip()[:120])
        preview.set_xalign(0)
        preview.set_ellipsize(3)
        row.append(preview)

        gesture = Gtk.GestureClick()
        gesture.connect(
            "released",
            lambda _g, _n, _x, _y, fp=filepath: self.emit("file-selected", fp),
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
