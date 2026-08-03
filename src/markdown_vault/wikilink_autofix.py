"""Pre-save wikilink autofix and broken-link detection.

The analysis functions (:func:`analyze_text`, :func:`find_broken_ranges`) are
pure — no GTK, no config, no filesystem access.  All vault knowledge is
injected as callables, so the logic is fully unit-testable in isolation.

:class:`WikilinkResolver` is the thin adapter that binds those callables to
the real vault config and filesystem.  It is created fresh per operation so
each save/scan sees a current view of the vault.

What counts as *fixable*
------------------------
A link is **broken** when it does not resolve to an existing file.  A broken
link is only auto-repaired ("relink") when exactly **one** file in the vault
has a matching basename — an unambiguous moved/renamed target.  Zero or
multiple candidates are never guessed: those links are reported as broken so
the user (or the marks/dialog) can deal with them.  Casing mismatches fall out
of this naturally on case-sensitive filesystems: ``[[foo]]`` does not resolve
to ``Foo.md``, so it is broken and the unique candidate ``Foo`` repairs it.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from . import config
from .path_utils import find_vault_name_for_path, resolve_wikilink
from .tags import WIKILINK_RE, wikilink_info_from_match


@dataclass(frozen=True)
class BrokenLink:
    """A wikilink that does not resolve and could not be auto-fixed.

    ``start``/``end`` are character offsets of the full ``[[…]]`` span in the
    source text; ``display`` is the user-facing ``stem`` or ``stem|alias``.
    """

    start: int
    end: int
    raw: str
    display: str
    vault: str | None
    stem: str
    alias: str | None


@dataclass(frozen=True)
class WikilinkFix:
    """A single text replacement to apply before saving.

    ``kind`` is ``"normalize"`` (whitespace tidy) or ``"relink"`` (redirect a
    broken link to its unique target).
    """

    start: int
    end: int
    old: str
    new: str
    kind: str


def _basename_stem(stem: str) -> str:
    """Return the last path component of a wikilink stem, trimmed."""
    return stem.strip().rsplit("/", 1)[-1]


def _normalized_link(info) -> str:
    """Rebuild a wikilink from its parsed parts with trimmed whitespace."""
    vault = info.vault.strip() if info.vault else None
    stem = info.stem.strip()
    alias = info.alias.strip() if info.alias else None
    prefix = f"{vault}>" if vault else ""
    suffix = f"|{alias}" if alias else ""
    return f"[[{prefix}{stem}{suffix}]]"


def _build_relink(info, source_vault, target_vault: str, target_rel: str) -> str:
    """Build the corrected link string for a unique relink target.

    Mirrors the qualified-vs-unqualified rule used when renaming links: emit
    the ``[[Vault>rel]]`` form if the original link was vault-qualified or the
    target lives in a different vault than the source file; otherwise the short
    ``[[rel]]`` form.
    """
    alias_text = info.alias.strip() if info.alias else ""
    alias = f"|{alias_text}" if alias_text else ""
    if info.vault or source_vault != target_vault:
        return f"[[{target_vault}>{target_rel}{alias}]]"
    return f"[[{target_rel}{alias}]]"


def _unique(items: list) -> list:
    """Return *items* de-duplicated, order preserved."""
    seen: list = []
    for it in items:
        if it not in seen:
            seen.append(it)
    return seen


def analyze_text(
    text: str,
    source_file: str,
    *,
    source_vault: str | None,
    resolve,
    find_candidates,
    normalize: bool,
    relink: bool,
) -> tuple[list[WikilinkFix], list[BrokenLink]]:
    """Analyse wikilinks in *text* and return ``(fixes, broken)``.

    Parameters
    ----------
    resolve : callable
        ``resolve(info) -> str | None`` — absolute path of the *existing*
        target file, or ``None`` if the link does not resolve.
    find_candidates : callable
        ``find_candidates(basename) -> list[(vault_name, relative_path)]`` —
        all vault files whose basename stem matches (case-insensitive).
    normalize, relink : bool
        Feature flags; when both are off only broken-link detection runs.

    Returns
    -------
    fixes : list[WikilinkFix]
        Replacements to apply to the buffer before saving.
    broken : list[BrokenLink]
        Links that stay unresolved (not auto-fixed) — for the save-time
        notice and editor marks.
    """
    fixes: list[WikilinkFix] = []
    broken: list[BrokenLink] = []
    for m in WIKILINK_RE.finditer(text):
        info = wikilink_info_from_match(m)
        if not info.stem or not info.stem.strip():
            continue
        start, end, full = m.start(), m.end(), m.group(0)
        if resolve(info) is not None:
            if normalize:
                new = _normalized_link(info)
                if new != full:
                    fixes.append(WikilinkFix(start, end, full, new, "normalize"))
            continue
        # Broken: only touch it to repair when the target is unambiguous.
        if relink:
            candidates = _unique(find_candidates(_basename_stem(info.stem)))
            if len(candidates) == 1:
                target_vault, target_rel = candidates[0]
                new = _build_relink(info, source_vault, target_vault, target_rel)
                if new != full:
                    fixes.append(WikilinkFix(start, end, full, new, "relink"))
                continue
        broken.append(
            BrokenLink(start, end, info.raw, info.display,
                       info.vault, info.stem, info.alias)
        )
    return fixes, broken


def find_broken_ranges(text: str, resolve) -> list[tuple[int, int]]:
    """Return ``(start, end)`` offsets of every unresolved wikilink in *text*.

    Used for live editor marking — reflects the current on-disk state
    regardless of whether a link would be auto-fixable on save.
    """
    ranges: list[tuple[int, int]] = []
    for m in WIKILINK_RE.finditer(text):
        info = wikilink_info_from_match(m)
        if not info.stem or not info.stem.strip():
            continue
        if resolve(info) is None:
            ranges.append((m.start(), m.end()))
    return ranges


def apply_fixes(text: str, fixes: list[WikilinkFix]) -> str:
    """Return *text* with *fixes* applied (right-to-left, offsets preserved)."""
    for fix in sorted(fixes, key=lambda f: f.start, reverse=True):
        text = text[: fix.start] + fix.new + text[fix.end:]
    return text


class WikilinkResolver:
    """Bind the pure autofix logic to the real vault config + filesystem.

    Create one per save/scan operation.  The basename → candidates index is
    built lazily on first :meth:`find_candidates` call (i.e. only when a broken
    link is actually encountered) and cached for the lifetime of the instance.
    """

    def __init__(self) -> None:
        self._index: dict[str, list[tuple[str, str]]] | None = None

    def resolve(self, info, source_file: str) -> str | None:
        """Return the existing target path for *info*, or ``None``.

        A trailing ``#heading`` anchor is stripped before resolving (the
        preview does the same); a bare ``[[#Heading]]`` same-file anchor
        resolves to the source file itself, so neither is ever flagged
        broken (R21.2).
        """
        page = info.stem.split("#", 1)[0].strip()
        if not page:
            return source_file  # same-file heading anchor
        vault = info.vault or find_vault_name_for_path(source_file)
        if not vault:
            return None
        target = resolve_wikilink(vault, page)
        if target and os.path.exists(target):
            return target
        return None

    def find_candidates(self, basename: str) -> list[tuple[str, str]]:
        """Return ``(vault, rel)`` for all files matching *basename* (ci)."""
        return self._candidate_index().get(basename.lower(), [])

    def _candidate_index(self) -> dict[str, list[tuple[str, str]]]:
        if self._index is None:
            index: dict[str, list[tuple[str, str]]] = {}
            for entry in config.load_vaults():
                vault = entry["name"]
                vpath = entry["path"]
                for root, _dirs, files in os.walk(vpath):
                    for fname in files:
                        if not fname.endswith(".md"):
                            continue
                        stem = Path(fname).stem
                        rel = str(
                            Path(os.path.join(root, fname))
                            .relative_to(vpath)
                            .with_suffix("")
                        )
                        index.setdefault(stem.lower(), []).append((vault, rel))
            self._index = index
        return self._index
