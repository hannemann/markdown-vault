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

    def test_on_busy_brackets_build(self):
        events = []
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3, on_busy=events.append)
        m.build()
        self.assertEqual(events, [True, False])  # one enter, one exit

    def test_snippet_skips_leading_markup_lines(self):
        from markdown_vault.semantic_search import Chunk
        chunk = Chunk("/n.md", 47, "---\nreal content on line 48.")
        snippet, line = SemanticIndexManager._snippet(chunk)
        self.assertEqual(snippet, "real content on line 48.")
        self.assertEqual(line, 48)  # line number advances past the rule

    def test_invalidate_cache_forces_reembed(self):
        e1 = _StubEmbedder()
        m = self._manager(e1)
        m.build()
        self.assertTrue(m._json_path.exists())
        m.invalidate_cache()
        self.assertFalse(m._json_path.exists())
        self.assertFalse(m._npy_path.exists())
        # Same signature tag, but the cache is gone → must re-embed, not reuse.
        e2 = _StubEmbedder()
        self._manager(e2).build()
        self.assertGreaterEqual(e2.calls, 1)

    def _park_pending(self, m, mapping):
        """Populate the pending map directly — bypasses _enqueue so no async
        worker starts and _drain_pending can be driven deterministically."""
        with m._pending_lock:
            m._pending.update(mapping)

    def test_on_busy_brackets_incremental_ops(self):
        events = []
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3, on_busy=events.append)
        m.build()
        events.clear()
        (self._vault / "a.md").write_text("alpha beta gamma delta", encoding="utf-8")
        self._park_pending(m, {str(self._vault / "a.md"): ("update",)})
        m._drain_pending()
        self.assertEqual(events, [True, False])  # one enter/exit around the drain

    def test_events_coalesce_per_path(self):
        m = self._manager(_StubEmbedder())
        m.build()
        m.shutdown()  # park the worker; only inspect the coalesced map
        p = str(self._vault / "a.md")
        m._enqueue(p, ("update",))
        m._enqueue(p, ("update",))
        with m._pending_lock:
            self.assertEqual(len(m._pending), 1)  # only the latest op survives

    def test_event_before_ready_is_not_dropped(self):
        # R22.6: an update that arrives during the build window must survive it.
        m = self._manager(_StubEmbedder())
        p = str(self._vault / "a.md")
        self._park_pending(m, {p: ("update",)})
        m._drain_pending()  # not ready → no-op, event kept
        with m._pending_lock:
            self.assertIn(p, m._pending)
        m.build()           # now ready
        m._drain_pending()  # drains the queued event
        with m._pending_lock:
            self.assertEqual(m._pending, {})

    def test_drain_saves_once_for_a_batch(self):
        # R22.4: many file events → one cache write, not one per file.
        m = self._manager(_StubEmbedder())
        m.build()
        saves = []
        m._save_cache = lambda: saves.append(1)
        (self._vault / "c.md").write_text("gamma gamma", encoding="utf-8")
        (self._vault / "d.md").write_text("delta delta", encoding="utf-8")
        self._park_pending(m, {str(self._vault / "c.md"): ("update",),
                               str(self._vault / "d.md"): ("update",)})
        m._drain_pending()
        self.assertEqual(len(saves), 1)

    def test_shutdown_aborts_drain_without_saving(self):
        # R23.2: a superseded/disabled manager must not embed its queue or write.
        m = self._manager(_StubEmbedder())
        m.build()
        saves = []
        m._save_cache = lambda: saves.append(1)
        (self._vault / "c.md").write_text("gamma gamma", encoding="utf-8")
        self._park_pending(m, {str(self._vault / "c.md"): ("update",)})
        m.shutdown()
        m._drain_pending()
        self.assertEqual(saves, [])                       # no write after shutdown
        self.assertIn(str(self._vault / "c.md"), m._pending)  # left unprocessed

    def test_rename_enqueues_remove_and_move(self):
        # R23.1: a rename leaves BOTH a remove(old) and a move(new) pending.
        m = self._manager(_StubEmbedder())
        m.build()
        m.shutdown()  # park the worker; only inspect the pending map
        old = str(self._vault / "a.md")
        new = str(self._vault / "b.md")
        m.rename_file(old, new)
        with m._pending_lock:
            self.assertEqual(m._pending.get(old), ("remove",))
            self.assertEqual(m._pending.get(new), ("move", old))

    def test_update_after_rename_does_not_orphan(self):
        # R23.1: a save of the renamed file clobbers the move to ("update",); the
        # separate remove must still drop the old path (no phantom hit).
        m = self._manager(_StubEmbedder())
        m.build()
        (self._vault / "renamed.md").write_text("alpha alpha topic", encoding="utf-8")
        (self._vault / "a.md").unlink()
        self._park_pending(m, {  # the coalesced result of rename + save of dest
            str(self._vault / "a.md"): ("remove",),
            str(self._vault / "renamed.md"): ("update",),
        })
        m._drain_pending()
        names = {Path(f).name for f in m._files}
        self.assertNotIn("a.md", names)      # old path gone, no orphan
        self.assertIn("renamed.md", names)   # new path indexed

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
