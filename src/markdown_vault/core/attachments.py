"""Attachment lifecycle — keep a note's downloaded images in sync with the note.

Downloaded images live under one per-vault tree that mirrors the note tree:
``<vault>/attachments/<note-relative-dir>/<note-stem>/``. A note's ``.md`` stem
becomes a directory, so the same mirror maps a note *and* a folder:

    <vault>/notes/a.md        -> <vault>/attachments/notes/a/
    <vault>/notes/            -> <vault>/attachments/notes/

This module owns that layout and the operations that keep it consistent when a
note is created, deleted, renamed or moved — driven from the file monitor, so it
fires for in-app and external changes alike. The path logic is pure; the
filesystem helpers move/remove directories and rewrite a note's image links.
"""

import logging
import os
import re
from pathlib import Path

from markdown_vault.core import vault_fs

logger = logging.getLogger(__name__)


def is_internal(path) -> bool:
    """Whether *path* is inside an ``attachments/`` tree — an app-managed location,
    never a note. Used to keep it out of drop targets and to mark it in the tree."""
    return "attachments" in Path(path).parts


def _mirror(vault_root, rel) -> Path:
    return Path(os.path.normpath(Path(vault_root) / "attachments" / rel))


def attachment_target(vault_root, note_dir, stem: str):
    """Where a note's downloaded images live, and how to link them.

    Returns ``(attachments_dir: Path, link_prefix: str)``. The link prefix is
    relative to the note so it resolves in the preview wherever the note sits: a
    note in ``sub/`` gets ``../attachments/sub/<note>/``."""
    note_dir = Path(note_dir)
    attach = _mirror(vault_root, os.path.join(os.path.relpath(note_dir, vault_root), stem))
    link_prefix = Path(os.path.relpath(attach, note_dir)).as_posix()
    return attach, link_prefix


def mirror_dir(vault_root, path) -> Path:
    """The attachments directory mirroring a note (``…/attachments/<dir>/<stem>/``)
    or a folder (``…/attachments/<folder>/``). A note's ``.md`` extension is
    stripped so its stem becomes a directory."""
    p = Path(path)
    if p.suffix.lower() == ".md":
        p = p.with_suffix("")
    return _mirror(vault_root, os.path.relpath(p, vault_root))


def link_prefix(vault_root, note_path) -> str:
    """The relative image-link prefix for a note at *note_path*."""
    note_path = Path(note_path)
    return attachment_target(vault_root, note_path.parent, note_path.stem)[1]


def relink(text: str, old_prefix: str, new_prefix: str) -> str:
    """Rewrite image links whose path starts with *old_prefix* to *new_prefix*, in
    both markdown ``![](old/…)`` and HTML ``<img src="old/…">`` forms. Only that
    prefix is touched, so unrelated links are left alone."""
    if old_prefix == new_prefix:
        return text
    md = re.compile(r"!\[([^\]]*)\]\(" + re.escape(old_prefix) + r"/([^)]+)\)")
    html = re.compile(r'(<img\b[^>]*\bsrc=")' + re.escape(old_prefix) + r'/([^"]+)(")')
    text = md.sub(lambda m: f"![{m.group(1)}]({new_prefix}/{m.group(2)})", text)
    text = html.sub(lambda m: f"{m.group(1)}{new_prefix}/{m.group(2)}{m.group(3)}", text)
    return text


def relink_file(path, old_prefix: str, new_prefix: str) -> None:
    """Rewrite a note's image links on disk (used when the note is not open)."""
    p = Path(path)
    if old_prefix == new_prefix or not p.is_file():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("attachments: could not relink %s: %s", path, exc)
        return
    relinked = relink(text, old_prefix, new_prefix)
    if relinked != text:
        try:
            vault_fs.write_text(str(p), relinked)
        except (OSError, vault_fs.VaultWriteError) as exc:
            # Best-effort relink; the caller (app_window) does not wrap this. A stale link in
            # a note the guard refused to rewrite is harmless, an uncaught raise is not.
            logger.warning("attachments: could not relink %s: %s", path, exc)


def _prune_empty(start, stop) -> None:
    """Remove empty directories from *start* upward, stopping before *stop* (the
    ``<vault>/attachments`` root is kept even when empty)."""
    start, stop = Path(start), Path(stop)
    while start != stop and start != start.parent and start.is_dir() \
            and not any(start.iterdir()):
        try:
            vault_fs.rmdir(str(start))
        except (OSError, vault_fs.VaultWriteError):
            # best-effort upward prune; stop as soon as a dir won't remove (or is refused)
            return
        start = start.parent


def _within_attachments(d: Path, vault_root) -> bool:
    """Whether *d* is a directory strictly inside ``<vault>/attachments`` — the
    only place remove/move may touch. Guards against a mirror that normalised out
    of the tree (a path not under the vault yields ``..`` segments), so an
    irreversible rmtree/move can never escape it, whatever the caller passed."""
    root = Path(vault_root) / "attachments"
    return d != root and d.is_relative_to(root)


def remove(vault_root, path) -> None:
    """Delete the attachments mirroring a deleted note or folder."""
    d = mirror_dir(vault_root, path)
    if not _within_attachments(d, vault_root):
        return
    if d.is_dir():
        try:
            # ignore_errors=True keeps shutil's best-effort walk: one un-removable file costs
            # that one file, not the whole mirror. So no OSError reaches here — only the
            # guard's defensive VaultWriteError, which _within_attachments already precludes.
            vault_fs.rmtree(str(d), ignore_errors=True)
        except vault_fs.VaultWriteError as exc:
            logger.warning("attachments: refused remove %s: %s", d, exc)
            return
        _prune_empty(d.parent, Path(vault_root) / "attachments")


_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


def _unique_name(dest_dir: Path, filename: str) -> str:
    """A safe, unique filename within *dest_dir*, keeping the extension (defaulting
    to ``.png`` when the source has none, e.g. a pasted screenshot)."""
    base = _SAFE_NAME.sub("-", Path(filename).name.lower()).strip("-.") or "image"
    stem, dot, ext = base.rpartition(".")
    if not dot or not ext:
        stem, ext = base, "png"
    candidate = f"{stem}.{ext}"
    n = 2
    while (dest_dir / candidate).exists():
        candidate = f"{stem}-{n}.{ext}"
        n += 1
    return candidate


def store_image(vault_root, note_path, data: bytes, filename: str) -> str:
    """Save a user-supplied image into the note's attachments dir under a safe,
    unique name and return the relative link to insert. Used by paste / drag-drop /
    "Insert Image…" so the user never touches the app-managed attachments tree."""
    note_path = Path(note_path)
    dest_dir, prefix = attachment_target(vault_root, note_path.parent, note_path.stem)
    vault_fs.mkdir(str(dest_dir), parents=True, exist_ok=True)
    fname = _unique_name(dest_dir, filename)
    # Atomic: a non-.md image is monitor-filtered (no false-reload banner), so the atomic
    # writer is free here and guards against a half-written image on crash. A refusal or OS
    # error raises to the caller (editor / document import), which handles it.
    vault_fs.write_bytes_atomic(str(dest_dir / fname), data)
    return f"{prefix}/{fname}"


_IMG_LINK = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)")


def classify_image_links(text: str, note_dir, vault_root):
    """Classify each local image link in *text* relative to a note in *note_dir*.

    Returns a list of ``(start, end, line, kind, source)`` where *kind* is
    ``"broken"`` (the target file doesn't exist; *source* is ``None``) or
    ``"adoptable"`` (an existing local file outside the attachments tree, that the
    user could move in; *source* is its resolved path). Remote/``data:`` links and
    images already inside the attachments tree are ignored."""
    note_dir = Path(note_dir)
    out = []
    for m in _IMG_LINK.finditer(text):
        target = m.group(1)
        if target.startswith(("http://", "https://", "data:", "mailto:", "#")):
            continue
        resolved = Path(os.path.normpath(note_dir / target))
        line = text.count("\n", 0, m.start())
        if not resolved.exists():
            out.append((m.start(), m.end(), line, "broken", None))
        elif is_internal(str(resolved)):
            continue                    # already managed — nothing to flag
        else:
            out.append((m.start(), m.end(), line, "adoptable", str(resolved)))
    return out


def retarget_image(text: str, note_dir, source, new_link: str) -> str:
    """Repoint every image link in *text* whose target resolves to *source* at
    *new_link* — used when adopting an external image into the attachments tree."""
    note_dir = Path(note_dir)
    source = str(Path(os.path.normpath(source)))

    def repl(m):
        old = m.group(1)
        if str(Path(os.path.normpath(note_dir / old))) == source:
            return m.group(0).replace(old, new_link, 1)
        return m.group(0)

    return _IMG_LINK.sub(repl, text)


def move(old_vault, old_path, new_vault, new_path) -> None:
    """Move the attachments mirroring a renamed/moved note or folder. The note's
    own image links are relinked separately (:func:`relink` / :func:`relink_file`)
    by the caller, which knows whether the note is open."""
    old_d = mirror_dir(old_vault, old_path)
    new_d = mirror_dir(new_vault, new_path)
    if not _within_attachments(old_d, old_vault) or not _within_attachments(new_d, new_vault):
        return
    if old_d != new_d and old_d.is_dir():
        try:
            vault_fs.mkdir(str(new_d.parent), parents=True, exist_ok=True)
            vault_fs.move(str(old_d), str(new_d))
        except vault_fs.VaultWriteError as exc:
            # Defensive: _within_attachments already confined both ends to <vault>/attachments,
            # so a containment refusal cannot happen for a legit call. Contain the new type
            # here rather than leak it past the caller's `except OSError`; OSError still flows
            # through to that handler unchanged.
            logger.warning("attachments: refused move %s -> %s: %s", old_d, new_d, exc)
            return
        _prune_empty(old_d.parent, Path(old_vault) / "attachments")
