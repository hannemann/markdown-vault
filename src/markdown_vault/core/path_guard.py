"""Read-only path-containment primitives for the VaultFS / StateFS facades.

Unlike the lexical :func:`markdown_vault.core.path_utils.path_is_within` (which uses
``abspath`` and a string prefix — fine for mapping a file to its vault *name*, but
symlink-bypassable), these resolve symlinks with ``realpath`` so a crafted link cannot
smuggle a write out of its allowed tree. They do no mutation — ``realpath`` is a read —
so they live outside the two facades and stay independently testable.

Two resolution modes, matching how an operation treats the last path component:

- ``follow_last=True`` — a write, or a move *destination*: resolve the whole target. A
  write through a symlink lands where the link points, so the link must be resolved.
- ``follow_last=False`` — a delete/rename, or a move *source*: resolve the parent, keep
  the last component literal. Deleting a symlink acts on the link itself, not its target;
  resolving it would misjudge where the operation lands.
"""

import logging
import os

logger = logging.getLogger(__name__)


class InvalidRoot(ValueError):
    """An allowed-root that would make containment meaningless: empty, the filesystem
    root, or relative (a relative root resolves against the current directory, so
    "under the root" would silently mean "under wherever the process started")."""


def resolve(target, *, follow_last: bool) -> str:
    """``realpath`` of *target*, resolving the last component only when *follow_last*.

    With *follow_last* false the parent is resolved and the final component appended
    literally, so a leaf symlink is judged by where it *sits*, not where it points.
    """
    if follow_last:
        return os.path.realpath(target)
    # Resolve the ORIGINAL dirname, not os.path.abspath(target)'s: abspath collapses a
    # '..' lexically before realpath can see the directory symlink in front of it, so
    # `dirlink/../x` would be judged at `x` while the operation lands where dirlink points.
    literal = os.fspath(target)
    parent = os.path.realpath(os.path.dirname(literal) or os.curdir)
    return os.path.join(parent, os.path.basename(literal))


def within_any(roots, target, *, follow_last: bool) -> bool:
    """True if *target* resolves inside, or equal to, at least one of *roots*.

    Both sides are ``realpath``-resolved (the roots too), so a symlinked root is handled
    as well. An empty *roots* yields False.
    """
    resolved = resolve(target, follow_last=follow_last)
    for root in roots:
        real_root = os.path.realpath(root)
        if resolved == real_root or resolved.startswith(real_root + os.sep):
            return True
    return False


def checked_root(root) -> str:
    """Return *root* unchanged if it is a usable containment root, else raise
    :class:`InvalidRoot`.

    Rejects the empty string, any relative path, ``/`` and anything that resolves to the
    filesystem root — the cases where containment would admit everything.
    """
    path = os.fspath(root)
    if not path or not os.path.isabs(path):
        raise InvalidRoot(f"not an absolute path: {path!r}")
    if os.path.dirname(os.path.realpath(path)) == os.path.realpath(path):
        raise InvalidRoot(f"the filesystem root is not an allowed root: {path!r}")
    return path
