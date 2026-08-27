"""The single owner of the raw filesystem write for debug dumps.

Six components — the file, backlink and vault-tree indices, the tab bar, the sidebar and
the preview — each build their own dump *data*, but the actual disk write lives here, so
the raw ``write_text`` sits in one place instead of scattered across six modules (this is
step one of consolidating filesystem access behind a chokepoint).

A debug dump must never take down the app: a failure to serialise or write is logged and
swallowed, never raised. That is the whole contract — the caller passes data and a human
label and does not have to guard the call.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def dump_json(path: str | Path, data, label: str) -> None:
    """Write *data* as indented UTF-8 JSON to *path* (overwrites).

    Swallows and logs both a write failure (``OSError``) and a serialisation failure
    (``TypeError`` for a non-serialisable value, ``ValueError`` for a circular reference)
    — a debug dump must not crash its caller.
    """
    try:
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError):
        logger.warning("Failed to write debug dump %s to %s", label, path, exc_info=True)


def dump_text(path: str | Path, text: str, label: str) -> None:
    """Write *text* verbatim as UTF-8 to *path* (overwrites). Swallows and logs an
    ``OSError`` — a debug dump must not crash its caller."""
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError:
        logger.warning("Failed to write debug dump %s to %s", label, path, exc_info=True)
