"""Filesystem operations for vault files and folders.

Decoupled from MainWindow — handles creating files, creating folders,
and resolving the active vault.  All methods are pure filesystem logic;
UI concerns (dialogs, tree refresh, tab cleanup) stay in the caller.
"""

import logging
import os
import shutil
from pathlib import Path

from markdown_vault.core import path_utils
from markdown_vault.core.i18n import _

logger = logging.getLogger(__name__)


class FileOps:
    """Filesystem operations for vault items.

    Parameters
    ----------
    skip_fn : callable
        ``(path) -> None`` — notify the vault monitor to skip the next
        event for *path* (prevents external-change detection for
        user-initiated operations).
    """

    def __init__(self, skip_fn) -> None:
        self._skip_fn = skip_fn

    def resolve_active_vault(
        self, tab, tree_selected_path: str | None, active_vault: str | None,
        vaults: list[str],
    ) -> str:
        """Determine the vault root for a new file.

        Priority: open tab's file → tree selection → *active_vault* → last vault.
        """
        # 1. Derive from the currently open tab's file path.
        if tab and tab.editor.file_path:
            file_parent = str(Path(tab.editor.file_path).parent)
            result = path_utils.find_vault_for_dir(file_parent)
            if result:
                return result

        # 2. Derive from vault tree selection.
        if tree_selected_path:
            result = path_utils.find_vault_for_dir(tree_selected_path)
            if result:
                return result

        # 3. Fallback to stored active vault if valid.
        if active_vault and active_vault in vaults:
            return active_vault

        # 4. Last resort: most recently added vault.
        return vaults[-1]

    def create_file(self, vault_path: str, name: str) -> str | None:
        """Create a new .md file in *vault_path*.

        Handles intermediate directory creation and vault-monitor skip
        events.  Returns an error message on failure, ``None`` on success.
        """
        if not name.endswith(".md"):
            name += ".md"

        file_path = os.path.join(vault_path, name)
        # Guard the computed target (after the .md-append above), BEFORE makedirs — a
        # name with ../ or an absolute path must not create or touch anything outside
        # the vault. Lexical check, matching the rest of the app; symlink escapes are a
        # separate, app-wide concern (see the Security ticket).
        if not path_utils.path_is_within(vault_path, file_path):
            logger.warning("create_file: rejected name escaping the vault: %r", name)
            return _("Invalid file name: path escapes the vault")
        parent = str(Path(file_path).parent)

        if parent != vault_path:
            # Intermediate directories needed.
            try:
                os.makedirs(parent, exist_ok=True)
                self._skip_fn(parent)
            except (OSError, ValueError) as e:  # ValueError: embedded NUL byte in the name
                logger.warning("create_file: makedirs failed for %s: %s", parent, e)
                return str(e)
            # _emit_existing_entries fires 1 CREATED for the file when the
            # new directory monitor starts — only 1 skip needed.
            self._skip_fn(file_path)
        else:
            # touch() fires created + changed on existing monitor — need 2.
            self._skip_fn(file_path)
            self._skip_fn(file_path)

        try:
            Path(file_path).touch()
        except (OSError, ValueError) as e:  # ValueError: embedded NUL byte in the name
            logger.warning("create_file: touch failed for %s: %s", file_path, e)
            return str(e)

        logger.info("create_file: created %s", file_path)
        return None

    def create_folder(self, vault_path: str, name: str) -> str | None:
        """Create a new folder in *vault_path*.

        Returns an error message on failure, ``None`` on success.
        """
        folder_path = os.path.join(vault_path, name)
        # Guard BEFORE mkdir — unlike create_file, no .md is appended, so a bare '..'
        # already escapes here. Lexical check (see create_file).
        if not path_utils.path_is_within(vault_path, folder_path):
            logger.warning("create_folder: rejected name escaping the vault: %r", name)
            return _("Invalid folder name: path escapes the vault")
        try:
            os.mkdir(folder_path)
        except (OSError, ValueError) as e:  # ValueError: embedded NUL byte in the name
            logger.warning("create_folder: mkdir failed for %s: %s", folder_path, e)
            return str(e)

        logger.info("create_folder: created %s", folder_path)
        return None

    @staticmethod
    def delete_path(path: str) -> str | None:
        """Delete a file or directory.

        Returns an error message on failure, ``None`` on success.
        """
        try:
            if Path(path).is_dir():
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as e:
            logger.warning("delete_path: failed for %s: %s", path, e)
            return str(e)

        logger.info("delete_path: deleted %s", path)
        return None
