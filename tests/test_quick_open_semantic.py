"""Tests for the quick-open palette: async semantic append, and the Ask toggle's
availability (it must not offer a mode that cannot answer)."""

import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

Adw.init()

from markdown_vault.search.quick_open_palette import QuickOpenPalette
from markdown_vault.search.quick_open import QuickResult


def _sem(path):
    return QuickResult(path=path, name="note", folder="/v", score=0.5, source="semantic")


class TestSemanticAppend(unittest.TestCase):
    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [])

    def _row_count(self, p):
        n = 0
        child = p._results.get_first_child()
        while child is not None:
            n += 1
            child = child.get_next_sibling()
        return n

    def test_appends_fresh_semantic_rows(self):
        p = self._palette()
        p._sem_generation = 5
        p._shown_paths = set()
        p._append_semantic(5, [_sem("/v/note.md")])
        self.assertEqual(self._row_count(p), 1)
        self.assertIn("/v/note.md", p._shown_paths)

    def test_stale_generation_is_ignored(self):
        p = self._palette()
        p._sem_generation = 5
        p._shown_paths = set()
        p._append_semantic(4, [_sem("/v/note.md")])  # older generation
        self.assertEqual(self._row_count(p), 0)

    def test_already_shown_not_duplicated(self):
        p = self._palette()
        p._sem_generation = 5
        p._raw_file_results = [_sem("/v/note.md")]  # already a filename hit
        p._append_semantic(5, [_sem("/v/note.md")])
        self.assertEqual(p._raw_sem_results, [])    # deduped against the file hit

    def test_request_semantic_skips_short_queries(self):
        calls = []
        p = QuickOpenPalette(make_engine=lambda: None,
                             semantic_query=lambda q: calls.append(q) or [])
        p._request_semantic("a")  # below _SEMANTIC_MIN_CHARS
        self.assertEqual(calls, [])


class TestFirstRow(unittest.TestCase):
    """R37.1 — ↓ must reach the citation rows past the non-openable answer row."""

    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [])

    def test_first_row_skips_non_openable_answer_row(self):
        from markdown_vault.search.ask import Source
        p = self._palette()
        p._results.append(p._answer_row("the answer"))          # no _mv_open
        p._results.append(p._source_row(Source(1, "/v/cite.md", 42)))  # openable
        first = p._first_row()
        self.assertIsNotNone(first)
        self.assertEqual(first._mv_open, ("/v/cite.md", 42))

    def test_message_only_yields_none(self):
        p = self._palette()
        p._results.append(p._message_row("No files"))
        self.assertIsNone(p._first_row())

    def test_first_stop_lands_on_answer_before_citations(self):
        from markdown_vault.search.ask import Source
        p = self._palette()
        answer = p._answer_row("the answer")
        p._results.append(answer)
        p._results.append(p._source_row(Source(1, "/v/cite.md", 42)))
        self.assertIs(p._first_stop(), answer)  # ↓ lands on the answer first

    def test_copy_answer_yields_the_markdown_source(self):
        # The answer is rendered as Markdown, but copying must give the raw
        # source (pipes and all), not the rendered surface.
        p = self._palette()
        p._answer_text = "| a | b |\n|---|---|\n| 1 | 2 |"
        copied = {}
        p.get_clipboard = lambda: type("C", (), {"set": lambda _s, v:
                                                  copied.setdefault("v", v)})()
        p._toast_overlay = type("T", (), {"add_toast": lambda *_a: None})()
        p._copy_answer()
        self.assertEqual(copied["v"], "| a | b |\n|---|---|\n| 1 | 2 |")

    def test_rendered_answer_content_is_selectable_for_visible_copy(self):
        # rendered text is selectable so select + context menu copies the
        # visible text; the button (below) still copies the Markdown source.
        p = self._palette()
        row = p._answer_row("**bold** and a table")
        label = row.get_child().get_first_child()
        self.assertTrue(label.get_selectable())

    def test_copy_button_tooltip_mentions_markdown_source(self):
        p = self._palette()
        self.assertIn("Markdown source", p._copy_btn.get_tooltip_text())

    def test_answer_copy_button_hidden_and_unfocusable(self):
        # R39.1 — the sticky copy button must not be a hidden Tab stop: it starts
        # invisible (out of hit-test/a11y) and is never focusable.
        p = self._palette()
        self.assertFalse(p._copy_btn.get_visible())
        self.assertFalse(p._copy_btn.get_focusable())

    def test_reveal_copy_needs_an_answer(self):
        # Hovering reveals the button only when there is an answer to copy.
        p = self._palette()
        p._reveal_copy(True)
        self.assertFalse(p._copy_btn.get_visible())   # no answer yet
        p._has_answer = True
        p._reveal_copy(True)
        self.assertTrue(p._copy_btn.get_visible())
        p._reveal_copy(False)
        self.assertFalse(p._copy_btn.get_visible())

    def test_last_question_accessors_persist_value(self):
        p = self._palette()
        p.set_last_question("which planet is heaviest?")
        self.assertEqual(p.get_last_question(), "which planet is heaviest?")
        p.set_last_question(None)  # tolerate a missing/None restore
        self.assertEqual(p.get_last_question(), "")


class TestCandidatePick(unittest.TestCase):
    """Pick-your-own-sources: pre-selection, cap, and freeing a slot."""

    def _palette(self, top_k=3):
        return QuickOpenPalette(
            make_engine=lambda: None, semantic_query=lambda q: [],
            ask_candidates=lambda q: [], ask_answer_selected=lambda q, p: None,
            get_top_k=lambda: top_k)

    def _cands(self, n=5):
        return [(f"/v/n{i}.md", 1.0 - i * 0.1) for i in range(n)]

    def test_preselects_top_k(self):
        p = self._palette(top_k=3)
        p._show_candidate_list(p._ask_generation, self._cands())
        self.assertEqual(p._selected, ["/v/n0.md", "/v/n1.md", "/v/n2.md"])

    def test_cap_blocks_extra_selection(self):
        p = self._palette(top_k=3)
        p._show_candidate_list(p._ask_generation, self._cands())
        row = p._results.get_row_at_index(3)          # 4th candidate, unselected
        row._mv_check.set_active(True)                # refused at the cap
        self.assertFalse(row._mv_check.get_active())
        self.assertNotIn("/v/n3.md", p._selected)
        self.assertEqual(len(p._selected), 3)

    def test_deselect_frees_a_slot(self):
        p = self._palette(top_k=3)
        p._show_candidate_list(p._ask_generation, self._cands())
        p._results.get_row_at_index(0)._mv_check.set_active(False)  # drop n0
        self.assertNotIn("/v/n0.md", p._selected)
        p._results.get_row_at_index(3)._mv_check.set_active(True)   # now fits
        self.assertIn("/v/n3.md", p._selected)
        self.assertEqual(len(p._selected), 3)

    def _score_labels(self, p):
        labels = []
        row = p._results.get_first_child()
        while row is not None:
            box = row.get_child()
            last = box.get_last_child()          # the relevance label
            labels.append(last.get_text())
            row = row.get_next_sibling()
        return labels

    def test_score_is_shown_relative_to_top(self):
        # R42.4 — scale-free relevance: the top candidate is 100%, the rest are
        # a spread of percentages (not a couple of collapsed raw values).
        p = self._palette(top_k=3)
        # tiny RRF-like scores that :.2f would collapse to "≈0.03"/"≈0.02"
        p._show_candidate_list(p._ask_generation,
                               [("/v/a.md", 0.032), ("/v/b.md", 0.028),
                                ("/v/c.md", 0.016)])
        labels = self._score_labels(p)
        self.assertEqual(labels[0], "100%")            # top candidate
        self.assertEqual(labels[-1], "50%")            # 0.016 / 0.032
        self.assertEqual(len(set(labels)), 3)          # spread, not collapsed


class TestStatusPhases(unittest.TestCase):
    """The running row shows a phase ('Loading model…'/'Thinking…') + timer."""

    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [],
                                ask_answer=lambda q, on_phase=None: None)

    def test_set_phase_updates_the_status_label(self):
        p = self._palette()
        p._ask_generation = 1
        p._results.append(p._status_row())
        p._start_timer()
        p._set_phase(1, "loading")
        self.assertIn("Loading model", p._phase_label.get_text())
        p._set_phase(1, "thinking")
        self.assertIn("Thinking", p._phase_label.get_text())
        self.assertIn("s", p._phase_label.get_text())   # timer appended
        p._stop_ticking()

    def test_stale_generation_phase_is_ignored(self):
        p = self._palette()
        p._ask_generation = 2
        p._results.append(p._status_row())
        p._start_timer()
        p._set_phase(2, "loading")
        p._set_phase(1, "thinking")     # older question → ignored
        self.assertIn("Loading model", p._phase_label.get_text())
        p._stop_ticking()


class TestCloseCancels(unittest.TestCase):
    def test_close_bumps_generation_to_invalidate_and_abort(self):
        p = QuickOpenPalette(
            make_engine=lambda: None, semantic_query=lambda q: [],
            ask_answer=lambda q, on_phase=None, should_cancel=None: None)
        p._ask_generation = 5
        p._on_closed()
        self.assertEqual(p._ask_generation, 6)   # a captured should_cancel now True


class TestFooterModelPicker(unittest.TestCase):
    """Footer model picker: only with >1 model, in Ask mode; drives set_ask_model."""

    def _palette(self, models, current=None):
        chosen = {}
        p = QuickOpenPalette(
            make_engine=lambda: None, semantic_query=lambda q: [],
            ask_answer=lambda q, on_phase=None: None,
            list_ask_models=lambda: models,
            set_ask_model=lambda path: chosen.__setitem__("path", path),
            current_ask_model=lambda: current)
        p._chosen = chosen
        return p

    def test_hidden_with_a_single_model(self):
        p = self._palette([("a.gguf", "/a")])
        p._ask_mode = True
        p._refresh_models()
        self.assertFalse(p._model_dropdown.get_visible())

    def test_shown_and_preselected_with_several(self):
        p = self._palette([("a.gguf", "/a"), ("b.gguf", "/b")], current="/b")
        p._ask_mode = True
        p._refresh_models()
        self.assertTrue(p._model_dropdown.get_visible())
        self.assertEqual(p._model_paths[p._model_dropdown.get_selected()], "/b")

    def test_hidden_outside_ask_mode(self):
        p = self._palette([("a.gguf", "/a"), ("b.gguf", "/b")])
        p._ask_mode = False
        p._refresh_models()
        self.assertFalse(p._model_dropdown.get_visible())

    def test_selecting_sets_the_model(self):
        p = self._palette([("a.gguf", "/a"), ("b.gguf", "/b")], current="/a")
        p._ask_mode = True
        p._refresh_models()
        p._model_dropdown.set_selected(1)          # → /b
        self.assertEqual(p._chosen.get("path"), "/b")


class TestAskAnswerAccessor(unittest.TestCase):
    """ask_answer_text() must track the stream and reset — not hand back a stale
    answer to a poll-until-stable client (R66.1)."""

    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [])

    def test_grows_while_streaming(self):
        p = self._palette()
        p._ask_generation = 1
        self.assertEqual(p.ask_answer_text(), "")
        p._stream_delta(1, "Saturn")
        self.assertEqual(p.ask_answer_text(), "Saturn")
        p._stream_delta(1, "Saturn hat Ringe")   # full text each time, set not append
        self.assertEqual(p.ask_answer_text(), "Saturn hat Ringe")

    def test_clear_resets_so_no_stale_answer(self):
        p = self._palette()
        p._ask_generation = 1
        p._stream_delta(1, "alte Antwort")
        p._clear()
        self.assertEqual(p.ask_answer_text(), "")   # a new question starts empty

    def _ask_palette(self):
        from types import SimpleNamespace
        ans = SimpleNamespace(text="Saturn", error=None, sources=[])
        p = QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [],
                             ask_answer=lambda q, **k: ans)
        p._ask_mode = True
        p._entry.set_text("Was ist Saturn?")
        return p, ans

    def test_is_idle_false_from_submit_until_answer(self):
        # Drives the real path: _run_ask marks busy, _show_answer clears it — so it
        # stays busy through generation, not just prefill (the old timer bug).
        p, ans = self._ask_palette()
        self.assertTrue(p.is_idle())
        p._run_ask()
        self.assertFalse(p.is_idle())               # busy while the worker runs
        p._show_answer(p._ask_generation, ans)      # completion callback lands
        self.assertTrue(p.is_idle())

    def test_close_clears_busy(self):
        p, _ = self._ask_palette()
        p._run_ask()
        self.assertFalse(p.is_idle())
        p._on_closed()                              # abandoning the answer
        self.assertTrue(p.is_idle())

    def test_leaving_ask_mode_clears_busy(self):
        # R69.1: bumping the generation to abandon an answer must clear busy too.
        from types import SimpleNamespace
        p, _ = self._ask_palette()
        p._engine = None
        p._run_ask()
        self.assertFalse(p.is_idle())
        p._on_ask_toggled(SimpleNamespace(get_active=lambda: False))
        self.assertTrue(p.is_idle())

    def test_pick_sources_clears_busy(self):
        p, _ = self._ask_palette()
        p._ask_candidates = lambda q: []            # enable the pick-sources path
        p._run_ask()
        self.assertFalse(p.is_idle())
        p._show_candidates()
        self.assertTrue(p.is_idle())


class TestAskAvailability(unittest.TestCase):
    """With semantic search off (or the index/engine unavailable) Ask cannot
    answer, so the toggle must be greyed out and say why — instead of accepting
    the click and explaining itself afterwards in the result area."""

    def _palette(self, reason=""):
        return QuickOpenPalette(make_engine=lambda: None,
                                semantic_query=lambda q: [],
                                ask_answer=lambda *a, **k: None,
                                can_ask=lambda: not reason,
                                ask_hint=lambda: reason)

    def test_toggle_is_disabled_and_names_the_reason(self):
        p = self._palette("Semantic search is off.")
        p.refresh_ask_availability()
        self.assertFalse(p._ask_toggle.get_sensitive())
        self.assertIn("Semantic search is off.", p._ask_toggle.get_tooltip_text())

    def test_toggle_stays_usable_when_ask_works(self):
        p = self._palette()
        p.refresh_ask_availability()
        self.assertTrue(p._ask_toggle.get_sensitive())

    def test_ask_mode_is_left_even_after_the_user_chose_it(self):
        # _mode_locked keeps the user's choice across opens — but a locked choice
        # of a mode that cannot answer would strand the palette in Ask.
        p = self._palette()
        p._ask_toggle.set_active(True)
        self.assertTrue(p._ask_mode)
        p._can_ask = lambda: False
        p._ask_hint = lambda: "Semantic search is off."
        p.refresh_ask_availability()
        self.assertFalse(p._ask_mode)
        self.assertFalse(p._ask_toggle.get_active())

    def test_no_ask_configured_is_not_an_error(self):
        # Without an ask_answer callback there is no toggle at all.
        p = QuickOpenPalette(make_engine=lambda: None)
        p.refresh_ask_availability()
        self.assertIsNone(p._ask_toggle)


class TestScopeChangeInAskMode(unittest.TestCase):
    """Switching the vault scope must not start an answer by itself — that is a
    full model run (minutes, and a request to the server) triggered by a dropdown.
    The user submits: the submit button, or Enter in the question field."""

    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None,
                                semantic_query=lambda q: [],
                                ask_answer=lambda *a, **k: None,
                                scope=None)

    def _row_texts(self, p):
        texts, row = [], p._results.get_first_child()
        while row is not None:
            child = row.get_child()
            texts.append(child.get_label() if isinstance(child, Gtk.Label) else "")
            row = row.get_next_sibling()
        return texts

    def test_scope_change_does_not_answer_by_itself(self):
        p = self._palette()
        p._ask_toggle.set_active(True)
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("which planet is heaviest?")
        p._on_scope_changed()
        self.assertEqual(asked, [])
        # The question is kept — submitting is one keystroke away.
        self.assertEqual(p._entry.get_text(), "which planet is heaviest?")

    def test_the_answer_of_the_previous_scope_is_not_left_standing(self):
        from markdown_vault.search.ask import Answer
        p = self._palette()
        p._ask_toggle.set_active(True)
        p._ask_started = 0.0
        p._show_answer(p._ask_generation, Answer(text="Jupiter"))
        self.assertTrue(p._has_answer)
        p._on_scope_changed()
        texts = self._row_texts(p)
        self.assertFalse(any("Jupiter" in t for t in texts), texts)
        self.assertFalse(p._has_answer)          # nothing stale left to copy
        self.assertTrue(any("Type a question" in t for t in texts), texts)

    def test_file_mode_still_filters_immediately(self):
        # Not a model run but a filter over the loaded list: leaving it stale would
        # show files from the vault that was just deselected.
        p = self._palette()
        refreshed = []
        p._refresh = lambda: refreshed.append(1)
        p._on_scope_changed()
        self.assertEqual(refreshed, [1])


class TestEndpointStatusInPalette(unittest.TestCase):
    """What the server said about itself, shown where the question is typed.

    The rule under test: warn about anything odd, but take asking away *only* when
    it is certain to fail (server unreachable, credentials rejected). A server
    without a list endpoint answers fine and must stay usable.
    """

    def _status(self, state, models=(), error=""):
        from markdown_vault.search import ask_models
        return ask_models.EndpointStatus(state, "http://h:8080",
                                         models=list(models), error=error)

    def _palette(self, status, models=(("m1", "m1"), ("m2", "m2"))):
        self.rechecked = []
        p = QuickOpenPalette(make_engine=lambda: None,
                             ask_answer=lambda *a, **k: None,
                             ask_status=lambda: status,
                             ask_recheck=lambda: self.rechecked.append(1),
                             list_ask_models=lambda: list(models),
                             current_ask_model=lambda: "m1")
        p._ask_toggle.set_active(True)          # Ask mode: this is where it shows
        return p

    def _st(self, name):
        from markdown_vault.search import ask_models
        return getattr(ask_models, name)

    # ── the banner ────────────────────────────────────────────────
    def test_silent_when_the_server_listed_models(self):
        p = self._palette(self._status(self._st("OK"), ["m1", "m2"]))
        self.assertFalse(p._banner.get_revealed())

    def test_silent_while_the_probe_is_still_out(self):
        # Otherwise a warning flashes up on every open before the answer arrives.
        p = self._palette(self._status(self._st("PROBING")))
        self.assertFalse(p._banner.get_revealed())

    def test_silent_when_the_server_has_no_list_endpoint(self):
        # llama.cpp serves one model and lists nothing — nothing is wrong.
        p = self._palette(self._status(self._st("NO_LIST")))
        self.assertFalse(p._banner.get_revealed())

    def test_warns_when_unreachable_and_names_the_url(self):
        p = self._palette(self._status(self._st("UNREACHABLE"), error="refused"))
        self.assertTrue(p._banner.get_revealed())
        self.assertIn("http://h:8080", p._banner.get_title())

    def test_warns_on_an_empty_list_and_on_a_list_error(self):
        for name in ("EMPTY", "LIST_ERROR"):
            p = self._palette(self._status(self._st(name), error="HTTP 500"))
            self.assertTrue(p._banner.get_revealed(), name)

    def test_no_banner_outside_ask_mode(self):
        p = self._palette(self._status(self._st("UNREACHABLE")))
        p._ask_toggle.set_active(False)
        self.assertFalse(p._banner.get_revealed())

    def test_try_again_triggers_a_recheck(self):
        p = self._palette(self._status(self._st("UNREACHABLE")))
        p._on_banner_retry()
        self.assertTrue(self.rechecked)

    # ── submitting ────────────────────────────────────────────────
    def test_submit_blocked_only_when_asking_cannot_work(self):
        for name in ("UNREACHABLE", "UNAUTHORIZED"):
            p = self._palette(self._status(self._st(name)))
            self.assertFalse(p._submit.get_sensitive(), name)
        for name in ("OK", "EMPTY", "NO_LIST", "PROBING"):
            p = self._palette(self._status(self._st(name), ["m1"]))
            self.assertTrue(p._submit.get_sensitive(), name)

    def test_enter_sends_nothing_when_asking_cannot_work(self):
        p = self._palette(self._status(self._st("UNREACHABLE")))
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("a question worth minutes")
        p._on_entry_activate(p._entry)
        self.assertEqual(asked, [])
        self.assertEqual(p._entry.get_text(), "a question worth minutes")

    def test_enter_while_probing_waits_for_the_verdict(self):
        # Ctrl+Space, Enter takes under a second; the probe up to five. Asking
        # blind would run a dead server into the 120 s chat timeout.
        p = self._palette(self._status(self._st("PROBING")))
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("held question")
        p._on_entry_activate(p._entry)
        self.assertEqual(asked, [])
        self.assertEqual(p._pending_question, "held question")

    def test_a_held_question_runs_once_the_server_turns_out_fine(self):
        p = self._palette(self._status(self._st("PROBING")))
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("held question")
        p._on_entry_activate(p._entry)
        p._ask_status = lambda: self._status(self._st("OK"), ["m1"])
        p.refresh_endpoint_status()
        self.assertEqual(asked, [1])
        self.assertEqual(p._pending_question, "")

    def test_a_held_question_is_dropped_when_the_server_is_dead(self):
        p = self._palette(self._status(self._st("PROBING")))
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("held question")
        p._on_entry_activate(p._entry)
        p._ask_status = lambda: self._status(self._st("UNREACHABLE"))
        p.refresh_endpoint_status()
        self.assertEqual(asked, [])
        self.assertTrue(p._banner.get_revealed())
        self.assertEqual(p._entry.get_text(), "held question")   # not lost

    def test_closing_discards_a_held_question(self):
        # A late verdict must not fire a question into a dialog the user left —
        # that is 120 s of model time for an answer nobody sees.
        p = self._palette(self._status(self._st("PROBING")))
        asked = []
        p._run_ask = lambda: asked.append(1)
        p._entry.set_text("held question")
        p._on_entry_activate(p._entry)
        p._on_closed()
        p._ask_status = lambda: self._status(self._st("OK"), ["m1"])
        p.refresh_endpoint_status()
        self.assertEqual(asked, [])

    # ── the model picker ──────────────────────────────────────────
    def test_picker_stays_visible_but_dead_without_a_usable_list(self):
        # Hiding it made "server unreachable" look like "one local GGUF".
        p = self._palette(self._status(self._st("UNREACHABLE")), models=[("m1", "m1")])
        self.assertTrue(p._model_dropdown.get_visible())
        self.assertFalse(p._model_dropdown.get_sensitive())

    def test_picker_is_usable_when_the_server_listed_models(self):
        p = self._palette(self._status(self._st("OK"), ["m1", "m2"]))
        self.assertTrue(p._model_dropdown.get_visible())
        self.assertTrue(p._model_dropdown.get_sensitive())

    def test_a_failed_answer_locks_submitting_right_away(self):
        # Observed: with the palette already open, stopping the server left it
        # cheerful — no banner, and the same question could be sent again and again.
        from markdown_vault.search.ask import Answer
        state = [self._status(self._st("OK"), ["m1", "m2"])]
        p = self._palette(state[0])
        p._ask_status = lambda: state[0]
        self.assertTrue(p._submit.get_sensitive())
        state[0] = self._status(self._st("UNREACHABLE"), error="refused")
        p._ask_started = 0.0
        p._show_answer(p._ask_generation, Answer(text="", error="not reachable"))
        self.assertTrue(p._banner.get_revealed())
        self.assertFalse(p._submit.get_sensitive())

    def _row_texts(self, p):
        def text_of(widget):
            # The status row wraps its label in a box (label + spinner), so read
            # nested labels too, not just a row's direct child.
            if isinstance(widget, Gtk.Label):
                return widget.get_label() or ""
            parts, child = [], widget.get_first_child()
            while child is not None:
                parts.append(text_of(child))
                child = child.get_next_sibling()
            return " ".join(part for part in parts if part)

        texts, row = [], p._results.get_first_child()
        while row is not None:
            texts.append(text_of(row.get_child()))
            row = row.get_next_sibling()
        return texts

    def test_try_again_clears_the_error_from_the_last_attempt(self):
        from markdown_vault.search.ask import Answer
        state = [self._status(self._st("UNREACHABLE"), error="refused")]
        p = self._palette(state[0])
        p._ask_status = lambda: state[0]
        p._ask_started = 0.0
        p._show_answer(p._ask_generation, Answer(text="", error="not reachable"))
        self.assertTrue(any("not reachable" in t for t in self._row_texts(p)))
        state[0] = self._status(self._st("PROBING"))
        p._on_banner_retry()
        texts = self._row_texts(p)
        self.assertFalse(any("not reachable" in t for t in texts), texts)
        self.assertTrue(any("Checking the server" in t for t in texts), texts)

    def test_after_a_recheck_succeeds_the_palette_is_plain_again(self):
        # Neither the old error nor a stale "Checking…" row may survive the verdict.
        state = [self._status(self._st("PROBING"))]
        p = self._palette(state[0])
        p._ask_status = lambda: state[0]
        p._on_banner_retry()
        state[0] = self._status(self._st("OK"), ["m1", "m2"])
        p.refresh_endpoint_status()
        texts = self._row_texts(p)
        self.assertFalse(any("Checking the server" in t for t in texts), texts)
        self.assertTrue(any("Type a question" in t for t in texts), texts)
        self.assertFalse(p._banner.get_revealed())

    def test_a_dropped_held_question_leaves_no_stale_checking_row(self):
        state = [self._status(self._st("PROBING"))]
        p = self._palette(state[0])
        p._ask_status = lambda: state[0]
        p._run_ask = lambda: None
        p._entry.set_text("held question")
        p._on_entry_activate(p._entry)
        state[0] = self._status(self._st("UNREACHABLE"), error="refused")
        p.refresh_endpoint_status()
        texts = self._row_texts(p)
        self.assertFalse(any("Checking the server" in t for t in texts), texts)

    def test_opening_does_not_recheck_a_server_without_a_list_endpoint(self):
        # F1: llama.cpp never lists models — probing it on every Ctrl+Space is a
        # round trip for a configuration the design calls healthy.
        p = self._palette(self._status(self._st("NO_LIST")))
        self.rechecked.clear()
        p.recheck_if_stale()
        self.assertEqual(self.rechecked, [])

    def test_opening_rechecks_a_server_that_last_failed(self):
        # A server started in the meantime must become usable by reopening the
        # palette — without this, the failed verdict would stick for the session.
        p = self._palette(self._status(self._st("UNREACHABLE")))
        self.rechecked.clear()
        p.recheck_if_stale()
        self.assertTrue(self.rechecked)

    def test_opening_does_not_recheck_a_healthy_server(self):
        p = self._palette(self._status(self._st("OK"), ["m1", "m2"]))
        self.rechecked.clear()
        p.recheck_if_stale()
        self.assertEqual(self.rechecked, [])

    def test_opening_does_not_restart_a_running_check(self):
        p = self._palette(self._status(self._st("PROBING")))
        self.rechecked.clear()
        p.recheck_if_stale()
        self.assertEqual(self.rechecked, [])

    def test_local_backend_keeps_the_two_model_rule(self):
        # No endpoint to check (ask_status returns None) → one model is no choice.
        p = QuickOpenPalette(make_engine=lambda: None,
                             ask_answer=lambda *a, **k: None,
                             ask_status=lambda: None,
                             list_ask_models=lambda: [("a.gguf", "/m/a.gguf")],
                             current_ask_model=lambda: "/m/a.gguf")
        p._ask_toggle.set_active(True)
        self.assertFalse(p._model_dropdown.get_visible())
        self.assertFalse(p._banner.get_revealed())


class TestBannerButtonDispatch(unittest.TestCase):
    """The banner button follows the verdict: a local-model error leads into the
    settings; a server error re-probes."""

    def _palette(self, *, is_local, with_settings=True):
        import types
        opened = []
        p = QuickOpenPalette(
            make_engine=lambda: None,
            ask_status=lambda: types.SimpleNamespace(is_local=is_local),
            open_ask_settings=((lambda: opened.append("settings"))
                               if with_settings else None))
        p._on_banner_retry = lambda: opened.append("retry")   # stub the server probe
        p.close = lambda: opened.append("closed")
        return p, opened

    def test_local_verdict_opens_settings(self):
        import unittest.mock as m
        p, opened = self._palette(is_local=True)
        with m.patch("markdown_vault.search.quick_open_palette.GLib.idle_add",
                     side_effect=lambda fn, *a: fn()):
            p._on_banner_clicked()
        self.assertEqual(opened, ["closed", "settings"])   # close, then open settings

    def test_server_verdict_reprobes(self):
        p, opened = self._palette(is_local=False)
        p._on_banner_clicked()
        self.assertEqual(opened, ["retry"])                # no close, no settings

    def test_local_without_a_settings_callback_falls_back_to_reprobe(self):
        p, opened = self._palette(is_local=True, with_settings=False)
        p._on_banner_clicked()
        self.assertEqual(opened, ["retry"])

    def test_entering_ask_mode_rechecks_a_stale_verdict(self):
        # Opening in file mode no longer re-probes (the Ctrl+Space saving), so the
        # toggle has to: otherwise a server started meanwhile stays "dead" with
        # submit locked until the user presses Try again.
        import unittest.mock as m
        from markdown_vault.search import ask_models
        p = QuickOpenPalette(
            make_engine=lambda: None,
            ask_answer=lambda *a, **k: None,     # wires the Ask toggle
            ask_status=lambda: ask_models.EndpointStatus(
                state=ask_models.UNREACHABLE, url="http://x"))
        p._ask_recheck = m.MagicMock()
        p._ask_toggle.set_active(True)
        p._ask_recheck.assert_called_once()

    def test_no_endpoint_check_outside_ask_mode(self):
        # A plain Ctrl+Space (file switcher) must not run the model check —
        # for the local backend that is a file probe plus a llama_cpp import.
        calls = []
        p = QuickOpenPalette(make_engine=lambda: None,
                             ask_status=lambda: (calls.append(1), None)[1])
        p._ask_mode = False
        p.refresh_endpoint_status()
        p.recheck_if_stale()
        self.assertEqual(calls, [])                 # not asked outside Ask mode
        p._ask_mode = True
        p.refresh_endpoint_status()
        self.assertEqual(len(calls), 1)             # asked once Ask is active

    def test_button_label_follows_the_verdict(self):
        from markdown_vault.search import ask_models
        p = QuickOpenPalette(
            make_engine=lambda: None,
            ask_status=lambda: ask_models.local_unavailable("gone"))
        p._ask_mode = True
        p.refresh_endpoint_status()
        self.assertEqual(p._banner.get_button_label(), "Settings")
        p._ask_status = lambda: ask_models.EndpointStatus(state=ask_models.UNREACHABLE)
        p.refresh_endpoint_status()
        self.assertEqual(p._banner.get_button_label(), "Try again")


class _RunInline:
    """A drop-in for threading.Thread that runs the target on ``start()``."""

    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class TestCandidateRetrievalError(unittest.TestCase):
    """A crash in candidate retrieval must surface as an error, not be masked as
    'No candidate notes found.' (which reads as a legitimate empty result).
    Drives the real ``_show_candidates`` worker so the wiring is guarded, not
    just the display callback."""

    def _palette(self):
        return QuickOpenPalette(make_engine=lambda: None, semantic_query=lambda q: [])

    def _run(self, palette):
        with mock.patch(
            "markdown_vault.search.quick_open_palette.threading.Thread", _RunInline
        ), mock.patch(
            "markdown_vault.search.quick_open_palette.GLib.idle_add",
            lambda fn, *a: fn(*a),
        ):
            palette._show_candidates()

    def _spy_messages(self, palette):
        seen = []
        orig = palette._message_row
        palette._message_row = lambda text: seen.append(text) or orig(text)
        return seen

    def test_retrieval_crash_shows_error_not_empty(self):
        p = self._palette()
        p._entry.set_text("question")
        p._abandon_answer = mock.Mock()
        p._ask_candidates = mock.Mock(side_effect=RuntimeError("boom"))
        seen = self._spy_messages(p)
        self._run(p)
        self.assertNotIn("No candidate notes found.", seen)
        self.assertTrue(
            any("couldn't" in t.lower() or "fail" in t.lower() for t in seen),
            f"expected an error message, got {seen!r}",
        )

    def test_empty_result_still_says_no_candidates(self):
        p = self._palette()
        p._entry.set_text("question")
        p._abandon_answer = mock.Mock()
        p._ask_candidates = mock.Mock(return_value=[])
        seen = self._spy_messages(p)
        self._run(p)
        self.assertIn("No candidate notes found.", seen)


if __name__ == "__main__":
    unittest.main()
