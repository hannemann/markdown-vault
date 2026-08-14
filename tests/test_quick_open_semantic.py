"""Tests for async semantic append in the quick-open palette."""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

Adw.init()

from markdown_vault.quick_open_palette import QuickOpenPalette
from markdown_vault.quick_open import QuickResult


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
        from markdown_vault.ask import Source
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
        from markdown_vault.ask import Source
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


if __name__ == "__main__":
    unittest.main()
