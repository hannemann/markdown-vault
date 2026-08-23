"""File-event routing between VaultMonitor and consumer modules.

Provides a dedicated ``FileEventDispatcher`` that decouples
``VaultMonitor`` / ``MonitorHandler`` from the consumer modules
(MainWindow, Sidebar, VaultTree).

The dispatcher emits typed ``FileEvent`` objects so that each consumer
can react independently without importing one another.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Protocol

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────


class FileEvent(NamedTuple):
    """Immutable file-system event payload.

    Attributes
    ----------
    vault_path : str
        Root path of the affected vault.
    file_path : str
        Path of the file that was affected.
    event_type : str
        One of ``'created'``, ``'deleted'``, ``'moved'``,
        ``'renamed'``, ``'content_changed'``.
    other_path : str | None
        Original path for ``moved`` / ``renamed`` events, or ``None``.
    """

    vault_path: str
    file_path: str
    event_type: str
    other_path: str | None = None


class SidebarRefresher(Protocol):
    """Structural interface for objects that can refresh a sidebar.

    Uses ``Protocol`` (structural subtyping) rather than ``ABC``
    to avoid metaclass conflicts with GTK / GObject classes.
    """

    def refresh(self, event: FileEvent) -> None:
        """Refresh the sidebar in response to *event*."""
        ...


# ── Dispatcher ────────────────────────────────────────────────────


class FileEventDispatcher:
    """Central event router between VaultMonitor and consumer modules.

    Parameters
    ----------
    sidebar_refresher : SidebarRefresher
        Object that implements ``refresh(event)``.
    backlink_index : object
        Index that tracks wikilinks between files (updated on idle).
    file_index : object
        Index that maps file stems to paths.
    debug_fn : callable
        Debug-dump helper (same signature as ``MainWindow._dump_debug``).
    """

    def __init__(
        self,
        sidebar_refresher: SidebarRefresher,
        backlink_index,
        file_index,
        debug_fn,
    ) -> None:
        self._sidebar_refresher = sidebar_refresher
        self._backlink_index = backlink_index
        self._file_index = file_index
        self._debug_fn = debug_fn

    # ── Event entry points ──────────────────────────────────────

    def on_file_created(self, vault_path: str, file_path: str) -> None:
        """Handle file-created event from VaultMonitor."""
        self._sidebar_refresher.refresh(
            FileEvent(vault_path, file_path, "created"),
        )

    def on_file_deleted(self, vault_path: str, file_path: str) -> None:
        """Handle file-deleted event from VaultMonitor."""
        self._sidebar_refresher.refresh(
            FileEvent(vault_path, file_path, "deleted"),
        )

    def on_file_moved(
        self,
        vault_path: str,
        file_path: str,
        other_path: str | None = None,
    ) -> None:
        """Handle file-moved event from VaultMonitor.

        Convention: *file_path* = new path, *other_path* = old path.
        When *other_path* is ``None`` the file came from outside
        (MOVED_IN).
        """
        self._sidebar_refresher.refresh(
            FileEvent(vault_path, file_path, "moved", other_path),
        )

    def on_content_changed(self, vault_path: str, file_path: str) -> None:
        """Handle content-changed event from VaultMonitor."""
        self._sidebar_refresher.refresh(
            FileEvent(vault_path, file_path, "content_changed"),
        )
