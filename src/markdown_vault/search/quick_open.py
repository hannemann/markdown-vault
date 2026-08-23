"""Quick-open engine — fuzzy file switcher backend (Ctrl+Space).

Pure logic, no GTK.  Designed around *providers* so more result sources can be
added later (e.g. a semantic / vector provider) without touching the palette:

    engine = QuickOpenEngine([FilenameProvider(candidates, recent), ...])
    results = engine.search("eng handbook")

Each provider returns scored :class:`QuickResult` objects for a query; the
engine merges them per file (keeping the best score) and returns a ranked list.
The current provider fuzzy-matches file names, frontmatter aliases and — when
the query contains a ``/`` — the vault-relative path.  Semantic hits are merged
in separately by the palette via ``SemanticIndexManager.query_open`` rather than
through a provider.
"""

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Characters that start a new "word" — a match right after one scores higher.
_WORD_SEPARATORS = frozenset(" -_/.")


@dataclass
class Candidate:
    """One indexed note."""

    path: str        # absolute file path
    name: str        # display name (file stem, no .md)
    folder: str      # containing directory (shown as subtitle)
    mtime: float
    aliases: list = field(default_factory=list)  # frontmatter alias names
    rel: str = ""                                 # path relative to its vault


@dataclass
class QuickResult:
    """A ranked quick-open hit."""

    path: str
    name: str
    folder: str
    score: float
    positions: list = field(default_factory=list)  # matched char indices in matched text
    matched_text: str | None = None  # the alias/path that matched, if not the name
    source: str = "name"             # which provider produced it


# Scoring weights.  A contiguous run and a big gap between matches are the two
# dominant signals: a match right after the previous one scores well, while a
# gap is penalised per skipped character, so a literal substring outranks a
# wide-gap "acronym" match of the same letters.
_BASE = 1.0
_START_BONUS = 6.0     # match at a word start (position 0 or after a separator)
_CAMEL_BONUS = 4.0     # match at a camelCase boundary
_CONSEC_BONUS = 4.0    # match immediately following the previous match
_GAP_PENALTY = 0.7     # per character skipped between two matches
_LEADING_PENALTY = 0.2  # per character before the first match
_LENGTH_TIEBREAK = 0.02  # slight preference for shorter names


def fuzzy_match(query: str, text: str):
    """Fuzzy-match *query* against *text* (case-insensitive subsequence).

    Returns ``(score, positions)`` where *positions* are the indices in *text*
    that matched, or ``None`` if *query* is not a subsequence of *text*.
    Higher scores reward matches at word starts, camelCase boundaries and
    contiguous runs; gaps between matches and a late first match are penalised.
    """
    if not query:
        return (0.0, [])
    q = query.lower()
    t = text.lower()

    positions: list[int] = []
    score = 0.0
    prev = -1  # index of the previous matched char (-1 = none yet)
    start = 0
    for qc in q:
        idx = t.find(qc, start)
        if idx == -1:
            return None
        bonus = _BASE
        if idx == 0 or t[idx - 1] in _WORD_SEPARATORS:
            bonus += _START_BONUS
        elif text[idx].isupper() and text[idx - 1].islower():
            bonus += _CAMEL_BONUS
        if prev == -1:
            score -= _LEADING_PENALTY * idx        # prefer an earlier first match
        elif idx == prev + 1:
            bonus += _CONSEC_BONUS                  # contiguous run
        else:
            score -= _GAP_PENALTY * (idx - prev - 1)  # penalise the gap
        score += bonus
        positions.append(idx)
        prev = idx
        start = idx + 1

    score -= _LENGTH_TIEBREAK * len(text)
    return (score, positions)


def _best_match(query: str, candidate: "Candidate"):
    """Best fuzzy match for *query* over a candidate's name, aliases and path.

    Returns ``(score, positions, matched_text)`` or ``None``.  The name and
    aliases are always tried; the relative path is only tried when the query
    contains a ``/`` (so a plain query stays name/alias-only, not noisy).
    """
    best = None
    for text in (candidate.name, *candidate.aliases):
        hit = fuzzy_match(query, text)
        if hit is not None and (best is None or hit[0] > best[0]):
            best = (hit[0], hit[1], text)
    if "/" in query and candidate.rel:
        hit = fuzzy_match(query, candidate.rel)
        if hit is not None and (best is None or hit[0] > best[0]):
            best = (hit[0], hit[1], candidate.rel)
    return best


def build_candidates(vault_paths) -> list[Candidate]:
    """Walk *vault_paths* and index every ``.md`` file as a :class:`Candidate`.

    Each file's frontmatter is read for ``aliases``/``alias`` so notes are also
    findable under their alternative names.
    """
    candidates: list[Candidate] = []
    for vault in vault_paths:
        if not os.path.isdir(vault):
            continue
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                path = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    # file vanished during the walk → mtime 0 (sorts last)
                    mtime = 0.0
                candidates.append(Candidate(
                    path=path, name=fname[:-3], folder=root, mtime=mtime,
                    aliases=_read_aliases(path),
                    rel=os.path.relpath(path, vault),
                ))
    return candidates


def _read_aliases(path: str) -> list[str]:
    """Read ``aliases``/``alias`` from a note's leading YAML frontmatter."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return []
            lines: list[str] = []
            for line in fh:
                if line.strip() == "---":
                    break
                lines.append(line)
                if len(lines) > 200:  # runaway guard for a missing closing fence
                    return []
    except OSError:
        # unreadable note contributes no aliases
        return []

    raw = None
    try:
        import yaml
        data = yaml.safe_load("".join(lines))
        if isinstance(data, dict):
            raw = data.get("aliases", data.get("alias"))
    except Exception:  # noqa: BLE001 — malformed frontmatter → no aliases for this note
        raw = None
    return _normalize_aliases(raw)


def _normalize_aliases(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(a).strip() for a in raw if str(a).strip()]
    if isinstance(raw, str):
        parts = re.split(r"[,\n]", raw.strip().strip("[]"))
        return [p.strip().strip("\"'") for p in parts if p.strip()]
    return [str(raw).strip()]


class FilenameProvider:
    """Provider that fuzzy-matches the query against file names.

    With an empty query it lists *recent_paths* first (in the given order),
    then the remaining notes by recency (mtime), so Ctrl+Space with no input is a
    recent-files switcher.
    """

    source = "name"

    def __init__(self, candidates, recent_paths=()):
        self._candidates = list(candidates)
        self._recent = list(recent_paths)

    def search(self, query: str, limit: int = 30) -> list[QuickResult]:
        if not query:
            return self._recent_first(limit)

        scored: list[QuickResult] = []
        for c in self._candidates:
            best = _best_match(query, c)
            if best is None:
                continue
            score, positions, text = best
            scored.append(QuickResult(
                path=c.path, name=c.name, folder=c.folder,
                score=score, positions=positions,
                matched_text=None if text == c.name else text,
                source=self.source,
            ))
        scored.sort(key=lambda r: (-r.score, r.name.lower(), r.path))
        return scored[:limit]

    def _recent_first(self, limit: int) -> list[QuickResult]:
        by_path = {c.path: c for c in self._candidates}
        ordered: list[Candidate] = []
        seen: set[str] = set()
        for path in self._recent:
            c = by_path.get(path)
            if c is not None and path not in seen:
                ordered.append(c)
                seen.add(path)
        rest = [c for c in self._candidates if c.path not in seen]
        rest.sort(key=lambda c: -c.mtime)
        ordered.extend(rest)

        # Descending synthetic scores so ordering survives the engine merge.
        n = len(ordered)
        return [
            QuickResult(path=c.path, name=c.name, folder=c.folder,
                        score=float(n - i), source=self.source)
            for i, c in enumerate(ordered[:limit])
        ]


class QuickOpenEngine:
    """Merge several providers' results, keeping the best score per file."""

    def __init__(self, providers):
        self._providers = list(providers)

    def search(self, query: str, limit: int = 30) -> list[QuickResult]:
        best: dict[str, QuickResult] = {}
        for provider in self._providers:
            for r in provider.search(query, limit):
                cur = best.get(r.path)
                if cur is None or r.score > cur.score:
                    best[r.path] = r
        results = list(best.values())
        results.sort(key=lambda r: (-r.score, r.name.lower(), r.path))
        return results[:limit]
