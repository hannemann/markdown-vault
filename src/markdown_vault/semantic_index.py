"""Background semantic index over the vault (Phase 5, opt-in).

Builds and maintains a :class:`~markdown_vault.semantic_search.VectorIndex` over
all vault ``.md`` files.  Storage is **per file** (hash + chunks + vectors) so:

* the initial build in a background thread only re-embeds files whose content
  changed since the cached run (Ollama may be slow), and
* individual files can be re-embedded / dropped incrementally when they change,
  are created, deleted or renamed — no full rebuild.

Queries run on the caller's thread (the search worker), embedding just the one
query string.
"""

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

from . import lexical_search
from .semantic_search import Chunk, VectorIndex, chunk_markdown

logger = logging.getLogger(__name__)

# Bump to invalidate all caches when the chunking / embedding / cache format
# changes.
_INDEX_FORMAT_VERSION = "6"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# Common German (+ a few English) filler words that carry no entity signal, so a
# note is never boosted just because the question contains "über" or "wissen".
# Only ≥4-char entries — shorter tokens are already dropped by the length filter
# in _query_terms, so listing them here would be dead weight.
_ASK_STOPWORDS = frozenset({
    "über", "oder", "auch", "eine", "einen", "einem", "sind", "haben", "kann",
    "mehr", "dass", "weiss", "wissen", "gibt", "habe", "sich", "noch", "wird",
    "welche", "welcher", "warum", "wieso", "alle",
    "about", "what", "which", "know", "tell", "does", "have",
})

# One Unicode-aware tokenizer for all three matchers, so a query term, a file
# name and a heading tokenise identically — otherwise `café` matches only via
# the whole-stem shortcut and `mein café.md` never does. `[^\W_]` is a word
# character except the underscore, so `snake_case.md` splits into two boostable
# words (plain `\w` would keep it whole and lose the filename boost).
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokenize(text: str) -> set:
    return set(_WORD_RE.findall(text.lower()))


def _query_terms(query: str) -> set:
    """Content words from a question — lowercased, stopwords and short tokens
    dropped — used to match against note names."""
    return {t for t in _tokenize(query) if len(t) >= 4 and t not in _ASK_STOPWORDS}


def _name_matches(path: str, terms: set) -> bool:
    """Whether a note's file name shares a whole word with the question terms."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return stem in terms or bool(_tokenize(stem) & terms)


# A file-name match is a strong "this note is about X" signal — ranked just below
# an H1 heading (the written title) and level with an H2.
_FILENAME_BOOST = 5


def _heading_words_by_level(text: str) -> dict:
    """``{level: words}`` — whole words per Markdown heading level in a chunk."""
    out: dict = {}
    for line in text.split("\n"):
        m = re.match(r"\s{0,3}(#{1,6})\s+(.*)", line)
        if not m:
            continue
        out.setdefault(len(m.group(1)), set()).update(_tokenize(m.group(2)))
    return out


def _boost(chunk, terms: set) -> int:
    """Retrieval boost (higher ranks first). The shallower the matching heading,
    the bigger the boost — H1=6 … H6=1 — because a top-level heading is the
    note's title while a deep sub-heading is a passing mention. A file-name match
    contributes _FILENAME_BOOST. A chunk's boost is the strongest match it has."""
    best = _FILENAME_BOOST if _name_matches(chunk.path, terms) else 0
    for level, words in _heading_words_by_level(chunk.text).items():
        if words & terms:
            best = max(best, 7 - level)  # H1 -> 6, H6 -> 1
    return best


class BackendUnavailable(Exception):
    """The embedding backend could not be reached (network error / timeout).

    Distinct from a data error (a bad chunk) so a build or reindex that fails
    because the server is down is *not* recorded as a successful empty result —
    which would cache the failure and leave the index empty forever (R26.1).
    """


class SemanticIndexManager:
    """Owns the vault's semantic index: background build, per-file cache, and
    incremental updates."""

    def __init__(self, embedder, get_vault_paths, state_dir, signature_tag,
                 min_score: float = 0.35, on_busy=None, on_status=None,
                 on_progress=None) -> None:
        self._embedder = embedder
        self._get_vault_paths = get_vault_paths
        self._state_dir = Path(state_dir)
        self._signature_tag = signature_tag  # e.g. "ollama:nomic-embed-text"
        self._min_score = min_score
        self._index = VectorIndex(embedder)
        self._lock = threading.Lock()
        self._ready = False
        # Busy signalling: on_busy(True/False) fires on the 0↔1 transition of a
        # refcount over all background work (build + incremental ops), so the UI
        # can show a single "indexing" indicator regardless of backend.
        self._on_busy = on_busy
        self._busy = 0
        self._busy_lock = threading.Lock()
        # Backend availability signalling: on_status(available: bool) fires only
        # on a change, so the UI can surface "search backend unreachable" (R26.1).
        self._on_status = on_status
        self._backend_ok = True
        self._on_progress = on_progress  # on_progress(done, total) during a build
        # path -> {"hash": str, "chunks": list[Chunk], "vecs": np.ndarray}
        self._files: dict[str, dict] = {}
        # Lazy BM25 (sparse) index for hybrid retrieval, cached by a
        # (files, mtimes) signature so it rebuilds only when notes change.
        self._lex_cache: dict = {}               # scope roots -> (signature, BM25)
        self._lex_lock = threading.Lock()        # built from the ask worker thread
        self._json_path = self._state_dir / "semantic-index.json"
        self._npy_path = self._state_dir / "semantic-index.npy"
        # Incremental-update queue: one worker drains a coalesced (path -> op)
        # map instead of a thread + full cache rewrite per file event.
        self._pending: dict[str, tuple] = {}
        self._pending_lock = threading.Lock()
        self._wake = threading.Event()
        self._worker = None
        self._worker_lock = threading.Lock()
        self._shutdown = False

    # ── Lifecycle ──────────────────────────────────────────────────

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def _busy_enter(self) -> None:
        if self._on_busy is None:
            return
        with self._busy_lock:
            self._busy += 1
            first = self._busy == 1
        if first:
            try:
                self._on_busy(True)
            except Exception:
                logger.debug("on_busy(True) callback failed", exc_info=True)

    def _busy_exit(self) -> None:
        if self._on_busy is None:
            return
        with self._busy_lock:
            self._busy -= 1
            last = self._busy == 0
        if last:
            try:
                self._on_busy(False)
            except Exception:
                logger.debug("on_busy(False) callback failed", exc_info=True)

    def _report_progress(self, done: int, total: int) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(done, total)
        except Exception:
            logger.debug("on_progress callback failed", exc_info=True)

    def _report_status(self, available: bool) -> None:
        """Fire on_status only when backend availability actually changes."""
        if self._on_status is None or available == self._backend_ok:
            return
        self._backend_ok = available
        try:
            self._on_status(available)
        except Exception:
            logger.debug("on_status callback failed", exc_info=True)

    def build(self) -> None:
        """Load the per-file cache and re-embed only files that changed."""
        self._busy_enter()
        try:
            self._build()
        finally:
            self._busy_exit()

    def _build(self) -> None:
        cached = self._load_cache()
        files = self._walk_files()
        new_files: dict[str, dict] = {}
        # First pass: keep unchanged files from the cache, collect the paths of
        # the rest so we know the total up front (for determinate progress).  We
        # store only paths, not text — re-reading in the second pass avoids
        # buffering every changed file's content (the whole vault on a cold
        # cache); the file was just read, so it's in the page cache (R27.2).
        to_embed: list[str] = []
        for path in files:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            h = _hash(text)
            entry = cached.get(path)
            if entry is not None and entry["hash"] == h:
                new_files[path] = entry
            else:
                to_embed.append(path)
        # Second pass: embed the changed files, reporting progress.
        total = len(to_embed)
        changed = 0
        aborted = False
        for path in to_embed:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                kept, vecs = self._embed_all(chunk_markdown(text, path))
            except BackendUnavailable:
                aborted = True         # stop; no point taking a timeout per file
                break
            new_files[path] = {"hash": _hash(text), "chunks": kept, "vecs": vecs}
            changed += 1
            self._report_progress(changed, total)
        with self._lock:
            self._files = new_files    # keep what we embedded / loaded, in memory
            self._rebuild_index_locked()
            self._ready = True
        if aborted:
            # Do NOT persist: writing these files with their current hash would
            # cache the failure as success and skip them on every restart (R26.1).
            # Leaving the on-disk cache untouched means the next start retries.
            logger.warning("semantic index: backend unavailable during build; "
                           "cache left untouched, will retry on next start")
            self._report_status(False)
            return
        self._save_cache()
        self._wake.set()  # drain any events queued during the build (R22.6)
        self._report_status(True)
        logger.info("semantic index: %d files (%d re-embedded)",
                    len(new_files), changed)

    # ── Incremental updates (single worker, coalesced, batched save) ──
    #
    # File events enqueue one coalesced op per path; a single worker applies
    # them serially and saves the cache ONCE per drain — instead of a thread
    # plus a full cache rewrite per event, which turned a git checkout of 300
    # files into a 300-thread storm (R22.4).  Events that arrive during the
    # initial build stay queued and drain when it finishes (R22.6).

    def update_file(self, path: str) -> None:
        if path.endswith(".md"):
            self._enqueue(path, ("update",))

    def remove_file(self, path: str) -> None:
        self._enqueue(path, ("remove",))

    def rename_file(self, old_path: str, new_path: str) -> None:
        # Two independent coalescible ops (R23.1): move the destination (which
        # reuses the cached vectors while the source is still present) AND drop
        # the source explicitly.  If a later save clobbers the move to
        # ("update",), the separate remove still deletes the old path instead of
        # leaving it in the index as a phantom hit.
        with self._pending_lock:
            self._pending[old_path] = ("remove",)
            self._pending[new_path] = ("move", old_path)
        self._ensure_worker()
        self._wake.set()

    def _enqueue(self, path: str, op: tuple) -> None:
        with self._pending_lock:
            self._pending[path] = op            # coalesce: latest op per path wins
        self._ensure_worker()
        self._wake.set()

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker is None and not self._shutdown:
                self._worker = threading.Thread(
                    target=self._worker_loop, daemon=True)
                self._worker.start()

    def shutdown(self) -> None:
        """Stop the worker — call when the manager is replaced or disabled."""
        self._shutdown = True
        self._wake.set()

    def _worker_loop(self) -> None:
        while not self._shutdown:
            self._wake.wait()
            if self._shutdown:
                return
            self._wake.clear()
            if self.is_ready():        # keep events queued until the build lands
                self._drain_pending()

    def _drain_pending(self) -> None:
        if not self.is_ready():
            return                     # don't pop before the build has landed
        self._busy_enter()
        try:
            changed = False
            while not self._shutdown:  # a superseded/disabled manager bails out
                with self._pending_lock:
                    if not self._pending:
                        break
                    path, op = self._pending.popitem()
                try:
                    changed = self._apply_op(path, op) or changed
                except BackendUnavailable:
                    # Backend down: re-queue the path (the previous entry is left
                    # untouched, never emptied) and stop draining.  No _wake, so
                    # we don't tight-loop into the timeout; the next real event
                    # (or a restart) retries it (R26.1).
                    with self._pending_lock:
                        self._pending.setdefault(path, op)
                    logger.warning("semantic index: backend unavailable, "
                                   "deferring incremental updates")
                    self._report_status(False)
                    break
                except Exception:
                    logger.warning("semantic index: op %s on %s failed",
                                   op[0], os.path.basename(path), exc_info=True)
            if changed and not self._shutdown:
                self._save_cache()     # one write per drain, not per file
        finally:
            self._busy_exit()

    def _apply_op(self, path: str, op: tuple) -> bool:
        kind = op[0]
        if kind == "update":
            return self._reindex_file(path)
        if kind == "remove":
            return self._drop_file(path)
        if kind == "move":
            return self._move_file(op[1], path)
        return False

    # Core ops mutate the in-memory index (thread-safe via self._lock) and
    # return whether anything changed; the drain loop owns saving + busy.

    def _reindex_file(self, path: str) -> bool:
        if not self.is_ready():
            return False
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return self._drop_file(path)
        h = _hash(text)
        with self._lock:
            cur = self._files.get(path)
        if cur is not None and cur["hash"] == h:
            return False  # unchanged
        kept, vecs = self._embed_all(chunk_markdown(text, path))
        with self._lock:
            self._files[path] = {"hash": h, "chunks": kept, "vecs": vecs}
            self._rebuild_index_locked()
        self._report_status(True)  # a real embed succeeded → backend is up
        logger.info("semantic index: reindexed %s (%d chunks)",
                    os.path.basename(path), len(kept))
        return True

    def _drop_file(self, path: str) -> bool:
        with self._lock:
            if path not in self._files:
                return False
            del self._files[path]
            self._rebuild_index_locked()
        logger.info("semantic index: dropped %s", os.path.basename(path))
        return True

    def _move_file(self, old_path: str, new_path: str) -> bool:
        with self._lock:
            entry = self._files.pop(old_path, None)
            if entry is not None:
                for chunk in entry["chunks"]:
                    chunk.path = new_path
                self._files[new_path] = entry
                self._rebuild_index_locked()
        if entry is None:
            return self._reindex_file(new_path)  # wasn't indexed → embed fresh
        logger.info("semantic index: renamed %s -> %s",
                    os.path.basename(old_path), os.path.basename(new_path))
        return True

    # ── Embedding & index assembly ─────────────────────────────────

    def _embed_all(self, chunks, batch: int = 32):
        """Embed chunk texts in batches; skip any that persistently fail so a
        single bad/oversized chunk or a transient error can't waste the build."""
        import numpy as np
        kept: list = []
        vecs: list = []
        for i in range(0, len(chunks), batch):
            group = chunks[i:i + batch]
            try:
                embs = self._embedder.embed([c.text for c in group], is_query=False)
                kept.extend(group)
                vecs.extend(embs)
            except OSError as exc:
                # Backend unreachable/hung (URLError/TimeoutError/HTTPError all
                # subclass OSError): retrying per chunk would hit the same wall 32
                # more times (R25.1).  Signal the caller distinctly so it does NOT
                # record this file as a successful empty result (R26.1).
                raise BackendUnavailable(str(exc)) from exc
            except Exception:
                # A data problem (one bad/oversized chunk): retry per chunk so a
                # single failure can't waste the whole batch.
                logger.debug("batch embed failed; retrying per chunk", exc_info=True)
                for c in group:
                    try:
                        vecs.append(self._embedder.embed([c.text], is_query=False)[0])
                        kept.append(c)
                    except Exception:
                        logger.warning("semantic index: skipping chunk %s:%d "
                                       "(embed failed)", c.path, c.line)
        if not vecs:
            return kept, np.zeros((0, 0), dtype="float32")
        arr = np.asarray(vecs, dtype="float32")
        arr /= np.clip(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9, None)
        return kept, arr

    def _rebuild_index_locked(self):
        """Re-stack all files' chunks/vectors into the index (holds the lock)."""
        import numpy as np
        all_chunks: list = []
        mats: list = []
        for entry in self._files.values():
            if entry["chunks"] is not None and len(entry["chunks"]):
                all_chunks.extend(entry["chunks"])
                mats.append(entry["vecs"])
        matrix = np.vstack(mats) if mats else None
        self._index.set_precomputed(all_chunks, matrix)

    # ── Query ──────────────────────────────────────────────────────

    @staticmethod
    def _snippet(chunk):
        """First content-bearing line of the chunk + its 1-based line number, so
        a chunk that opens with a rule/markup line doesn't display as ``---``."""
        lines = chunk.text.split("\n")
        offset = next(
            (i for i, ln in enumerate(lines) if any(c.isalnum() for c in ln)), 0)
        return lines[offset], chunk.line + offset

    def query_files(self, query: str, top_k: int = 20):
        """Return semantic FileResults (best chunk per file), marked semantic."""
        from .search_backend import FileResult, Match
        results = []
        seen: set[str] = set()
        for chunk, score in self._top_hits(query, top_k):
            if chunk.path in seen:
                continue
            seen.add(chunk.path)
            snippet, line = self._snippet(chunk)
            try:
                mtime = os.path.getmtime(chunk.path)
            except OSError:
                mtime = 0.0
            results.append(FileResult(
                path=chunk.path, score=score,
                matches=[Match(chunk.path, line, snippet, [])],
                total_matches=1, name_hit=False, title_hit=False,
                heading_hits=0, mtime=mtime, semantic=True,
            ))
            if len(results) >= top_k:
                break
        return results

    def query_open(self, query: str, top_k: int = 20):
        """Return semantic hits as quick-open results (best chunk per file)."""
        from .quick_open import QuickResult
        results = []
        seen: set[str] = set()
        for chunk, score in self._top_hits(query, top_k):
            if chunk.path in seen:
                continue
            seen.add(chunk.path)
            name = os.path.basename(chunk.path)
            if name.endswith(".md"):
                name = name[:-3]
            results.append(QuickResult(
                path=chunk.path, name=name, folder=os.path.dirname(chunk.path),
                score=score, source="semantic",
            ))
            if len(results) >= top_k:
                break
        return results

    def retrieve(self, query, top_k: int = 6, vaults=None, hybrid: bool = False):
        """Top *note-level* ``(passage, score)`` for RAG answering — one passage
        per note, holding the note's WHOLE text. *vaults*, if given, restricts
        results to files under those vault roots (scope for the Ask feature).

        Retrieval matches at chunk granularity, but a comparison question ("which
        planet is the heaviest?") needs each candidate note's *full* data block,
        not just the single chunk whose phrasing matched — the deciding number
        (mass, density, …) usually sits in a different section than the match.
        So the best chunk per note wins the slot, then the whole note is handed
        to the generator. Keeping context to a few whole notes (not many stray
        chunks) is what small local models answer reliably on.

        A **title/name boost** additionally floats the note actually *about* the
        subject to the front (``erde`` → ``erde.md``), instead of burying it
        among near-identical siblings. Scoped to RAG only.

        With *hybrid*, a BM25 (sparse) ranking is fused in via Reciprocal Rank
        Fusion, so notes matched by an exact token (proper noun, config key,
        shortcut) that the embedding blurred still surface.
        """
        # Widen the pool when a vault filter may discard many hits.
        pool = top_k * (6 if vaults else 2)
        limit = top_k
        if hybrid:                       # need more candidates to fuse against
            pool = max(pool, top_k * 5, 60)
            limit = max(top_k * 3, 30)
        notes = self._semantic_notes(query, pool, limit, vaults)
        if not hybrid:
            return [(Chunk(path, 1, self._note_text(path, best.text)), score)
                    for path, score, best in notes[:top_k]]

        sem_paths = [os.path.abspath(p) for p, _, _ in notes]
        lex_paths = self._lexical(vaults).search(query, limit)
        scores = lexical_search.rrf_scores([sem_paths, lex_paths])
        fused = sorted(scores, key=lambda p: -scores[p])[:top_k]
        best_by_path = {os.path.abspath(p): b for p, _, b in notes}
        out = []
        for path in fused:
            best = best_by_path.get(path)
            around = best.text if best is not None else ""
            # Carry the fused score so the source picker and the ask log keep a
            # real ranking signal instead of a flat 0.00 on every hit.
            out.append((Chunk(path, 1, self._note_text(path, around)),
                        round(scores[path], 4)))
        return out

    def note_hits(self, paths):
        """``(passage, score)`` for an explicit list of note *paths* — whole
        notes, no ranking. For the Ask "pick your own sources" flow, where the
        user selected the context notes instead of letting retrieval choose."""
        return [(Chunk(p, 1, self._note_text(p, "")), 0.0) for p in paths]

    def _semantic_notes(self, query, pool: int, limit: int, vaults):
        """Ranked note candidates ``[(path, score, best_chunk)]`` — the best
        chunk per note, boosted, deduped, up to *limit*. Shared by plain and
        hybrid retrieval."""
        hits = self._top_hits(query, pool)
        if not hits:
            return []
        if vaults:
            roots = tuple(os.path.abspath(v) + os.sep for v in vaults)
            hits = [(c, s) for c, s in hits
                    if os.path.abspath(c.path).startswith(roots)]
            if not hits:
                return []
        terms = _query_terms(query)
        if terms:
            hits = sorted(
                hits,
                key=lambda cs: (_boost(cs[0], terms), cs[1]),
                reverse=True,
            )
        seen: dict[str, tuple] = {}
        for chunk, score in hits:
            if chunk.path not in seen:
                seen[chunk.path] = (score, chunk)
            if len(seen) >= limit:
                break
        return [(path, score, best) for path, (score, best) in seen.items()]

    def _lexical(self, vaults) -> "lexical_search.BM25Index":
        """Lazily build (and cache) a BM25 index over the ``.md`` notes under
        *vaults* (or all managed vaults). One cache slot *per scope*, so
        alternating the vault scope swaps between kept indices instead of
        rebuilding the whole thing every question; a slot rebuilds only when its
        file set or an mtime changes. Guarded by a lock because retrieval runs on
        the ask worker thread."""
        roots = tuple(sorted(os.path.abspath(v)
                             for v in (vaults or self._get_vault_paths())))
        files = []
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                files += [os.path.join(dirpath, f)
                          for f in filenames if f.endswith(".md")]
        sig = tuple(sorted((f, int(os.path.getmtime(f)))
                           for f in files if os.path.exists(f)))
        with self._lex_lock:
            cached = self._lex_cache.get(roots)
            if cached is None or cached[0] != sig:
                docs = {}
                for f, _ in sig:
                    try:
                        docs[f] = Path(f).read_text(encoding="utf-8",
                                                    errors="replace")
                    except OSError:
                        pass
                index = lexical_search.BM25Index(docs)
                self._lex_cache[roots] = (sig, index)
                return index
            return cached[1]

    # Per-note generation-context cap. The model gets whole notes (a comparison
    # needs the full data block), but an unbounded 6 × whole-file could post
    # hundreds of KB and either time out or be silently truncated by the server
    # — and truncation is the worse outcome: the excerpts vanish while the
    # "cite [n]" instruction survives, producing confident wrong citations. Cap
    # each note, windowed around the matching chunk so the relevant part stays.
    _MAX_NOTE_CHARS = 8000

    def _note_text(self, path: str, around: str = "") -> str:
        """Note text for a note-level passage — read from disk (truest to the
        file, falling back to the indexed chunks), capped to _MAX_NOTE_CHARS."""
        try:
            txt = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            entry = self._files.get(path)
            txt = ("\n\n".join(c.text for c in entry["chunks"])
                   if entry and entry.get("chunks") else "")
        if len(txt) <= self._MAX_NOTE_CHARS:
            return txt
        start = 0
        if around:
            i = txt.find(around[:200].strip())
            # Only shift off the head when the match would otherwise be cut, and
            # clamp to the tail so the whole budget is used (keeps the H1/intro on
            # a note that is only marginally over the cap).
            if i >= 0 and i + len(around) > self._MAX_NOTE_CHARS:
                start = max(0, min(i - self._MAX_NOTE_CHARS // 3,
                                   len(txt) - self._MAX_NOTE_CHARS))
        return txt[start:start + self._MAX_NOTE_CHARS]

    def _top_hits(self, query, top_k):
        if not query:
            return []
        # Embed OUTSIDE the lock — with Ollama this is an HTTP roundtrip, and
        # holding the lock across it would block every incremental update and
        # cache write.  The lock covers only the matrix multiply.
        q = self._index.embed_query(query)
        if q is None:
            return []
        with self._lock:
            if not self._ready:
                return []
            hits = self._index.search_vector(q, top_k=top_k * 3)
        return [(c, s) for c, s in hits if s >= self._min_score]

    # ── Cache & walk ───────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        """Delete the on-disk cache so the next ``build()`` re-embeds every
        file from scratch (used by the manual 'rebuild index' action)."""
        for p in (self._json_path, self._npy_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("could not remove cache file %s", p, exc_info=True)

    def _walk_files(self):
        files = []
        for vault in self._get_vault_paths():
            if not os.path.isdir(vault):
                continue
            for root, dirs, names in os.walk(vault):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                files.extend(os.path.join(root, n) for n in names if n.endswith(".md"))
        files.sort()
        return files

    def _load_cache(self) -> dict:
        import numpy as np
        try:
            meta = json.loads(self._json_path.read_text())
            if (meta.get("version") != _INDEX_FORMAT_VERSION
                    or meta.get("sig") != self._signature_tag):
                return {}
            vecs = np.load(self._npy_path)
            files: dict[str, dict] = {}
            off = 0
            for path, fm in meta["files"].items():
                chunks = [Chunk(path, c["line"], c["text"]) for c in fm["chunks"]]
                n = len(chunks)
                files[path] = {"hash": fm["hash"], "chunks": chunks,
                               "vecs": vecs[off:off + n]}
                off += n
            if off != len(vecs):
                return {}
            return files
        except Exception:
            return {}

    def _save_cache(self) -> None:
        import numpy as np
        with self._lock:
            meta_files = {}
            mats = []
            for path, entry in self._files.items():
                meta_files[path] = {
                    "hash": entry["hash"],
                    "chunks": [{"line": c.line, "text": c.text}
                               for c in entry["chunks"]],
                }
                if len(entry["chunks"]):
                    mats.append(entry["vecs"])
            matrix = np.vstack(mats) if mats else np.zeros((0, 0), dtype="float32")
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            # Atomic write (tmp + os.replace) so a concurrent reader/writer never
            # sees a half-written .npy or a .json/.npy pair that disagree.  The
            # npy tmp name ends in .npy so np.save doesn't append its own suffix.
            npy_tmp = self._npy_path.with_name(self._npy_path.stem + ".tmp.npy")
            json_tmp = self._json_path.with_name(self._json_path.stem + ".tmp.json")
            np.save(npy_tmp, matrix)
            json_tmp.write_text(json.dumps({
                "version": _INDEX_FORMAT_VERSION,
                "sig": self._signature_tag,
                "files": meta_files,
            }))
            os.replace(npy_tmp, self._npy_path)
            os.replace(json_tmp, self._json_path)
        except Exception:
            logger.warning("failed to save semantic index cache", exc_info=True)
