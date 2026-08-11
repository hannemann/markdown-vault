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
        p._shown_paths = {"/v/note.md"}  # already a fuzzy hit
        p._append_semantic(5, [_sem("/v/note.md")])
        self.assertEqual(self._row_count(p), 0)

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

    def test_maybe_select_answer_selects_full_text(self):
        p = self._palette()
        row = p._answer_row("hello world")
        p._maybe_select_answer(row)
        ok, start, end = p._answer_label.get_selection_bounds()
        self.assertTrue(ok)
        self.assertEqual((start, end), (0, len("hello world")))

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


if __name__ == "__main__":
    unittest.main()
