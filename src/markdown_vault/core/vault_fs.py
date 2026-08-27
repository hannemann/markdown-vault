"""VaultFS — the guarded chokepoint for filesystem writes INSIDE a vault: note rewrites,
attachments, renames, deletes.

The mirror of StateFS. Where StateFS asks "is this a state dir and NOT a vault?", VaultFS
asks the single positive question "does this stay inside a configured vault?" — and refuses
anything that would escape, so a crafted symlink in a vault cannot redirect a write out of
it (the symlink-escape ticket's job).

The two path_guard modes carry the distinction the escape hinges on:

- a **write**, ``mkdir``, or the **destination** of a rename/move *follows* the link
  (``follow_last=True``): it must land where the link points, so the link is resolved.
- a **delete**/``rmdir``/``rmtree``, or the **source** of a rename/move *acts on the link*
  (``follow_last=False``): deleting a symlink removes the link, not its target, so only the
  parent is resolved and the last component kept literal.

A rename/move is therefore checked on **both** ends, each in its own mode.

Writes are direct, not atomic: the vault tree is watched by VaultMonitor, and a ``.part``
appearing and being renamed would surface as spurious file events. All raw filesystem
mutation for vault writes lives here; the AST guard forbids it anywhere else.
"""

import logging
import os
import shutil
from pathlib import Path

from markdown_vault.core import config
from markdown_vault.core.path_guard import within_any

logger = logging.getLogger(__name__)


class VaultWriteError(Exception):
    """Base for a VaultFS containment refusal."""


class OutsideVault(VaultWriteError):
    """The target is outside every configured vault — a write there would escape."""


def _vault_roots() -> list[str]:
    return [entry["path"] for entry in config.load_vaults()]


def _guard(target, *, follow_last: bool) -> None:
    """Refuse a target that resolves outside every vault, in the given mode."""
    if not within_any(_vault_roots(), target, follow_last=follow_last):
        raise OutsideVault(f"{target!s} is outside every vault")


def write_text(path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to a vault file (guarded)."""
    _guard(path, follow_last=True)
    Path(path).write_text(text, encoding=encoding)


def write_bytes(path, data: bytes) -> None:
    """Write *data* to a vault file (guarded)."""
    _guard(path, follow_last=True)
    Path(path).write_bytes(data)


def mkdir(path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a directory inside a vault (guarded)."""
    _guard(path, follow_last=True)
    Path(path).mkdir(parents=parents, exist_ok=exist_ok)


def unlink(path, *, missing_ok: bool = False) -> None:
    """Remove a file (or symlink) inside a vault (guarded, delete mode)."""
    _guard(path, follow_last=False)
    Path(path).unlink(missing_ok=missing_ok)


def rmdir(path) -> None:
    """Remove an empty directory inside a vault (guarded, delete mode)."""
    _guard(path, follow_last=False)
    Path(path).rmdir()


def rmtree(path) -> None:
    """Recursively remove a directory tree inside a vault (guarded, delete mode)."""
    _guard(path, follow_last=False)
    shutil.rmtree(path)


def rename(src, dst) -> None:
    """Rename *src* to *dst*, both inside a vault. The source acts on the link (delete
    mode), the destination follows it (write mode); both must stay in the vault."""
    _guard(src, follow_last=False)
    _guard(dst, follow_last=True)
    os.rename(src, dst)


def move(src, dst) -> None:
    """Move *src* to *dst*, both inside a vault (``shutil.move`` semantics; *dst* may be a
    directory). Source in delete mode, destination in write mode."""
    _guard(src, follow_last=False)
    _guard(dst, follow_last=True)
    shutil.move(src, dst)
