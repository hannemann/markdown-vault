"""Markdown Vault — incremental backlink index.

Maintains a reverse index: for each target stem (filename without
``.md``), keeps the set of source files that link to it via
``[[wikilink]]``.  The index is built once on startup and updated
incrementally as files are created, deleted, renamed, or modified.
"""

import logging
import os
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

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find_backlinks(self, target_file: str | Path) -> list[str]:
        """Return sorted list of source paths that link to *target_file*."""
        stem = Path(target_file).stem
        sources = self._target_to_sources.get(stem, set())
        return sorted(sources)

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
            sources = self._target_to_sources.get(stem, set())
            sources.discard(path_str)
            if not sources:
                del self._target_to_sources[stem]
