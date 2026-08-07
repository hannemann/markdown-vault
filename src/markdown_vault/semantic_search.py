"""Semantic (vector) search — Phase 5 core (opt-in).

Pure logic, no GTK:

* :func:`chunk_markdown` — split a note into blocks with 1-based line tracking.
* An *embedder* turns texts into vectors.  Two backends are provided:
  :class:`OllamaEmbedder` (stdlib HTTP, no extra dependency, no download) and
  :class:`FastembedEmbedder` (local ONNX via the optional ``fastembed`` package).
* :class:`VectorIndex` — holds normalised chunk vectors and answers cosine
  top-K queries with numpy.
* :class:`SemanticProvider` — adapts the index to the quick-open engine's
  provider interface so semantic hits can be merged with name matches.

Nothing here imports ``fastembed``, contacts Ollama or imports ``numpy`` at
module load — those happen only when a backend / index is actually used, so the
feature stays free when disabled.
"""

import json
import logging
import os
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Chunking ────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """A passage of a note, with its 1-based start line."""

    path: str
    line: int
    text: str


# Chunking is tuned for retrieval quality: small blocks (bare headings, short
# paragraphs) are merged up to a target size so each chunk carries context, and
# oversized blocks are split into overlapping windows so nothing exceeds the
# embedding model's context window.
_MIN_CHUNK_CHARS = 3
_TARGET_CHARS = 600       # accumulate small blocks up to this
_MIN_FLUSH_CHARS = 250    # only break before a heading once this much is buffered
_MAX_CHARS = 2000         # split blocks larger than this
_OVERLAP_CHARS = 200      # overlap between split windows


def _blank_line_blocks(lines, start):
    """Yield (1-based start line, text) for each blank-line-separated block."""
    cur: list[str] = []
    cur_start = start
    for i in range(start, len(lines)):
        if lines[i].strip() == "":
            if cur:
                yield cur_start + 1, "\n".join(cur)
                cur = []
        else:
            if not cur:
                cur_start = i
            cur.append(lines[i])
    if cur:
        yield cur_start + 1, "\n".join(cur)


def _split_oversized(start_line: int, text: str):
    """Split a long block into overlapping windows, tracking each start line."""
    step = max(1, _MAX_CHARS - _OVERLAP_CHARS)
    i = 0
    n = len(text)
    while i < n:
        piece = text[i:i + _MAX_CHARS]
        yield start_line + text[:i].count("\n"), piece
        if i + _MAX_CHARS >= n:
            break
        i += step


def chunk_markdown(text: str, path: str = "") -> list[Chunk]:
    """Split *text* into retrieval-sized chunks with 1-based line tracking.

    Frontmatter is skipped.  Blank-line blocks are merged up to ~600 chars (a
    heading starts a fresh chunk once a real section has accumulated, so headings
    group with their body); blocks over ~2000 chars are split with overlap.
    """
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    chunks: list[Chunk] = []
    acc: list[str] = []
    acc_start = 0
    acc_len = 0

    def flush():
        nonlocal acc, acc_len
        if acc:
            chunks.append(Chunk(path, acc_start, "\n".join(acc)))
        acc = []
        acc_len = 0

    for bstart, btext in _blank_line_blocks(lines, start):
        if btext.lstrip().startswith("#") and acc_len >= _MIN_FLUSH_CHARS:
            flush()
        if len(btext) > _MAX_CHARS:
            flush()
            for wstart, wtext in _split_oversized(bstart, btext):
                chunks.append(Chunk(path, wstart, wtext))
            continue
        if not acc:
            acc_start = bstart
        acc.append(btext)
        acc_len += len(btext) + 1
        if acc_len >= _TARGET_CHARS:
            flush()
    flush()
    return [c for c in chunks if len(c.text.strip()) >= _MIN_CHUNK_CHARS]


# ── Embedders ───────────────────────────────────────────────────────
#
# An embedder is any object with ``embed(texts, is_query=False) -> list[list
# [float]]``.  ``is_query`` lets a backend prefix queries vs passages (some
# models require it); backends that don't need it ignore the flag.

class OllamaEmbedder:
    """Embed via a running Ollama server (``POST /api/embeddings``).

    No extra Python dependency and no model download by the app — the user
    manages models with ``ollama pull``.  With a localhost URL the data never
    leaves the machine.
    """

    def __init__(self, model: str, url: str = "http://localhost:11434",
                 timeout: float = 60.0, batch: int = 32) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.batch = batch
        # nomic-embed-text is trained with task-instruction prefixes; using them
        # noticeably improves retrieval.  Other models don't use these, so only
        # apply for nomic.
        self._nomic = "nomic" in model.lower()

    def _prep(self, texts, is_query: bool):
        if not self._nomic:
            return list(texts)
        tag = "search_query: " if is_query else "search_document: "
        return [tag + t for t in texts]

    def available(self) -> bool:
        """Whether the server responds (used before enabling the feature)."""
        try:
            urllib.request.urlopen(self.url + "/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def embed(self, texts, is_query: bool = False) -> list[list[float]]:
        texts = self._prep(texts, is_query)
        if not texts:
            return []
        try:
            return self._embed_batch(texts)
        except Exception:
            logger.debug("ollama /api/embed unavailable; falling back to "
                         "/api/embeddings", exc_info=True)
            return self._embed_singular(texts)

    def _post(self, endpoint: str, payload: dict):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url + endpoint, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _embed_batch(self, texts) -> list[list[float]]:
        """Modern batch endpoint: several inputs per request (fewer round-trips)."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            group = texts[i:i + self.batch]
            data = self._post("/api/embed", {"model": self.model, "input": group})
            embs = data["embeddings"]
            if len(embs) != len(group):
                raise ValueError("ollama /api/embed returned wrong count")
            out.extend([float(x) for x in e] for e in embs)
        return out

    def _embed_singular(self, texts) -> list[list[float]]:
        """Legacy endpoint: one prompt per request (older Ollama)."""
        out: list[list[float]] = []
        for text in texts:
            data = self._post("/api/embeddings", {"model": self.model, "prompt": text})
            out.append([float(x) for x in data["embedding"]])
        return out


class FastembedEmbedder:
    """Embed locally with the optional ``fastembed`` package (ONNX, no torch)."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding  # optional dependency, lazy
        self._model = TextEmbedding(model_name=model_name)
        self._is_e5 = "e5" in model_name.lower()

    def embed(self, texts, is_query: bool = False) -> list[list[float]]:
        texts = list(texts)
        if self._is_e5:  # e5 models need query:/passage: prefixes
            prefix = "query: " if is_query else "passage: "
            texts = [prefix + t for t in texts]
        return [[float(x) for x in v] for v in self._model.embed(texts)]


# ── Vector index ────────────────────────────────────────────────────

class VectorIndex:
    """Normalised chunk vectors + cosine top-K search (numpy brute force)."""

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self._vectors = None       # numpy (N, D), L2-normalised
        self._chunks: list[Chunk] = []

    def __len__(self) -> int:
        return len(self._chunks)

    def set_precomputed(self, chunks, vectors) -> None:
        """Install already-embedded, L2-normalised *vectors* for *chunks*.

        Used by the index manager, which handles embedding + caching itself.
        """
        self._chunks = list(chunks)
        self._vectors = vectors if self._chunks else None

    def build(self, chunks) -> None:
        import numpy as np
        self._chunks = list(chunks)
        if not self._chunks:
            self._vectors = None
            return
        raw = self._embedder.embed([c.text for c in self._chunks], is_query=False)
        vecs = np.asarray(raw, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        self._vectors = vecs / np.clip(norms, 1e-9, None)

    def query(self, text: str, top_k: int = 10) -> list[tuple[Chunk, float]]:
        import numpy as np
        if self._vectors is None or not text:
            return []
        q = np.asarray(self._embedder.embed([text], is_query=True)[0], dtype="float32")
        q = q / max(float(np.linalg.norm(q)), 1e-9)
        scores = self._vectors @ q
        order = np.argsort(-scores)[:top_k]
        return [(self._chunks[i], float(scores[i])) for i in order]


# ── Quick-open provider ─────────────────────────────────────────────

class SemanticProvider:
    """Adapt a :class:`VectorIndex` to the quick-open engine's provider API.

    Returns the best-scoring chunk per file as a quick-open result.  Cosine
    scores are on a 0..1 scale; merging with the fuzzy name provider needs
    score normalisation at the engine level (a later wiring step).
    """

    source = "semantic"

    def __init__(self, index: VectorIndex, min_score: float = 0.3) -> None:
        self._index = index
        self._min_score = min_score

    def search(self, query: str, limit: int = 30):
        if not query or len(self._index) == 0:
            return []
        from .quick_open import QuickResult
        results = []
        seen: set[str] = set()
        for chunk, score in self._index.query(query, top_k=limit * 3):
            if score < self._min_score or chunk.path in seen:
                continue
            seen.add(chunk.path)
            name = os.path.basename(chunk.path)
            if name.endswith(".md"):
                name = name[:-3]
            results.append(QuickResult(
                path=chunk.path, name=name, folder=os.path.dirname(chunk.path),
                score=score, source=self.source,
            ))
            if len(results) >= limit:
                break
        return results
