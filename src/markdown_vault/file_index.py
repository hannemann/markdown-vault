"""In-memory index mapping file stems to their file system paths.

Provides O(1) stem-to-path lookups for incremental file tracking
(add/remove/rename).  The index is also used for debug dumps and
path-existence checks in the monitor handler.

Root-only indexing:
    Only ``.md`` files in the vault root (no subdirectories) are indexed.

Hidden file/directory filtering:
    Files and directories starting with ``.`` are excluded (e.g.
    ``.git/``, ``.DS_Store``, ``.hidden.md``).
"""

import json
import logging
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
        Stems are qualified by vault: ``vault::stem`` when multiple vaults
        have the same stem.

    ``_path_to_stem``
        ``{path_str: stem}``
        Reverse map for incremental updates (add/remove/rename).
    """

    def __init__(self) -> None:
        self._stem_to_path: dict[str, str] = {}
        self._path_to_stem: dict[str, str] = {}
        self._vault_paths: list[str] = []
        self._vault_names: list[str] = []

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def build(self, vaults: list[dict[str, str]]) -> None:
        """Scan all vaults and build the index from scratch.

        R12.2: Root-only — only ``.md`` files in the vault root are indexed,
        not files in subdirectories.

        Skips hidden files and directories (those starting with ``.``).
        Non-``.md`` files are ignored.
        """
        self._stem_to_path.clear()
        self._path_to_stem.clear()
        self._vault_paths = [v["path"] for v in vaults]
        self._vault_names = [v["name"] for v in vaults]
        for v in vaults:
            vp = v["path"]
            try:
                vault = Path(vp)
            except (OSError, ValueError):
                logger.warning("Cannot index non-existent vault: %s", vp, exc_info=True)
                continue
            if not vault.is_dir():
                logger.warning("Vault path is not a directory: %s", vp)
                continue
            # Only index .md files in the vault root (R12.2 root-only).
            for fname in sorted(vault.iterdir()):
                if not fname.is_file():
                    continue
                if not fname.name.endswith(".md"):
                    continue
                if self._is_hidden(fname.name):
                    continue
                stem = fname.stem  # Remove '.md'
                self._add_stem_mapping(v["name"], stem, str(fname))

    # ------------------------------------------------------------------
    # Incremental updates
    # ------------------------------------------------------------------

    def add_file(self, file_path: str, vault: str) -> None:
        """Add *file_path* to the index after a file creation."""
        path = Path(file_path)
        if not path.name.endswith(".md") or self._is_hidden(path.name):
            return
        stem = path.stem
        # Resolve vault path → vault name if needed
        if vault in self._vault_paths:
            idx = self._vault_paths.index(vault)
            vault = self._vault_names[idx]
        self._add_stem_mapping(vault, stem, file_path)

    def remove_file(self, file_path: str) -> None:
        """Remove *file_path* from the index after file deletion."""
        stem = self._path_to_stem.pop(file_path, None)
        if stem is not None:
            self._remove_stem_key(stem, file_path)

    def rename_file(self, old_path: str, new_path: str) -> None:
        """Update the index for a file rename/move."""
        old_key = self._path_to_stem[old_path]
        old_vault = old_key.rsplit(">", 1)[0]
        self.remove_file(old_path)
        self.add_file(new_path, vault=old_vault)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def has_path(self, file_path: str) -> bool:
        """Return ``True`` if *file_path* is tracked in the index."""
        return file_path in self._path_to_stem

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_to_file(self, path: str | Path) -> None:
        """Write the stem→path map as JSON to *path* (overwrites).

        Strips the vault prefix from keys for readability.
        """
        try:
            dump = {}
            for key, path_str in self._stem_to_path.items():
                # Strip vault prefix: "VaultName>stem" → "stem"
                if ">" in key:
                    _, stem = key.split(">", 1)
                else:
                    stem = key
                dump[stem] = path_str
            Path(path).write_text(
                json.dumps(dump, indent=2, ensure_ascii=False),
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

    def _add_stem_mapping(self, vault_name: str, stem: str, path_str: str) -> None:
        """Register *vault_name*>*stem* → *path_str*."""
        key = f"{vault_name}>{stem}"
        self._stem_to_path[key] = path_str
        self._path_to_stem[path_str] = key

    def _remove_stem_key(self, stem: str, path_str: str) -> None:
        """Remove *path_str* from the stem map under *stem*."""
        if self._stem_to_path.get(stem) == path_str:
            del self._stem_to_path[stem]
        self._path_to_stem.pop(path_str, None)
