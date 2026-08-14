"""Sparse BM25 retrieval over vault notes — the lexical half of hybrid Ask.

The semantic index matches by *meaning*, which blurs exact tokens (proper nouns,
config keys, shortcuts like ``Strg+B`` or identifiers like ``vaults.yaml``). BM25
matches those literally. Fusing the two (see :func:`reciprocal_rank_fusion`) gets
both. Pure and GTK-free, so it is unit-testable on its own.

Tokenization is language-aware when the optional ``simplemma`` (lemmatizer) and
``stopwordsiso`` (stopword lists) packages are installed: each text is
stopword-filtered and lemmatized in *its own* detected language, so inflected
forms collapse to a shared base (``Gesteinsplaneten`` → ``gesteinsplanet``).
Detecting per text — rather than forcing one language — avoids cross-language
mis-lemmatization (English ``list`` must not become German ``listen``). The same
tokenizer runs over documents and queries, so the collapse is symmetric. Without
the deps, or for text whose language can't be confidently detected, it degrades
to plain lowercase tokenization, and the module stays import-safe for the base
install and CI.
"""

import collections
import functools
import math
import re

# Same word rule as the semantic tokenizer: letters/digits, underscore excluded
# so ``snake_case`` splits into searchable parts.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

try:                                    # optional AI deps (see requirements-ai.txt)
    import simplemma
    import stopwordsiso
    _HAVE_LEMMA = True
except Exception:                       # base install / CI — plain tokenizer
    _HAVE_LEMMA = False

# Candidate content languages for detection; simplemma and stopwordsiso both
# support all of these.
_LANGS: tuple[str, ...] = ("en", "de", "fr", "es", "it", "pt", "nl")
# Below this detection confidence we don't trust the language, so we skip
# lemmatization rather than risk mangling out-of-scope text.
_MIN_CONFIDENCE = 0.5


@functools.lru_cache(maxsize=None)
def _stopwords(lang: str) -> frozenset:
    try:
        return frozenset(stopwordsiso.stopwords(lang))
    except Exception:
        return frozenset()


@functools.lru_cache(maxsize=200_000)
def _lemma(word: str, lang: str) -> str:
    try:
        return simplemma.lemmatize(word, lang=lang).lower()
    except Exception:
        return word.lower()


def _detect(text: str):
    """Best-fitting content language for *text*, or ``None`` if unsure."""
    try:
        lang, score = simplemma.langdetect(text, lang=_LANGS)[0]
    except Exception:
        return None
    return lang if lang != "unk" and score >= _MIN_CONFIDENCE else None


def _plain(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens for BM25 — lemmatized and stopword-filtered in the
    text's detected language when the optional deps are present, else plain."""
    if not _HAVE_LEMMA:
        return _plain(text)
    lang = _detect(text)
    if lang is None:
        return _plain(text)
    stop = _stopwords(lang)
    return [_lemma(w, lang) for w in _WORD_RE.findall(text) if w.lower() not in stop]


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
