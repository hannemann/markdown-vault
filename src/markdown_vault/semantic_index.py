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
import threading
from pathlib import Path

from .semantic_search import Chunk, VectorIndex, chunk_markdown

logger = logging.getLogger(__name__)

# Bump to invalidate all caches when the chunking / embedding / cache format
# changes.
_INDEX_FORMAT_VERSION = "6"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class SemanticIndexManager:
    """Owns the vault's semantic index: background build, per-file cache, and
    incremental updates."""

    def __init__(self, embedder, get_vault_paths, state_dir, signature_tag,
                 min_score: float = 0.35, on_busy=None) -> None:
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
        # path -> {"hash": str, "chunks": list[Chunk], "vecs": np.ndarray}
        self._files: dict[str, dict] = {}
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
        changed = 0
        for path in files:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            h = _hash(text)
            entry = cached.get(path)
            if entry is not None and entry["hash"] == h:
                new_files[path] = entry
                continue
            kept, vecs = self._embed_all(chunk_markdown(text, path))
            new_files[path] = {"hash": h, "chunks": kept, "vecs": vecs}
            changed += 1
        with self._lock:
            self._files = new_files
            self._rebuild_index_locked()
            self._ready = True
        self._save_cache()
        self._wake.set()  # drain any events queued during the build (R22.6)
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
            except Exception:
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
