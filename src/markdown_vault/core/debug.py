"""The single place that formats debug dumps and hands them to StateFS.

Six components — the file, backlink and vault-tree indices, the tab bar, the sidebar and
the preview — each build their own dump *data*, but the disk write is delegated here, and
from here through :mod:`markdown_vault.core.state_fs` (dumps land under the state dir), so
no component touches raw filesystem mutation itself.

A debug dump must not take the app down. :func:`dump_json` swallows and logs a write or
containment failure (``OSError`` / :class:`state_fs.StateWriteError`) and a serialisation
failure (``TypeError`` / ``ValueError``); :func:`dump_text` swallows the write/containment
failure but lets a non-str argument raise — a programming error is better loud. StateFS
writes atomically (``.part`` then rename), so a crash mid-dump leaves the previous dump
intact rather than a truncated one; a stale-but-valid dump is why deleting the dumps is its
own step before a debug run.
"""

import json
import logging
from pathlib import Path

from markdown_vault.core import state_fs

logger = logging.getLogger(__name__)


def dump_json(path: str | Path, data, label: str) -> None:
    """Write *data* as indented UTF-8 JSON to *path* via StateFS (guarded, atomic).

    Swallows and logs a write/containment failure (``OSError`` / ``StateWriteError``) and a
    serialisation failure (``TypeError`` for a non-serialisable value, ``ValueError`` for a
    circular reference) — a debug dump must not crash its caller.
    """
    try:
        state_fs.write_text_atomic(path,json.dumps(data, indent=2, ensure_ascii=False))
    except (OSError, ValueError, TypeError, state_fs.StateWriteError):
        logger.warning("Failed to write debug dump %s to %s", label, path, exc_info=True)


def dump_text(path: str | Path, text: str, label: str) -> None:
    """Write *text* verbatim to *path* via StateFS (guarded, atomic). Swallows and logs a
    write/containment failure; a non-str *text* is a programming error and is left to raise."""
    try:
        state_fs.write_text_atomic(path,text)
    except (OSError, state_fs.StateWriteError):
        logger.warning("Failed to write debug dump %s to %s", label, path, exc_info=True)
