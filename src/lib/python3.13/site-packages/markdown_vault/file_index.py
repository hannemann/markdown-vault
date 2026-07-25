"""In-memory index mapping file stems to their file system paths.

Provides O(1) stem-to-path resolution for wikilink lookups, replacing
the O(n) ``vault.rglob("*.md")`` scan in ``Preview._resolve_wikilink``.

Underscore↔Space normalization:
    A file ``Datei B.md`` is indexed under both ``"Datei B"`` and
    ``"Datei_B"`` so that wikilinks with either separator resolve
    correctly.

Hidden file/directory filtering:
    Files and directories starting with ``.`` are excluded (e.g.
    ``.git/``, ``.DS_Store``, ``.hidden.md``).
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Hidden files and directories that should be excluded from indexing.
_HIDDEN_PREFIXES = (".",)


class FileIndex:
    """In-memory index mapping file stems to their file system paths.

    Two internal maps:

    ``_stem_to_path``
        ``{stem: path_str}``
        O(1) lookup from stem (filename without ``.md``) to file path.

    ``_path_to_stem``
        ``{path_str: stem}``
        Reverse map for incremental updates (add/remove/rename).
    """

    def __init__(self) -> None:
        self._stem_to_path: dict[str, str] = {}
        self._path_to_stem: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def build(self, vault_paths: list[str]) -> None:
        """Scan all vaults and build the index from scratch.

        Skips hidden files and directories (those starting with ``.``).
        Non-``.md`` files are ignored.
        """
        self._stem_to_path.clear()
        self._path_to_stem.clear()
        for vp in vault_paths:
            try:
                vault = Path(vp)
            except (OSError, ValueError):
                logger.warning("Cannot index non-existent vault: %s", vp, exc_info=True)
                continue
            if not vault.is_dir():
                logger.warning("Vault path is not a directory: %s", vp)
                continue
            for root, dirs, files in os.walk(vp):
                # Prune hidden directories in-place so os.walk doesn't descend.
                dirs[:] = sorted(d for d in dirs if not self._is_hidden(d))
                for fname in sorted(files):
                    if not fname.endswith(".md"):
                        continue
                    if self._is_hidden(fname):
                        continue
                    fpath = str(Path(root) / fname)
                    stem = fname[:-3]  # Remove '.md'
                    self._add_stem_mapping(stem, fpath)

    # ------------------------------------------------------------------
    # Incremental updates
    # ------------------------------------------------------------------

    def add_file(self, file_path: str) -> None:
        """Add *file_path* to the index after a file creation."""
        path = Path(file_path)
        if not path.name.endswith(".md") or self._is_hidden(path.name):
            return
        stem = path.stem
        self._add_stem_mapping(stem, file_path)

    def remove_file(self, file_path: str) -> None:
        """Remove *file_path* from the index after file deletion."""
        stem = self._path_to_stem.pop(file_path, None)
        if stem is not None:
            self._remove_stem_key(stem, file_path)
            # Also remove underscore↔space variant
            alt = stem.replace(" ", "_")
            if alt != stem:
                self._remove_stem_key(alt, file_path)

    def rename_file(self, old_path: str, new_path: str) -> None:
        """Update the index for a file rename/move."""
        self.remove_file(old_path)
        self.add_file(new_path)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def resolve(self, stem: str) -> str | None:
        """Look up a stem and return the file path, or ``None``.

        Supports underscore↔space normalization so that ``Datei_B``
        resolves to ``Datei B.md``.
        """
        if not stem:
            return None
        # Direct lookup
        result = self._stem_to_path.get(stem)
        if result is not None:
            if os.path.isfile(result):
                return result
            self.remove_file(result)
        # Underscore↔space variant
        alt = stem.replace("_", " ")
        if alt != stem:
            result = self._stem_to_path.get(alt)
            if result is not None:
                if os.path.isfile(result):
                    return result
                self.remove_file(result)
        return None

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_to_file(self, path: str | Path) -> None:
        """Write the stem→path map as JSON to *path* (overwrites)."""
        try:
            Path(path).write_text(
                json.dumps(self._stem_to_path, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to dump FileIndex to %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _is_hidden(name: str) -> bool:
        """Return ``True`` if *name* starts with a dot (hidden)."""
        return name.startswith(_HIDDEN_PREFIXES)

    def _add_stem_mapping(self, stem: str, path_str: str) -> None:
        """Register *stem* → *path_str*, including underscore↔space variants."""
        # Primary stem
        self._stem_to_path[stem] = path_str
        self._path_to_stem[path_str] = stem
        # Alternate: replace spaces with underscores
        alt = stem.replace(" ", "_")
        if alt != stem:
            # Only add alternate if it doesn't conflict with an existing
            # mapping to a different file (preserve the primary match).
            existing = self._stem_to_path.get(alt)
            if existing is None or existing == path_str:
                self._stem_to_path[alt] = path_str

    def _remove_stem_key(self, stem: str, path_str: str) -> None:
        """Remove *path_str* from the stem map under *stem*."""
        if self._stem_to_path.get(stem) == path_str:
            del self._stem_to_path[stem]
