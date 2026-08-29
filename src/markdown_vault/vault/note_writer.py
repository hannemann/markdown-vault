"""Shared note-writing helpers for the importers.

The generic parts of turning imported content into a vault file — a safe
kebab-case filename stem and a collision-free target path — live here so that
``web_import`` and ``document_import`` both depend on this small module rather
than on each other.
"""

import logging
import re
from pathlib import Path

from markdown_vault.core import vault_fs

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
    """A currently free ``<vault_dir>/<stem><suffix>``. When that name is taken a numeric
    suffix is appended (``-2``, ``-3`` …). The directory is not created here, and neither is
    the file: the name is only TESTED, so it can be taken again before the caller writes.
    A caller that does work in between wants :func:`reserve_path` instead."""
    vault_dir = Path(vault_dir)
    target = vault_dir / f"{stem}{suffix}"
    n = 2
    while target.exists():
        target = vault_dir / f"{stem}-{n}{suffix}"
        n += 1
    return target


#: Bound for :func:`reserve_path`'s search. It CREATES in a loop, so a pathological case
#: (something taking every candidate) must terminate rather than hang the calling thread.
_RESERVE_ATTEMPTS = 1000


def reserve_path(vault_dir: str | Path, stem: str, suffix: str = ".md") -> Path:
    """Exclusively create — and return — a free ``<vault_dir>/<stem><suffix>``.

    The counterpart to :func:`unique_path` for a caller that does work between choosing the
    name and writing the content: an importer stores images in that window, and the images'
    links are rewritten to THIS name, so the name has to be final and owned before that runs.
    Testing a name and writing later would both truncate whatever appeared meanwhile and,
    if the caller then picked another name, leave the note pointing at the first one's
    attachment folder.

    The file is created empty; the caller fills it with a plain (non-exclusive) write, and
    removes it if the fill fails. Raises ``FileExistsError`` when no free name is found.

    The residue this design accepts: only *exceptions* can be cleaned up. A crash or a kill
    between the reservation and the fill leaves the empty note behind — and unlike a
    ``.part`` file, which the monitor's suffix filter drops, an empty ``.md`` is visible in
    the tree, gets indexed, and syncs. The window is the caller's work in between, which for
    an import is storing the images. Inherent to reserving first; the alternative was losing
    the race instead.
    """
    vault_dir = Path(vault_dir)
    for n in range(1, _RESERVE_ATTEMPTS + 1):
        name = f"{stem}{suffix}" if n == 1 else f"{stem}-{n}{suffix}"
        target = vault_dir / name
        try:
            vault_fs.write_text(str(target), "", exclusive=True)
        except FileExistsError:
            continue    # that name is taken — the loop's own outcome, try the next

        return target
    raise FileExistsError(f"no free name for {stem!r} after {_RESERVE_ATTEMPTS} attempts")
