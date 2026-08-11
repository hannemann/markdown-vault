"""Sparse BM25 retrieval over vault notes — the lexical half of hybrid Ask.

The semantic index matches by *meaning*, which blurs exact tokens (proper nouns,
config keys, shortcuts like ``Strg+B`` or identifiers like ``vaults.yaml``). BM25
matches those literally. Fusing the two (see :func:`reciprocal_rank_fusion`) gets
both. Pure and GTK-free, so it is unit-testable on its own.
"""

import collections
import math
import re

# Same word rule as the semantic tokenizer: letters/digits, underscore excluded
# so ``snake_case`` splits into searchable parts.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


class BM25Index:
    """In-memory BM25 over ``{path: text}`` documents (one document per note)."""

    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.paths = list(docs)
        self._tf = {p: collections.Counter(tokenize(t)) for p, t in docs.items()}
        self._len = {p: sum(c.values()) for p, c in self._tf.items()}
        self._avgdl = (sum(self._len.values()) / len(self._len)) if self._len else 0.0
        df: collections.Counter = collections.Counter()
        for counter in self._tf.values():
            df.update(counter.keys())
        n = len(docs)
        self._idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query: str, top_k: int) -> list[str]:
        """Return the *top_k* document paths, ranked by BM25 (best first)."""
        terms = tokenize(query)
        if not terms or not self._avgdl:
            return []
        scores: dict[str, float] = {}
        for path in self.paths:
            tf, dl, s = self._tf[path], self._len[path], 0.0
            for w in terms:
                f = tf.get(w, 0)
                if f and w in self._idf:
                    s += self._idf[w] * (f * (self.k1 + 1)) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            if s > 0:
                scores[path] = s
        return sorted(scores, key=lambda p: -scores[p])[:top_k]


def rrf_scores(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """The Reciprocal Rank Fusion score of each path across *rankings*.

    Each list contributes ``1 / (k + rank)`` to a path's score, so a document
    ranked well by *either* retriever floats up.
    """
    score: dict[str, float] = collections.defaultdict(float)
    for ranking in rankings:
        for rank, path in enumerate(ranking):
            score[path] += 1.0 / (k + rank + 1)
    return score


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Fuse several ranked path lists into one (Reciprocal Rank Fusion).

    The result is never worse than the better single retriever on the items they
    agree on. See :func:`rrf_scores` for the per-path fusion score.
    """
    score = rrf_scores(rankings, k)
    return sorted(score, key=lambda p: -score[p])
