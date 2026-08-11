"""Tests for markdown_vault.lexical_search — BM25 + RRF fusion."""

import unittest

from markdown_vault import lexical_search as L


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_splits_on_underscore(self):
        # underscore is a word separator so snake_case is searchable
        self.assertEqual(L.tokenize("Semantic_Min_Score"),
                         ["semantic", "min", "score"])

    def test_keeps_digits_drops_punctuation(self):
        self.assertEqual(L.tokenize("v1.2, foo!"), ["v1", "2", "foo"])


class TestBM25(unittest.TestCase):
    def _idx(self):
        return L.BM25Index({
            "sonne": "die sonne ist ein stern im zentrum des sonnensystems",
            "vaults": "vaults.yaml hält die einstellungen und die vault-liste",
            "editor": "der editor basiert auf gtksourceview fünf",
        })

    def test_exact_token_ranks_its_doc_first(self):
        idx = self._idx()
        self.assertEqual(idx.search("gtksourceview", 1), ["editor"])
        self.assertEqual(idx.search("einstellungen yaml", 1), ["vaults"])

    def test_ranking_orders_by_relevance(self):
        idx = self._idx()
        self.assertEqual(idx.search("stern sonnensystems", 3)[0], "sonne")

    def test_empty_or_wordless_query_returns_nothing(self):
        idx = self._idx()
        self.assertEqual(idx.search("", 5), [])
        self.assertEqual(idx.search("!!! ---", 5), [])

    def test_unknown_term_returns_nothing(self):
        self.assertEqual(self._idx().search("supernova", 5), [])

    def test_empty_corpus_is_safe(self):
        self.assertEqual(L.BM25Index({}).search("x", 5), [])


class TestRRF(unittest.TestCase):
    def test_agreement_floats_to_top(self):
        sem = ["x", "y", "z"]
        lex = ["z", "w", "x"]
        fused = L.reciprocal_rank_fusion([sem, lex])
        self.assertEqual(fused[0], "x")          # ranked well by both
        self.assertEqual(set(fused), {"x", "y", "z", "w"})

    def test_recovers_lexical_only_hit(self):
        # a doc only the lexical retriever found still appears in the fusion
        sem = ["a", "b"]
        lex = ["c", "a"]
        self.assertIn("c", L.reciprocal_rank_fusion([sem, lex]))

    def test_rrf_scores_are_positive_and_rank_consistent(self):
        # the scored variant backs both the picker's ≈score and the ask log.
        scores = L.rrf_scores([["x", "y", "z"], ["z", "w", "x"]])
        self.assertTrue(all(s > 0 for s in scores.values()))
        # x (top of both) outranks w (bottom of one, absent from the other)
        self.assertGreater(scores["x"], scores["w"])
        self.assertEqual(sorted(scores, key=lambda p: -scores[p])[0], "x")


if __name__ == "__main__":
    unittest.main()
