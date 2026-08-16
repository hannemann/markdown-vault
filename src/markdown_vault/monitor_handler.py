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

from markdown_vault.core.event_router import FileEventDispatcher
from markdown_vault.core.path_utils import find_vault_name_for_path
from .vault_monitor import _is_valid_md_file

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
        self._file_index.add_file(file_path, vault_path=vault_path)
        self._debug_fn(["file_index", "vault_tree"])

        # Update backlink index on idle (file I/O — don't block signal chain)
        def _update_backlink():
            try:
                text = Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            self._backlink_index.update_file(file_path, text)
            # Refresh the sidebar so backlinks appear without a tab switch.
            # "created" refreshes the backlinks view (unlike "content_changed").
            self._dispatcher.on_file_created(vault_path, file_path)
            self._debug_fn(["backlink_index", "sidebar"])
            return False

        GLib.idle_add(_update_backlink)

    def on_file_deleted(self, vault_path: str, file_path: str) -> None:
        """Handle file deleted event from VaultMonitor."""
        self._vault_tree._handle_file_deleted(file_path)
        if not file_path.endswith(".md"):
            # Directory deleted — defer tab-close and index purge to idle
            # to avoid synchronous multi-WebView teardown (R13.2).
            prefix = file_path + os.sep

            def _cleanup_dir():
                all_paths = self._tab_bar.get_all_paths() if self._tab_bar else []
                for path in all_paths:
                    if path.startswith(prefix):
                        self._tab_bar.close_tab(path)
                        self._backlink_index.remove_wikilinks(path)
                        self._backlink_index.remove_file(path)
                        self._file_index.remove_file(path)
                self._purge_index_prefix(self._file_index, prefix)
                self._purge_index_prefix(self._backlink_index, prefix)
                self._dispatcher.on_file_deleted(vault_path, file_path)
                self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs", "sidebar"])
                return False

            GLib.idle_add(_cleanup_dir)
            return

        # Remove wikilinks BEFORE index update
        self._backlink_index.remove_wikilinks(file_path)
        self._backlink_index.remove_file(file_path)
        self._file_index.remove_file(file_path)
        if file_path in self._tab_bar.get_all_paths():
            self._tab_bar.close_tab(file_path)
        self._dispatcher.on_file_deleted(vault_path, file_path)
        self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs", "sidebar"])

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
           in the file index) or both paths are valid ``.md`` files under the
           vault (R14.3: subdirectory files are not in the root-only
           FileIndex). Update index paths and tree and rewrite backlinks.

        2. **Atomic save** — *other_path* is an untracked temp file, but
           *file_path* is tracked (open tab or in the index). Treat as a
           content replacement: refresh index and trigger the reload banner.

        3. **New file** — neither path is tracked. Treat as a file creation
           (same as MOVED_IN without *other_path*).
        """
        if other_path is not None:
            # A genuine move leaves the file at *file_path* (the new path).
            # If it isn't there, this is a stale/duplicate monitor event — e.g.
            # an app-initiated move that was already handled via the tree's
            # ``file-renamed`` signal and whose ``skip_next_event`` leaked, or a
            # reverse-direction event arriving late. Applying it would revert
            # live state (moving the tree node / backlinks back to a path that
            # no longer exists). Ignore it.
            if not os.path.exists(file_path):
                logger.debug(
                    "Ignoring stale moved event; new path missing: %s -> %s",
                    other_path, file_path,
                )
                return
            source_tracked = (
                other_path in self._tab_bar.get_all_paths()
                or self._file_index.has_path(other_path)
            )
            dest_tracked = (
                file_path in self._tab_bar.get_all_paths()
                or self._file_index.has_path(file_path)
            )

            # R14.3: FileIndex is root-only, so a subdirectory .md that is
            # not in an open tab is never "tracked" via has_path. A rename
            # whose source and destination are both valid .md files under
            # this vault is a genuine rename regardless of index tracking —
            # inbound backlinks must be rewritten, not left dangling.
            genuine_md_rename = (
                not source_tracked
                and self._is_vault_md(other_path, vault_path)
                and self._is_vault_md(file_path, vault_path)
            )

            if source_tracked or genuine_md_rename:
                self._handle_genuine_rename(vault_path, file_path, other_path)
            elif dest_tracked:
                self._notify_external_change(vault_path, file_path)
            else:
                self._handle_new_file_from_move(vault_path, file_path, other_path)
        else:
            self._handle_new_file_from_move(vault_path, file_path, None)

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
    def _is_vault_md(path: str, vault_path: str) -> bool:
        """Return ``True`` if *path* is a valid ``.md`` file under a vault.

        Checks the path against its actual vault **root** (via config), not the
        *vault_path* the monitor reported — that is the specific monitored
        subdirectory, so a cross-subdirectory rename's old path (e.g.
        ``inbox/x.md`` when the monitor fired on ``projects/``) would wrongly
        fail a ``startswith(vault_path)`` test and get misclassified as an
        atomic save (raising a false "modified externally" banner). Name-based
        and tolerant of the path no longer existing (a rename's old path is
        gone). Temp files (hidden / non-``.md``) are still rejected by
        ``_is_valid_md_file``, so atomic-save detection is preserved.
        """
        return (
            path is not None
            and _is_valid_md_file(path)
            and find_vault_name_for_path(path) is not None
        )

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
        if not file_path.endswith(".md"):
            # A tracked .md file renamed to a non-markdown extension is not
            # supported: treat it like a deletion so the tab closes, the tree
            # entry disappears and all index entries are purged (R9.3).
            self.on_file_deleted(vault_path, other_path)
            return
        self._backlink_index.rename_wikilinks(other_path, file_path)
        self._backlink_index.rename_file(other_path, file_path)
        self._file_index.rename_file(other_path, file_path)
        new_parent = str(Path(file_path).parent)
        self._vault_tree._handle_file_moved(other_path, new_parent, file_path)
        if other_path in self._tab_bar.get_all_paths():
            self._tab_bar.update_path(other_path, file_path)
        self._dispatcher.on_file_moved(vault_path, file_path, other_path)
        self._debug_fn(["file_index", "backlink_index", "vault_tree", "tabs", "sidebar"])

    def _notify_external_change(self, vault_path: str, file_path: str) -> None:
        """Treat as a content replacement: refresh index + banner."""
        self._file_index.add_file(file_path, vault_path=vault_path)
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

    def _handle_new_file_from_move(
        self, vault_path: str, file_path: str, other_path: str | None = None
    ) -> None:
        if other_path is not None and other_path.endswith(".md"):
            if not file_path.endswith(".md"):
                # A .md file renamed to a non-markdown extension is not
                # supported: treat it like a deletion — the tree entry is
                # removed and all index entries are purged (R9.3).
                self.on_file_deleted(vault_path, other_path)
                return
            # Untracked .md→.md rename: move the tree node and swap the old
            # index entries out for the new path.
            self._vault_tree._handle_file_moved(
                other_path, str(Path(file_path).parent), file_path,
            )
            self._file_index.remove_file(other_path)
            self._backlink_index.remove_file(other_path)
        else:
            self._vault_tree._handle_file_created(vault_path, file_path)
        if file_path.endswith(".md"):
            self._file_index.add_file(file_path, vault_path=vault_path)
            self._debug_fn(["file_index", "vault_tree"])

            def _update_backlink():
                try:
                    text = Path(file_path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                self._backlink_index.update_file(file_path, text)
                # Refresh the sidebar so backlinks appear without a tab switch.
                # "created" refreshes the backlinks view (unlike "content_changed").
                self._dispatcher.on_file_created(vault_path, file_path)
                self._debug_fn(["backlink_index", "sidebar"])
                return False

            GLib.idle_add(_update_backlink)
