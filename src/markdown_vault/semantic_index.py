"""Background semantic index over the vault (Phase 5, opt-in).

Builds a :class:`~markdown_vault.semantic_search.VectorIndex` over all vault
``.md`` files in a background thread — the embedding backend (Ollama) may be
slow, so it must never block the UI — and caches the result to the state dir
keyed by a content+model signature, so an unchanged vault loads instantly on
the next run instead of re-embedding.

Queries run on the caller's thread (the global-search worker thread), embedding
just the one query string.
"""

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from .semantic_search import Chunk, VectorIndex, chunk_markdown

logger = logging.getLogger(__name__)

# Bump to invalidate all caches when the chunking / index format changes.
_INDEX_FORMAT_VERSION = "2"


class SemanticIndexManager:
    """Owns the vault's semantic index: background build, cache, and query."""

    def __init__(self, embedder, get_vault_paths, state_dir, signature_tag,
                 min_score: float = 0.35) -> None:
        self._embedder = embedder
        self._get_vault_paths = get_vault_paths
        self._state_dir = Path(state_dir)
        self._signature_tag = signature_tag  # e.g. "ollama:nomic-embed-text"
        self._min_score = min_score
        self._index = VectorIndex(embedder)
        self._lock = threading.Lock()
        self._ready = False
        self._json_path = self._state_dir / "semantic-index.json"
        self._npy_path = self._state_dir / "semantic-index.npy"

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self, on_ready=None) -> None:
        """Build the index in a daemon thread; call *on_ready* when done."""
        threading.Thread(target=self._run, args=(on_ready,), daemon=True).start()

    def _run(self, on_ready) -> None:
        try:
            self.build()
        except Exception:
            logger.warning("semantic index build failed", exc_info=True)
        if on_ready:
            on_ready()

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def build(self) -> None:
        """Load the cached index if the vault is unchanged, else (re)embed it."""
        files = self._walk_files()
        sig = self._signature(files)
        loaded = self._load_cache(sig)
        if loaded is not None:
            chunks, vecs = loaded
            logger.info("semantic index: loaded %d chunks from cache", len(chunks))
        else:
            chunks = []
            for path in files:
                try:
                    text = Path(path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                chunks.extend(chunk_markdown(text, path))
            vecs = self._embed_chunks(chunks)
            self._save_cache(sig, chunks, vecs)
            logger.info("semantic index: embedded %d chunks", len(chunks))
        with self._lock:
            self._index.set_precomputed(chunks, vecs if len(chunks) else None)
            self._ready = True

    def _embed_chunks(self, chunks):
        import numpy as np
        if not chunks:
            return np.zeros((0, 0), dtype="float32")
        raw = self._embedder.embed([c.text for c in chunks], is_query=False)
        vecs = np.asarray(raw, dtype="float32")
        vecs /= np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)
        return vecs

    # ── Query ──────────────────────────────────────────────────────

    def query_files(self, query: str, top_k: int = 20):
        """Return semantic FileResults (best chunk per file), marked semantic."""
        from .search_backend import FileResult, Match
        with self._lock:
            if not self._ready or not query:
                return []
            hits = self._index.query(query, top_k=top_k * 3)
        results = []
        seen: set[str] = set()
        for chunk, score in hits:
            if score < self._min_score or chunk.path in seen:
                continue
            seen.add(chunk.path)
            snippet = chunk.text.split("\n", 1)[0]
            try:
                mtime = os.path.getmtime(chunk.path)
            except OSError:
                mtime = 0.0
            results.append(FileResult(
                path=chunk.path, score=score,
                matches=[Match(chunk.path, chunk.line, snippet, [])],
                total_matches=1, name_hit=False, title_hit=False,
                heading_hits=0, mtime=mtime, semantic=True,
            ))
            if len(results) >= top_k:
                break
        return results

    # ── Cache & walk ───────────────────────────────────────────────

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

    def _signature(self, files) -> str:
        h = hashlib.sha256()
        h.update(_INDEX_FORMAT_VERSION.encode())
        h.update(self._signature_tag.encode())
        for path in files:
            try:
                h.update(path.encode())
                h.update(str(os.path.getmtime(path)).encode())
            except OSError:
                pass
        return h.hexdigest()

    def _load_cache(self, sig):
        import numpy as np
        try:
            meta = json.loads(self._json_path.read_text())
            if meta.get("signature") != sig:
                return None
            vecs = np.load(self._npy_path)
            chunks = [Chunk(c["path"], c["line"], c["text"]) for c in meta["chunks"]]
            if len(chunks) != len(vecs):
                return None
            return chunks, vecs
        except Exception:
            return None

    def _save_cache(self, sig, chunks, vecs) -> None:
        import numpy as np
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            np.save(self._npy_path, vecs)
            self._json_path.write_text(json.dumps({
                "signature": sig,
                "chunks": [{"path": c.path, "line": c.line, "text": c.text}
                           for c in chunks],
            }))
        except Exception:
            logger.warning("failed to save semantic index cache", exc_info=True)
