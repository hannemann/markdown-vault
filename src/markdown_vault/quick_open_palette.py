"""Quick-open palette — the Ctrl+Space overlay.

A modal :class:`Adw.Dialog` with a search entry and a live-filtered result
list.  All matching/ranking lives in :mod:`quick_open`; this widget only builds
a fresh engine on open, renders results and handles keyboard navigation.
"""

import logging
import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GObject, GLib, Gdk

from . import markdown_widgets, path_utils
from .quick_open import fuzzy_match

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
    _SEMANTIC_MIN_CHARS = 2

    def __init__(self, make_engine, semantic_query=None, ask_answer=None,
                 scope=None, can_ask=None, ask_candidates=None,
                 ask_answer_selected=None, get_top_k=None) -> None:
        super().__init__()
        self._make_engine = make_engine
        self._semantic_query = semantic_query  # callable(query) -> list, off-thread
        self._ask_answer = ask_answer          # callable(question) -> ask.Answer
        self._can_ask = can_ask                # () -> bool: is Ask usable right now?
        # "Pick your own sources": candidates() -> [(path, score)], and
        # answer_selected(question, paths) -> ask.Answer; get_top_k() -> int cap.
        self._ask_candidates = ask_candidates
        self._ask_answer_selected = ask_answer_selected
        self._get_top_k = get_top_k
        self._pick_toggle = None        # "pick sources" toggle (footer)
        self._answer_btn = None         # "Answer (n)" button shown while picking
        self._candidates = []           # [(path, score)] currently offered
        self._selected: list[str] = []  # chosen paths (ordered, capped at top_k)
        self._pick_question = ""        # the question the candidates are for
        self._scope = scope                    # shared vault-scope callbacks
        self._mode_locked = False              # user explicitly chose a mode
        self._suppress_toggle = False          # guard programmatic toggle changes
        self._engine = None
        self._sem_generation = 0        # invalidates in-flight semantic queries
        self._ask_mode = False
        self._ask_generation = 0        # invalidates in-flight answers
        self._last_question = ""        # kept so reopening shows it beside the answer
        self._answer_text = ""          # raw Markdown source, copied on Ctrl+C
        self._has_answer = False        # gates the sticky copy button
        self._timer_label = None        # footer elapsed-time readout (ask mode)
        self._timer_id = None           # GLib source ticking the elapsed time
        self._ask_started = 0.0         # time.monotonic() when the ask began
        self._shown_paths: set[str] = set()
        self.set_title("Quick Open")
        self.set_content_width(640)
        self.set_content_height(480)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Wrap in a ToastOverlay so copy actions can flash a "Copied!" pill.
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(box)
        self.set_child(self._toast_overlay)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(8)
        header.set_margin_end(8)
        self._entry = Gtk.SearchEntry(hexpand=True)
        self._entry.set_placeholder_text("Go to file…")
        self._entry.connect("search-changed", lambda _e: self._refresh())
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("stop-search", lambda _e: self.close())
        entry_keys = Gtk.EventControllerKey()
        entry_keys.connect("key-pressed", self._on_entry_key)
        self._entry.add_controller(entry_keys)
        header.append(self._entry)
        # Submit button for mouse users — mirrors pressing Enter (open the
        # selected file, or run the question in Ask mode).
        self._submit = Gtk.Button(label="↵")  # ↵ return glyph (U+21B5)
        self._submit.add_css_class("suggested-action")  # reads as the primary button
        self._submit.set_tooltip_text("Run — search or ask (Enter)")
        self._submit.connect("clicked",
                             lambda *_: self._on_entry_activate(self._entry))
        header.append(self._submit)
        self._close_btn = Gtk.Button(icon_name="window-close-symbolic")
        self._close_btn.add_css_class("flat")
        self._close_btn.set_tooltip_text("Close (Esc)")
        self._close_btn.connect("clicked", lambda *_: self.close())
        header.append(self._close_btn)
        box.append(header)

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

        # Copy button for mouse users — pinned to the top-right of the scroll
        # viewport (not the answer row) so it stays put while the answer scrolls.
        # Hidden until there's an answer AND the pointer is over the results;
        # unfocusable so Tab never lands on it (keyboard users use Ctrl+C).
        results_overlay = Gtk.Overlay()
        results_overlay.set_child(scrolled)
        self._copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self._copy_btn.add_css_class("osd")
        self._copy_btn.add_css_class("circular")
        self._copy_btn.set_tooltip_text(
            "Copy the whole answer as Markdown source "
            "(select text to copy just what you highlighted)")
        self._copy_btn.set_halign(Gtk.Align.END)
        self._copy_btn.set_valign(Gtk.Align.START)
        self._copy_btn.set_margin_top(6)
        self._copy_btn.set_margin_end(14)   # clear the scrollbar
        self._copy_btn.set_focusable(False)
        self._copy_btn.set_visible(False)
        self._copy_btn.connect("clicked", lambda *_: self._copy_answer())
        results_overlay.add_overlay(self._copy_btn)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._reveal_copy(True))
        motion.connect("leave", lambda *_a: self._reveal_copy(False))
        results_overlay.add_controller(motion)
        box.append(results_overlay)

        # Footer: an elapsed-time readout on the left (Ask mode), the vault scope
        # and mode toggle on the right. The timer label hexpands, pushing the
        # controls to the right edge.
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_margin_top(6)
        footer.set_margin_bottom(8)
        footer.set_margin_start(8)
        footer.set_margin_end(8)
        self._timer_label = Gtk.Label(xalign=0)
        self._timer_label.add_css_class("dim-label")
        self._timer_label.set_hexpand(True)
        footer.append(self._timer_label)
        # "Answer (n)" — the confirm button while picking sources (hidden otherwise).
        self._answer_btn = Gtk.Button(label="Answer")
        self._answer_btn.add_css_class("suggested-action")
        self._answer_btn.set_visible(False)
        self._answer_btn.connect("clicked", lambda *_: self._answer_from_selection())
        footer.append(self._answer_btn)
        self._scope_dropdown = None
        if self._scope:
            from .vault_scope import VaultScope
            self._scope_dropdown = VaultScope(
                self._scope["get_vaults_named"], self._scope["get_active"],
                self._scope["get_scope"], self._scope["set_scope"],
                on_change=self._on_scope_changed)
            footer.append(self._scope_dropdown)
        if ask_candidates is not None:
            self._pick_toggle = Gtk.ToggleButton()
            self._pick_toggle.set_icon_name("view-list-symbolic")
            self._pick_toggle.set_tooltip_text(
                "Pick sources — choose which notes to answer from")
            self._pick_toggle.connect("toggled", self._on_pick_toggled)
            footer.append(self._pick_toggle)
        if ask_answer is not None:
            self._ask_toggle = Gtk.ToggleButton()
            self._ask_toggle.set_icon_name("dialog-question-symbolic")
            self._ask_toggle.set_tooltip_text(
                "Ask — answer from your notes (instead of jumping to a file)")
            self._ask_toggle.connect("toggled", self._on_ask_toggled)
            footer.append(self._ask_toggle)
        if self._scope or ask_answer is not None:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(footer)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self, parent: Gtk.Widget) -> None:
        """Build a fresh index, show recent files and present over *parent*."""
        self._engine = self._make_engine()
        # Default to Ask mode only when it can actually work (semantic search
        # enabled AND the index is built) and the user hasn't picked a mode this
        # session — otherwise the file switcher stays the default it always was.
        if self._ask_answer is not None and not self._mode_locked:
            want = bool(self._can_ask()) if self._can_ask else False
            if want != self._ask_mode:
                self._suppress_toggle = True
                self._ask_toggle.set_active(want)  # updates mode + chrome
                self._suppress_toggle = False
        # In ask mode keep the last question beside its (still shown) answer, and
        # preselect it so the user can edit or replace it right away.
        if self._ask_mode and self._last_question:
            self._entry.set_text(self._last_question)
            self._entry.select_region(0, -1)
        else:
            self._entry.set_text("")
        self.refresh_scope()
        self._refresh()
        self.present(parent)
        self._entry.grab_focus()

    def refresh_scope(self) -> None:
        if self._scope_dropdown is not None:
            self._scope_dropdown.refresh()

    def get_last_question(self) -> str:
        """The most recently asked question — persisted across restarts so the
        palette reopens with the Ask entry pre-filled."""
        return self._last_question

    def set_last_question(self, text: str) -> None:
        self._last_question = text or ""

    def _on_scope_changed(self) -> None:
        """Scope changed via the dropdown: re-run the current search so it takes
        effect immediately — in Ask mode, re-answer the current/last question
        (instead of _refresh, which no-ops in Ask mode and looks inert)."""
        if self._ask_mode:
            question = self._entry.get_text().strip() or self._last_question
            if question:
                self._entry.set_text(question)
                self._run_ask()
                return
        self._refresh()

    def _scope_filter(self, results):
        """Keep only results under the currently scoped vault roots."""
        if not self._scope:
            return results
        roots = tuple(os.path.abspath(v) + os.sep
                      for v in self._scope["scope_vaults"]())
        return [r for r in results
                if os.path.abspath(r.path).startswith(roots)]

    def _refresh(self) -> None:
        if self._ask_mode:
            return  # the entry holds a question; answering runs on Enter
        query = self._entry.get_text().strip()
        self._clear()
        self._sem_generation += 1  # discard any in-flight semantic query
        if self._engine is None:
            return
        results = self._scope_filter(
            self._engine.search(query, limit=self.MAX_RESULTS))
        self._shown_paths = {r.path for r in results}
        if results:
            for r in results:
                self._results.append(self._build_row(r))
            first = self._results.get_row_at_index(0)
            if first is not None:
                self._results.select_row(first)
        else:
            self._results.append(self._message_row("No files"))
        self._request_semantic(query)

    def _request_semantic(self, query: str) -> None:
        """Fetch semantic matches off the main thread (the embed may be slow)."""
        if not self._semantic_query or len(query) < self._SEMANTIC_MIN_CHARS:
            return
        generation = self._sem_generation

        def worker():
            try:
                results = self._semantic_query(query)
            except Exception:
                logger.debug("semantic quick-open query failed", exc_info=True)
                results = []
            GLib.idle_add(self._append_semantic, generation, results)

        threading.Thread(target=worker, daemon=True).start()

    def _append_semantic(self, generation: int, results) -> bool:
        """Append semantic-only hits once they arrive (if still current)."""
        if generation != self._sem_generation:
            return False  # superseded by newer input
        fresh = self._scope_filter(
            [r for r in results if r.path not in self._shown_paths])
        if not fresh:
            return False
        # Drop the "No files" placeholder if that's all there is.
        first = self._results.get_row_at_index(0)
        if first is not None and getattr(first, "_mv_open", None) is None:
            self._results.remove(first)
        for r in fresh:
            self._shown_paths.add(r.path)
            self._results.append(self._build_row(r))
        if self._results.get_selected_row() is None:
            row = self._first_row()
            if row is not None:
                self._results.select_row(row)
        return False

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _build_row(self, result) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._mv_open = (result.path, 1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("quick-open-result")

        if getattr(result, "source", "") == "semantic":
            marker = Gtk.Label(label="≈")
            marker.add_css_class("dim-label")
            marker.set_tooltip_text("Semantic match")
            box.append(marker)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)

        # The hit's title is its vault-relative path ("<vault>/<path>", no .md)
        # so the vault and location are visible without a second line.  Bold the
        # characters of the current query that occur in that path.
        display = path_utils.vault_relative_name(result.path)
        name = Gtk.Label()
        name.set_xalign(0)
        name.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name.set_markup(_highlight_positions(display, _match_positions(
            self._entry.get_text().strip(), display)))
        text.append(name)

        # Only when a frontmatter alias is what matched, show it so the reason
        # for the hit stays clear (the path above wouldn't reveal it).
        if result.matched_text and result.matched_text != result.name:
            alias = Gtk.Label(label=f"≡ {result.matched_text}")
            alias.set_xalign(0)
            alias.add_css_class("dim-label")
            alias.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            text.append(alias)

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

    def _thinking_row(self) -> Gtk.ListBoxRow:
        """The 'Thinking…' placeholder with a spinner pinned to its right —
        a running-indicator while the (possibly slow, local) model answers."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label="Thinking…")
        label.set_xalign(0)
        label.set_hexpand(True)          # pushes the spinner to the right edge
        label.add_css_class("dim-label")
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        label.set_margin_start(8)
        box.append(label)
        spinner = Gtk.Spinner()
        spinner.set_margin_end(8)
        spinner.start()
        box.append(spinner)
        row.set_child(box)
        return row

    def _answer_row(self, text: str) -> Gtk.ListBoxRow:
        """The generated answer, rendered as Markdown (tables, lists, bold, …).
        Selectable as a keyboard stop (so ↓ lands here first) but not activatable
        (nothing to open). Two copy paths: a mouse selection + the label context
        menu copies the highlighted *visible* text, while the sticky copy button
        (and Ctrl+C on this row) copies the whole answer as Markdown *source*
        (``self._answer_text``). The copy button is sticky over the scroll area
        (see __init__), not on this row, so it stays put while the answer
        scrolls."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(True)
        row._mv_answer = True
        content = markdown_widgets.render_markdown(text)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        row.set_child(content)
        return row

    def _source_row(self, source) -> Gtk.ListBoxRow:
        """A citation row; activating it opens the note at the passage line."""
        row = Gtk.ListBoxRow()
        row._mv_open = (source.path, source.line)
        label = Gtk.Label(
            label=f"[{source.n}]  "
                  f"{path_utils.vault_relative_name(source.path)}:{source.line}")
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.add_css_class("mono")
        label.set_ellipsize(1)  # PANGO_ELLIPSIZE_START — keep the tail
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        label.set_margin_start(8)
        row.set_child(label)
        return row

    def _clear(self) -> None:
        child = self._results.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._results.remove(child)
            child = nxt
        self._has_answer = False
        self._copy_btn.set_visible(False)
        self._stop_ticking()
        if self._timer_label is not None:
            self._timer_label.set_text("")
        self._candidates = []
        self._selected = []
        if self._answer_btn is not None:
            self._answer_btn.set_visible(False)

    def _reveal_copy(self, show: bool) -> None:
        """Show the sticky copy button while the pointer is over the results —
        but only when there is actually an answer to copy."""
        self._copy_btn.set_visible(show and self._has_answer)

    # ------------------------------------------------------------------
    # Ask elapsed-time readout (footer, left) — ticks while the model runs
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_secs(seconds: float) -> str:
        return f"{seconds:.1f} s"          # period: the palette chrome is English

    def _start_timer(self) -> None:
        self._ask_started = time.monotonic()
        self._stop_ticking()
        self._timer_label.set_text(self._fmt_secs(0.0))
        self._timer_id = GLib.timeout_add(100, self._tick)

    def _tick(self) -> bool:
        self._timer_label.set_text(self._fmt_secs(time.monotonic() - self._ask_started))
        return True  # keep ticking

    def _stop_ticking(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    # ------------------------------------------------------------------
    # Activation & keyboard
    # ------------------------------------------------------------------

    def _on_row_activated(self, _list_box, row) -> None:
        self._activate(row)

    def _on_entry_activate(self, _entry) -> None:
        if self._ask_mode:
            if self._pick_active():
                self._show_candidates()
            else:
                self._run_ask()
            return
        row = self._results.get_selected_row() or self._first_row()
        self._activate(row)

    def _pick_active(self) -> bool:
        return (self._pick_toggle is not None and self._pick_toggle.get_active()
                and self._ask_candidates is not None)

    def _top_k(self) -> int:
        try:
            return max(1, int(self._get_top_k())) if self._get_top_k else 10
        except (TypeError, ValueError):
            return 10

    def _activate(self, row) -> None:
        target = getattr(row, "_mv_open", None) if row is not None else None
        if target is not None:
            self.close()
            self.emit("file-selected", target[0], target[1])

    # ------------------------------------------------------------------
    # Ask mode (RAG): answer from the user's notes instead of jumping to a file
    # ------------------------------------------------------------------

    def _on_ask_toggled(self, btn) -> None:
        self._ask_mode = btn.get_active()
        if not self._suppress_toggle:
            self._mode_locked = True  # remember the user's explicit choice
        self._ask_generation += 1  # cancel any in-flight answer
        self._clear()
        if self._ask_mode:
            self._entry.set_placeholder_text("Ask a question and press Enter…")
            self._results.append(self._message_row("Type a question, then Enter."))
        else:
            self._entry.set_placeholder_text("Go to file…")
            self._refresh()
        self._entry.grab_focus()

    def _run_ask(self) -> None:
        question = self._entry.get_text().strip()
        if not question or self._ask_answer is None:
            return
        self._last_question = question
        self._clear()
        self._results.append(self._thinking_row())
        self._start_timer()
        self._ask_generation += 1
        generation = self._ask_generation

        def worker():
            try:
                ans = self._ask_answer(question)
            except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
                logger.debug("ask failed", exc_info=True)
                from .ask import Answer
                ans = Answer(text="", error=str(exc))
            GLib.idle_add(self._show_answer, generation, ans)

        threading.Thread(target=worker, daemon=True).start()

    def _show_answer(self, generation: int, ans) -> bool:
        if generation != self._ask_generation:
            return False  # superseded by a newer question / mode switch
        elapsed = time.monotonic() - self._ask_started
        self._clear()          # also stops the ticking timer + clears its label
        if ans.error:
            self._results.append(self._message_row(f"Error: {ans.error}"))
            self._timer_label.set_text(self._fmt_secs(elapsed))
            return False
        self._answer_text = ans.text or "(empty answer)"
        self._results.append(self._answer_row(self._answer_text))
        for source in ans.sources:
            self._results.append(self._source_row(source))
        for warning in getattr(ans, "warnings", []):
            self._results.append(self._message_row(f"⚠ {warning}"))
        # Leave the total time in the footer, and arm the copy button (hidden
        # until the pointer hovers the results — up-front it distracts).
        self._timer_label.set_text(self._fmt_secs(elapsed))
        self._has_answer = True
        return False

    # ------------------------------------------------------------------
    # Pick-your-own-sources: offer the top candidates, answer from the chosen
    # ------------------------------------------------------------------

    def _on_pick_toggled(self, _btn) -> None:
        if self._ask_mode:
            self._clear()
            self._results.append(self._message_row("Type a question, then Enter."))

    def _show_candidates(self) -> None:
        question = self._entry.get_text().strip()
        if not question or self._ask_candidates is None:
            return
        self._pick_question = question
        self._last_question = question
        self._clear()
        self._results.append(self._message_row("Finding candidates…"))
        self._ask_generation += 1
        generation = self._ask_generation

        def worker():
            try:
                cands = self._ask_candidates(question)
            except Exception:  # noqa: BLE001 — surface an empty list to the UI
                logger.debug("candidate retrieval failed", exc_info=True)
                cands = []
            GLib.idle_add(self._show_candidate_list, generation, cands)

        threading.Thread(target=worker, daemon=True).start()

    def _show_candidate_list(self, generation: int, cands) -> bool:
        if generation != self._ask_generation:
            return False
        self._clear()
        if not cands:
            self._results.append(self._message_row("No candidate notes found."))
            return False
        self._candidates = cands
        self._selected = [p for p, _ in cands[:self._top_k()]]  # pre-select top-k
        top = max((s for _, s in cands), default=0.0) or 1.0
        for path, score in cands:
            self._results.append(self._candidate_row(path, score / top))
        self._update_answer_btn()
        first = self._results.get_row_at_index(0)
        if first is not None:
            self._results.select_row(first)
            first.grab_focus()
        return False

    def _candidate_row(self, path, relevance) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row._mv_pick_path = path
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(2)
        box.set_margin_bottom(2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        check = Gtk.CheckButton()
        check.set_active(path in self._selected)
        check.set_can_focus(False)          # keyboard focus stays on the row
        check.connect("toggled", self._on_candidate_toggled, path)
        row._mv_check = check
        box.append(check)
        title = Gtk.Label(label=path_utils.vault_relative_name(path))
        title.set_xalign(0)
        title.set_hexpand(True)
        title.set_ellipsize(3)              # PANGO_ELLIPSIZE_END
        box.append(title)
        # Relevance relative to the top candidate (0–100%): scale-free, so it
        # reads the same whether the scores are cosine or RRF, and it spreads
        # the batch instead of collapsing to a couple of rounded values.
        sc = Gtk.Label(label=f"{relevance:.0%}")
        sc.add_css_class("dim-label")
        sc.add_css_class("mono")
        box.append(sc)
        row.set_child(box)
        return row

    def _on_candidate_toggled(self, check, path) -> None:
        if check.get_active():
            if path in self._selected:
                pass
            elif len(self._selected) >= self._top_k():
                check.set_active(False)     # cap reached — refuse the extra pick
                return
            else:
                self._selected.append(path)
        elif path in self._selected:
            self._selected.remove(path)
        self._update_answer_btn()

    def _update_answer_btn(self) -> None:
        n = len(self._selected)
        self._answer_btn.set_label(f"Answer ({n})")
        self._answer_btn.set_sensitive(n >= 1)
        self._answer_btn.set_visible(True)
        self._timer_label.set_text(f"{n}/{self._top_k()} selected")

    def _answer_from_selection(self) -> None:
        if not self._selected or self._ask_answer_selected is None:
            return
        question = self._pick_question
        paths = list(self._selected)
        self._clear()
        self._results.append(self._thinking_row())
        self._start_timer()
        self._ask_generation += 1
        generation = self._ask_generation

        def worker():
            try:
                ans = self._ask_answer_selected(question, paths)
            except Exception as exc:  # noqa: BLE001 — surface failure to the UI
                logger.debug("ask (selected) failed", exc_info=True)
                from .ask import Answer
                ans = Answer(text="", error=str(exc))
            GLib.idle_add(self._show_answer, generation, ans)

        threading.Thread(target=worker, daemon=True).start()

    def _on_entry_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            row = self._first_stop()
            if row is not None:
                self._results.select_row(row)
                row.grab_focus()
                return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            return True  # keep focus in the entry
        return False

    def _on_results_key(self, _ctrl, keyval, _keycode, state) -> bool:
        # Pick-sources: Space toggles the highlighted candidate, Enter answers.
        sel = self._results.get_selected_row()
        if keyval == Gdk.KEY_space and sel is not None and hasattr(sel, "_mv_check"):
            sel._mv_check.set_active(not sel._mv_check.get_active())
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._candidates:
            self._answer_from_selection()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            if self._results.get_selected_row() is self._first_stop():
                self._entry.grab_focus()
                return True
        if (state & Gdk.ModifierType.CONTROL_MASK
                and keyval in (Gdk.KEY_c, Gdk.KEY_C)):
            sel = self._results.get_selected_row()
            if sel is not None and getattr(sel, "_mv_answer", False):
                self._copy_answer()
                return True
        return False

    def _first_stop(self):
        # First keyboard-landing row: the first *selectable* row. In Ask mode
        # that is the answer row (↓ lands there to copy before the citations);
        # in file mode it is the first result; a lone message row is
        # non-selectable → None (↓ stays inert).
        i = 0
        while (row := self._results.get_row_at_index(i)) is not None:
            if row.get_selectable():
                return row
            i += 1
        return None

    def _first_row(self):
        # First activatable (openable) row — Enter-to-open fallback (file mode).
        i = 0
        while (row := self._results.get_row_at_index(i)) is not None:
            if getattr(row, "_mv_open", None) is not None:
                return row
            i += 1
        return None

    def _copy_answer(self) -> None:
        if self._answer_text:
            self.get_clipboard().set(self._answer_text)
            self._toast_overlay.add_toast(Adw.Toast(title="Copied!", timeout=2))


def _match_positions(query: str, text: str) -> list:
    """Char indices in *text* the fuzzy *query* matches, for bolding the title.

    Recomputed against the *displayed* path (not the ranking's positions, which
    index the file stem or an alias), so the highlight lines up with what is
    shown.  Empty when there is no query or no subsequence match.
    """
    if not query:
        return []
    hit = fuzzy_match(query, text)
    return hit[1] if hit else []


def _highlight_positions(name: str, positions: list) -> str:
    """Pango markup for *name* with the matched *positions* bolded."""
    marked = set(positions)
    out: list[str] = []
    for i, ch in enumerate(name):
        esc = GLib.markup_escape_text(ch)
        out.append(f"<b>{esc}</b>" if i in marked else esc)
    return "".join(out)
