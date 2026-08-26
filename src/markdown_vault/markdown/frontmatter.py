"""Shared YAML-frontmatter helpers for the OKF-derived fields (lifecycle status,
title, description). One small parser the new features share, so they don't each
grow their own — see the per-feature parsers in ``sidebar``/``preview``/etc. that
predate this and are left untouched.
"""

import datetime
import os
import re

import yaml

from markdown_vault.markdown.md_text import strip_markdown

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.DOTALL)

# OKF lifecycle statuses. Absent or unrecognised → 'stable' (the spec default;
# consumers MUST tolerate unknown values rather than reject the note).
STATUSES = ("draft", "stable", "deprecated")


def parse(text: str) -> dict:
    """The leading YAML frontmatter of *text* as a dict, or ``{}`` if there is
    none or it is invalid. Safe to call with only the head of a file — the block
    is matched at the very start."""
    if not text:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        # invalid frontmatter → treat as none, per the documented {} contract
        return {}
    return data if isinstance(data, dict) else {}


def status(meta: dict) -> str:
    """Lifecycle status: ``'draft'`` | ``'stable'`` | ``'deprecated'``. Absent or
    unrecognised → ``'stable'``."""
    s = str(meta.get("status", "")).strip().lower()
    return s if s in STATUSES else "stable"


def _stale_after(meta: dict) -> datetime.date | None:
    """The ``stale_after`` date (``YYYY-MM-DD``) as a ``date``, or ``None`` if
    absent/unparseable. ``datetime`` is tested before ``date`` because the former
    subclasses the latter."""
    raw = meta.get("stale_after")
    if raw is None:
        return None
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except (ValueError, TypeError):
        # a value that won't parse as a date → no date (str() coerces first, so
        # ValueError is the live path; TypeError is a defensive belt)
        return None


def is_stale(meta: dict, today: datetime.date | None = None) -> bool:
    """Whether the note is stale: its ``stale_after`` date is today or in the
    past. Missing or unparseable → not stale."""
    date = _stale_after(meta)
    return date is not None and (today or datetime.date.today()) >= date


# path -> (mtime, status, stale_after_date_or_None). The date is cached (it only
# changes when the file does); staleness is recomputed against *today* on every
# call, so a note crossing its stale_after date is picked up without a re-read.
_LIFECYCLE_CACHE: dict[str, tuple] = {}


def lifecycle_of(path: str, today: datetime.date | None = None) -> tuple:
    """``(status, stale)`` for a note *file*, reading only the file head and
    caching the parse by mtime. ``('stable', False)`` if unreadable. The single
    reader shared by the vault tree and the search surfaces, so a note is read
    once for the "hide deprecated" filter and the lifecycle badges."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # unreadable note → the neutral default (stable, not stale) lifecycle
        return ("stable", False)
    cached = _LIFECYCLE_CACHE.get(path)
    if cached is None or cached[0] != mtime:
        head = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(8192)
        except OSError:
            # unreadable head → empty parse below yields the stable default
            pass
        meta = parse(head)
        cached = (mtime, status(meta), _stale_after(meta))
        _LIFECYCLE_CACHE[path] = cached
    _mtime, st, date = cached
    stale = date is not None and (today or datetime.date.today()) >= date
    return (st, stale)


def status_of(path: str) -> str:
    """Cached lifecycle status of a note *file* — see :func:`lifecycle_of`."""
    return lifecycle_of(path)[0]


def invalidate(path: str | None = None) -> None:
    """Drop the cached lifecycle for *path* (or the whole cache when ``None``), so
    the next read re-parses. Call after a note's frontmatter may have changed."""
    if path is None:
        _LIFECYCLE_CACHE.clear()
    else:
        _LIFECYCLE_CACHE.pop(path, None)


def _stem(path: str) -> str:
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".md") else base


def tip_of(path: str, preview_chars: int = 200) -> tuple:
    """``(title, description)`` for a graph-node hover panel / card, reading only the
    file head. Frontmatter ``title``/``description`` win; otherwise the title falls
    back to the filename stem and the description to the body preview (whitespace
    collapsed). Either way the description is bounded to ``preview_chars`` so the
    fixed-size hover panel and cards cannot overflow. Unreadable → ``(stem, '')``."""
    head = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        # unreadable file → fall back to the filename stem for the tooltip
        return (_stem(path), "")
    meta = parse(head)
    t = title(meta) or _stem(path)
    d = description(meta)
    if d:
        d = d[:preview_chars]
    else:
        m = _FRONTMATTER_RE.match(head)
        body = head[m.end():] if m else head
        body = strip_markdown(body)
        d = re.sub(r"\s+", " ", body).strip()[:preview_chars].strip()
    return (t, d)


def title(meta: dict) -> str:
    """Frontmatter ``title`` (display name), or ``''`` if absent."""
    value = meta.get("title")
    return str(value).strip() if value not in (None, "") else ""


def description(meta: dict) -> str:
    """Frontmatter ``description`` (one-line summary), or ``''`` if absent."""
    value = meta.get("description")
    return str(value).strip() if value not in (None, "") else ""
