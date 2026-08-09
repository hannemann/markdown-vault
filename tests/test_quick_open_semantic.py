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


if __name__ == "__main__":
    unittest.main()
