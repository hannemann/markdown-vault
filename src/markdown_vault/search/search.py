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
import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject, GLib, Gdk

from markdown_vault.search import search_backend, search_logic
from markdown_vault.markdown import frontmatter
from markdown_vault.core import path_utils
from markdown_vault.core.i18n import _

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

    def __init__(self, get_vault_paths=None, semantic_query=None,
                 scope=None, hide_deprecated=None,
                 set_hide_deprecated=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._get_vault_paths = get_vault_paths
        self._semantic_query = semantic_query  # callable(query) -> list[FileResult]
        self._scope = scope  # shared vault-scope callbacks (see _scope_callbacks)
        self._hide_deprecated = hide_deprecated  # callable() -> bool, shared state
        self._set_hide_deprecated = set_hide_deprecated  # callable(bool), sets it
        self._last_results: list = []     # last completed set, for re-render
        self.set_visible(False)
        self.set_vexpand(True)

        self._generation = 0          # newest search id; older results discarded
        self._debounce_id = None      # pending debounce timeout
        self._busy = False            # a search is running (for WaitIdle/E2E)

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
        self._entry.set_placeholder_text(_("Search across all vaults…"))
        self._entry.set_tooltip_text(
            _("Terms are AND-combined.  \"quoted phrase\", -exclude, "
              "tag:foo, path:sub, vault:name")
        )
        self._entry.connect("search-changed", self._on_search_changed)
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("stop-search", lambda _e: self.emit("close-requested"))
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._on_entry_key)
        self._entry.add_controller(entry_keys)
        input_box.append(self._entry)

        # Query modifiers.
        self._case_btn = self._make_toggle("Aa", _("Case sensitive"))
        self._word_btn = self._make_toggle("W", _("Whole word"))
        self._regex_btn = self._make_toggle(".*", _("Regular expression"))
        for btn in (self._case_btn, self._word_btn, self._regex_btn):
            btn.connect("toggled", lambda *_: self._run_search())
            input_box.append(btn)

        # Scope: shared vault-scope dropdown (current vault / all / a vault).
        self._scope_dropdown = None
        if self._scope:
            scope_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            scope_sep.set_margin_start(2)
            scope_sep.set_margin_end(2)
            input_box.append(scope_sep)
            from markdown_vault.search.vault_scope import VaultScope
            self._scope_dropdown = VaultScope(
                self._scope["get_vaults_named"], self._scope["get_active"],
                self._scope["get_scope"], self._scope["set_scope"],
                on_change=self._run_search)
            input_box.append(self._scope_dropdown)

        # Persistent "hide deprecated" toggle, on the left next to the vault-scope
        # filter — mirrors and drives the shared state.
        self._dep_toggle = Gtk.ToggleButton(icon_name="view-conceal-symbolic")
        self._dep_toggle.set_tooltip_text(_("Hide deprecated notes"))
        if self._hide_deprecated is not None:
            self._dep_toggle.set_active(self._hide_deprecated())
        self._dep_toggle.connect("toggled", self._on_dep_toggled)
        input_box.append(self._dep_toggle)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        input_box.append(self._spinner)

        spacer = Gtk.Box(hexpand=True)  # push the trailing buttons to the right
        input_box.append(spacer)

        help_btn = Gtk.MenuButton(icon_name="help-about-symbolic")
        help_btn.add_css_class("flat")
        help_btn.set_tooltip_text(_("Search syntax"))
        help_btn.set_popover(self._build_help_popover())
        input_box.append(help_btn)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text(_("Close (Esc)"))
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

    def run_query(self, text: str) -> None:
        """Run a search programmatically (debug/automation). Unlike typing, this
        fires the search *immediately* — the SearchEntry's own input delay and the
        debounce would otherwise leave a WaitIdle racing an unstarted search."""
        self._entry.set_text(text or "")
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        self._run_search()

    def is_idle(self) -> bool:
        """True when no search is pending or in flight — the WaitIdle predicate."""
        return self._debounce_id is None and not self._busy

    def result_paths(self) -> list:
        """Paths of the last completed result set (for debug/automation)."""
        return [fr.path for fr in self._last_results]

    @staticmethod
    def _build_help_popover() -> Gtk.Popover:
        """A small cheat-sheet explaining the query operators and filters."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(12)

        title = Gtk.Label()
        title.set_markup(f"<b>{_('Search syntax')}</b>")
        title.set_xalign(0)
        box.append(title)

        grid = Gtk.Grid(column_spacing=14, row_spacing=4)
        rows = [
            ("foo bar", _("All terms must match (AND)")),
            ('"foo bar"', _("Exact phrase")),
            ("-foo", _("Exclude term")),
            ("tag:work", _("Has frontmatter tag")),
            ("path:sub", _("Path contains text")),
            ("vault:Notes", _("Restrict to a vault")),
        ]
        for i, (code, desc) in enumerate(rows):
            c = Gtk.Label(label=code)
            c.add_css_class("mono")
            c.set_xalign(0)
            d = Gtk.Label(label=desc)
            d.add_css_class("dim-label")
            d.set_xalign(0)
            grid.attach(c, 0, i, 1, 1)
            grid.attach(d, 1, i, 1, 1)
        box.append(grid)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        foot = Gtk.Label()
        foot.set_xalign(0)
        foot.set_wrap(True)
        foot.set_max_width_chars(34)
        foot.add_css_class("dim-label")
        foot.set_markup(
            _("<tt>Aa</tt> case · <tt>W</tt> whole word · <tt>.*</tt> regex · "
              "folder = current vault only.\n"
              "In regex mode the query is one raw pattern (operators off).")
        )
        box.append(foot)

        pop.set_child(box)
        return pop

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

    def _scope_vaults(self) -> list:
        """The vault paths to search, honouring the shared scope selection."""
        if self._scope:
            return list(self._scope["scope_vaults"]())
        return list(self._get_vault_paths()) if self._get_vault_paths else []

    def refresh_scope(self) -> None:
        """Rebuild the scope dropdown (active-vault or scope changed elsewhere)."""
        if self._scope_dropdown is not None:
            self._scope_dropdown.refresh()

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
        vault_paths = self._scope_vaults()
        if not query or not vault_paths:
            self._stop_spinner()
            self._busy = False
            self._last_results = []   # nothing to show → don't keep the old set around
            return False

        options = self._current_options()
        pattern_err = search_backend.pattern_error(query, options)
        if pattern_err is not None:
            self._stop_spinner()
            self._busy = False
            self._last_results = []   # a broken pattern has no result set to keep
            self._results.append(self._message_row(pattern_err))
            return False

        self._generation += 1
        generation = self._generation
        self._busy = True
        self._spinner.set_visible(True)
        self._spinner.start()
        threading.Thread(
            target=self._worker,
            args=(generation, query, list(vault_paths), options),
            daemon=True,
        ).start()
        return False  # one-shot

    def _worker(self, generation, query, vault_paths, options) -> None:
        results = search_backend.search_grouped(
            query, vault_paths, options, max_files=self.MAX_RESULTS,
        )
        results = self._merge_semantic(results, query, vault_paths, options)
        GLib.idle_add(self._on_complete, generation, results)

    # Field filters (tag:/path:/vault:) and the exclusion operator (-term).
    _OPERATOR_TOKEN = re.compile(r'(?:^|\s)(?:-\S+|(?:tag|path|vault):\S+)')

    @classmethod
    def _has_operators(cls, query: str) -> bool:
        """Whether the query uses a structured filter/exclusion.  Such a query is
        precise on purpose, so fuzzy semantic hits (which ignore the operators)
        must not dilute it.  Quoted phrases are not operators."""
        return bool(cls._OPERATOR_TOKEN.search(query))

    @classmethod
    def _strip_operators(cls, query: str) -> str:
        """Drop operator/filter tokens and quotes so only prose reaches the
        embedder (embedding ``-foo tag:bar "baz"`` produces noise)."""
        text = cls._OPERATOR_TOKEN.sub(" ", query).replace('"', " ")
        return " ".join(text.split())

    @staticmethod
    def _under_vault(path: str, vault_paths) -> bool:
        """True if *path* is inside one of *vault_paths* — separator-aware, so
        ``~/notes`` does not admit hits under a sibling ``~/notes-archive``."""
        return any(
            path == v or path.startswith(v.rstrip(os.sep) + os.sep)
            for v in vault_paths
        )

    def _merge_semantic(self, keyword_results, query, vault_paths, options):
        """Append semantic-only hits (scoped to *vault_paths*) after keyword ones.

        Runs on the worker thread, so the (possibly slow) query embedding never
        blocks the UI.  Keyword matches keep priority; semantic finds surface
        files the keyword search missed.  Semantic is skipped for regex mode and
        for structured queries (filters/exclusions), which are precise on purpose.
        """
        if not self._semantic_query or options.regex:
            return keyword_results  # a raw regex pattern is meaningless to embed
        if self._has_operators(query):
            return keyword_results  # keep a filtered query filtered
        text = self._strip_operators(query)
        if not text:
            return keyword_results
        try:
            semantic = self._semantic_query(text)
        except Exception:
            logger.debug("semantic query failed", exc_info=True)
            return keyword_results
        have = {r.path for r in keyword_results}
        extra = [r for r in semantic
                 if r.path not in have and self._under_vault(r.path, vault_paths)]
        return (keyword_results + extra)[:self.MAX_RESULTS]

    def _on_complete(self, generation: int, file_results: list) -> bool:
        if generation != self._generation:
            return False  # superseded by a newer search
        self._stop_spinner()
        self._busy = False
        self._last_results = file_results
        self._render_results()
        return False

    def _render_results(self) -> None:
        """Render (or re-render) the last completed result set, hiding deprecated
        notes when the shared toggle is on. When the filter hides matches, say so
        instead of leaving the user with a bare "No results found"."""
        self._clear_results()
        hide = self._hide_deprecated is not None and self._hide_deprecated()
        hidden = 0
        for fr in self._last_results:
            if hide and frontmatter.status_of(fr.path) == "deprecated":
                hidden += 1
                continue
            self._results.append(self._build_file_header(fr))
            for match in fr.matches:
                self._results.append(self._build_match_row(match))
            if fr.total_matches > len(fr.matches):
                self._results.append(self._more_row(fr))
        empty = self._results.get_row_at_index(0) is None
        if hidden:
            self._results.append(self._message_row(
                search_logic.deprecated_hidden_message(hidden, empty)))
        elif empty:
            self._results.append(self._message_row("No results found"))

    def _on_dep_toggled(self, btn) -> None:
        """The persistent toggle drives the shared 'hide deprecated' state."""
        if self._set_hide_deprecated is not None:
            self._set_hide_deprecated(btn.get_active())

    def refresh_deprecated(self) -> None:
        """Sync the toggle to the shared state and re-render — called when the
        state changes here or elsewhere so search and tree stay consistent."""
        active = self._hide_deprecated is not None and self._hide_deprecated()
        self._dep_toggle.handler_block_by_func(self._on_dep_toggled)
        self._dep_toggle.set_active(active)
        self._dep_toggle.handler_unblock_by_func(self._on_dep_toggled)
        if self._last_results:
            self._render_results()

    # ------------------------------------------------------------------
    # Result rows
    # ------------------------------------------------------------------

    def _build_file_header(self, fr) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        open_line = fr.matches[0].line if fr.matches else 1
        row._mv_open = (fr.path, open_line)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("search-file-header")

        if getattr(fr, "semantic", False):
            marker = Gtk.Label(label="≈")
            marker.add_css_class("dim-label")
            marker.set_tooltip_text(_("Semantic match"))
            box.append(marker)

        # The file identity is its vault-relative path ("<vault>/<path>", no
        # .md) so the hit's vault and location are visible at a glance.
        name = Gtk.Label(label=path_utils.vault_relative_name(fr.path))
        name.add_css_class("search-file-name")
        name.set_xalign(0)
        name.set_ellipsize(3)  # PANGO_ELLIPSIZE_END — keep the vault name
        name.set_hexpand(True)
        box.append(name)

        if fr.total_matches:
            count = Gtk.Label(label=str(fr.total_matches))
            count.add_css_class("dim-label")
            count.set_halign(Gtk.Align.END)
            box.append(count)

        row.set_child(box)
        return row

    def _build_match_row(self, match: search_backend.Match) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._mv_open = (match.path, match.line)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("search-result")
        box.set_margin_start(16)  # indent under the file header

        lineno = Gtk.Label(label=str(match.line))
        lineno.add_css_class("dim-label")
        lineno.add_css_class("mono")
        lineno.set_xalign(1)
        lineno.set_width_chars(4)
        box.append(lineno)

        preview = Gtk.Label()
        preview.set_xalign(0)
        preview.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        preview.set_hexpand(True)
        preview.set_markup(_highlight_markup(match.text, match.spans))
        box.append(preview)

        row.set_child(box)
        return row

    def _more_row(self, fr) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._mv_open = (fr.path, fr.matches[0].line if fr.matches else 1)
        extra = fr.total_matches - len(fr.matches)
        label = Gtk.Label(label=_("+{count} more…").format(count=extra))
        label.set_xalign(0)
        label.set_margin_start(16)
        label.add_css_class("dim-label")
        row.set_child(label)
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
        target = getattr(row, "_mv_open", None)
        if target is not None:
            self.emit("file-selected", target[0], target[1])

    def _on_entry_activate(self, _entry) -> None:
        """Enter in the entry opens the selected (or first) result."""
        row = self._results.get_selected_row() or self._first_result_row()
        target = getattr(row, "_mv_open", None) if row is not None else None
        if target is not None:
            self.emit("file-selected", target[0], target[1])

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
        if row is not None and getattr(row, "_mv_open", None) is None:
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
