"""Tests for markdown_vault.semantic_index.SemanticIndexManager."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.semantic_index import SemanticIndexManager


class _StubEmbedder:
    _VOCAB = {"alpha": 0, "beta": 1, "gamma": 2, "delta": 3}

    def __init__(self):
        self.calls = 0

    def embed(self, texts, is_query=False):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0, 0.0, 0.0, 0.0]
            for w in t.lower().split():
                if w in self._VOCAB:
                    v[self._VOCAB[w]] += 1.0
            out.append(v)
        return out


class _PoisonEmbedder:
    def embed(self, texts, is_query=False):
        raise AssertionError("embed() must not be called on a cache hit")


class TestSemanticIndexManager(unittest.TestCase):
    def setUp(self):
        self._vault = Path(tempfile.mkdtemp())
        self._state = Path(tempfile.mkdtemp())
        (self._vault / "a.md").write_text("alpha alpha topic\n\nmore alpha", encoding="utf-8")
        (self._vault / "b.md").write_text("beta subject here", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self._vault, ignore_errors=True)
        shutil.rmtree(self._state, ignore_errors=True)

    def _manager(self, embedder, tag="ollama:test"):
        return SemanticIndexManager(
            embedder, lambda: [str(self._vault)], self._state, tag, min_score=0.3,
        )

    def test_build_makes_index_ready_and_queryable(self):
        m = self._manager(_StubEmbedder())
        m.build()
        self.assertTrue(m.is_ready())
        res = m.query_files("beta", top_k=5)
        self.assertTrue(res)
        self.assertEqual(Path(res[0].path).name, "b.md")
        self.assertTrue(res[0].semantic)
        self.assertEqual(res[0].matches[0].line, 1)

    def test_cache_hit_skips_reembedding(self):
        self._manager(_StubEmbedder()).build()          # builds + caches
        m2 = self._manager(_PoisonEmbedder())            # would raise if it embedded
        m2.build()                                       # must load from cache
        self.assertTrue(m2.is_ready())

    def test_changed_vault_invalidates_cache(self):
        e1 = _StubEmbedder()
        self._manager(e1).build()
        # A different signature tag (e.g. model change) forces a rebuild.
        e2 = _StubEmbedder()
        self._manager(e2, tag="ollama:other-model").build()
        self.assertGreaterEqual(e2.calls, 1)  # re-embedded

    def test_query_before_ready_is_empty(self):
        m = self._manager(_StubEmbedder())
        self.assertEqual(m.query_files("alpha"), [])  # not built yet

    def test_min_score_filters(self):
        m = self._manager(_StubEmbedder())
        m.build()
        self.assertEqual(m.query_files("gamma"), [])  # unrelated to alpha/beta


if __name__ == "__main__":
    unittest.main()
