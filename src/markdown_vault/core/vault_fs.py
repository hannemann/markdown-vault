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

Writes are direct, not atomic — a deliberate, still-open trade. A tmp-then-rename would
NOT confuse VaultMonitor by its ``.part`` (the monitor filters by suffix, so a ``.part`` is
already invisible); the real cost is that the rename arrives as a RENAMED/MOVED_IN event
rather than a change, and the vault tree, backlink index and file index treat those
differently — every note save would look like a rename. Against that, ``Path.write_text``
truncates first, so a crash mid-save leaves an EMPTY note, losing all prior content, not
just the last edit. Which way the frequent editor-save path should go is a product
decision (tracked in the VaultFS ticket); a separate atomic writer for the batch callers
may land later.

All raw filesystem mutation for vault writes lives here; the AST guard forbids it anywhere
else.
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


def atomic_save_paths(path) -> tuple[str, str]:
    """The ``(part, target)`` pair :func:`write_text_atomic` will rename, for *path*.

    The single source of that naming. VaultMonitor announces this exact pair before an
    atomic save so it can recognise the resulting rename as the app's own rather than an
    external change; a second guess at the call site would drift from the writer — notably
    on a symlinked note, where the writer resolves the leaf (see :func:`_atomic_bytes`) —
    and the announcement would silently never match.

    The ``.part`` suffix is load-bearing: VaultMonitor only watches ``.md`` files, so the
    temp file must not end in ``.md`` or it raises an extra created event of its own.
    """
    real = os.path.realpath(path)
    return real + ".part", real


def _atomic_bytes(target: Path, data: bytes) -> None:
    # Write at the RESOLVED leaf. os.replace renames onto the name, it does not write
    # through a symlink there — so to preserve a symlinked note's semantics (update the
    # target, keep the link), as the direct writer does, we must target the real file. The
    # guard already ran on the ORIGINAL path (follow_last=True), so the resolved leaf is
    # known to be in the vault; a link pointing out was refused before we got here.
    tmp_str, real_str = atomic_save_paths(target)
    real, tmp = Path(real_str), Path(tmp_str)
    real.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, real)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup; the caller re-raises the real failure
        raise


def write_text_atomic(path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *text* to a vault file (guarded): a ``.part`` renamed into place, so
    a crash leaves the PREVIOUS content intact rather than the empty file a direct
    :func:`write_text` would leave (it truncates before writing).

    For the batch callers (backlink rewrites, importers, attachments), where a truncated
    file would be worst and the write is infrequent. Caveat for the migration: on a TRACKED
    (open tab / indexed) ``.md`` file the final rename surfaces to VaultMonitor as a moved
    event, which it reads as an external change (a false reload banner). A caller that can
    hit such a file must ``skip_next_event`` at the call site, as ``FileOps`` does; a
    non-``.md`` attachment is filtered by the monitor's suffix test and is free of this."""
    _guard(path, follow_last=True)
    _atomic_bytes(Path(path), text.encode(encoding))


def write_bytes_atomic(path, data: bytes) -> None:
    """Atomically write *data* to a vault file (guarded). See :func:`write_text_atomic`;
    a non-``.md`` attachment carries no VaultMonitor interaction."""
    _guard(path, follow_last=True)
    _atomic_bytes(Path(path), data)


def mkdir(path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a directory inside a vault (guarded)."""
    _guard(path, follow_last=True)
    Path(path).mkdir(parents=parents, exist_ok=exist_ok)


def touch(path) -> None:
    """Create an empty file at *path* inside a vault, or bump its mtime if it exists
    (guarded). Distinct from ``write_text(path, "")`` on purpose: touch does NOT truncate
    an existing file, which a create-a-new-note path with no existence check relies on.
    Follows the link (a touch through a symlink lands on the target, like a direct write)."""
    _guard(path, follow_last=True)
    Path(path).touch()


def unlink(path, *, missing_ok: bool = False) -> None:
    """Remove a file (or symlink) inside a vault (guarded, delete mode)."""
    _guard(path, follow_last=False)
    Path(path).unlink(missing_ok=missing_ok)


def rmdir(path) -> None:
    """Remove an empty directory inside a vault (guarded, delete mode)."""
    _guard(path, follow_last=False)
    Path(path).rmdir()


def rmtree(path, *, ignore_errors: bool = False) -> None:
    """Recursively remove a directory tree inside a vault (guarded, delete mode). *ignore_errors*
    is forwarded to ``shutil.rmtree``: when set, the walk clears everything it can and leaves
    only what it cannot delete, instead of aborting on the first error and leaving the rest of
    the tree behind — a best-effort caller (attachments cleanup) depends on that. The guard runs
    first on the top path, so the flag only affects error handling inside the walk, never
    containment."""
    _guard(path, follow_last=False)
    shutil.rmtree(path, ignore_errors=ignore_errors)


def rename(src, dst) -> None:
    """Rename *src* to *dst*, both inside a vault. Both ends act on the link, not through
    it: ``os.rename`` REPLACES a symlink at the destination rather than following it, so
    *dst* is checked in delete mode too (follow_last=False) — otherwise a rename that would
    stay inside by replacing an outward link is refused for no reason."""
    _guard(src, follow_last=False)
    _guard(dst, follow_last=False)
    os.rename(src, dst)


def move(src, dst) -> None:
    """Move *src* to *dst*, both inside a vault (``shutil.move`` semantics). Source acts on
    the link (delete mode); the destination follows it (write mode), because ``shutil.move``
    may fall back to ``copy2`` across filesystems, which writes THROUGH a destination link.

    When *dst* is an existing directory the file lands at ``dst/basename(src)``, so the
    guard checks that final resting path, not just the directory — otherwise a symlink
    planted at that path could redirect the write outside the vault.

    Note: a failed ``shutil.move`` can leave a partial copy at the final name (not a
    ``.part``), which looks complete and would be indexed as a note. That is shutil's
    contract; VaultFS does not roll it back."""
    target = Path(dst)
    if target.is_dir():                        # same test shutil.move uses (os.path.isdir)
        target = target / Path(src).name
    _guard(src, follow_last=False)
    _guard(target, follow_last=True)
    shutil.move(src, dst)
