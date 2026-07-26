"""Validation helpers for vault tree operations.

Pure-logic functions for validating inline rename and drag-drop
operations. No GTK dependencies.
"""

import os
from pathlib import Path


def validate_rename(
    new_name: str,
    old_name: str,
    sibling_names: list[str],
    is_vault_root: bool = False,
    target_exists: bool = False,
) -> str | None:
    """Validate a proposed new filename.

    Returns an error message string on failure, ``None`` on success.
    """
    if not new_name or not new_name.strip():
        return "Name cannot be empty."

    if new_name.strip() != new_name:
        return "Name cannot have leading/trailing whitespace."

    # Check for both Unix and Windows path separators.
    if "/" in new_name or "\\" in new_name:
        return "Name cannot contain path separators."

    if new_name == old_name:
        return "Name is unchanged."

    if target_exists:
        return "A file with this name already exists."

    # Case-insensitive duplicate check against siblings.
    lower_new = new_name.lower()
    for sibling in sibling_names:
        if sibling.lower() == lower_new:
            return "A file with this name already exists (case-insensitive)."

    # Vault roots cannot be renamed.
    if is_vault_root:
        return "Vault root directory cannot be renamed."

    return None


def validate_drop(
    source_path: str,
    target_dir: str,
    target_is_dir: bool,
) -> str | None:
    """Validate a drag-and-drop move operation.

    Returns an error message string on failure, ``None`` on success.
    """
    if not target_is_dir:
        return "Cannot drop onto a file."

    # Cannot drop onto self.
    if source_path == target_dir:
        return "Cannot move a directory into itself."

    # Cannot drop into own child directory (would create cycle).
    if target_dir.startswith(source_path + os.sep):
        return "Cannot move a directory into its own subdirectory."

    # Check for name collision in target directory.
    source_name = Path(source_path).name
    dest_path = Path(target_dir) / source_name
    if dest_path.exists():
        return "A file or folder with this name already exists in the target."

    # If moving to same parent, check for name collision.
    if Path(source_path).parent == Path(target_dir):
        return "Source and destination are the same."

    return None


def validate_new_item(name: str, parent_dir: str) -> str | None:
    """Validate a proposed name for a new file or folder.

    Prevents path traversal attacks and ensures the resolved path stays
    within *parent_dir* (the vault directory).  Path separators are
    allowed to support subdirectory creation (e.g. ``sub/note.md``).

    Returns an error message string on failure, ``None`` on success.
    """
    if not name or not name.strip():
        return "Name cannot be empty."

    if name.strip() != name:
        return "Name cannot have leading/trailing whitespace."

    # Reject absolute paths — os.path.join ignores the left side.
    if os.path.isabs(name):
        return "Name cannot be an absolute path."

    # Reject path-traversal segments.
    parts = Path(name).parts
    for part in parts:
        if part == "..":
            return "Name cannot contain path traversal (..)."

    # Ensure the resolved path stays within parent_dir.
    try:
        resolved = str(Path(parent_dir) / name)
        parent_resolved = os.path.realpath(parent_dir)
        final_resolved = os.path.realpath(resolved)
    except (OSError, ValueError):
        return "Invalid path."

    if not (final_resolved == parent_resolved or
            final_resolved.startswith(parent_resolved + os.sep)):
        return "Name would create a file outside the vault."

    return None