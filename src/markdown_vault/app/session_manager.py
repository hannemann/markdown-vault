"""Markdown Vault — session persistence manager.

Extracted from ``MainWindow`` to separate session save/restore logic from
the monolithic window class.  The ``SessionManager`` orchestrates saving
and restoring window state, tab data, and per-vault sessions.
"""

import logging
from pathlib import Path

from markdown_vault.core import session

logger = logging.getLogger(__name__)


def _number(value):
    """A number from persisted JSON, or ``None`` for anything else — a corrupted
    ``"editor_scroll": "x"`` must not reach ``restore_scroll_position``. ``bool`` is
    not a number here (``True`` is not a scroll offset). Mirrors the filter in
    :meth:`NavEntry.from_state`."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


class SessionManager:
    """Manages session save and restore operations.

    Parameters
    ----------
    get_window_state : callable
        Returns a dict with window geometry and layout state:
        ``width``, ``height``, ``sidebar_visible``, ``expanded_vaults``,
        ``search_visible``, ``search_paned_position``,
        ``sidebar_paned_position``, ``main_paned_position``.
    tab_bar : TabBar
        The tab bar widget that owns tab state.
    mru_manager : MRUManager
        MRU tracking for tab switch order.
    """

    def __init__(self, *, get_window_state, tab_bar, mru_manager) -> None:
        self._get_window_state = get_window_state
        self._tab_bar = tab_bar
        self._mru = mru_manager

    # ── Collect ────────────────────────────────────────────────────

    def collect_tab_data(self, content_stack) -> list[dict]:
        """Gather per-tab state for session saving."""
        data = []
        for path in self._tab_bar.get_all_paths():
            tab = self._tab_bar.get_tab(path)
            if not tab:
                continue
            split_pos = 600
            child = content_stack.get_child_by_name(path)
            paned = self._find_paned(child)
            if paned is not None:
                split_pos = paned.get_position()
            editor_scroll, editor_cursor = tab.editor.capture_scroll_position()
            data.append({
                "path": path,
                "view_mode": tab.view_mode,
                "split_position": split_pos,
                "editor_zoom": tab.editor.zoom_factor,
                "preview_zoom": tab.preview.zoom_level,
                # Reading position, so a restart lands where the reader was.
                # For every tab: a background tab keeps its scroll (measured), a
                # never-shown one reports 0 (no position to remember).
                "editor_scroll": editor_scroll,
                "editor_cursor": editor_cursor,
                "preview_scroll": tab.preview.preview_scroll_position(),
            })
        return data

    @staticmethod
    def _find_paned(widget):
        """Find the first Gtk.Paned descendant (or self)."""
        from gi.repository import Gtk
        if isinstance(widget, Gtk.Paned):
            return widget
        if isinstance(widget, Gtk.Box):
            for child in widget:
                paned = SessionManager._find_paned(child)
                if paned is not None:
                    return paned
        return None

    # ── Save ───────────────────────────────────────────────────────

    def save_session(self, active_vault, content_stack) -> None:
        """Persist the current window state to disk."""
        ses = session.load_session()
        vault_sessions = ses.get("vault_sessions", {})
        if active_vault:
            vault_sessions[active_vault] = {
                "tabs": self.collect_tab_data(content_stack),
                "active_tab": self._tab_bar.get_current_path(),
                "mru": self._mru.tabs,
            }
        ws = self._get_window_state()
        session.save_session(
            width=ws["width"],
            height=ws["height"],
            sidebar_visible=ws["sidebar_visible"],
            active_vault=active_vault,
            vault_sessions=vault_sessions,
            expanded_vaults=ws["expanded_vaults"],
            search_visible=ws["search_visible"],
            search_paned_position=ws["search_paned_position"],
            sidebar_paned_position=ws["sidebar_paned_position"],
            main_paned_position=ws["main_paned_position"],
            nav_history=ws.get("nav_history"),
            ask_last_question=ws.get("ask_last_question", ""),
        )

    def save_vault_session(self, active_vault, content_stack) -> None:
        """Save the current vault's tab state to the session on disk."""
        if not active_vault:
            return
        self.save_session(active_vault, content_stack)

    # ── Restore ────────────────────────────────────────────────────

    def restore_vault_session(
        self,
        vault_path: str,
        *,
        open_file_fn,
        mru_push_fn,
        nav_target: str | None = None,
    ) -> None:
        """Restore tabs for *vault_path* from the persisted session.

        History is deliberately not touched here: opening the tabs and
        activating one both feed the global nav history, so the caller wraps the
        whole restore in a suppress clamp and pushes the "here I landed" entry
        itself (see ``_switch_vault_complete_phase3`` and the startup restore).

        Each tab's saved reading position is applied after it opens (the editor
        instantly, the preview armed for its render). *nav_target* names the one
        tab whose position must **not** be applied here: a cross-vault
        back/forward restores that tab from the history instead
        (``post_open_fn`` → ``ScrollMemory.restore_current``), and applying the
        tab entry too would move it twice. At startup ``nav_target`` is ``None``
        and the history is not even loaded yet, so every tab restores from its
        own entry — the only source there.
        """
        ses = session.load_session()
        vault_data = ses.get("vault_sessions", {}).get(vault_path, {})
        vault_data = session.prune_vault_session(vault_data)
        for tab_data in vault_data.get("tabs", []):
            fp = tab_data.get("path", "")
            if fp and Path(fp).exists():
                open_file_fn(
                    fp,
                    view_mode=tab_data.get("view_mode", "edit"),
                    split_position=tab_data.get("split_position", 600),
                    editor_zoom=tab_data.get("editor_zoom", 1.0),
                    preview_zoom=tab_data.get("preview_zoom", 1.0),
                )
                if fp != nav_target:
                    self._restore_tab_scroll(fp, tab_data)
        active_tab = vault_data.get("active_tab")
        if active_tab and active_tab in self._tab_bar.get_all_paths():
            self._tab_bar.set_active_tab(active_tab)
        # Restore MRU from session.
        mru_data = vault_data.get("mru", [])
        if mru_data:
            for fp in reversed(mru_data):
                if fp in self._tab_bar.get_all_paths():
                    mru_push_fn(fp)
        else:
            # Fallback: rebuild MRU from tab order.
            for tab_data in reversed(vault_data.get("tabs", [])):
                fp = tab_data.get("path", "")
                if fp and fp in self._tab_bar.get_all_paths():
                    mru_push_fn(fp)
            if active_tab and active_tab in self._tab_bar.get_all_paths():
                mru_push_fn(active_tab)

    def _restore_tab_scroll(self, file_path: str, tab_data: dict) -> None:
        """Apply *file_path*'s saved reading position to its tab. The editor is
        set instantly (a restart is a note load, not an in-page hop); the preview
        scroll is armed so it fires on the note's render (``FINISHED``). Broken or
        missing values are skipped — an old session without the fields, or a
        corrupted one, restores nothing rather than crashing."""
        tab = self._tab_bar.get_tab(file_path)
        if tab is None:
            return
        scroll = _number(tab_data.get("editor_scroll"))
        cursor = _number(tab_data.get("editor_cursor"))
        if scroll is not None or cursor is not None:
            tab.editor.restore_scroll_position(
                scroll, int(cursor) if cursor is not None else None)
        preview_scroll = _number(tab_data.get("preview_scroll"))
        if preview_scroll is not None:
            tab.preview.arm_scroll(preview_scroll)
