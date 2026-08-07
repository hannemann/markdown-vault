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


_MIN_CHUNK_CHARS = 3


def chunk_markdown(text: str, path: str = "") -> list[Chunk]:
    """Split *text* into blank-line-separated blocks, tracking the 1-based start
    line and skipping a leading YAML frontmatter block.  Tiny blocks are dropped.
    """
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_start = 0
    for i in range(start, len(lines)):
        if lines[i].strip() == "":
            if cur:
                chunks.append(Chunk(path, cur_start + 1, "\n".join(cur)))
                cur = []
        else:
            if not cur:
                cur_start = i
            cur.append(lines[i])
    if cur:
        chunks.append(Chunk(path, cur_start + 1, "\n".join(cur)))
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
                 timeout: float = 30.0) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        """Whether the server responds (used before enabling the feature)."""
        try:
            urllib.request.urlopen(self.url + "/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def embed(self, texts, is_query: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            body = json.dumps({"model": self.model, "prompt": text}).encode()
            req = urllib.request.Request(
                self.url + "/api/embeddings", data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
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
