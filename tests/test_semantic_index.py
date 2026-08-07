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


class _FlakyEmbedder(_StubEmbedder):
    """Raises (like a 500) for any text containing *bad*, else embeds normally."""

    def __init__(self, bad):
        super().__init__()
        self._bad = bad

    def embed(self, texts, is_query=False):
        if any(self._bad in t for t in texts):
            raise RuntimeError("simulated 500")
        return super().embed(texts, is_query)


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

    def test_query_open_returns_quickresults(self):
        m = self._manager(_StubEmbedder())
        m.build()
        res = m.query_open("beta", top_k=5)
        self.assertTrue(res)
        self.assertEqual(Path(res[0].path).name, "b.md")
        self.assertEqual(res[0].source, "semantic")

    def test_min_score_filters(self):
        m = self._manager(_StubEmbedder())
        m.build()
        self.assertEqual(m.query_files("gamma"), [])  # unrelated to alpha/beta

    def test_reindex_adds_new_file(self):
        m = self._manager(_StubEmbedder())
        m.build()
        (self._vault / "c.md").write_text("gamma gamma topic", encoding="utf-8")
        m._reindex_file(str(self._vault / "c.md"))
        names = {Path(r.path).name for r in m.query_open("gamma", top_k=10)}
        self.assertIn("c.md", names)

    def test_reindex_updates_changed_file(self):
        m = self._manager(_StubEmbedder())
        m.build()
        (self._vault / "a.md").write_text("beta beta beta", encoding="utf-8")
        m._reindex_file(str(self._vault / "a.md"))
        names = {Path(r.path).name for r in m.query_open("beta", top_k=10)}
        self.assertIn("a.md", names)

    def test_drop_removes_file(self):
        m = self._manager(_StubEmbedder())
        m.build()
        m._drop_file(str(self._vault / "a.md"))
        names = {Path(r.path).name for r in m.query_open("alpha", top_k=10)}
        self.assertNotIn("a.md", names)

    def test_move_repaths_without_reembedding(self):
        e = _StubEmbedder()
        m = self._manager(e)
        m.build()
        calls = e.calls
        m._move_file(str(self._vault / "a.md"), str(self._vault / "renamed.md"))
        self.assertEqual(e.calls, calls)  # rename must not re-embed
        names = {Path(r.path).name for r in m.query_open("alpha", top_k=10)}
        self.assertIn("renamed.md", names)
        self.assertNotIn("a.md", names)

    def test_failed_chunk_is_skipped_not_fatal(self):
        # A chunk whose embed 500s must be skipped; the build still completes.
        (self._vault / "bad.md").write_text("beta trigger boom", encoding="utf-8")
        m = self._manager(_FlakyEmbedder(bad="trigger"))
        m.build()
        self.assertTrue(m.is_ready())
        paths = {Path(r.path).name for r in m.query_files("alpha", top_k=10)}
        self.assertIn("a.md", paths)       # good chunk indexed
        self.assertNotIn("bad.md", paths)  # failing chunk skipped


if __name__ == "__main__":
    unittest.main()
