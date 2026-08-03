"""Markdown Vault — incremental backlink index.

Maintains a reverse index: for each target (canonical ``vault:`` URL),
keeps the set of source files that link to it via ``[[wikilink]]``.
The index is built once on startup and updated incrementally as files
are created, deleted, renamed, or modified.
"""

import json
import logging
import os
from pathlib import Path

from .path_utils import (
    find_vault_name_for_path,
    resolve_vault_path,
    wikilink_url,
)
from .tags import WIKILINK_RE, parse_wikilinks, wikilink_info_from_match

logger = logging.getLogger(__name__)


class BacklinkIndex:
    """In-memory index mapping canonical ``vault:`` targets to source paths.

    Two internal maps:

    ``_target_to_sources``
        ``{canonical_url: {source_path_str, ...}}``
        The reverse index used for O(1) backlink lookups.

    ``_source_to_targets``
        ``{source_path_str: {canonical_url, ...}}``
        Tracks which targets a given source links to so we can
        cleanly remove stale entries on file update/delete.
    """

    def __init__(self) -> None:
        self._target_to_sources: dict[str, set[str]] = {}
        self._source_to_targets: dict[str, set[str]] = {}
        # R16.1: bumped by every incremental mutation so the async build can
        # detect edits applied during its scan window (and re-scan instead of
        # overwriting them with a stale disk snapshot).
        self._mutation_seq = 0

    @property
    def mutation_seq(self) -> int:
        """Monotonic counter of incremental mutations since construction."""
        return self._mutation_seq

    def _bump_mutation_seq(self) -> None:
        self._mutation_seq += 1

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def build(self, vaults: list[dict[str, str]]) -> None:
        """Scan all vaults and build the index from scratch.

        Delegates to the pure :func:`scan_vaults` and swaps the result
        in atomically.  For a large vault, callers should run the scan
        off the main thread (see ``scan_vaults``) and apply it via
        :meth:`set_index` on the main thread.
        """
        self.set_index(*scan_vaults(vaults))

    def set_index(
        self,
        target_to_sources: dict[str, set[str]],
        source_to_targets: dict[str, set[str]],
    ) -> None:
        """Replace both maps atomically (called on the main thread).

        The maps are taken over by reference — they must not be mutated
        afterwards by the worker that produced them.
        """
        self._target_to_sources = target_to_sources
        self._source_to_targets = source_to_targets

    # ------------------------------------------------------------------
    # Incremental updates
    # ------------------------------------------------------------------

    def update_file(self, file_path: str | Path, text: str) -> None:
        """Re-index *file_path* after its content changed."""
        self._bump_mutation_seq()
        path_str = str(file_path)
        self._remove_source(path_str)
        _index_file_into(
            self._target_to_sources, self._source_to_targets, path_str, text,
        )

    def remove_file(self, file_path: str | Path) -> None:
        """Remove *file_path* from the index entirely."""
        self._bump_mutation_seq()
        self._remove_source(str(file_path))

    def remove_vault(self, vault_path: str) -> None:
        """Remove all entries under *vault_path* from the index."""
        self._bump_mutation_seq()
        abs_path = os.path.abspath(vault_path) + os.sep
        keys_to_remove = [
            k for k in self._target_to_sources
            if k.startswith(f"vault:{abs_path}") or k.startswith(f"vault:?path={abs_path}")
        ]
        for key in keys_to_remove:
            del self._target_to_sources[key]
        keys_to_remove = [
            k for k in self._source_to_targets
            if k.startswith(abs_path)
        ]
        for key in keys_to_remove:
            del self._source_to_targets[key]

    def rename_file(self, old_path: str | Path, new_path: str | Path) -> None:
        """Update the index for a file rename / move."""
        self._bump_mutation_seq()
        old_str = str(old_path)
        new_str = str(new_path)
        targets = self._source_to_targets.pop(old_str, set())
        self._source_to_targets[new_str] = targets
        for stem in targets:
            sources = self._target_to_sources.get(stem, set())
            sources.discard(old_str)
            sources.add(new_str)

    def remove_wikilinks(self, file_path: str | Path) -> list[str]:
        """Remove all ``[[wikilink]]`` references to *file_path* from linking files.

        Only links whose canonical target is exactly *file_path* are removed.
        Returns list of modified file paths.
        """
        self._bump_mutation_seq()
        key = self._file_key(str(file_path))
        if not key:
            return []
        sources = self._target_to_sources.get(key, set())
        if not sources:
            return []
        modified = []
        for path_str in list(sources):
            try:
                text = Path(path_str).read_text(encoding="utf-8")
            except FileNotFoundError:
                # Source file already gone — nothing to clean up.
                logger.debug("Skip wikilink removal, file gone: %s", path_str)
                continue
            except (OSError, UnicodeDecodeError):
                logger.warning("Cannot read %s for wikilink removal", path_str, exc_info=True)
                continue
            new_text = self._remove_links_to(text, path_str, key)
            if new_text != text:
                try:
                    Path(path_str).write_text(new_text, encoding="utf-8")
                except OSError:
                    logger.warning("Cannot write %s after wikilink removal", path_str, exc_info=True)
                    continue
                modified.append(path_str)
                # Update _source_to_targets for this file
                targets = self._source_to_targets.get(path_str, set())
                targets.discard(key)
        # All wikilinks removed — clean up target entry
        if modified:
            self._target_to_sources.pop(key, None)
        return modified

    def _remove_links_to(self, text: str, source_file: str, key: str) -> str:
        """Return *text* with links targeting *key* removed (exact match)."""

        def repl(m):
            info = wikilink_info_from_match(m)
            if _link_key(info, source_file) == key:
                return ""
            return m.group(0)

        return WIKILINK_RE.sub(repl, text)

    def rename_wikilinks(self, old_path: str | Path, new_path: str | Path) -> list[str]:
        """Redirect ``[[wikilink]]`` references from *old_path* to *new_path*.

        Also updates ``_target_to_sources`` keys so ``find_backlinks`` works.
        Returns list of modified file paths.
        """
        self._bump_mutation_seq()
        old_key = self._file_key(str(old_path))
        new_key = self._file_key(str(new_path))
        if not old_key or not new_key or old_key == new_key:
            return []
        new_parts = self._file_key_parts(str(new_path))
        if new_parts is None:
            return []
        new_vault, new_rel = new_parts
        sources = self._target_to_sources.get(old_key, set())
        if not sources:
            return []
        modified = []
        for path_str in list(sources):
            try:
                text = Path(path_str).read_text(encoding="utf-8")
            except FileNotFoundError:
                logger.debug("Skip wikilink rename, file gone: %s", path_str)
                continue
            except (OSError, UnicodeDecodeError):
                logger.warning("Cannot read %s for wikilink rename", path_str, exc_info=True)
                continue
            new_text = self._rename_links_to(text, path_str, old_key, new_vault, new_rel)
            if new_text != text:
                try:
                    Path(path_str).write_text(new_text, encoding="utf-8")
                except OSError:
                    logger.warning("Cannot write %s after wikilink rename", path_str, exc_info=True)
                    continue
                modified.append(path_str)
                # Update _source_to_targets for this file
                targets = self._source_to_targets.get(path_str, set())
                if old_key in targets:
                    targets.discard(old_key)
                    targets.add(new_key)
        # Update _target_to_sources keys
        if old_key in self._target_to_sources:
            new_sources = self._target_to_sources.pop(old_key)
            self._target_to_sources.setdefault(new_key, set()).update(new_sources)
        return modified

    def _rename_links_to(
        self, text: str, source_file: str, old_key: str,
        new_vault: str, new_rel: str,
    ) -> str:
        """Return *text* with links targeting *old_key* redirected to the new target."""

        def repl(m):
            info = wikilink_info_from_match(m)
            if _link_key(info, source_file) != old_key:
                return m.group(0)
            alias = f"|{info.alias}" if info.alias else ""
            source_vault = find_vault_name_for_path(source_file)
            if info.vault or source_vault != new_vault:
                return f"[[{new_vault}>{new_rel}{alias}]]"
            return f"[[{new_rel}{alias}]]"

        return WIKILINK_RE.sub(repl, text)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find_backlinks(self, target_file: str | Path) -> list[str]:
        """Return sorted list of source paths that link to *target_file*.

        Exact canonical-key lookup only — the target file's ``vault:`` URL
        is computed from the config (SSOT) and matched verbatim.  No fuzzy
        matching, no cross-vault or unqualified fallbacks.
        """
        key = self._file_key(str(target_file))
        if not key:
            return []
        return sorted(self._target_to_sources.get(key, set()))

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_to_file(self, path: str | Path) -> None:
        """Write the backlink index as JSON to *path* (overwrites)."""
        try:
            data = {
                "target_to_sources": {
                    k: sorted(v) for k, v in self._target_to_sources.items()
                },
                "source_to_targets": {
                    k: sorted(v) for k, v in self._source_to_targets.items()
                },
            }
            Path(path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to dump BacklinkIndex to %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _file_key(self, file_path: str) -> str | None:
        """Return the canonical ``vault:`` key a file is targeted by."""
        parts = self._file_key_parts(file_path)
        if not parts:
            return None
        return wikilink_url(*parts)

    def _file_key_parts(self, file_path: str) -> tuple[str, str] | None:
        """Return ``(vault_name, relative_path)`` for a file, or ``None``."""
        vault = find_vault_name_for_path(file_path)
        if not vault:
            return None
        vault_path = resolve_vault_path(vault)
        if not vault_path:
            return None
        relative = str(Path(file_path).relative_to(Path(vault_path)).with_suffix(""))
        return vault, relative

    def _remove_source(self, path_str: str) -> None:
        """Remove *path_str* from all reverse-mapping entries."""
        targets = self._source_to_targets.pop(path_str, set())
        for key in targets:
            sources = self._target_to_sources.get(key)
            if sources is not None:
                sources.discard(path_str)
                if not sources:
                    del self._target_to_sources[key]


# ----------------------------------------------------------------------
# Pure functions (no instance state, no GTK/GLib) — safe to run in a
# background thread.  Used by BacklinkIndex.build() and by the async
# scan scheduled from the app window (R5.3).
# ----------------------------------------------------------------------

def _link_key(info, source_file: str) -> str | None:
    """Return the canonical ``vault:`` key for a parsed link.

    Links without an explicit vault prefix are qualified with the
    source file's vault.  ``None`` means the link cannot be resolved
    (source file outside any vault) — never an empty vault.
    """
    if info.vault:
        return wikilink_url(info.vault, info.stem)
    vault = find_vault_name_for_path(source_file)
    if not vault:
        logger.error("Source file not in any vault: %s", source_file)
        return None
    return wikilink_url(vault, info.stem)


def _index_file_into(
    target_to_sources: dict[str, set[str]],
    source_to_targets: dict[str, set[str]],
    path_str: str,
    text: str,
) -> None:
    """Parse wikilinks in *text* and add them to the given maps.

    Every target is fully vault-qualified: a link without an explicit
    ``Vault>`` prefix resolves against the source file's own vault.
    """
    targets: set[str] = set()
    for info in parse_wikilinks(text):
        key = _link_key(info, path_str)
        if not key:
            continue
        targets.add(key)
        target_to_sources.setdefault(key, set()).add(path_str)
    if targets:
        source_to_targets[path_str] = targets


def scan_vaults(
    vaults: list[dict[str, str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Scan all vaults and return ``(target_to_sources, source_to_targets)``.

    Pure function — no instance state, no GTK/GLib — safe to run in a
    background thread.  Returns freshly built maps so callers can swap
    them in atomically on the main thread.
    """
    target_to_sources: dict[str, set[str]] = {}
    source_to_targets: dict[str, set[str]] = {}
    for v in vaults:
        vp = v["path"]
        for root, _dirs, files in os.walk(vp):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = str(Path(root) / fname)
                try:
                    text = Path(fpath).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    logger.debug("Cannot read %s for indexing", fpath, exc_info=True)
                    continue
                _index_file_into(target_to_sources, source_to_targets, fpath, text)
    return target_to_sources, source_to_targets
