"""Path utilities for vault and file operations."""

import os
from pathlib import Path


def find_vault_for_path(file_path: str, vault_paths: list[str]) -> str | None:
    """Return the vault root that contains *file_path*, or ``None``.

    Checks whether the parent directory of *file_path* equals or is a
    subdirectory of any vault root.

    .. deprecated:: 2026
       Use :func:`find_vault_for_dir` instead.  This function incorrectly
       takes a file path but compares its parent, causing confusion at call sites.
    """
    file_parent = str(Path(file_path).parent)
    for v in vault_paths:
        if file_parent == v or file_parent.startswith(v + os.sep):
            return v
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