"""Shared note-writing helpers for the importers.

The generic parts of turning imported content into a vault file — a safe
kebab-case filename stem and a collision-free target path — live here so that
``web_import`` and ``document_import`` both depend on this small module rather
than on each other.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_DASH = re.compile(r"[\s_-]+")


def slug(text: str, max_len: int = 60, fallback: str = "untitled") -> str:
    """A safe kebab-case filename stem (no extension) from *text*. Non-word
    characters are dropped, whitespace/underscores collapse to single dashes, and
    the result is trimmed to *max_len*. Falls back to *fallback* when nothing
    usable remains (e.g. a title of only punctuation)."""
    text = _SLUG_STRIP.sub("", (text or "").lower())
    text = _SLUG_DASH.sub("-", text).strip("-")
    return text[:max_len].strip("-") or fallback


def unique_path(vault_dir: str | Path, stem: str, suffix: str = ".md") -> Path:
    """A non-colliding ``<vault_dir>/<stem><suffix>``. When that name is taken a
    numeric suffix is appended (``-2``, ``-3`` …) so an import never overwrites an
    existing note. The directory is not created here."""
    vault_dir = Path(vault_dir)
    target = vault_dir / f"{stem}{suffix}"
    n = 2
    while target.exists():
        target = vault_dir / f"{stem}-{n}{suffix}"
        n += 1
    return target
