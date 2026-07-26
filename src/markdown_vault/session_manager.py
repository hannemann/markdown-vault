"""Markdown Vault — session persistence manager.

Extracted from ``MainWindow`` to separate session save/restore logic from
the monolithic window class.  The ``SessionManager`` orchestrates saving
and restoring window state, tab data, and per-vault sessions.
"""

import logging
from pathlib import Path

from . import session

logger = logging.getLogger(__name__)


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
        from gi.repository import Gtk
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
            data.append({
                "path": path,
                "view_mode": tab.view_mode,
                "split_position": split_pos,
                "editor_zoom": tab.editor.zoom_factor,
                "preview_zoom": tab.preview.zoom_level,
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
        push_history_fn,
        suppress_nav_fn,
        mru_push_fn,
    ) -> None:
        """Restore tabs for *vault_path* from the persisted session."""
        ses = session.load_session()
        vault_data = ses.get("vault_sessions", {}).get(vault_path, {})
        vault_data = session.prune_vault_session(vault_data)
        suppress_nav_fn(True)
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
        suppress_nav_fn(False)
        active_tab = vault_data.get("active_tab")
        if active_tab and active_tab in self._tab_bar.get_all_paths():
            self._tab_bar.set_active_tab(active_tab)
            push_history_fn(active_tab)
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
