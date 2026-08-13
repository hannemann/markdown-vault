"""Tests for markdown_vault.semantic_index.SemanticIndexManager."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault import semantic_index as SI
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


class _HungEmbedder:
    """Every call raises OSError, like a server that never answers."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts, is_query=False):
        self.calls += 1
        raise TimeoutError("hung")


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

    def test_hybrid_recovers_exact_token_semantic_missed(self):
        # c.md's distinctive token isn't in the stub embedder's vocab → the
        # semantic side can't rank it, but BM25 (hybrid) recovers it.
        (self._vault / "c.md").write_text("configkey vaults.yaml here",
                                          encoding="utf-8")
        m = self._manager(_StubEmbedder())
        m.build()
        scope = [str(self._vault)]
        plain = {Path(c.path).name for c, _ in
                 m.retrieve("configkey", top_k=5, vaults=scope)}
        hybrid = {Path(c.path).name for c, _ in
                  m.retrieve("configkey", top_k=5, vaults=scope, hybrid=True)}
        self.assertNotIn("c.md", plain)     # embedding blurred the exact token
        self.assertIn("c.md", hybrid)       # BM25 fusion brought it back

    def test_hybrid_hits_carry_a_real_score(self):
        # R41.3 — fused hits must keep a ranking signal (not a flat 0.0), so the
        # source picker's ≈score and the ask log stay meaningful.
        (self._vault / "c.md").write_text("configkey vaults.yaml here",
                                          encoding="utf-8")
        m = self._manager(_StubEmbedder())
        m.build()
        hits = m.retrieve("configkey", top_k=5, vaults=[str(self._vault)],
                          hybrid=True)
        self.assertTrue(hits)
        self.assertTrue(all(score > 0 for _, score in hits))

    def test_lexical_cache_keeps_a_slot_per_scope(self):
        # R42.1 — one cache slot per scope: alternating the vault scope swaps
        # between kept indices instead of rebuilding the whole BM25 index every
        # question.
        sub = self._vault / "sub"
        sub.mkdir()
        (sub / "d.md").write_text("configkey here", encoding="utf-8")
        m = self._manager(_StubEmbedder())
        m.build()
        idx_all = m._lexical([str(self._vault)])
        idx_sub = m._lexical([str(sub)])
        self.assertIsNot(idx_all, idx_sub)                      # distinct scopes
        # switching back and forth returns the SAME cached indices (no rebuild)
        self.assertIs(m._lexical([str(self._vault)]), idx_all)
        self.assertIs(m._lexical([str(sub)]), idx_sub)

    def test_lexical_cache_evicts_least_recently_used(self):
        # the per-scope cache is bounded: past the cap the least-recently-used
        # slot is evicted (and rebuilt on next use).
        subs = []
        for name in ("s1", "s2", "s3"):
            d = self._vault / name
            d.mkdir()
            (d / "n.md").write_text("configkey here", encoding="utf-8")
            subs.append(str(d))
        m = self._manager(_StubEmbedder())
        m.build()
        m._LEX_CACHE_MAX = 2
        a = m._lexical([subs[0]])
        m._lexical([subs[1]])
        m._lexical([subs[0]])                    # touch s0 → s1 is now the LRU
        b_again = m._lexical([subs[1]])          # keep a handle before eviction
        m._lexical([subs[0]])                    # touch s0 again → s1 LRU
        m._lexical([subs[2]])                    # inserts s2 → evicts s1
        self.assertIs(m._lexical([subs[0]]), a)  # s0 survived
        self.assertIsNot(m._lexical([subs[1]]), b_again)  # s1 was rebuilt

    def test_lexical_cache_rebuilds_on_mtime_change(self):
        # a slot rebuilds only when its own file set / mtime changes.
        m = self._manager(_StubEmbedder())
        m.build()
        idx = m._lexical([str(self._vault)])
        stamp = int(os.path.getmtime(self._vault / "a.md")) + 5
        (self._vault / "a.md").write_text("alpha changed here", encoding="utf-8")
        os.utime(self._vault / "a.md", (stamp, stamp))
        self.assertIsNot(m._lexical([str(self._vault)]), idx)   # rebuilt

    def test_note_hits_returns_whole_notes_for_given_paths(self):
        m = self._manager(_StubEmbedder())
        m.build()
        p = str(self._vault / "a.md")
        hits = m.note_hits([p])
        self.assertEqual(len(hits), 1)
        chunk, score = hits[0]
        self.assertEqual(chunk.path, p)
        self.assertIn("alpha", chunk.text)   # whole note text

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

    def test_backend_error_aborts_without_per_chunk_retry(self):
        # R25.1/R26.1: a hung backend aborts the embed via BackendUnavailable —
        # no per-chunk retry, and the caller decides what (not) to persist.
        from markdown_vault.semantic_search import Chunk
        from markdown_vault.semantic_index import BackendUnavailable
        e = _HungEmbedder()
        m = self._manager(e)
        chunks = [Chunk("/a.md", i, f"text number {i}") for i in range(40)]
        with self.assertRaises(BackendUnavailable):
            m._embed_all(chunks)
        self.assertEqual(e.calls, 1)     # aborted after the first batch, no retries

    def test_on_progress_reports_during_build(self):
        events = []
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3,
            on_progress=lambda d, t: events.append((d, t)))
        m.build()  # setUp writes a.md + b.md → both embedded
        self.assertEqual(events, [(1, 2), (2, 2)])

    def test_on_status_reports_backend_down(self):
        events = []
        m = SemanticIndexManager(
            _HungEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3, on_status=events.append)
        m.build()
        self.assertEqual(events, [False])   # fired once, on the transition

    def test_backend_free_op_does_not_clear_unavailable(self):
        # R27.1: a delete (no embed) must NOT report the backend as available.
        events = []
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3, on_status=events.append)
        m.build()
        m._report_status(False)      # pretend the backend went down
        events.clear()
        p = str(self._vault / "a.md")
        self._park_pending(m, {p: ("remove",)})
        m._drain_pending()
        self.assertEqual(events, [])  # drop embedded nothing → status untouched

    def test_successful_reindex_reports_recovery(self):
        # R27.1: availability comes from an embed that actually succeeded.
        events = []
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(self._vault)], self._state,
            "ollama:test", min_score=0.3, on_status=events.append)
        m.build()
        m._report_status(False)
        events.clear()
        (self._vault / "a.md").write_text("changed alpha beta", encoding="utf-8")
        p = str(self._vault / "a.md")
        self._park_pending(m, {p: ("update",)})
        m._drain_pending()
        self.assertEqual(events, [True])  # embed succeeded → recovery reported

    def test_failed_build_is_not_cached_as_success(self):
        # R26.1: a build against a down backend must not persist an empty cache,
        # so the next start (backend up) actually re-embeds.
        m1 = self._manager(_HungEmbedder())
        m1.build()
        self.assertFalse(m1._json_path.exists())  # nothing persisted
        self.assertEqual(m1._files, {})
        m2 = self._manager(_StubEmbedder())       # fresh manager, same state dir
        m2.build()
        self.assertGreater(sum(len(f["chunks"]) for f in m2._files.values()), 0)
        self.assertTrue(m2.query_files("alpha"))

    def test_drain_defers_and_keeps_entry_on_backend_down(self):
        # R26.1 incremental: a save while the backend is down must not empty the
        # existing entry; the event is re-queued instead.
        m = self._manager(_StubEmbedder())
        m.build()
        p = str(self._vault / "a.md")
        old_hash = m._files[p]["hash"]
        self.assertTrue(m._files[p]["chunks"])
        m._embedder = _HungEmbedder()             # backend goes down
        (self._vault / "a.md").write_text("changed alpha alpha", encoding="utf-8")
        self._park_pending(m, {p: ("update",)})
        m._drain_pending()
        self.assertIn(p, m._pending)              # re-queued, not lost
        self.assertTrue(m._files[p]["chunks"])    # old entry not emptied
        self.assertEqual(m._files[p]["hash"], old_hash)  # untouched

    def test_failed_chunk_is_skipped_not_fatal(self):
        # A chunk whose embed 500s must be skipped; the build still completes.
        (self._vault / "bad.md").write_text("beta trigger boom", encoding="utf-8")
        m = self._manager(_FlakyEmbedder(bad="trigger"))
        m.build()
        self.assertTrue(m.is_ready())
        paths = {Path(r.path).name for r in m.query_files("alpha", top_k=10)}
        self.assertIn("a.md", paths)       # good chunk indexed
        self.assertNotIn("bad.md", paths)  # failing chunk skipped


from markdown_vault.semantic_index import (
    _tokenize, _query_terms, _name_matches, _heading_words_by_level, _boost,
    _ASK_STOPWORDS,
)
from markdown_vault.semantic_search import Chunk


class TestBoostHelpers(unittest.TestCase):
    """Pure title/heading-boost helpers (no I/O)."""

    def test_tokenize_is_unicode(self):
        self.assertEqual(_tokenize("Café über Föö"), {"café", "über", "föö"})

    def test_query_terms_drop_short_and_stopwords(self):
        self.assertEqual(_query_terms("was wissen wir über jupiter"), {"jupiter"})

    def test_tokenize_splits_underscore(self):  # R35.1
        self.assertEqual(_tokenize("snake_case"), {"snake", "case"})
        self.assertTrue(_name_matches("/v/snake_case.md", {"snake"}))

    def test_stopwords_are_all_reachable(self):
        self.assertTrue(all(len(w) >= 4 for w in _ASK_STOPWORDS))

    def test_name_matches_accented_and_kebab(self):
        self.assertTrue(_name_matches("/v/mein café.md", {"café"}))
        self.assertTrue(_name_matches("/v/richtlinie-homeoffice.md", {"homeoffice"}))
        self.assertFalse(_name_matches("/v/richtlinie-urlaub.md", {"homeoffice"}))

    def test_heading_words_by_level(self):
        hw = _heading_words_by_level("# Erde\n## Steckbrief\ntext\n### Große Monde")
        self.assertEqual(hw[1], {"erde"})
        self.assertEqual(hw[2], {"steckbrief"})
        self.assertEqual(hw[3], {"große", "monde"})

    def test_boost_prefers_shallower_heading_over_filename(self):
        # filename match = 5; H1 = 6, H2 = 5, H3 = 4.
        self.assertEqual(_boost(Chunk("/v/erde.md", 1, "# Erde"), {"erde"}), 6)
        self.assertEqual(_boost(Chunk("/v/erde.md", 1, "### Erde"), {"erde"}), 5)  # name wins
        self.assertEqual(_boost(Chunk("/v/mars.md", 1, "## Erde"), {"erde"}), 5)  # H2 only
        self.assertEqual(_boost(Chunk("/v/mars.md", 1, "nothing"), {"erde"}), 0)


class TestRetrieveAndNoteText(unittest.TestCase):
    def setUp(self):
        self._v1 = Path(tempfile.mkdtemp())
        self._v2 = Path(tempfile.mkdtemp())
        self._state = Path(tempfile.mkdtemp())
        (self._v1 / "alpha.md").write_text(
            "# Alpha\n\nalpha alpha\n\n## More\nalpha again", encoding="utf-8")
        (self._v1 / "beta.md").write_text("beta beta", encoding="utf-8")
        (self._v2 / "gamma.md").write_text("gamma gamma", encoding="utf-8")

    def tearDown(self):
        for p in (self._v1, self._v2, self._state):
            shutil.rmtree(p, ignore_errors=True)

    def _built(self, vaults):
        m = SemanticIndexManager(
            _StubEmbedder(), lambda: [str(v) for v in vaults], self._state,
            "t", min_score=0.0)
        m.build()
        return m

    def test_note_level_returns_whole_note_and_dedups(self):
        m = self._built([self._v1])
        hits = m.retrieve("alpha", top_k=6)
        paths = [c.path for c, _ in hits]
        self.assertEqual(len(paths), len(set(paths)))  # one passage per note
        alpha = next(c for c, _ in hits if c.path.endswith("alpha.md"))
        self.assertIn("## More", alpha.text)  # whole note, not the one chunk
        self.assertEqual(alpha.line, 1)

    def test_vault_filter_scopes_results(self):
        m = self._built([self._v1, self._v2])
        hits = m.retrieve("gamma", top_k=6, vaults=[str(self._v2)])
        self.assertTrue(hits)
        self.assertTrue(all(c.path.startswith(str(self._v2)) for c, _ in hits))
        scoped = m.retrieve("gamma", top_k=6, vaults=[str(self._v1)])
        self.assertTrue(all(not c.path.endswith("gamma.md") for c, _ in scoped))

    def test_note_text_caps_and_windows_around_match(self):
        big = self._v1 / "big.md"
        big.write_text("HEAD\n" + "x" * 20000 + "\nNEEDLE\n" + "y" * 20000,
                       encoding="utf-8")
        m = self._built([self._v1])
        txt = m._note_text(str(big), around="NEEDLE")
        self.assertLessEqual(len(txt), m._MAX_NOTE_CHARS)
        self.assertIn("NEEDLE", txt)  # window kept the matching region

    def test_note_text_window_keeps_match_just_under_cap(self):  # R35.4
        # A long matching block starting just under the cap: the fix windows
        # around it, so its END survives; the old head-only cut dropped it.
        cap = SemanticIndexManager._MAX_NOTE_CHARS
        block = "STARTTOKEN" + "z" * 800 + "ENDTOKEN"
        note = self._v1 / "edge.md"
        note.write_text("x" * (cap - 100) + block + "y" * 3000, encoding="utf-8")
        m = self._built([self._v1])
        txt = m._note_text(str(note), around=block[:200])
        self.assertIn("ENDTOKEN", txt)

    def test_note_text_keeps_head_when_barely_over_cap(self):  # R36.1
        # Note only marginally over the cap, short match in the middle: the head
        # (H1/frontmatter) must not be shifted out and the budget not wasted.
        cap = SemanticIndexManager._MAX_NOTE_CHARS
        note = self._v1 / "head.md"
        body = "HEADTOKEN\n" + "a" * 5000 + "MIDMATCH" + "b" * (cap + 13 - 5000)
        note.write_text(body, encoding="utf-8")
        m = self._built([self._v1])
        txt = m._note_text(str(note), around="MIDMATCH")
        self.assertIn("HEADTOKEN", txt)  # head kept (no needless shift)
        self.assertIn("MIDMATCH", txt)


class TestCategoryCompletion(unittest.TestCase):
    """Pure trigger logic — no embedder/index needed (tag index is injected)."""

    def _planets(self):
        names = ["merkur", "venus", "erde", "mars",
                 "jupiter", "saturn", "uranus", "neptun"]
        rocky = {"merkur", "venus", "erde", "mars"}
        paths = {n: f"/v/{n}.md" for n in names}
        tag2 = {"planet": {paths[n] for n in names},
                "gesteinsplanet": {paths[n] for n in rocky}}
        path2 = {paths[n]: (["planet", "gesteinsplanet"] if n in rocky
                            else ["planet", "gasplanet"]) for n in names}
        return paths, tag2, path2

    def _mgr(self, tag2, path2):
        m = SemanticIndexManager(_StubEmbedder(), lambda: ["/v"],
                                 tempfile.mkdtemp(), "t", min_score=0.3)
        m._tag_index = lambda vaults: (tag2, path2)
        return m

    def test_frontmatter_tags_inline_and_block(self):
        self.assertEqual(
            SI._frontmatter_tags("---\ntags: [Planet, Gesteinsplanet]\n---\nx"),
            ["planet", "gesteinsplanet"])
        self.assertEqual(
            SI._frontmatter_tags("---\ntags:\n  - planet\n  - moon\n---\n"),
            ["planet", "moon"])
        self.assertEqual(SI._frontmatter_tags("no front matter here"), [])

    def test_inline_hashtags_harvested(self):
        self.assertEqual(
            SI._inline_hashtags("intro #planet and #gas-giant here"),
            ["planet", "gas-giant"])

    def test_hashtags_ignore_headings_code_urls_and_numbers(self):
        txt = ("## Heading is not a tag\n"
               "see http://x.io/p#frag not a tag\n"
               "`#codetag` skipped\n"
               "#2026 numeric dropped but #q4goal kept\n")
        self.assertEqual(SI._inline_hashtags(txt), ["q4goal"])

    def test_note_tags_merge_frontmatter_hashtags_and_nesting(self):
        txt = "---\ntags: [Planet]\n---\nbody with #projekt/aktiv here"
        self.assertEqual(SI._note_tags(txt),
                         ["planet", "projekt", "projekt/aktiv"])

    def test_named_category_completes_full_set(self):
        paths, tag2, path2 = self._planets()
        m = self._mgr(tag2, path2)
        base = [paths[n] for n in
                ("merkur", "neptun", "saturn", "erde", "uranus", "venus")]
        out = m._maybe_complete_category("welcher planet ist am schwersten",
                                         base, {}, {}, ["/v"])
        got = {os.path.basename(p) for p, _, _ in out}
        self.assertEqual(len(got), 8)
        self.assertIn("jupiter.md", got)      # crowded-out answer recovered

    def test_named_subcategory_beats_broader_tag(self):
        paths, tag2, path2 = self._planets()
        m = self._mgr(tag2, path2)
        base = [paths[n] for n in ("merkur", "venus", "erde", "mars", "jupiter")]
        out = m._maybe_complete_category("liste gesteinsplanet", base,
                                         {}, {}, ["/v"])
        got = {os.path.basename(p) for p, _, _ in out}
        self.assertEqual(got, {"merkur.md", "venus.md", "erde.md", "mars.md"})

    def test_unnamed_category_does_not_fire(self):
        # a lookup that lands among planet notes but names no category tag
        # (a Sun question) must not be hijacked into completing the planets
        paths, tag2, path2 = self._planets()
        m = self._mgr(tag2, path2)
        base = [paths[n] for n in ("merkur", "venus", "erde", "mars", "jupiter")]
        self.assertIsNone(m._maybe_complete_category(
            "wie heiss ist die sonne", base, {}, {}, ["/v"]))

    def test_focused_lookup_is_not_expanded(self):
        # names the category (planet) AND a specific member (jupiter) → focused
        paths, tag2, path2 = self._planets()
        m = self._mgr(tag2, path2)
        base = [paths[n] for n in ("jupiter", "saturn", "neptun", "mars", "erde")]
        self.assertIsNone(m._maybe_complete_category(
            "welcher planet ist schwerer als jupiter", base, {}, {}, ["/v"]))

    def test_untagged_vault_falls_through(self):
        m = self._mgr({}, {})
        self.assertIsNone(m._maybe_complete_category(
            "welcher planet ist am schwersten", ["/v/a.md"], {}, {}, ["/v"]))


if __name__ == "__main__":
    unittest.main()
