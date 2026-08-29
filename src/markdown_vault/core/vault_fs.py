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

import glob
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

    The **process id** in the name is what keeps that announcement honest. Announcer and
    writer compute the pair independently, so it must stay derivable — but derivable must
    not mean guessable: ``.part`` is a common convention (wget, browsers, sync clients), so
    a plain ``<note>.md.part`` is a name an EXTERNAL tool can produce, and its atomic save
    would be indistinguishable from ours and swallowed as our own, losing a genuine change.
    The pid is known process-wide, so both sides still derive it without threading the pair
    through the writer's signature, while no outside tool can hit it.

    The ``.part`` suffix is load-bearing: VaultMonitor only watches ``.md`` files, so the
    temp file must not end in ``.md`` or it raises an extra created event of its own.
    """
    real = os.path.realpath(path)
    return f"{real}.{os.getpid()}.part", real


def _pid_alive(pid: int) -> bool:
    """Whether *pid* still names a running process. Unknown counts as alive — see
    :func:`_sweep_stale_parts` for why the doubt resolves that way."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # No such process: the answer itself, not a swallowed failure.
        return False
    except PermissionError:
        # Signalling refused means the process EXISTS and belongs to someone else.
        return True
    except (OSError, OverflowError):
        # Cannot tell — report alive so the caller keeps the file (see _sweep_stale_parts).
        # OverflowError is NOT an OSError and is the one that matters: a digit run too large
        # for a C long comes straight from a foreign filename, so without it a file named
        # <note>.md.<many digits>.part made that note unsaveable (the error escaped every
        # handler up to the GTK callback). A malformed pid never reaches here — the caller's
        # ASCII-decimal test rejects it before converting.
        return True
    return True


def _sweep_stale_parts(real: Path) -> None:
    """Delete *real*'s leftover ``.<pid>.part`` files whose owning process is gone.

    The pid in the temp name (see :func:`atomic_save_paths`) costs the self-healing the old
    fixed name had: a crash-orphan used to be truncated and renamed away by the next save of
    that note, whereas a later process has a different pid and never touches it. Without this
    sweep they accumulate per (note, process) — untracked entries in the git panel, and files
    carried into whatever syncs the vault.

    Goes by whether the owner still runs, not by age, because a *live* pid's temp belongs to
    a concurrently running instance saving the same note (duplicate instances do happen), and
    deleting that would corrupt its write. Every doubt therefore keeps the file — including a
    pid that cannot even be parsed or converted, which comes from a foreign filename rather
    than from us: an orphan is recoverable, a destroyed in-flight write is not.

    A pid says nothing about which MACHINE it belongs to, and a vault is often synced. A temp
    file from another machine whose number happens to be dead here is therefore swept while
    that machine may still be writing it. Accepted knowingly: the consequence is not data
    loss but a failed save over there (``os.replace`` hits a missing source), surfaced to
    that user with the note intact. The mirror case — a foreign pid that happens to be live
    here — merely keeps the orphan.
    """
    prefix, suffix = real.name + ".", ".part"
    for candidate in real.parent.glob(glob.escape(real.name) + ".*.part"):
        pid = candidate.name[len(prefix):-len(suffix)]
        # ASCII decimal only. str.isdigit() alone is not that test: '²' passes it and makes
        # int() raise (here, at the call site, where _pid_alive's handler cannot see it), and
        # '٣' passes it AND converts to 3 — reading a foreign file as one of our processes
        # and licensing its deletion on a misreading. Our own pids are ASCII decimal by
        # construction, so anything else is by construction not ours and is kept.
        if not (pid.isascii() and pid.isdigit()) or _pid_alive(int(pid)):
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            # Opportunistic cleanup; a leftover is harmless next to a failed save.
            logger.debug("could not remove stale part file %s: %s", candidate, exc)


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
        _sweep_stale_parts(real)
    except OSError as exc:
        # Cleanup is opportunistic — never let it cost the save it precedes.
        logger.debug("stale part sweep failed for %s: %s", real, exc)
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

    Caveat for a ``.md`` caller: the final rename surfaces to VaultMonitor as a move, which
    it reads as an external change. A caller writing a note must therefore announce it with
    ``VaultMonitor.expect_atomic_save(path)`` **before** the write (and
    ``forget_atomic_save`` if it fails), as the save sites do — NOT with
    ``skip_next_event``, which ignores the next event whatever it is and so is consumed by a
    concurrent external change instead. A non-``.md`` attachment is filtered by the monitor's
    suffix test and needs neither."""
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
