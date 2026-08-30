"""StateFS — the guarded chokepoint for filesystem writes that belong OUTSIDE any vault:
settings, session, semantic caches, downloaded models, debug dumps, logs.

The mirror of VaultFS. Where VaultFS asks "does this stay inside the vault?", StateFS asks
the *positive* twin: does the target land under an allowed state root **and** under no
vault? The negative clause is load-bearing — a model folder the user pointed inside a vault
(a folder picker makes that a click) must not receive gigabyte writes ungoverned; that is
VaultFS's job, not StateFS's.

Allowed roots are read **per call**, not at import: the four XDG dirs are constants, but a
model folder can be repointed at runtime. A hand-edited, unusable model root ("", relative)
is logged and dropped rather than allowed to break every state write.

All raw filesystem mutation for state writes lives here (and in VaultFS for vault writes);
the AST guard forbids it anywhere else. Writes are atomic — a ``.part`` file renamed into
place — so a crash or a rejected download never leaves a half-written file looking complete.
"""

import logging
import os
from pathlib import Path

from markdown_vault.core import config
from markdown_vault.core import paths
from markdown_vault.core.path_guard import InvalidRoot, checked_root, within_any

logger = logging.getLogger(__name__)


class StateWriteError(Exception):
    """Base for a StateFS containment refusal."""


class OutsideAllowedRoots(StateWriteError):
    """The target is under none of the allowed state roots."""


class InsideVault(StateWriteError):
    """The target is under a configured vault — a vault write must go through VaultFS."""


class RejectedContent(StateWriteError):
    """A streamed download failed the caller's ``validate()`` (carries the reason)."""


def _checked(candidates) -> list[str]:
    """Validate roots, logging and dropping any unusable one rather than failing every
    write: a "" / relative / filesystem-root value simply grants no containment."""
    roots = []
    for candidate in candidates:
        try:
            roots.append(checked_root(candidate))
        except InvalidRoot:
            logger.warning("StateFS: ignoring unusable root %r", candidate, exc_info=True)
    return roots


def _state_roots() -> list[str]:
    """The four XDG dirs — the roots EVERY state op is confined to. Deliberately without
    the model folders: those come from a folder chooser and can point anywhere (a home
    directory, say), so admitting them for write_text/mkdir/unlink would widen the
    chokepoint far past "state" for ops that have nothing to do with models."""
    return _checked([paths.CONFIG_DIR, paths.STATE_DIR, paths.CACHE_DIR, paths.DATA_DIR])


def _model_roots() -> list[str]:
    """The configured model folders (Ask GGUF, semantic ONNX), read per call. Allowed for
    the download alone (:func:`write_stream`), so a user-picked folder widens containment
    only where a model is actually written."""
    s = config.settings()
    return _checked([
        config.ask_models_dir(s),
        Path(config.get_setting(s, "semantic.onnx.dir") or (paths.DATA_DIR / "onnx")),
    ])


def _vault_roots() -> list[str]:
    return [entry["path"] for entry in config.load_vaults()]


def _guard(target, *, follow_last: bool, extra_roots=()) -> None:
    """Refuse a target that is under no allowed root, or under a vault.

    The two clauses run in DIFFERENT modes on purpose. The **positive** clause judges where
    the file SITS (``follow_last=False``): StateFS guards against our own future code, not a
    crafted path (the ticket says so), so a state file the user symlinked out of the state
    dir — ``settings.yaml`` into a dotfiles repo — is a deliberate instruction, not an
    escape; resolving and refusing it would break saving for that user without protecting
    anyone. The **vault** clause resolves as the operation does (*follow_last*): a write
    follows the link, so it must not land IN a vault (the one protection that matters here);
    a delete acts on the link and leaves the target. *extra_roots* widens the positive clause
    for a single op (the download passes the model folders)."""
    if not within_any(_state_roots() + list(extra_roots), target, follow_last=False):
        raise OutsideAllowedRoots(f"{target!s} is under no allowed state root")
    if within_any(_vault_roots(), target, follow_last=follow_last):
        raise InsideVault(f"{target!s} is inside a vault — use VaultFS")


def _discard(tmp: Path) -> None:
    """Best-effort removal of a partial; the real failure is re-raised by the caller."""
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass  # best-effort cleanup; the caller re-raises the real failure


def _atomic_bytes(target: Path, data: bytes) -> None:
    # Write at the RESOLVED leaf so a user-symlinked state file is written THROUGH it (the
    # link survives), matching VaultFS. os.replace renames onto the name, not through the
    # link, so without this a symlinked settings.yaml would be replaced by a plain file.
    # The guard already validated the original path (the positive clause on where it sits,
    # the vault clause on where it resolves).
    real = Path(os.path.realpath(target))
    tmp = real.with_name(real.name + ".part")
    real.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, real)
    except BaseException:
        _discard(tmp)
        raise


def write_text_atomic(path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *text* to *path* (guarded). tmp-then-rename, so a crash leaves the
    previous file intact rather than a truncated one.

    The ``_atomic`` suffix is not decoration: :func:`vault_fs.write_text` is NOT atomic (its
    atomic twin is a separate function), so the same bare name would carry the opposite
    guarantee in the sibling facade — and which of the two a caller gets is load-bearing
    here, not cosmetic. Every write in THIS facade is atomic; the suffix says so rather than
    leaving it to be inferred from the module you happen to be in.
    """
    _guard(path, follow_last=True)
    _atomic_bytes(Path(path), text.encode(encoding))


def write_bytes_atomic(path, data: bytes) -> None:
    """Atomically write *data* to *path* (guarded). See :func:`write_text_atomic` on the
    suffix — the sibling facade's bare ``write_bytes`` is not atomic."""
    _guard(path, follow_last=True)
    _atomic_bytes(Path(path), data)


def write_stream(path, chunks, *, validate=None) -> int:
    """Atomically write the byte iterator *chunks* to *path* (guarded); return the byte
    count.

    Streams through a ``.part`` file. If *validate(tmp_path)* returns a truthy reason the
    write is rejected (:class:`RejectedContent`). Any failure — a producer raising
    mid-stream, a write error, a rejected validation — removes the ``.part`` and re-raises,
    so a partial never survives. The producer counts its own bytes (progress is its job,
    not the facade's); this returns the total actually written.

    Unlike the other ops, the download may target a user-configured model folder, so the
    model roots widen the positive clause here — and only here.
    """
    _guard(path, follow_last=True, extra_roots=_model_roots())
    target = Path(os.path.realpath(path))   # write THROUGH a symlinked target (see _atomic_bytes)
    tmp = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with open(tmp, "wb") as fh:
            for buf in chunks:
                fh.write(buf)
                written += len(buf)
        problem = validate(tmp) if validate is not None else None
        if problem:
            raise RejectedContent(problem)
        os.replace(tmp, target)
        return written
    except BaseException:
        _discard(tmp)
        raise


def promote(tmp, target) -> None:
    """Rename *tmp* onto *target* (both guarded) — the final step of a write this facade did
    not perform itself.

    For a producer that insists on writing its own file: ``numpy.save`` streams the semantic
    index's matrix, and buffering it through :func:`write_bytes` would mean holding the whole
    thing in memory for nothing. So the foreign writer produces a temp file and the facade
    owns WHERE the result lands, which is the part that matters — it cannot own how a foreign
    library streams its own bytes, and pretending otherwise would be the more dishonest
    boundary.

    Both ends are checked. The source, because ``os.replace`` CONSUMES it: leaving it
    unguarded would turn this into a way of deleting any file by moving it somewhere allowed.
    It is checked in delete mode (the operation acts on the link, not through it), the target
    in write mode — the same split the vault-side rename uses.
    """
    _guard(tmp, follow_last=False)
    _guard(target, follow_last=True)
    os.replace(tmp, target)


def mkdir(path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a directory at *path* (guarded)."""
    _guard(path, follow_last=True)
    Path(path).mkdir(parents=parents, exist_ok=exist_ok)


def unlink(path, *, missing_ok: bool = False) -> None:
    """Remove the file (or symlink) at *path* (guarded). Resolved in delete mode: a symlink
    that sits inside a root but points outside can be removed — the operation acts on the
    link, not its target."""
    _guard(path, follow_last=False)
    Path(path).unlink(missing_ok=missing_ok)
