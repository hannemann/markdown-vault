"""Semantic (vector) search — Phase 5 core (opt-in).

Pure logic, no GTK:

* :func:`chunk_markdown` — split a note into blocks with 1-based line tracking.
* An *embedder* turns texts into vectors.  Two backends are provided:
  :class:`OllamaEmbedder` (stdlib HTTP to an Ollama server) and
  :class:`OnnxEmbedder` (local, in-process via onnxruntime + a HuggingFace
  tokenizer — distro packages / Flatpak wheels, no pip on the host).
* :class:`VectorIndex` — holds normalised chunk vectors and answers cosine
  top-K queries with numpy.

The index manager (:mod:`markdown_vault.semantic_index`) owns embedding,
caching and threading, and exposes the query methods the search bar and
quick-open consume.

Nothing here imports onnxruntime/tokenizers, contacts Ollama or imports
``numpy`` at module load — those happen only when a backend / index is actually
used, so the feature stays free when disabled.
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


# Chunking is paragraph-level for retrieval precision: each paragraph is its own
# chunk (so a topic sentence isn't diluted by unrelated neighbours), a heading is
# coupled to its following paragraph for context, and tiny fragments (stray
# markup, code fences) are glued to their neighbour rather than embedded alone.
# Oversized blocks are split into overlapping windows so nothing exceeds the
# embedding model's context window.
_MIN_CHUNK_CHARS = 3
_TINY_MERGE = 15          # blocks this short glue onto the current chunk
# Kept well under the smallest supported context: the recommended ONNX model
# (multilingual MiniLM) tokenises at ~128 and is capped at 512, and ~1000 chars
# of prose is ~300-400 tokens — so a chunk's tail is never silently truncated.
_MAX_CHARS = 1000         # split blocks larger than this
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

    Frontmatter is skipped.  Chunking is paragraph-level: each blank-line block
    is its own chunk, a heading couples to the block that follows it, and tiny
    fragments glue onto their neighbour; blocks over ``_MAX_CHARS`` are split
    into overlapping windows.
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
    pending_heading = False  # acc holds only heading(s), still awaiting a body

    def flush():
        nonlocal acc
        if acc:
            chunks.append(Chunk(path, acc_start, "\n".join(acc)))
        acc = []

    for bstart, btext in _blank_line_blocks(lines, start):
        if len(btext) > _MAX_CHARS:
            flush()
            pending_heading = False
            for wstart, wtext in _split_oversized(bstart, btext):
                chunks.append(Chunk(path, wstart, wtext))
            continue

        is_heading = btext.lstrip().startswith("#")
        tiny = len(btext.strip()) < _TINY_MERGE

        if not acc:
            acc_start = bstart
            acc.append(btext)
            pending_heading = is_heading
        elif tiny:
            acc.append(btext)          # stray markup glues onto the current chunk
            pending_heading = False
        elif is_heading:
            if pending_heading:
                acc.append(btext)      # consecutive headings stay together
            else:
                flush()
                acc_start = bstart
                acc.append(btext)
                pending_heading = True
        else:  # a substantial paragraph
            if pending_heading:
                acc.append(btext)      # give the heading its body
                pending_heading = False
            else:
                flush()                # keep paragraphs as separate chunks
                acc_start = bstart
                acc.append(btext)
    flush()
    # Drop chunks with no alphanumeric content — a lone horizontal rule (``---``,
    # ``***``), a code-fence marker or similar carries no meaning but would still
    # be embedded and surface as an irrelevant hit.
    return [c for c in chunks
            if len(c.text.strip()) >= _MIN_CHUNK_CHARS
            and any(ch.isalnum() for ch in c.text)]


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


class OnnxEmbedder:
    """Embed locally, in-process, with onnxruntime + a HuggingFace tokenizer.

    No pip on the host: needs the distro packages ``onnxruntime``,
    ``tokenizers`` and ``numpy`` (or, in a Flatpak, the bundled wheels).  Point
    it at a downloaded sentence-transformer ONNX model and its ``tokenizer.json``
    — the same code path works for local dev and Flatpak.

    Mean-pools the token embeddings with the attention mask (the standard
    sentence-transformers pooling); the index normalises the result.
    """

    def __init__(self, model_path: str, tokenizer_path: str,
                 max_length: int = 512) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=max_length)
        self._input_names = {i.name for i in self._session.get_inputs()}

    def embed(self, texts, is_query: bool = False) -> list[list[float]]:
        import numpy as np
        texts = list(texts)
        if not texts:
            return []
        encs = self._tokenizer.encode_batch(texts)
        maxlen = max((len(e.ids) for e in encs), default=1)
        n = len(encs)
        input_ids = np.zeros((n, maxlen), dtype=np.int64)
        attention_mask = np.zeros((n, maxlen), dtype=np.int64)
        type_ids = np.zeros((n, maxlen), dtype=np.int64)
        for i, e in enumerate(encs):
            k = len(e.ids)
            input_ids[i, :k] = e.ids
            attention_mask[i, :k] = e.attention_mask
            type_ids[i, :k] = e.type_ids
        feed = {"input_ids": input_ids, "attention_mask": attention_mask,
                "token_type_ids": type_ids}
        feed = {k: v for k, v in feed.items() if k in self._input_names}
        outputs = self._session.run(None, feed)
        return self._mean_pool(outputs[0], attention_mask).tolist()

    @staticmethod
    def _mean_pool(token_embeddings, attention_mask):
        """Attention-masked mean over the token dimension → (n, hidden)."""
        import numpy as np
        emb = np.asarray(token_embeddings, dtype="float32")     # (n, seq, hidden)
        mask = np.asarray(attention_mask, dtype="float32")[:, :, None]
        summed = (emb * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        return summed / counts


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

    def embed_query(self, text: str):
        """Embed + normalise a query string → a 1-D vector (or ``None``).

        Split out from :meth:`query` so a caller can run the (possibly slow, e.g.
        an Ollama HTTP roundtrip) embedding *without* holding a lock, then take
        the lock only for the cheap matrix multiply in :meth:`search_vector`.
        """
        import numpy as np
        if self._vectors is None or not text:
            return None
        q = np.asarray(self._embedder.embed([text], is_query=True)[0], dtype="float32")
        return q / max(float(np.linalg.norm(q)), 1e-9)

    def search_vector(self, q, top_k: int = 10) -> list[tuple[Chunk, float]]:
        """Top-K (chunk, score) for an already-embedded, normalised vector."""
        import numpy as np
        vecs, chunks = self._vectors, self._chunks  # snapshot the pair together
        if vecs is None or q is None:
            return []
        scores = vecs @ q
        order = np.argsort(-scores)[:top_k]
        return [(chunks[i], float(scores[i])) for i in order]

    def query(self, text: str, top_k: int = 10) -> list[tuple[Chunk, float]]:
        return self.search_vector(self.embed_query(text), top_k)
