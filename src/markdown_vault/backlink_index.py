"""Markdown Vault — incremental backlink index.

Maintains a reverse index: for each target stem (filename without
``.md``), keeps the set of source files that link to it via
``[[wikilink]]``.  The index is built once on startup and updated
incrementally as files are created, deleted, renamed, or modified.
"""

import json
import logging
import os
import re
from pathlib import Path

from .tags import parse_wikilinks

logger = logging.getLogger(__name__)


class BacklinkIndex:
    """In-memory index mapping target stems to source file paths.

    Two internal maps:

    ``_target_to_sources``
        ``{target_stem: {source_path_str, ...}}``
        The reverse index used for O(1) backlink lookups.

    ``_source_to_targets``
        ``{source_path_str: {target_stem, ...}}``
        Tracks which targets a given source links to so we can
        cleanly remove stale entries on file update/delete.
    """

    def __init__(self) -> None:
        self._target_to_sources: dict[str, set[str]] = {}
        self._source_to_targets: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def build(self, vault_paths: list[str]) -> None:
        """Scan all vaults and build the index from scratch."""
        self._target_to_sources.clear()
        self._source_to_targets.clear()
        for vp in vault_paths:
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
                    self._index_file(fpath, text)

    # ------------------------------------------------------------------
    # Incremental updates
    # ------------------------------------------------------------------

    def update_file(self, file_path: str | Path, text: str) -> None:
        """Re-index *file_path* after its content changed."""
        path_str = str(file_path)
        self._remove_source(path_str)
        self._index_file(path_str, text)

    def remove_file(self, file_path: str | Path) -> None:
        """Remove *file_path* from the index entirely."""
        self._remove_source(str(file_path))

    def rename_file(self, old_path: str | Path, new_path: str | Path) -> None:
        """Update the index for a file rename / move."""
        old_str = str(old_path)
        new_str = str(new_path)
        targets = self._source_to_targets.pop(old_str, set())
        self._source_to_targets[new_str] = targets
        for stem in targets:
            sources = self._target_to_sources.get(stem, set())
            sources.discard(old_str)
            sources.add(new_str)

    def remove_wikilinks(self, stem: str) -> list[str]:
        """Remove all [[stem]] and [[stem|alias]] from files linking to *stem*.

        Returns list of modified file paths.
        """
        sources = self._target_to_sources.get(stem, set())
        if not sources:
            return []
        pattern = re.compile(r"\[\[" + re.escape(stem) + r"(?:\|[^\]]+)?\]\]")
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
            new_text = pattern.sub("", text)
            if new_text != text:
                try:
                    Path(path_str).write_text(new_text, encoding="utf-8")
                except OSError:
                    logger.warning("Cannot write %s after wikilink removal", path_str, exc_info=True)
                    continue
                modified.append(path_str)
                # Update _source_to_targets for this file
                targets = self._source_to_targets.get(path_str, set())
                targets.discard(stem)
        # All wikilinks removed — clean up target entry
        if modified:
            self._target_to_sources.pop(stem, None)
        return modified

    def rename_wikilinks(self, old_stem: str, new_stem: str) -> list[str]:
        """Replace [[old]] → [[new]] and [[old|alias]] → [[new|alias]].

        Also updates _target_to_sources keys so find_backlinks works.

        Returns list of modified file paths.
        """
        sources = self._target_to_sources.get(old_stem, set())
        if not sources:
            return []
        old_pattern = re.compile(
            r"\[\[" + re.escape(old_stem) + r"\]\]"
        )
        old_alias_pattern = re.compile(
            r"\[\[" + re.escape(old_stem) + r"\|([^\]]+)\]\]"
        )
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
            new_text = old_pattern.sub(f"[[{new_stem}]]", text)
            new_text = old_alias_pattern.sub(
                lambda m: f"[[{new_stem}|{m.group(1)}]]", new_text
            )
            if new_text != text:
                try:
                    Path(path_str).write_text(new_text, encoding="utf-8")
                except OSError:
                    logger.warning("Cannot write %s after wikilink rename", path_str, exc_info=True)
                    continue
                modified.append(path_str)
                # Update _source_to_targets for this file
                targets = self._source_to_targets.get(path_str, set())
                if old_stem in targets:
                    targets.discard(old_stem)
                    targets.add(new_stem)
        # Update _target_to_sources keys
        if old_stem in self._target_to_sources:
            new_sources = self._target_to_sources.pop(old_stem)
            self._target_to_sources.setdefault(new_stem, set()).update(new_sources)
        return modified

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find_backlinks(self, target_file: str | Path) -> list[str]:
        """Return sorted list of source paths that link to *target_file*."""
        target_stem = Path(target_file).stem
        sources = set(self._target_to_sources.get(target_stem, set()))
        return sorted(sources)

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

    def _index_file(self, path_str: str, text: str) -> None:
        """Parse wikilinks in *text* and add them to the index."""
        targets: set[str] = set()
        for page, _alias in parse_wikilinks(text):
            targets.add(page)
            self._target_to_sources.setdefault(page, set()).add(path_str)
        if targets:
            self._source_to_targets[path_str] = targets

    def _remove_source(self, path_str: str) -> None:
        """Remove *path_str* from all reverse-mapping entries."""
        targets = self._source_to_targets.pop(path_str, set())
        for stem in targets:
            sources = self._target_to_sources.get(stem)
            if sources is not None:
                sources.discard(path_str)
                if not sources:
                    del self._target_to_sources[stem]
