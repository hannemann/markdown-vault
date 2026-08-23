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

from markdown_vault.search import search_logic
from markdown_vault.preview import markdown_widgets
from markdown_vault.markdown import frontmatter
from markdown_vault.core import path_utils
from markdown_vault.search.quick_open import fuzzy_match

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
    _ASK_TOOLTIP = "Ask — answer from your notes (instead of jumping to a file)"

    def __init__(self, make_engine, semantic_query=None, ask_answer=None,
                 scope=None, can_ask=None, ask_hint=None, ask_status=None,
                 ask_recheck=None, ask_candidates=None,
                 ask_answer_selected=None, get_top_k=None,
                 list_ask_models=None, set_ask_model=None,
                 current_ask_model=None, hide_deprecated=None,
                 set_hide_deprecated=None, open_ask_settings=None) -> None:
        super().__init__()
        self._make_engine = make_engine
        self._semantic_query = semantic_query  # callable(query) -> list, off-thread
        self._hide_deprecated = hide_deprecated  # callable() -> bool, shared state
        self._set_hide_deprecated = set_hide_deprecated  # callable(bool), sets it
        self._raw_file_results: list = [] # unfiltered filename hits (for re-render)
        self._raw_sem_results: list = []  # unfiltered semantic hits (for re-render)
        self._ask_answer = ask_answer          # callable(question) -> ask.Answer
        self._can_ask = can_ask                # () -> bool: is Ask usable right now?
        self._ask_hint = ask_hint              # () -> str: why it is not, if not
        self._ask_toggle = None                # built only when Ask is wired up
        # The Ask server's own verdict about itself: ask_status() -> EndpointStatus
        # (None when no server is involved, i.e. the local backend), ask_recheck()
        # probes again. Shown as a banner, and it gates submitting.
        self._ask_status = ask_status
        self._ask_recheck = ask_recheck
        # For a local-model verdict the banner leads into the settings instead of
        # re-probing: open_ask_settings() closes the palette and opens Preferences
        # at Search -> Ask.
        self._open_ask_settings = open_ask_settings
        self._banner = None
        self._pending_question = ""     # held until a running probe has settled
        self._checking = False          # a "Try again" check is showing its row
        # "Pick your own sources": candidates() -> [(path, score)], and
        # answer_selected(question, paths) -> ask.Answer; get_top_k() -> int cap.
        self._ask_candidates = ask_candidates
        self._ask_answer_selected = ask_answer_selected
        self._get_top_k = get_top_k
        # Footer model picker (Ask): list_ask_models() -> [(name, path)],
        # set_ask_model(path), current_ask_model() -> path. Shown only with >1.
        self._list_ask_models = list_ask_models
        self._set_ask_model = set_ask_model
        self._current_ask_model = current_ask_model
        self._model_dropdown = None
        self._model_paths: list[str] = []
        self._model_updating = False
        self._phase_label = None        # the status row's label (loading/reading)
        self._phase_key = "initializing"  # current status phase
        self._stream_label = None       # live answer label while tokens stream in
        self._stream_text = ""          # accumulated streamed text
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
        self._ask_busy = False          # an answer worker is running (WaitIdle)
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
        # Closing bumps the generation, which both discards a late answer and
        # (via should_cancel) aborts an in-flight local decode instead of letting
        # the model keep churning in the background.
        self.connect("closed", lambda *_: self._on_closed())

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

        # What the Ask server said about itself — a banner rather than a result row,
        # because _clear() wipes the result list on every question, and this notice
        # must survive exactly the moment the user asks anyway.
        self._banner = Adw.Banner(revealed=False)
        self._banner.set_button_label("Try again")
        self._banner.connect("button-clicked", lambda *_: self._on_banner_clicked())
        box.append(self._banner)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Always-visible top bar: the persistent "hide deprecated" toggle plus the
        # vault-scope filter (moved here from the footer). The shared tree toggle is
        # unreachable behind this modal, so the dialog carries its own. When the
        # filter hides hits, _render_all shows a count so the user knows why.
        self._dep_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._dep_bar.add_css_class("search-deprecated-bar")
        self._dep_bar.append(Gtk.Box(hexpand=True))   # right-align the filters
        self._scope_dropdown = None
        if self._scope:
            from markdown_vault.search.vault_scope import VaultScope
            self._scope_dropdown = VaultScope(
                self._scope["get_vaults_named"], self._scope["get_active"],
                self._scope["get_scope"], self._scope["set_scope"],
                on_change=self._on_scope_changed)
            self._dep_bar.append(self._scope_dropdown)
        # Pick-sources toggle (Ask) — between the vault filter and the deprecated
        # toggle (moved up here from the footer).
        self._pick_toggle = None
        if self._ask_candidates is not None:
            self._pick_toggle = Gtk.ToggleButton()
            self._pick_toggle.set_icon_name("view-list-symbolic")
            self._pick_toggle.set_tooltip_text(
                "Pick sources — choose which notes to answer from")
            self._pick_toggle.connect("toggled", self._on_pick_toggled)
            self._dep_bar.append(self._pick_toggle)
        self._dep_toggle = Gtk.ToggleButton(icon_name="view-conceal-symbolic")
        self._dep_toggle.set_tooltip_text("Hide deprecated notes")
        if self._hide_deprecated is not None:
            self._dep_toggle.set_active(self._hide_deprecated())
        self._dep_toggle.connect("toggled", self._on_dep_toggled)
        self._dep_bar.append(self._dep_toggle)
        box.append(self._dep_bar)
        self.connect("map", lambda *_: self._sync_dep_toggle())

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
        # Model picker (Ask): only appears with more than one downloaded model.
        if self._list_ask_models is not None:
            self._model_list = Gtk.StringList()
            self._model_dropdown = Gtk.DropDown(model=self._model_list)
            self._model_dropdown.set_tooltip_text("Answer model")
            self._model_dropdown.set_visible(False)
            self._model_dropdown.connect("notify::selected", self._on_model_selected)
            footer.append(self._model_dropdown)
        if ask_answer is not None:
            self._ask_toggle = Gtk.ToggleButton()
            self._ask_toggle.set_icon_name("dialog-question-symbolic")
            self._ask_toggle.set_tooltip_text(self._ASK_TOOLTIP)
            self._ask_toggle.connect("toggled", self._on_ask_toggled)
            footer.append(self._ask_toggle)
        if self._scope or ask_answer is not None:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            box.append(footer)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def refresh_ask_availability(self) -> None:
        """Grey the Ask toggle out while Ask cannot answer, and say why.

        A control that takes the click and only then explains itself is worse than
        one that shows up front it is unavailable — and a mode the user picked
        earlier must not strand the palette in Ask once it stops working.
        """
        if self._ask_toggle is None:
            return
        usable = bool(self._can_ask()) if self._can_ask else True
        hint = (self._ask_hint() if self._ask_hint else "") if not usable else ""
        self._ask_toggle.set_sensitive(usable)
        self._ask_toggle.set_tooltip_text(
            f"{self._ASK_TOOLTIP}\n{hint}" if hint else self._ASK_TOOLTIP)
        if not usable and self._ask_mode:
            self._suppress_toggle = True         # not the user's choice — no lock
            self._ask_toggle.set_active(False)   # updates mode + chrome
            self._suppress_toggle = False

    def refresh_endpoint_status(self) -> bool:
        """Reflect what the Ask server answered: banner, picker, submit lock — and
        release a question that was held while the probe was still out.

        Called on open and whenever a probe settles, so a verdict that arrives while
        the user is still typing takes effect there and then. Returns ``False`` so it
        can be handed to ``GLib.idle_add`` directly.
        """
        # Only ask the verdict in Ask mode: for the local backend it runs
        # llama_runtime.availability() (a file check, and a llama_cpp import), and
        # a plain Ctrl+Space file switch has no use for it.
        st = self._ask_status() if (self._ask_status and self._ask_mode) else None
        if self._banner is not None:
            message = st.message if (st is not None and self._ask_mode) else ""
            local = st is not None and getattr(st, "is_local", False)
            self._banner.set_button_label("Settings" if local else "Try again")
            self._banner.set_title(message)
            self._banner.set_revealed(bool(message))
        self._update_submit_state(st)
        self._refresh_models()
        if st is None or st.pending:
            return False
        # The verdict is in. A question typed while the check was out runs now — or
        # is dropped, because the verdict is that asking cannot work (the banner says
        # so). Either way nothing stale may be left standing: not a "Checking the
        # server…" row, and not the error of the attempt the user just re-checked.
        question, self._pending_question = self._pending_question, ""
        was_checking, self._checking = self._checking, False
        if question and st.can_ask:
            self._entry.set_text(question)
            self._run_ask()
        elif question or was_checking:
            self._show_ask_idle()
        return False

    def _update_submit_state(self, st) -> None:
        """Submit and Enter are locked only when asking is certain to fail — an
        empty list or a missing list endpoint still answers questions."""
        blocked = bool(self._ask_mode and st is not None and not st.can_ask)
        self._submit.set_sensitive(not blocked)
        self._submit.set_tooltip_text(
            st.message if blocked else "Run — search or ask (Enter)")

    def recheck_if_stale(self) -> None:
        """On opening or entering Ask mode, probe again unless the last verdict was
        "models listed".

        A verdict that can change must not stick for the session — starting the
        server and reopening the palette is the natural retry. Verdicts that are
        properties of the server (a usable list, or no list endpoint at all) are
        left alone: re-probing them would cost a round trip on the Ctrl+Space path
        for a configuration that is working.
        """
        if not self._ask_mode:      # nothing to re-probe until Ask is actually used
            return
        st = self._ask_status() if self._ask_status else None
        if st is None or st.pending or not st.transient:
            return
        self._on_banner_retry()

    def _on_banner_clicked(self) -> None:
        """The banner button follows the verdict: a local-model error leads into
        the settings to fix it (close the palette, open Preferences at Search →
        Ask); a server error re-probes without leaving the palette."""
        st = self._ask_status() if self._ask_status else None
        if (st is not None and getattr(st, "is_local", False)
                and self._open_ask_settings is not None):
            self.close()
            GLib.idle_add(self._open_ask_settings)
        else:
            self._on_banner_retry()

    def _on_banner_retry(self) -> None:
        """"Try again": probe the server once more without leaving the palette.

        The error of the previous attempt goes away with the click — re-checking is
        the user saying "that was then", so keeping the old message on screen while a
        new check runs would be one more stale statement.
        """
        if self._ask_recheck is None:
            return
        self._checking = True
        self._phase_key = "checking"
        self._clear()
        self._results.append(self._status_row())
        self._ask_recheck()

    def open(self, parent: Gtk.Widget) -> None:
        """Build a fresh index, show recent files and present over *parent*."""
        self._engine = self._make_engine()
        self.refresh_ask_availability()
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
        self.recheck_if_stale()          # a server started since must become usable
        self.refresh_endpoint_status()   # list_for's probe may already be out
        self._refresh()
        self.present(parent)
        self._entry.grab_focus()

    def run_query(self, text: str) -> None:
        """Set the query programmatically (debug/automation): types into the entry.
        Submitting (Enter) is a separate step — see :meth:`submit`."""
        self._entry.set_text(text or "")

    def submit(self) -> None:
        """Activate the palette as pressing Enter would: in Ask mode this answers
        the question, in filename mode it opens the selected/first result."""
        self._on_entry_activate(self._entry)

    def ask_answer_text(self) -> str:
        """The current Ask answer's raw Markdown (debug/automation); grows while the
        answer streams, ``''`` until the first token. Poll until it stops changing."""
        return self._answer_text or ""

    def is_idle(self) -> bool:
        """False from Submit until the answer worker finishes (or the palette
        closes). The Ask half of WaitIdle's quiescence check, so a
        Submit()→WaitIdle()→AskAnswer() sequence waits for the *whole* answer —
        not just prefill (the elapsed timer stops at the first streamed token)."""
        return not self._ask_busy

    def _on_closed(self) -> None:
        """Invalidate any in-flight answer so it is both dropped and aborted."""
        self._abandon_answer()
        self._stop_ticking()

    def refresh_scope(self) -> None:
        if self._scope_dropdown is not None:
            self._scope_dropdown.refresh()
        self._refresh_models()

    def refresh_models(self) -> None:
        """Repopulate the footer picker — also called when a background fetch of
        a server's model list has arrived. Safe to call when it is hidden."""
        self._refresh_models()
        return False        # usable directly as a GLib.idle_add callback

    def _refresh_models(self) -> None:
        """Populate and show the footer model picker.

        For a **server** backend it stays visible whenever Ask mode is on, and goes
        insensitive when there is no usable list — hiding it made "the server is
        unreachable" indistinguishable from "one local model". For the **local**
        backend one downloaded model is no choice, so the picker stays hidden.
        """
        if self._model_dropdown is None:
            return
        models = self._list_ask_models() if self._list_ask_models else []
        st = self._ask_status() if self._ask_status else None
        if st is None and len(models) < 2:
            self._model_dropdown.set_visible(False)
            return
        self._model_updating = True
        self._model_paths = [p for _, p in models]
        self._model_list.splice(0, self._model_list.get_n_items(),
                                [n for n, _ in models])
        current = self._current_ask_model() if self._current_ask_model else None
        if current in self._model_paths:
            self._model_dropdown.set_selected(self._model_paths.index(current))
        self._model_updating = False
        self._model_dropdown.set_sensitive(st is None or st.models_usable)
        self._model_dropdown.set_visible(self._ask_mode)

    def _on_model_selected(self, dropdown, _pspec) -> None:
        if self._model_updating or self._set_ask_model is None:
            return
        i = dropdown.get_selected()
        if 0 <= i < len(self._model_paths):
            self._set_ask_model(self._model_paths[i])

    def get_last_question(self) -> str:
        """The most recently asked question — persisted across restarts so the
        palette reopens with the Ask entry pre-filled."""
        return self._last_question

    def set_last_question(self, text: str) -> None:
        self._last_question = text or ""

    def _on_scope_changed(self) -> None:
        """Scope changed via the dropdown.

        In **Ask mode** nothing is answered: that would be a full model run —
        minutes of work and a request to the server — started by a dropdown. The
        question stays in the entry and the user submits it (the submit button, or
        Enter while the entry has the focus). The previous answer is dropped, because
        it was computed over the vaults that are no longer selected, and the palette
        falls back to its plain Ask state — no separate sentence narrating a change
        the user just made themselves.

        In **file mode** the list is refreshed right away: no model, no network, just
        a filter over the loaded candidates — and leaving it would show files from
        the vault that was just deselected.
        """
        if self._ask_mode:
            self._abandon_answer()      # a running answer belongs to the old scope
            self._show_ask_idle()
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
        self._sem_generation += 1  # discard any in-flight semantic query
        if self._engine is None:
            self._raw_file_results = []
            self._raw_sem_results = []
            self._render_all()
            return
        self._raw_file_results = self._scope_filter(
            self._engine.search(query, limit=self.MAX_RESULTS))
        self._raw_sem_results = []
        self._render_all()
        self._request_semantic(query)

    def _render_all(self) -> None:
        """Rebuild the list from the cached filename + semantic hits, hiding
        deprecated notes when the shared toggle is on. Toggling re-renders from
        here — no re-query, so a fresh (semantic) pass needs a new search. When the
        filter hides hits, show a count so the user isn't left with a bare "No
        files"."""
        self._clear()
        hide = self._hide_deprecated is not None and self._hide_deprecated()
        self._shown_paths = set()
        hidden: set = set()
        for r in self._raw_file_results + self._raw_sem_results:
            if hide and frontmatter.status_of(r.path) == "deprecated":
                hidden.add(r.path)
                continue
            self._shown_paths.add(r.path)
            self._results.append(self._build_row(r))
        empty = self._results.get_row_at_index(0) is None
        if hidden:
            self._results.append(self._message_row(
                search_logic.deprecated_hidden_message(len(hidden), empty)))
        elif empty:
            self._results.append(self._message_row("No files"))
        first = self._results.get_row_at_index(0)
        if first is not None and getattr(first, "_mv_open", None) is not None:
            self._results.select_row(first)

    def _on_dep_toggled(self, btn) -> None:
        """The persistent toggle drives the shared 'hide deprecated' state; the
        file/semantic view re-renders (Ask applies it on the next question)."""
        if self._set_hide_deprecated is not None:
            self._set_hide_deprecated(btn.get_active())
        if not self._ask_mode:
            self._render_all()

    def _sync_dep_toggle(self) -> None:
        """Reflect the shared state on the toggle (e.g. changed from the tree while
        the dialog was closed) without re-firing the handler."""
        active = self._hide_deprecated is not None and self._hide_deprecated()
        self._dep_toggle.handler_block_by_func(self._on_dep_toggled)
        self._dep_toggle.set_active(active)
        self._dep_toggle.handler_unblock_by_func(self._on_dep_toggled)

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
        """Cache the semantic-only hits once they arrive (if still current) and
        re-render with the current filter."""
        if generation != self._sem_generation:
            return False  # superseded by newer input
        file_paths = {r.path for r in self._raw_file_results}
        self._raw_sem_results = self._scope_filter(
            [r for r in results if r.path not in file_paths])
        if self._raw_sem_results:
            self._render_all()
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
        label.set_wrap(True)            # long notices must wrap inside the palette
        label.add_css_class("dim-label")
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        label.set_margin_start(8)
        label.set_margin_end(8)
        row.set_child(label)
        return row

    #: Status phase → label shown in the running row (timer appended live).
    #: 'initializing' is the placeholder while the worker spins up + retrieves,
    #: before any backend phase fires; 'reading' is the prefill (reading the
    #: prompt/notes — usually the longest part), 'writing' the token generation;
    #: 'thinking' is a fallback (servers).
    _PHASE_TEXT = {"initializing": "Initializing…", "loading": "Loading model…",
                   "reading": "Reading your notes…", "writing": "Writing the answer…",
                   "thinking": "Thinking…", "checking": "Checking the server…"}

    def _status_row(self) -> Gtk.ListBoxRow:
        """The running-status row with a spinner — its label reflects the current
        phase ('Loading model…' only when the model actually loads, else
        'Thinking…') with the elapsed time ticking behind it."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(xalign=0)
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
        self._phase_label = label
        self._render_phase()
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

    def _source_row(self, source, considered: bool = False) -> Gtk.ListBoxRow:
        """A source row; activating it opens the note at the passage line. A
        *considered* row is a note that was retrieved (in the model's context)
        but not cited — shown without the ``[n]`` marker and further dimmed, so
        the full evidence set is visible without diluting the real citations."""
        row = Gtk.ListBoxRow()
        row._mv_open = (source.path, source.line)
        # No line suffix: Ask retrieval is note-level (every passage anchors at
        # line 1), so ":1" would be constant noise. The click still opens the
        # note via _mv_open below.
        prefix = "" if considered else f"[{source.n}]  "
        label = Gtk.Label(
            label=f"{prefix}{path_utils.vault_relative_name(source.path)}")
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.add_css_class("mono")
        label.set_ellipsize(1)  # PANGO_ELLIPSIZE_START — keep the tail
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        label.set_margin_start(8)
        if considered:
            label.set_opacity(0.55)   # retrieved but not cited — secondary
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
        self._stream_label = None
        self._stream_text = ""
        self._answer_text = ""   # reset with the stream, so a stale answer can't linger
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

    def _render_phase(self) -> None:
        """Draw the running row's label: '<phase>  <elapsed>'."""
        if self._phase_label is None:
            return
        text = self._PHASE_TEXT.get(self._phase_key, "Thinking…")
        elapsed = self._fmt_secs(time.monotonic() - self._ask_started)
        self._phase_label.set_text(f"{text}  {elapsed}")

    def _set_phase(self, generation: int, phase: str) -> bool:
        """Switch the status phase (called from the worker via idle_add)."""
        if generation == self._ask_generation:
            self._phase_key = phase
            self._render_phase()
        return False  # one-shot idle callback

    def _start_timer(self) -> None:
        self._ask_started = time.monotonic()
        self._phase_key = "initializing"
        self._stop_ticking()
        self._render_phase()
        self._timer_id = GLib.timeout_add(100, self._tick)

    def _tick(self) -> bool:
        self._render_phase()
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
            st = self._ask_status() if self._ask_status else None
            if st is not None and not st.can_ask:
                return              # the banner already says why; keep the question
            if st is not None and st.pending:
                self._hold_question()   # the check is out — wait for the verdict
                return
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
            # unset/invalid top-k setting → default of 10
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
        self._abandon_answer()  # cancel any in-flight answer
        self._clear()
        if self._ask_mode:
            self._entry.set_placeholder_text("Ask a question and press Enter…")
            self._show_ask_idle()
        else:
            self._entry.set_placeholder_text("Go to file…")
            self._refresh()
        # Banner, picker and submit lock are all Ask-mode only.
        self.recheck_if_stale()          # entering Ask is a retry, like reopening
        self.refresh_endpoint_status()
        self._entry.grab_focus()

    def _show_ask_idle(self) -> None:
        """The plain Ask-mode state — nothing asked, nothing to report. One place,
        so every path that has to drop a stale row lands on the same screen.

        The hint names ``↵`` rather than "Enter": that is the submit button's own
        label, so it covers both ways of running the question. Promising the key
        alone would be false as soon as the focus leaves the entry.
        """
        self._clear()
        self._results.append(self._message_row("Type a question, then ↵"))

    def _hold_question(self) -> None:
        """Keep the typed question until the server check settles, instead of firing
        it blind: a dead host would take it into the chat call's 120 s timeout."""
        question = self._entry.get_text().strip()
        if not question:
            return
        self._pending_question = question
        self._phase_key = "checking"
        self._clear()
        self._results.append(self._status_row())

    def _abandon_answer(self) -> None:
        """Discard any in-flight answer: bump the generation (so a late worker's
        _show_answer fails the supersede check) *and* clear the busy flag, in one
        place. Every abandon and every restart goes through here, so "generation
        bumped" and "no answer in flight" can never drift apart again.

        A question held for a pending server check is dropped here too — same path,
        so "held question" and "running answer" cannot grow separate abort routes. A
        late verdict must not fire a question into a dialog the user has left."""
        self._ask_generation += 1
        self._ask_busy = False
        self._pending_question = ""

    def _run_ask(self) -> None:
        question = self._entry.get_text().strip()
        if not question or self._ask_answer is None:
            return
        self._last_question = question
        self._clear()
        self._results.append(self._status_row())
        self._start_timer()
        self._abandon_answer()         # cancel any previous answer, then...
        self._ask_busy = True          # ...mark this one busy (until _show_answer)
        generation = self._ask_generation
        on_phase = lambda p, g=generation: GLib.idle_add(self._set_phase, g, p)
        on_token = lambda t, g=generation: GLib.idle_add(self._stream_delta, g, t)
        should_cancel = lambda g=generation: self._ask_generation != g

        def worker():
            try:
                ans = self._ask_answer(question, on_phase=on_phase,
                                       on_token=on_token,
                                       should_cancel=should_cancel)
            except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
                logger.debug("ask failed", exc_info=True)
                from markdown_vault.search.ask import Answer
                ans = Answer(text="", error=str(exc))
            GLib.idle_add(self._show_answer, generation, ans)

        threading.Thread(target=worker, daemon=True).start()

    def _duration_row(self, prefix: str, elapsed: float) -> Gtk.ListBoxRow:
        """A dim '<prefix> X.X s' line closing off the answer (the live timer is
        inline in the status row, so the total goes here, not the footer)."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        label = Gtk.Label(label=f"{prefix} {self._fmt_secs(elapsed)}")
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_margin_top(4)
        label.set_margin_bottom(6)
        label.set_margin_start(8)
        row.set_child(label)
        return row

    def _stream_delta(self, generation: int, text: str) -> bool:
        """Show the streamed answer-so-far in a live row (created on the first
        token, replacing the 'Reading…' status). *text* is the FULL visible text
        each time — set, don't append — because it can shrink or shift its prefix
        at a </think> boundary. _show_answer replaces the row with the final
        Markdown render + citations when generation finishes."""
        if generation != self._ask_generation:
            return False  # superseded
        if self._stream_label is None:
            self._clear()                      # drop the status row + stop ticking
            self._stream_label = Gtk.Label(xalign=0, wrap=True, selectable=False)
            for m in (self._stream_label.set_margin_top,
                      self._stream_label.set_margin_bottom,
                      self._stream_label.set_margin_start,
                      self._stream_label.set_margin_end):
                m(8)
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            row.set_child(self._stream_label)
            self._results.append(row)
        self._stream_text = text
        self._answer_text = text   # keep the accessor current while streaming
        self._stream_label.set_text(text)
        return False

    def _show_answer(self, generation: int, ans) -> bool:
        if generation != self._ask_generation:
            return False  # superseded by a newer question / mode switch
        self._ask_busy = False     # this answer (success or error) is done
        elapsed = time.monotonic() - self._ask_started
        self._clear()          # also stops the ticking timer + clears its label
        if ans.error:
            self._results.append(self._message_row(f"Error: {ans.error}"))
            self._results.append(self._duration_row("Failed after", elapsed))
            # The failure may have proved the server is gone (the answer path
            # records that): show the banner and lock submitting now, instead of
            # letting the user fire the same question again and again.
            self.refresh_endpoint_status()
            return False
        self._answer_text = ans.text or "(empty answer)"
        self._results.append(self._answer_row(self._answer_text))
        for source in ans.sources:
            self._results.append(self._source_row(source))
        for source in getattr(ans, "considered", []):
            self._results.append(self._source_row(source, considered=True))
        for warning in getattr(ans, "warnings", []):
            self._results.append(self._message_row(f"⚠ {warning}"))
        # The total time closes off the answer; arm the copy button (hidden until
        # the pointer hovers the results — up-front it distracts).
        self._results.append(self._duration_row("Answered in", elapsed))
        self._has_answer = True
        return False

    # ------------------------------------------------------------------
    # Pick-your-own-sources: offer the top candidates, answer from the chosen
    # ------------------------------------------------------------------

    def _on_pick_toggled(self, _btn) -> None:
        if self._ask_mode:
            self._show_ask_idle()

    def _show_candidates(self) -> None:
        question = self._entry.get_text().strip()
        if not question or self._ask_candidates is None:
            return
        self._pick_question = question
        self._last_question = question
        self._clear()
        self._results.append(self._message_row("Finding candidates…"))
        self._abandon_answer()   # a candidate search supersedes any in-flight answer
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
        self._results.append(self._status_row())
        self._start_timer()
        self._abandon_answer()         # cancel any previous answer, then...
        self._ask_busy = True          # ...mark this one busy (until _show_answer)
        generation = self._ask_generation
        on_phase = lambda p, g=generation: GLib.idle_add(self._set_phase, g, p)
        on_token = lambda t, g=generation: GLib.idle_add(self._stream_delta, g, t)
        should_cancel = lambda g=generation: self._ask_generation != g

        def worker():
            try:
                ans = self._ask_answer_selected(question, paths, on_phase=on_phase,
                                                on_token=on_token,
                                                should_cancel=should_cancel)
            except Exception as exc:  # noqa: BLE001 — surface failure to the UI
                logger.debug("ask (selected) failed", exc_info=True)
                from markdown_vault.search.ask import Answer
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
