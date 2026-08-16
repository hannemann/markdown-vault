"""Content change handler for external file modifications.

Shows a warning banner when a file is modified externally while open
in a tab, and lets the user reload or dismiss the change.
"""

import logging
from pathlib import Path

from markdown_vault.uikit.dialogs import show_error

logger = logging.getLogger(__name__)


class ContentChangeHandler:
    """Handles external content change detection and user response.

    Parameters
    ----------
    tab_bar : TabBar
        Tab bar widget providing access to tabs and banner management.
    parent : Gtk.Widget
        Parent window for error dialogs.
    """

    def __init__(self, tab_bar, parent=None) -> None:
        self._tab_bar = tab_bar
        self._parent = parent

    def handle_external_change(self, file_path: str) -> None:
        """React to an external modification of an open file.

        A **clean** tab (no unsaved edits) is reloaded silently so its buffer
        stays in sync with disk — no banner, no interruption. A tab with
        **unsaved edits** is a real conflict: show the reload/dismiss banner and
        flag the tab so autosave cannot clobber the external change before the
        user resolves it.
        """
        if file_path not in self._tab_bar.get_all_paths():
            return
        tab = self._tab_bar.get_tab(file_path)
        if tab is None:
            return
        if not tab.editor.is_modified:
            self.reload_content(file_path)
            return
        tab.external_change_pending = True
        name = Path(file_path).name
        self._tab_bar.show_warning_banner(
            file_path,
            f'"{name}" was modified externally.',
            buttons=[
                ("Reload", lambda: self.reload_content(file_path)),
                ("Dismiss", lambda: self.dismiss_content(file_path)),
            ],
        )

    def reload_content(self, file_path: str) -> None:
        """Reload external content and refresh preview.

        Shows an error dialog on I/O failure; the banner stays visible
        so the user can retry.
        """
        tab = self._tab_bar.get_tab(file_path)
        if not tab:
            return
        if not tab.reload_editor(file_path):
            name = Path(file_path).name
            logger.warning("Reload failed for %s", file_path, exc_info=True)
            if self._parent is not None:
                show_error(
                    self._parent,
                    "Reload Failed",
                    f"Could not read \"{name}\" from disk.",
                )
            return
        tab.preview.update_from_text(
            tab.editor.get_text(),
            str(Path(tab.editor.file_path).parent) if tab.editor.file_path else "",
            tab.editor.file_path or "",
        )
        tab.external_change_pending = False
        self._tab_bar.hide_warning_banner(file_path)

    def dismiss_content(self, file_path: str) -> None:
        """Dismiss the banner without reloading.

        The user chose to keep their version; clear the conflict flag so
        autosave resumes for this tab (saving will overwrite the external
        change — their explicit choice).
        """
        tab = self._tab_bar.get_tab(file_path)
        if tab is not None:
            tab.external_change_pending = False
        self._tab_bar.hide_warning_banner(file_path)
