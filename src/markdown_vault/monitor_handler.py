"""Orchestration of VaultMonitor signals.

Extracted from ``app_window.py`` to decouple MainWindow from the details
of file-index / backlink-index / vault-tree / tab management.

MainWindow only holds a ``MonitorHandler`` reference and connects the
four VaultMonitor signals to ``handler.on_file_created``, etc.
Banner logic stays in MainWindow because it needs a UI reference.
"""

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib

from .event_router import FileEventDispatcher

logger = logging.getLogger(__name__)


class MonitorHandler:
    """Orchestrates file-system events from VaultMonitor.

    Parameters
    ----------
    backlink_index : BacklinkIndex
        Tracks wikilinks between files.
    file_index : FileIndex
        Maps file stems to paths.
    vault_tree : VaultTree
        Left-panel file tree widget.
    tab_bar : TabBar
        Tab management (close, rename, path updates).
    dispatcher : FileEventDispatcher
        Event dispatcher that refreshes consumers (Sidebar, etc.).
    debug_fn : callable
        Debug-dump helper (same signature as ``MainWindow._dump_debug``).
    """

    def __init__(
        self,
        backlink_index,
        file_index,
        vault_tree,
        tab_bar,
        dispatcher: FileEventDispatcher,
        debug_fn,
        notify_banner_cb=None,
    ) -> None:
        self._backlink_index = backlink_index
        self._file_index = file_index
        self._vault_tree = vault_tree
        self._tab_bar = tab_bar
        self._dispatcher = dispatcher
        self._debug_fn = debug_fn
        self._notify_banner_cb = notify_banner_cb

    # ── Signal handlers ────────────────────────────────────────────

    def on_file_created(self, vault_path: str, file_path: str) -> None:
        """Handle file created event from VaultMonitor."""
        self._vault_tree._handle_file_created(vault_path, file_path)
        if not file_path.endswith(".md"):
            return
        self._file_index.add_file(file_path, vault=vault_path)
        self._debug_fn(["file_index", "vault_tree"])

        # Update backlink index on idle (file I/O — don't block signal chain)
        def _update_backlink():
            try:
                text = Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            self._backlink_index.update_file(file_path, text)
            self._debug_fn(["backlink_index"])
            return False

        GLib.idle_add(_update_backlink)

    def on_file_deleted(self, vault_path: str, file_path: str) -> None:
        """Handle file deleted event from VaultMonitor."""
        self._vault_tree._handle_file_deleted(file_path)
        if not file_path.endswith(".md"):
            # Directory deleted — close tabs inside it and purge ALL index
            # entries for .md files under the directory (R9.2).
            prefix = file_path + os.sep
            for path in list(self._tab_bar.get_all_paths()):
                if path.startswith(prefix):
                    self._tab_bar.close_tab(path)
                    self._backlink_index.remove_wikilinks(Path(path).stem)
                    self._backlink_index.remove_file(path)
                    self._file_index.remove_file(path)
            # Purge non-open .md files from indexes
            self._purge_index_prefix(self._file_index, prefix)
            self._purge_index_prefix(self._backlink_index, prefix)
            self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs"])
            self._dispatcher.on_file_deleted(vault_path, file_path)
            return

        # Remove wikilinks BEFORE index update
        self._backlink_index.remove_wikilinks(Path(file_path).stem)
        self._backlink_index.remove_file(file_path)
        self._file_index.remove_file(file_path)
        if file_path in self._tab_bar.get_all_paths():
            self._tab_bar.close_tab(file_path)
        self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs"])
        self._dispatcher.on_file_deleted(vault_path, file_path)

    def on_file_moved(
        self,
        vault_path: str,
        file_path: str,
        other_path: str | None = None,
    ) -> None:
        """Handle file moved event from VaultMonitor.

        Convention: *file_path* = new path, *other_path* = old path.
        When *other_path* is ``None`` the file came from outside (MOVED_IN).

        Distinguishes three cases:

        1. **Genuine rename** — *other_path* is a tracked file (open tab or
           in the file index). Update index paths and tree (existing behaviour).

        2. **Atomic save** — *other_path* is an untracked temp file, but
           *file_path* is tracked (open tab or in the index). Treat as a
           content replacement: refresh index and trigger the reload banner.

        3. **New file** — neither path is tracked. Treat as a file creation
           (same as MOVED_IN without *other_path*).
        """
        if other_path is not None:
            source_tracked = (
                other_path in self._tab_bar.get_all_paths()
                or self._file_index.has_path(other_path)
            )
            dest_tracked = (
                file_path in self._tab_bar.get_all_paths()
                or self._file_index.has_path(file_path)
            )

            if source_tracked:
                self._handle_genuine_rename(vault_path, file_path, other_path)
            elif dest_tracked:
                self._notify_external_change(vault_path, file_path)
            else:
                self._handle_new_file_from_move(vault_path, file_path)
        else:
            self._handle_new_file_from_move(vault_path, file_path)

    def on_content_changed(self, vault_path: str, file_path: str) -> None:
        """Handle content-changed event from VaultMonitor."""
        def _update():
            try:
                text = Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            self._backlink_index.update_file(file_path, text)
            self._debug_fn(["backlink_index", "preview_html", "sidebar"])
            self._dispatcher.on_content_changed(vault_path, file_path)
            return False

        GLib.idle_add(_update)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _purge_index_prefix(index, prefix: str) -> None:
        """Remove all entries from *index* whose path starts with *prefix*.

        Handles both ``FileIndex`` (``_path_to_stem`` → ``remove_file``)
        and ``BacklinkIndex`` (``_source_to_targets`` → ``_remove_source``).
        """
        path_to_stem = getattr(index, "_path_to_stem", None)
        if path_to_stem is not None:
            for p in list(path_to_stem.keys()):
                if p.startswith(prefix):
                    index.remove_file(p)
            return
        source_to_targets = getattr(index, "_source_to_targets", None)
        if source_to_targets is not None:
            for p in list(source_to_targets.keys()):
                if p.startswith(prefix):
                    if hasattr(index, "_remove_source"):
                        index._remove_source(p)
                    else:
                        index.remove_source(p)

    def _handle_genuine_rename(
        self, vault_path: str, file_path: str, other_path: str
    ) -> None:
        old_stem = Path(other_path).stem
        new_stem = Path(file_path).stem
        self._backlink_index.rename_wikilinks(old_stem, new_stem)
        self._backlink_index.rename_file(other_path, file_path)
        self._file_index.rename_file(other_path, file_path)
        new_parent = str(Path(file_path).parent)
        self._vault_tree._handle_file_moved(other_path, new_parent, file_path)
        if other_path in self._tab_bar.get_all_paths():
            self._tab_bar.update_path(other_path, file_path)
        self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs"])
        self._dispatcher.on_file_moved(vault_path, file_path, other_path)

    def _notify_external_change(self, vault_path: str, file_path: str) -> None:
        """Treat as a content replacement: refresh index + banner."""
        self._file_index.add_file(file_path, vault=vault_path)
        if self._notify_banner_cb is not None:
            self._notify_banner_cb(vault_path, file_path)

        def _update():
            try:
                text = Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            self._backlink_index.update_file(file_path, text)
            self._debug_fn(["backlink_index", "preview_html", "sidebar"])
            self._dispatcher.on_content_changed(vault_path, file_path)
            return False

        GLib.idle_add(_update)

    def _handle_new_file_from_move(self, vault_path: str, file_path: str) -> None:
        self._vault_tree._handle_file_created(vault_path, file_path)
        if file_path.endswith(".md"):
            self._file_index.add_file(file_path, vault=vault_path)
            self._debug_fn(["file_index", "vault_tree"])

            def _update_backlink():
                try:
                    text = Path(file_path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                self._backlink_index.update_file(file_path, text)
                self._debug_fn(["backlink_index"])
                return False

            GLib.idle_add(_update_backlink)
