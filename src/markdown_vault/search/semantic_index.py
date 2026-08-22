"""Background semantic index over the vault (Phase 5, opt-in).

Builds and maintains a :class:`~markdown_vault.search.semantic_search.VectorIndex` over
all vault ``.md`` files.  Storage is **per file** (hash + chunks + vectors) so:

* the initial build in a background thread only re-embeds files whose content
  changed since the cached run (Ollama may be slow), and
* individual files can be re-embedded / dropped incrementally when they change,
  are created, deleted or renamed — no full rebuild.

Queries run on the caller's thread (the search worker), embedding just the one
query string.
"""

import collections
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

from markdown_vault.search import lexical_search
from markdown_vault.search.semantic_search import (
    Chunk, DimensionMismatch, VectorIndex, chunk_markdown)

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


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)
_TAGS_INLINE_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.M)
_TAGS_BLOCK_RE = re.compile(r"^tags:\s*\n((?:[ \t]*-[ \t]*\S.*\n?)+)", re.M)


def _frontmatter_tags(text: str) -> list:
    """Lower-cased frontmatter ``tags:`` of a note — inline ``[a, b]`` or a YAML
    block list. Empty when there is no front matter or no tags."""
    m = _FRONTMATTER_RE.search(text)
    if not m:
        return []
    fm = m.group(1)
    im = _TAGS_INLINE_RE.search(fm)
    if im:
        raw = im.group(1).split(",")
    else:
        bm = _TAGS_BLOCK_RE.search(fm)
        if not bm:
            return []
        raw = [ln.strip().lstrip("-") for ln in bm.group(1).splitlines()]
    return [t.strip().strip("\"'").lower() for t in raw if t.strip()]


_TYPE_RE = re.compile(r"^type:\s*(.+?)\s*$", re.M)


def _frontmatter_type(text: str) -> list:
    """OKF-style single ``type:`` field (the note's entity class, e.g. ``video``
    or ``concept``) — treated as one more category key. A scalar only; a list
    form is ignored (``type`` is one value by the OKF spec)."""
    m = _FRONTMATTER_RE.search(text)
    if not m:
        return []
    tm = _TYPE_RE.search(m.group(1))
    if not tm:
        return []
    v = tm.group(1).strip().strip("\"'").lower()
    if not v or "," in v or v.startswith("["):
        return []
    return [v]


_HASHTAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9_/-]+)")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _inline_hashtags(text: str) -> list:
    """Obsidian-style ``#tag`` mentions in the note body — the common way casual
    note-takers tag without YAML front matter. Front matter and code (fenced or
    inline) are skipped; a heading (``## Foo`` — a space after ``#``) doesn't
    match, nor a URL fragment (``page#section`` — no leading space), and a tag
    must contain a letter so ``#2026`` / ``#1`` are ignored."""
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():] if m else text
    body = _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub(" ", body))
    return [t for t in (h.group(1).lower() for h in _HASHTAG_RE.finditer(body))
            if any(c.isalpha() for c in t)]


def _note_tags(text: str) -> list:
    """All category keys of a note — front matter ``tags:`` plus inline ``#tags``
    plus the OKF ``type:`` field — normalised. A nested tag (``project/active``)
    also registers its top-level segment (``project``), so a query naming the
    parent category still matches."""
    keys: set = set()
    for raw in _frontmatter_tags(text) + _frontmatter_type(text) + _inline_hashtags(text):
        t = raw.strip().lower()
        if not t:
            continue
        keys.add(t)
        if "/" in t:
            keys.add(t.split("/", 1)[0])
    return sorted(keys)


class SemanticIndexManager:
    """Owns the vault's semantic index: background build, per-file cache, and
    incremental updates."""

    def __init__(self, embedder, get_vault_paths, state_dir, signature_tag,
                 min_score: float = 0.35, on_busy=None, on_status=None,
                 on_progress=None, on_dim_mismatch=None) -> None:
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
        # on_dim_mismatch() fires once when the embedding dimension changes under
        # an unchanged signature. The manager does NOT rebuild itself — only the
        # window can, since it owns the manager lifecycle and the build lock (a
        # query-triggered rebuild here would run past its own shutdown and clobber
        # the next manager's cache). The manager just reports; the window drives.
        self._on_dim_mismatch = on_dim_mismatch
        self._dim_signalled = False
        # path -> {"hash": str, "chunks": list[Chunk], "vecs": np.ndarray}
        self._files: dict[str, dict] = {}
        # Lazy BM25 (sparse) index for hybrid retrieval, cached by a
        # (files, mtimes) signature so it rebuilds only when notes change.
        self._lex_cache: dict = {}               # scope roots -> (signature, BM25)
        self._lex_lock = threading.Lock()        # built from the ask worker thread
        # Frontmatter-tag index per scope (tag <-> note paths), cached by the
        # same (files, mtimes) signature; drives category-completion in retrieve.
        self._tag_cache: dict = {}               # scope roots -> (sig, tag2paths, path2tags)
        self._tag_lock = threading.Lock()
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
        abort_reason = ""
        for path in to_embed:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                kept, vecs = self._embed_all(chunk_markdown(text, path))
            except BackendUnavailable as exc:
                aborted = True         # stop; no point taking a timeout per file
                abort_reason = str(exc)
                break
            new_files[path] = {"hash": _hash(text), "chunks": kept, "vecs": vecs}
            changed += 1
            self._report_progress(changed, total)
        try:
            with self._lock:
                self._files = new_files   # keep what we embedded / loaded, in memory
                self._rebuild_index_locked()
                self._ready = True
        except DimensionMismatch:
            # Freshly embedded (new model) and cached (old model) widths mixed.
            # Don't persist this — clear it and let the window rebuild from scratch.
            self._handle_dim_mismatch()
            return
        if aborted:
            # Do NOT persist: writing these files with their current hash would
            # cache the failure as success and skip them on every restart (R26.1).
            # Leaving the on-disk cache untouched means the next start retries.
            logger.warning("semantic index: backend unavailable during build "
                           "(%s); cache left untouched, will retry on next start",
                           abort_reason or "unknown")
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
                except DimensionMismatch:
                    # A save reindexed a note at a new width against old cached
                    # ones. Drop the mixed state, ask the window to rebuild, and
                    # stop draining — don't save the mixed cache below.
                    self._handle_dim_mismatch()
                    return
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
        """Re-stack all files' chunks/vectors into the index (holds the lock).

        The single funnel for every path that assembles the matrix (_build,
        _reindex_file, _drop_file, _move_file), so the dimension check lives here
        once: if freshly embedded vectors (a new model) coexist with old cached
        ones, ``mats`` has two widths and ``np.vstack`` would crash. Raise instead
        — the callers turn it into a rebuild.
        """
        import numpy as np
        all_chunks: list = []
        mats: list = []
        for entry in self._files.values():
            if entry["chunks"] is not None and len(entry["chunks"]):
                all_chunks.extend(entry["chunks"])
                mats.append(entry["vecs"])
        if len({m.shape[1] for m in mats}) > 1:
            raise DimensionMismatch(sorted({int(m.shape[1]) for m in mats}))
        matrix = np.vstack(mats) if mats else None
        self._index.set_precomputed(all_chunks, matrix)

    def _handle_dim_mismatch(self) -> None:
        """Drop the now-inconsistent in-memory state (so nothing else stacks or
        multiplies mismatched widths) and ask the window to invalidate + rebuild.
        Fires the callback once; the replacement manager starts clean."""
        with self._lock:
            self._files = {}
            self._index.set_precomputed([], None)
            self._ready = False
            first = not self._dim_signalled
            self._dim_signalled = True
        logger.warning("semantic index: embedding dimension changed under an "
                       "unchanged signature (server model swapped); rebuilding")
        if first and self._on_dim_mismatch is not None:
            try:
                self._on_dim_mismatch()
            except Exception:
                logger.debug("on_dim_mismatch callback failed", exc_info=True)

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
        from markdown_vault.search.search_backend import FileResult, Match
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
        from markdown_vault.search.quick_open import QuickResult
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
        around_by_path = {os.path.abspath(p): (best.text if best is not None else "")
                          for p, _, best in notes}
        # Walk + stat the scope once and share it with BM25 and category
        # completion, instead of each re-walking the whole vault per question.
        scope = self._scope_signature(vaults)
        if not hybrid:
            score_map = {os.path.abspath(p): s for p, s, _ in notes}
            ranked = [(os.path.abspath(p), s, around_by_path.get(os.path.abspath(p), ""))
                      for p, s, _ in notes[:top_k]]
        else:
            sem_paths = [os.path.abspath(p) for p, _, _ in notes]
            lex_paths = self._lexical(vaults, scope).search(query, limit)
            score_map = lexical_search.rrf_scores([sem_paths, lex_paths])
            fused = sorted(score_map, key=lambda p: -score_map[p])[:top_k]
            ranked = [(p, score_map[p], around_by_path.get(p, "")) for p in fused]
        # Category-completion: for a "which member of a category …" question,
        # append every (bounded) category member the ranking crowded out, so the
        # comparison has all candidates in context — without dropping the ranked
        # notes (the answer to a nearby lookup stays; see _maybe_complete_category).
        members = self._maybe_complete_category(
            query, [p for p, _, _ in ranked], score_map, around_by_path, vaults,
            scope)
        if members is None:
            final = ranked
        else:
            seen = {p for p, _, _ in ranked}
            final = ranked + [m for m in members if m[0] not in seen]
        # Carry the score so the source picker and the ask log keep a real
        # ranking signal instead of a flat 0.00 on every hit.
        return [(Chunk(path, 1, self._note_text(path, around)), round(score, 4))
                for path, score, around in final]

    # Category-completion tuning. A dominant tag must cover a majority of the
    # top hits (so the query genuinely landed inside one category, not a mixed
    # lookup), the category must be bounded (a real set, not an everything-tag),
    # and small enough to hand over as whole notes.
    _CAT_MIN_MEMBERS = 3
    _CAT_MAX_MEMBERS = 12
    _CAT_CAP = 8

    def _maybe_complete_category(self, query, base_paths, score_map,
                                 around_by_path, vaults, scope=None):
        """The members of the category the query names — to be appended to the
        ranking so a comparison sees every candidate — or ``None`` to leave the
        ranking alone.

        Fires when the query names a bounded frontmatter tag that is present in
        the top hits *and* does not name a specific member (so it asks about the
        category as a whole, "which planet is the heaviest?" — not "how heavy is
        Jupiter?"). Then the deciding note that ranking crowded out (jupiter for
        "heaviest") joins the context. Purely structural: the trigger is the
        query naming a tag (matched through the lemmatizing tokenizer, so no
        per-language superlative word list), and it engages only on tagged notes,
        so untagged vaults fall through to the plain ranking."""
        tag2paths, path2tags = self._tag_index(vaults, scope)
        if not tag2paths:
            return None
        cnt = collections.Counter(t for p in base_paths for t in path2tags.get(p, ()))

        def bounded(t):
            return self._CAT_MIN_MEMBERS <= len(tag2paths.get(t, ())) <= self._CAT_MAX_MEMBERS

        # Fire only when the query itself NAMES the category (a bounded tag it
        # mentions), never merely because a tag dominates the neighbourhood — so
        # a question about the Sun that happens to land among planet notes is not
        # hijacked into "complete the planets". Reuse the lemmatizing tokenizer so
        # an inflected mention ("Planeten") still matches the tag ("planet").
        # Match the tag against the query both raw (casing-robust: the German
        # lemmatizer maps a capitalised "Planet" to "planet" but a lowercase
        # "planet" to the verb "planen", so a raw pass is needed) and lemmatized
        # (so an inflected mention "Planeten" still matches the tag "planet").
        q_raw = set(re.findall(r"[^\W_]+", query.lower()))
        q_lemmas = set(lexical_search.tokenize(query))

        def named(t):
            parts = t.lower().split()
            if parts and all(p in q_raw for p in parts):
                return True
            tl = lexical_search.tokenize(t)
            return bool(tl) and all(x in q_lemmas for x in tl)

        cands = [t for t in cnt
                 if named(t) and bounded(t) and cnt[t] >= self._CAT_MIN_MEMBERS]
        if not cands:
            return None
        # most represented in the top hits, ties broken toward the more specific
        # (smaller) category — so "Gesteinsplaneten" wins over the broader
        # "planet" tag.
        tag = max(cands, key=lambda t: (cnt[t], -len(tag2paths[t])))
        members = tag2paths[tag]
        if self._query_names_member(query, members):
            return None                      # focused lookup on one member
        ordered = sorted(members, key=lambda m: -score_map.get(m, 0.0))[:self._CAT_CAP]
        return [(m, score_map.get(m, 0.0), around_by_path.get(m, "")) for m in ordered]

    def _query_names_member(self, query, member_paths) -> bool:
        """True if the query mentions a specific member by its note name — a
        focused lookup, not a comparison over the category. Matches the note
        stem (a language-invariant proper noun), whole-word."""
        ql = query.lower()
        for m in member_paths:
            stem = os.path.splitext(os.path.basename(m))[0].lower()
            for name in {stem, stem.replace("-", " "), stem.replace("_", " ")}:
                if len(name) >= 3 and re.search(r"\b%s\b" % re.escape(name), ql):
                    return True
        return False

    def _scope_signature(self, vaults):
        """``(roots, sig)`` for a vault scope: the sorted absolute roots (the cache
        key) and a sorted ``(path, mtime)`` signature of the ``.md`` notes under
        them (a changed file set or mtime invalidates a cached index). Shared by
        the tag and lexical scope caches — :meth:`retrieve` computes it once and
        hands it to both, so the vault is walked and stat'd a single time."""
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
        return roots, sig

    def _tag_index(self, vaults, scope=None):
        """``(tag -> {abspath}, abspath -> [tags])`` over the ``.md`` notes under
        *vaults*, cached per scope. *scope* is an optional pre-computed
        ``(roots, sig)`` from :meth:`_scope_signature`, shared with :meth:`_lexical`
        to avoid a second walk."""
        roots, sig = scope or self._scope_signature(vaults)
        with self._tag_lock:
            cached = self._tag_cache.pop(roots, None)          # pop: re-insert = LRU
            if cached is not None and cached[0] == sig:
                self._tag_cache[roots] = cached
                return cached[1], cached[2]
            tag2paths: dict = collections.defaultdict(set)
            path2tags: dict = {}
            for f, _ in sig:
                try:
                    text = Path(f).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                ap = os.path.abspath(f)
                tags = _note_tags(text)
                path2tags[ap] = tags
                for t in tags:
                    tag2paths[t].add(ap)
            entry = (sig, dict(tag2paths), path2tags)
            self._tag_cache[roots] = entry
            while len(self._tag_cache) > self._LEX_CACHE_MAX:
                self._tag_cache.pop(next(iter(self._tag_cache)))
            return entry[1], entry[2]

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

    _LEX_CACHE_MAX = 8   # scopes are few (all vaults + per-vault); cap anyway

    def _lexical(self, vaults, scope=None) -> "lexical_search.BM25Index":
        """Lazily build (and cache) a BM25 index over the ``.md`` notes under
        *vaults* (or all managed vaults). One cache slot *per scope*, so
        alternating the vault scope swaps between kept indices instead of
        rebuilding the whole thing every question; a slot rebuilds only when its
        file set or an mtime changes. The cache is an LRU bounded to
        ``_LEX_CACHE_MAX`` slots — the "all vaults" slot necessarily overlaps the
        per-vault slots, and the bound keeps that duplication in check. Guarded by
        a lock because retrieval runs on the ask worker thread. *scope* is an
        optional pre-computed ``(roots, sig)`` shared with :meth:`_tag_index`."""
        roots, sig = scope or self._scope_signature(vaults)
        with self._lex_lock:
            cache = self._lex_cache
            cached = cache.pop(roots, None)          # pop: re-insert = move to end
            if cached is not None and cached[0] == sig:
                cache[roots] = cached                # refresh recency (LRU)
                return cached[1]
            docs = {}
            for f, _ in sig:
                try:
                    docs[f] = Path(f).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            index = lexical_search.BM25Index(docs)
            cache[roots] = (sig, index)
            while len(cache) > self._LEX_CACHE_MAX:
                cache.pop(next(iter(cache)))          # evict least-recently-used
            return index

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
        mismatch = False
        with self._lock:
            if not self._ready:
                return []
            try:
                hits = self._index.search_vector(q, top_k=top_k * 3)
            except DimensionMismatch:
                mismatch = True         # handle outside the lock (it re-acquires)
        if mismatch:
            self._handle_dim_mismatch()
            return []
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
