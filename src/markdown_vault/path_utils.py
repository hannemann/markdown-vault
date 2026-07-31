"""Path utilities for vault and file operations."""

import os
import re
from pathlib import Path

from . import config

# Regex for parsing Markdown headings (h1-h6).
# Used by preview, sidebar, and search_logic — keep in one place.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def resolve_vault_path(vault_name: str) -> str | None:
    """Resolve a vault name to its absolute path from the cached config.

    Returns ``None`` if the name is empty or not found in any configured
    vault.
    """
    if not vault_name:
        return None
    for entry in config.load_vaults():
        if entry["name"] == vault_name:
            return entry["path"]
    return None


def resolve_wikilink(vault_name: str, relative_path: str) -> str | None:
    """Resolve a vault-prefixed wikilink to an absolute .md file path.

    *vault_name* is the vault name extracted from a wikilink like
    ``[[VaultName>sub/Page]]``.  *relative_path* is the path component
    (e.g. ``"sub/Page"``).

    Returns the absolute path to the ``.md`` file, or ``None`` if the
    vault is not found or the relative path is empty.
    """
    if not relative_path:
        return None
    vault_path = resolve_vault_path(vault_name)
    if vault_path is None:
        return None
    return os.path.join(vault_path, relative_path) + ".md"


def find_vault_name_for_path(file_path: str) -> str | None:
    """Return the vault name for a file path, or ``None``.

    Looks up the vault whose ``path`` matches or contains *file_path*.
    """
    file_path = os.path.abspath(file_path)
    for entry in config.load_vaults():
        vp = os.path.abspath(entry["path"])
        if file_path == vp or file_path.startswith(vp + os.sep):
            return entry["name"]
    return None


def find_vault_for_dir(dir_path: str, vault_paths: list[str]) -> str | None:
    """Return the vault root that contains *dir_path*, or ``None``.

    *dir_path* is a directory path (not a file path).  This is the intended
    API for callers that already have a directory.
    """
    for v in vault_paths:
        if dir_path == v or dir_path.startswith(v + os.sep):
            return v
    return None
