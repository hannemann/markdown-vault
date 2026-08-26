"""Tests for markdown_vault.ui.card_store — the graph-cards collection (pure)."""

import unittest

from markdown_vault.ui.card_store import Card, CardStore


def _card(path="/v/a.md", title="A", desc="d", vault="v", color="#123456"):
    return Card(path=path, title=title, desc=desc, vault=vault, color=color)


class TestCardStore(unittest.TestCase):
    def test_add_new_returns_true_and_stores(self):
        s = CardStore()
        self.assertTrue(s.add(_card()))
        self.assertEqual(len(s), 1)
        self.assertEqual(s.cards()[0].path, "/v/a.md")

    def test_add_duplicate_path_is_noop_returns_false(self):
        s = CardStore()
        s.add(_card(title="first"))
        self.assertFalse(s.add(_card(title="second")))   # same path, different content
        self.assertEqual(len(s), 1)
        self.assertEqual(s.cards()[0].title, "first")    # kept, not replaced

    def test_insertion_order_preserved(self):
        s = CardStore()
        for p in ("/v/a.md", "/v/b.md", "/v/c.md"):
            s.add(_card(path=p))
        self.assertEqual([c.path for c in s.cards()],
                         ["/v/a.md", "/v/b.md", "/v/c.md"])

    def test_contains(self):
        s = CardStore()
        s.add(_card(path="/v/a.md"))
        self.assertIn("/v/a.md", s)
        self.assertNotIn("/v/x.md", s)

    def test_remove_existing_and_missing(self):
        s = CardStore()
        s.add(_card(path="/v/a.md"))
        s.add(_card(path="/v/b.md"))
        self.assertTrue(s.remove("/v/a.md"))
        self.assertEqual([c.path for c in s.cards()], ["/v/b.md"])
        self.assertFalse(s.remove("/v/a.md"))            # already gone

    def test_clear(self):
        s = CardStore()
        s.add(_card(path="/v/a.md"))
        s.add(_card(path="/v/b.md"))
        s.clear()
        self.assertEqual(len(s), 0)
        self.assertEqual(s.cards(), [])


if __name__ == "__main__":
    unittest.main()
