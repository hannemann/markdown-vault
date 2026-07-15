"""Path utilities for vault and file operations."""

import os
from pathlib import Path


def find_vault_for_path(file_path: str, vault_paths: list[str]) -> str | None:
    """Return the vault root that contains *file_path*, or ``None``.

    Checks whether the parent directory of *file_path* equals or is a
    subdirectory of any vault root.
    """
    file_parent = str(Path(file_path).parent)
    for v in vault_paths:
        if file_parent == v or file_parent.startswith(v + os.sep):
            return v
    return None