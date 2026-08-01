"""Path utilities for vault and file operations."""

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import config

# Regex for parsing Markdown headings (h1-h6).
# Used by preview, sidebar, and search_logic — keep in one place.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def wikilink_url(vault_name: str, relative_path: str, fragment: str = "") -> str:
    """Build the canonical ``vault:`` URL for a wikilink target.

    The vault is always explicit — an empty *vault_name* is a bug and must
    never be produced.  *fragment* is optional and only used for in-file
    navigation (heading anchors), never for target resolution.
    """
    url = f"vault:{quote(vault_name, safe='')}?path={quote(relative_path, safe='/')}"
    if fragment:
        url += "#" + quote(fragment, safe="")
    return url


def parse_wikilink_url(uri: str) -> tuple[str, str, str]:
    """Parse a canonical ``vault:`` URL into ``(vault_name, relative_path, fragment)``.

    *relative_path* and *fragment* may be empty.  *vault_name* must not be
    empty — callers MUST treat an empty vault as an error (never fuzzy-resolve).
    """
    parsed = urlparse(uri)
    vault_name = unquote(parsed.path)
    relative_path = parse_qs(parsed.query).get("path", [""])[0]
    fragment = unquote(parsed.fragment)
    return vault_name, relative_path, fragment


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


def _path_is_within(vault_root: str, target: str) -> bool:
    """Return ``True`` if *target* is inside or equal to *vault_root*."""
    root = os.path.abspath(vault_root)
    target = os.path.abspath(target)
    return target == root or target.startswith(root + os.sep)


def find_vault_name_for_path(file_path: str) -> str | None:
    """Return the vault name for a file path, or ``None``.

    Looks up the vault whose ``path`` matches or contains *file_path*.
    """
    for entry in config.load_vaults():
        if _path_is_within(entry["path"], file_path):
            return entry["name"]
    return None


def find_vault_for_dir(dir_path: str, vault_paths: list[str] | None = None) -> str | None:
    """Return the vault root that contains *dir_path*, or ``None``.

    *dir_path* is a directory path (not a file path).  This is the intended
    API for callers that already have a directory.  By default the vault
    roots are taken from the config (SSOT); pass *vault_paths* to override.
    """
    roots = vault_paths if vault_paths is not None else [
        entry["path"] for entry in config.load_vaults()
    ]
    for v in roots:
        if _path_is_within(v, dir_path):
            return v
    return None
