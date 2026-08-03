"""Tests for markdown_vault.find_bar — the in-view find bar widget."""

import unittest

from markdown_vault.find_bar import FindBar


class TestFindBar(unittest.TestCase):
    def test_constructs_hidden_with_empty_text(self):
        fb = FindBar()
        self.assertFalse(fb.get_visible())
        self.assertEqual(fb.get_text(), "")

    def test_set_count_variants_do_not_raise(self):
        fb = FindBar()
        for current, total in [(0, -1), (0, 0), (1, 5), (0, 3)]:
            fb.set_count(current, total)  # no query text → counter stays empty

    def test_emits_navigation_signals(self):
        fb = FindBar()
        events = []
        fb.connect("search-next", lambda *_: events.append("next"))
        fb.connect("search-prev", lambda *_: events.append("prev"))
        fb.emit("search-next")
        fb.emit("search-prev")
        self.assertEqual(events, ["next", "prev"])

    def test_close_hides_and_emits_closed(self):
        fb = FindBar()
        closed = []
        fb.connect("closed", lambda *_: closed.append(True))
        fb.set_visible(True)
        fb.close()
        self.assertFalse(fb.get_visible())
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
