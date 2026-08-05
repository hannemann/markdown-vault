"""Backend for vault full-text search.

Uses ripgrep (``rg``) when available — fast, correct, and it reports the exact
byte offsets of every match — and falls back to a pure-Python line scan
otherwise.  Both paths return the same :class:`Match` objects, including the
character spans of the matches within each line so the UI can highlight them.

Search is literal (fixed-string) and case-insensitive, matching the previous
behaviour; regex / case toggles are a later phase.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Match:
    """A single matching line."""

    path: str                      # absolute file path
    line: int                      # 1-based line number
    text: str                      # the matching line (no trailing newline)
    spans: list[tuple[int, int]]   # (start, end) character offsets in ``text``


@dataclass
class SearchOptions:
    """Query modifiers.  Defaults reproduce a literal, case-insensitive search."""

    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False


@dataclass
class FileResult:
    """All matches for one file, ranked for display."""

    path: str
    score: float
    matches: list                # capped list[Match] to show
    total_matches: int           # actual count (matches may be truncated)
    name_hit: bool               # the query matches the file name
    title_hit: bool              # the query matches the frontmatter title
    heading_hits: int            # number of matching heading lines
    mtime: float


def search(
    query: str,
    vault_paths: list[str],
    max_results: int = 50,
    options: "SearchOptions | None" = None,
) -> list[Match]:
    """Return up to *max_results* matches for *query* under *options*."""
    options = options or SearchOptions()
    if not query or not vault_paths:
        return []
    existing = [p for p in vault_paths if os.path.isdir(p)]
    if not existing:
        return []
    if shutil.which("rg"):
        try:
            return _search_ripgrep(query, existing, max_results, options)
        except Exception:
            logger.debug("ripgrep search failed; using Python fallback", exc_info=True)
    return _search_python(query, existing, max_results, options)


# ── ripgrep ─────────────────────────────────────────────────────────


def _search_ripgrep(
    query: str, vault_paths: list[str], max_results: int, options: SearchOptions,
) -> list[Match]:
    cmd = [
        "rg", "--json",
        "--no-ignore",       # search every .md, don't obey .gitignore/.ignore
        "--glob", "*.md",
        "--case-sensitive" if options.case_sensitive else "--ignore-case",
    ]
    if options.whole_word:
        cmd.append("--word-regexp")
    if not options.regex:
        cmd.append("--fixed-strings")
    cmd += ["-e", query, "--", *vault_paths]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # rg exits 1 when there are simply no matches — not an error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"rg failed ({proc.returncode}): {proc.stderr.strip()}")

    results: list[Match] = []
    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj["data"]
        path = data.get("path", {}).get("text")
        if path is None:
            continue  # non-UTF-8 path reported as bytes — skip
        text = data.get("lines", {}).get("text", "").rstrip("\n")
        spans = _submatch_spans(text, data.get("submatches", []))
        results.append(Match(path, data.get("line_number", 0), text, spans))
        if len(results) >= max_results:
            break
    return results


def _submatch_spans(text: str, submatches: list) -> list[tuple[int, int]]:
    """Convert ripgrep byte offsets into character offsets within *text*."""
    b = text.encode("utf-8")
    spans: list[tuple[int, int]] = []
    for sm in submatches:
        try:
            cs = len(b[: sm["start"]].decode("utf-8", "ignore"))
            ce = len(b[: sm["end"]].decode("utf-8", "ignore"))
        except (KeyError, TypeError):
            continue
        if ce > cs:
            spans.append((cs, ce))
    return spans


# ── Python fallback ─────────────────────────────────────────────────


def _search_python(
    query: str, vault_paths: list[str], max_results: int, options: SearchOptions,
) -> list[Match]:
    try:
        pattern = _compile(query, options)
    except re.error:
        logger.debug("Invalid search pattern: %r", query, exc_info=True)
        return []

    results: list[Match] = []
    for vault in vault_paths:
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if not d.startswith(".")]  # skip dotdirs
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for i, line in enumerate(fh, 1):
                            text = line.rstrip("\n")
                            spans = [
                                (m.start(), m.end())
                                for m in pattern.finditer(text)
                                if m.end() > m.start()  # ignore zero-width
                            ]
                            if spans:
                                results.append(Match(fpath, i, text, spans))
                                if len(results) >= max_results:
                                    return results
                except OSError:
                    logger.debug("Cannot read %s during search", fpath, exc_info=True)
                    continue
    return results


def _compile(query: str, options: SearchOptions) -> "re.Pattern":
    """Build the regex mirroring the ripgrep flags for the Python fallback."""
    pattern = query if options.regex else re.escape(query)
    if options.whole_word:
        pattern = r"\b(?:" + pattern + r")\b"
    flags = 0 if options.case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)


# ── Grouped + ranked results (Phase 2) ──────────────────────────────

# Relevance weights: a name/title hit outranks headings, which outrank body
# matches; body-match and heading counts saturate so a single very repetitive
# file cannot dominate.
_W_NAME = 6.0
_W_TITLE = 4.0
_W_HEADING = 3.0
_W_BODY = 1.0
_HEADING_CAP = 3
_BODY_CAP = 5


def search_grouped(
    query: str,
    vault_paths: list[str],
    options: "SearchOptions | None" = None,
    max_files: int = 50,
    max_lines: int = 20,
) -> list[FileResult]:
    """Search, group line matches per file, and rank files by relevance.

    Files are ranked by a weighted score (name/title > heading > body, with
    saturating counts), then by recency (mtime) and shorter path as tiebreaks.
    A file whose *name* matches is included even with no content match.
    """
    options = options or SearchOptions()
    if not query:
        return []
    existing = [p for p in vault_paths if os.path.isdir(p)]
    if not existing:
        return []

    line_matches = search(query, existing, max_results=2000, options=options)

    by_path: dict[str, list[Match]] = {}
    for m in line_matches:
        by_path.setdefault(m.path, []).append(m)

    # Files that match by name only (no content hit) still belong in results.
    for path in _filename_matches(query, existing, options):
        by_path.setdefault(path, [])

    results: list[FileResult] = []
    for path, ms in by_path.items():
        name_hit = _name_hit(os.path.basename(path), query, options)
        heading_hits = sum(1 for m in ms if m.text.lstrip().startswith("#"))
        title_hit = any(
            m.text.lstrip().lower().startswith("title:") for m in ms
        )
        score = (
            (_W_NAME if name_hit else 0.0)
            + (_W_TITLE if title_hit else 0.0)
            + _W_HEADING * min(heading_hits, _HEADING_CAP)
            + _W_BODY * min(len(ms), _BODY_CAP)
        )
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        results.append(FileResult(
            path=path, score=score, matches=ms[:max_lines],
            total_matches=len(ms), name_hit=name_hit, title_hit=title_hit,
            heading_hits=heading_hits, mtime=mtime,
        ))

    results.sort(key=lambda r: (-r.score, -r.mtime, len(r.path), r.path))
    return results[:max_files]


def _name_hit(filename: str, query: str, options: SearchOptions) -> bool:
    """Whether *query* matches the file's stem under *options*."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    try:
        return _compile(query, options).search(stem) is not None
    except re.error:
        return False


def _filename_matches(query, vault_paths, options) -> list[str]:
    matches: list[str] = []
    for vault in vault_paths:
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.endswith(".md") and _name_hit(fname, query, options):
                    matches.append(os.path.join(root, fname))
    return matches
