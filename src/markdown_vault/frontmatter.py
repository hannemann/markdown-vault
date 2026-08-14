"""Shared YAML-frontmatter helpers for the OKF-derived fields (lifecycle status,
title, description). One small parser the new features share, so they don't each
grow their own — see the per-feature parsers in ``sidebar``/``preview``/etc. that
predate this and are left untouched.
"""

import datetime
import os
import re

import yaml

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
        return {}
    return data if isinstance(data, dict) else {}


def status(meta: dict) -> str:
    """Lifecycle status: ``'draft'`` | ``'stable'`` | ``'deprecated'``. Absent or
    unrecognised → ``'stable'``."""
    s = str(meta.get("status", "")).strip().lower()
    return s if s in STATUSES else "stable"


def is_stale(meta: dict, today: datetime.date | None = None) -> bool:
    """Whether the note is stale: its ``stale_after`` date (``YYYY-MM-DD``) is
    today or in the past. Missing or unparseable → not stale."""
    raw = meta.get("stale_after")
    if raw is None:
        return False
    if isinstance(raw, datetime.datetime):
        raw = raw.date()
    if not isinstance(raw, datetime.date):
        try:
            raw = datetime.date.fromisoformat(str(raw).strip())
        except (ValueError, TypeError):
            return False
    return (today or datetime.date.today()) >= raw


_STATUS_CACHE: dict[str, tuple] = {}


def status_of(path: str) -> str:
    """Cached lifecycle status of a note *file* (keyed by mtime): reads only the
    file head. ``'stable'`` if unreadable. Shared by the vault tree and the search
    surfaces so a note is read once for the "hide deprecated" filter."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return "stable"
    cached = _STATUS_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    head = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        pass
    result = status(parse(head))
    _STATUS_CACHE[path] = (mtime, result)
    return result


def title(meta: dict) -> str:
    """Frontmatter ``title`` (display name), or ``''`` if absent."""
    value = meta.get("title")
    return str(value).strip() if value not in (None, "") else ""


def description(meta: dict) -> str:
    """Frontmatter ``description`` (one-line summary), or ``''`` if absent."""
    value = meta.get("description")
    return str(value).strip() if value not in (None, "") else ""
