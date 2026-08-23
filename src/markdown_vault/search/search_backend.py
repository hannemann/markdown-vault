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
    semantic: bool = False       # a semantic (vector) hit, not a keyword match


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
            # a non-JSON line in rg's --json stream is not a match; skip it
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
            # a submatch missing its byte offsets can't be span-mapped; skip it
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


def pattern_error(query: str, options: SearchOptions) -> "str | None":
    """A user-facing message if *query* is an invalid pattern under *options*,
    else ``None``. Only regex mode can fail — a literal query is escaped by
    :func:`_compile`. The search bar calls this before dispatching so a broken
    regex surfaces the reason instead of an empty result set the user cannot
    tell apart from 'no matches' (both content search and the filename match
    compile through :func:`_compile`, so one check covers both)."""
    try:
        _compile(query, options)
    except re.error as exc:
        # returned for the caller to surface; a mistyped regex is normal live-search
        # input, not a fault, and this runs on every keystroke — logging would be noise
        return f"Invalid search pattern: {exc}"
    return None


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


# ── Query operators & filters (Phase 3) ─────────────────────────────
#
# A non-regex query may combine several literal terms (AND), quoted phrases,
# ``-exclusions`` and ``key:value`` filters (``tag:``/``path:``/``vault:``).
# In regex mode the query is a single raw pattern and none of this applies.

_FILTER_KEYS = ("tag", "path", "vault")
# One token: an optional leading ``-``, an optional ``key:`` prefix, then either
# a "quoted phrase" or a bare run of non-space chars.
_TOKEN_RE = re.compile(r'-?(?:\w+:)?"[^"]*"|\S+')


@dataclass
class ParsedQuery:
    """A query decomposed into literal operators and field filters."""

    positives: list   # literal terms/phrases that must ALL appear (AND)
    excludes: list    # terms that must NOT appear anywhere in the file
    tags: list        # frontmatter tags that must all be present
    paths: list       # path fragments the file path must contain (all)
    vaults: list      # vault names to restrict the search to (any)


def parse_query(query: str) -> ParsedQuery:
    """Split *query* into positive terms, exclusions and ``key:`` filters."""
    positives: list[str] = []
    excludes: list[str] = []
    tags: list[str] = []
    paths: list[str] = []
    vaults: list[str] = []
    for raw in _TOKEN_RE.findall(query):
        neg = len(raw) > 1 and raw.startswith("-")
        tok = raw[1:] if neg else raw
        key = None
        m = re.match(r"(\w+):(.*)$", tok, re.DOTALL)
        if m and m.group(1).lower() in _FILTER_KEYS:
            key, tok = m.group(1).lower(), m.group(2)
        val = _unquote(tok)
        if not val:
            continue
        if key == "tag":
            tags.append(val)
        elif key == "path":
            paths.append(val)
        elif key == "vault":
            vaults.append(val)
        elif neg:
            excludes.append(val)
        else:
            positives.append(val)
    return ParsedQuery(positives, excludes, tags, paths, vaults)


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _has_operators(p: ParsedQuery) -> bool:
    """True unless the query is a single plain term/phrase (fast path)."""
    return bool(p.excludes or p.tags or p.paths or p.vaults or len(p.positives) != 1)


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

    Non-regex queries support operators and filters (see :func:`parse_query`):
    multiple terms are AND-combined, ``"quoted phrases"`` match literally,
    ``-term`` excludes, and ``tag:``/``path:``/``vault:`` narrow the file set.
    """
    options = options or SearchOptions()
    if not query.strip():
        return []
    existing = [p for p in vault_paths if os.path.isdir(p)]
    if not existing:
        return []

    # Regex mode: the query is one raw pattern; operators/filters don't apply.
    if options.regex:
        return _group_matches(
            search(query, existing, 2000, options),
            _filename_matches(query, existing, options),
            query, options, max_files, max_lines,
        )

    parsed = parse_query(query)
    scoped = _apply_vault_filter(existing, parsed.vaults)
    if not scoped:
        return []

    # Fast path: a single plain term/phrase → one ripgrep pass.
    if not _has_operators(parsed):
        term = parsed.positives[0]
        return _group_matches(
            search(term, scoped, 2000, options),
            _filename_matches(term, scoped, options),
            term, options, max_files, max_lines,
        )

    # Nothing to actually match on (e.g. a half-typed "tag:", an empty phrase,
    # or excludes only) — don't list the whole vault.
    if not (parsed.positives or parsed.tags or parsed.paths or parsed.vaults):
        return []

    return _search_operators(parsed, scoped, options, max_files, max_lines)


def _apply_vault_filter(vault_paths: list[str], names: list[str]) -> list[str]:
    """Restrict *vault_paths* to those whose basename contains any ``vault:`` name."""
    if not names:
        return vault_paths
    wanted = [n.lower() for n in names]
    return [
        v for v in vault_paths
        if any(w in os.path.basename(os.path.normpath(v)).lower() for w in wanted)
    ]


def _group_matches(line_matches, name_paths, query, options, max_files, max_lines):
    """Group flat line matches (plus name-only hits) into ranked FileResults."""
    by_path: dict[str, list[Match]] = {}
    for m in line_matches:
        by_path.setdefault(m.path, []).append(m)
    for path in name_paths:
        by_path.setdefault(path, [])

    results = [
        _build_file_result(
            path, ms, _name_hit(os.path.basename(path), query, options), max_lines
        )
        for path, ms in by_path.items()
    ]
    return _rank(results, max_files)


def _search_operators(parsed, vaults, options, max_files, max_lines):
    """Pure-Python engine for AND / exclusion / tag queries (reads each file once)."""
    pos = [_literal_pattern(t, options) for t in parsed.positives]
    exc = [_literal_pattern(t, options) for t in parsed.excludes]
    path_frags = [p.lower() for p in parsed.paths]

    results: list[FileResult] = []
    for vault in vaults:
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                if path_frags:
                    rel = os.path.relpath(fpath, vault).lower()
                    if not all(f in rel for f in path_frags):
                        continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    logger.debug("Cannot read %s during search", fpath, exc_info=True)
                    continue
                if exc and any(p.search(text) for p in exc):
                    continue
                if parsed.tags and not _has_tags(text, parsed.tags):
                    continue
                fr = _operator_file_result(fpath, fname, text, pos, max_lines)
                if fr is not None:
                    results.append(fr)
    return _rank(results, max_files)


def _operator_file_result(fpath, fname, text, pos_patterns, max_lines):
    """Build a FileResult for one file, enforcing AND across all positives.

    A positive is satisfied if it appears in the content *or* the file name.
    Returns ``None`` when not every positive is satisfied.
    """
    stem = fname[:-3] if fname.endswith(".md") else fname
    name_present = {i for i, pat in enumerate(pos_patterns) if pat.search(stem)}

    present: set[int] = set()
    matches: list[Match] = []
    for i, line in enumerate(text.splitlines(), 1):
        spans: list[tuple[int, int]] = []
        for idx, pat in enumerate(pos_patterns):
            found = [
                (m.start(), m.end()) for m in pat.finditer(line) if m.end() > m.start()
            ]
            if found:
                present.add(idx)
                spans.extend(found)
        if spans:
            spans.sort()
            matches.append(Match(fpath, i, line, spans))

    if pos_patterns and len(present | name_present) < len(pos_patterns):
        return None
    return _build_file_result(fpath, matches, bool(name_present), max_lines)


def _build_file_result(path, ms, name_hit, max_lines) -> FileResult:
    heading_hits = sum(1 for m in ms if m.text.lstrip().startswith("#"))
    title_hit = any(m.text.lstrip().lower().startswith("title:") for m in ms)
    score = (
        (_W_NAME if name_hit else 0.0)
        + (_W_TITLE if title_hit else 0.0)
        + _W_HEADING * min(heading_hits, _HEADING_CAP)
        + _W_BODY * min(len(ms), _BODY_CAP)
    )
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # file vanished between walk and stat → mtime 0 ranks it last
        mtime = 0.0
    return FileResult(
        path=path, score=score, matches=ms[:max_lines], total_matches=len(ms),
        name_hit=name_hit, title_hit=title_hit, heading_hits=heading_hits, mtime=mtime,
    )


def _rank(results: list[FileResult], max_files: int) -> list[FileResult]:
    results.sort(key=lambda r: (-r.score, -r.mtime, len(r.path), r.path))
    return results[:max_files]


def _literal_pattern(term: str, options: SearchOptions) -> "re.Pattern":
    """Compile *term* as a literal (never regex), honouring case/word options."""
    return _compile(term, SearchOptions(
        case_sensitive=options.case_sensitive,
        whole_word=options.whole_word,
        regex=False,
    ))


def _has_tags(text: str, wanted: list[str]) -> bool:
    have = {t.lower() for t in _frontmatter_tags(text)}
    return bool(have) and all(w.lower() in have for w in wanted)


def frontmatter_tags(text: str) -> list[str]:
    """Public: the ``tags`` from a leading YAML frontmatter block (same source of
    truth as the ``tag:`` search filter; used by the graph explorer)."""
    return _frontmatter_tags(text)


def _frontmatter_tags(text: str) -> list[str]:
    """Extract the ``tags`` field from a leading YAML frontmatter block."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    front = text[3:end]
    raw = None
    try:
        import yaml
        data = yaml.safe_load(front)
        if isinstance(data, dict):
            raw = data.get("tags")
    except Exception:  # noqa: BLE001 — malformed frontmatter → regex extractor below
        raw = None
    if raw is None:
        m = re.search(r"(?m)^tags:\s*(.+)$", front)
        if not m:
            return []
        raw = m.group(1)
    return _normalize_tags(raw)


def _normalize_tags(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(t).strip().lstrip("#") for t in raw if str(t).strip()]
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw.strip().strip("[]"))
        return [p.strip().strip("\"'").lstrip("#") for p in parts if p.strip()]
    return [str(raw).strip().lstrip("#")]


def _name_hit(filename: str, query: str, options: SearchOptions) -> bool:
    """Whether *query* matches the file's stem under *options*."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    try:
        return _compile(query, options).search(stem) is not None
    except re.error:
        # an invalid user regex simply doesn't match this file name
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
