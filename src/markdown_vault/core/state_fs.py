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


def _allowed_roots() -> list[str]:
    """The state roots, resolved per call. The four XDG dirs plus the two configured model
    folders (Ask GGUF, semantic ONNX); an unusable root is logged and dropped, not fatal."""
    s = config.settings()
    candidates = [
        paths.CONFIG_DIR, paths.STATE_DIR, paths.CACHE_DIR, paths.DATA_DIR,
        config.ask_models_dir(s),
        Path(config.get_setting(s, "semantic.onnx.dir") or (paths.DATA_DIR / "onnx")),
    ]
    roots = []
    for candidate in candidates:
        try:
            roots.append(checked_root(candidate))
        except InvalidRoot:
            logger.warning("StateFS: ignoring unusable allowed-root %r", candidate,
                           exc_info=True)
    return roots


def _vault_roots() -> list[str]:
    return [entry["path"] for entry in config.load_vaults()]


def _guard(target, *, follow_last: bool) -> None:
    """Refuse a target that is under no allowed root, or under a vault. Same resolution
    mode for both clauses (a write resolves the whole path; a delete keeps the leaf)."""
    if not within_any(_allowed_roots(), target, follow_last=follow_last):
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
    tmp = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        _discard(tmp)
        raise


def write_text(path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *text* to *path* (guarded). tmp-then-rename, so a crash leaves the
    previous file intact rather than a truncated one."""
    _guard(path, follow_last=True)
    _atomic_bytes(Path(path), text.encode(encoding))


def write_bytes(path, data: bytes) -> None:
    """Atomically write *data* to *path* (guarded)."""
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
    """
    _guard(path, follow_last=True)
    target = Path(path)
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
