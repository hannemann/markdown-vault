"""Markdown Vault — tab lifecycle orchestrator.

Extracted from ``MainWindow`` to separate tab creation, switching, and
navigation from the monolithic window class.  The ``TabOrchestrator``
does **not** own any widgets — it operates on the ``TabBar`` and
``MRUManager`` instances passed in at construction time.
"""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk

from .editor import Editor
from .preview import Preview
from . import banners as banner_mod

logger = logging.getLogger(__name__)


class TabOrchestrator:
    """Orchestrates tab creation, switching and navigation.

    Parameters
    ----------
    tab_bar : TabBar
        The tab bar widget that owns tab state.
    mru_manager : MRUManager
        MRU tracking for tab switch order.
    sidebar : Sidebar
        Sidebar widget for file-metadata display.
    settings : dict
        Application settings (must contain ``tab_switch_mode``).
    content_stack : Gtk.Stack
        Stack that shows the content widget for each open tab.
    file_index : FileIndex
        Stem-to-path index for wikilink resolution.
    backlink_index : BacklinkIndex
        Backlink index kept in sync on file edits.
    vault_tree : VaultTree
        Used to query vault root paths for the preview widget.
    callbacks : dict
        Mapping of callback names to callables.  Required keys:

        - ``on_preview_link_clicked``
        - ``on_preview_link_not_found``
        - ``on_preview_checkbox_toggled``
        - ``on_editor_text_changed``
        - ``on_editor_modified``
        - ``apply_view_mode``
        - ``sync_view_toggle``
        - ``refresh_preview``
        - ``push_history``
        - ``on_banner_reload``
        - ``on_banner_dismiss``
        - ``dump_debug``
    """

    def __init__(
        self,
        tab_bar,
        mru_manager,
        sidebar,
        settings: dict,
        content_stack: Gtk.Stack,
        file_index,
        backlink_index,
        vault_tree,
        callbacks: dict,
    ) -> None:
        self._tab_bar = tab_bar
        self._mru = mru_manager
        self._sidebar = sidebar
        self._settings = settings
        self._content_stack = content_stack
        self._file_index = file_index
        self._backlink_index = backlink_index
        self._vault_tree = vault_tree
        self._cb = callbacks

    # ── helpers ────────────────────────────────────────────────────

    def _cb_call(self, name: str, *args, **kwargs):
        """Invoke a registered callback by name."""
        fn = self._cb.get(name)
        if fn is not None:
            return fn(*args, **kwargs)
        logger.warning("TabOrchestrator: missing callback %r", name)
        return None

    # ── signal forwarding (used as GTK signal handlers) ────────────

    def _on_preview_link_clicked(self, _widget, file_path: str) -> None:
        """Forward preview ``link-clicked`` signal to MainWindow callback."""
        self._cb_call("on_preview_link_clicked", _widget, file_path)

    def _on_preview_link_not_found(self, _widget, path_str: str) -> None:
        """Forward preview ``link-not-found`` signal to MainWindow callback."""
        self._cb_call("on_preview_link_not_found", _widget, path_str)

    def _on_preview_checkbox_toggled(self, _widget, line: int, checked: bool) -> None:
        """Forward preview ``checkbox-toggled`` signal to MainWindow callback."""
        self._cb_call("on_preview_checkbox_toggled", _widget, line, checked)

    def _on_editor_text_changed(self, editor) -> None:
        """Forward editor ``text-changed`` signal to MainWindow callback."""
        self._cb_call("on_editor_text_changed", editor)

    def _on_editor_modified(self, editor, dirty: bool) -> None:
        """Forward editor ``modified-changed`` signal to MainWindow callback."""
        self._cb_call("on_editor_modified", editor, dirty)

    # ── tab creation ───────────────────────────────────────────────

    def open_tab(
        self,
        file_path: str,
        *,
        view_mode: str | None = None,
        split_position: int = 600,
        editor_zoom: float = 1.0,
        preview_zoom: float = 1.0,
        from_nav: bool = False,
    ) -> None:
        """Open *file_path* in a new or existing tab.

        When *view_mode* is ``None`` the current tab's view mode is
        inherited (or ``"edit"`` when no tab exists yet).  Session restore
        passes an explicit mode so it stays independent.  *from_nav* is
        ``True`` for programmatic back/forward navigation and suppresses
        history pushes.
        """
        # Activate existing tab if already open.
        for path in self._tab_bar.get_all_paths():
            if path == file_path:
                self._tab_bar.set_active_tab(file_path)
                if not from_nav:
                    self._cb_call("push_history", file_path)
                return

        if view_mode is None:
            cur = self._tab_bar.get_current_tab()
            view_mode = cur.view_mode if cur else "edit"

        editor = Editor(
            base_font_size=self._settings.get("editor_font_size", 14),
            tab_width=self._settings.get("editor_tab_width", 4),
            wrap_text=self._settings.get("editor_wrap_text", True),
        )
        preview = Preview()
        preview.connect("link-clicked", self._on_preview_link_clicked)
        preview.connect("link-not-found", self._on_preview_link_not_found)
        preview.connect("checkbox-toggled", self._on_preview_checkbox_toggled)

        editor.open_file(file_path)

        # Apply per-tab zoom.
        editor.zoom_factor = editor_zoom
        preview.zoom_level = preview_zoom

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.set_start_child(editor)
        split.set_end_child(preview)
        split.set_position(split_position)
        split.set_vexpand(True)

        # Warning banner (external changes)
        warning_revealer, warning_box = banner_mod.create_banner(
            banner_type="warning",
        )
        warning_box.add_button(
            "Reload",
            lambda: self._cb_call("on_banner_reload", file_path),
        )
        warning_box.add_button(
            "Dismiss",
            lambda: self._cb_call("on_banner_dismiss", file_path),
        )
        warning_box.connect(
            "dismissed",
            lambda _w: self._cb_call("on_banner_dismiss", file_path),
        )

        # Error banner (save failures)
        error_revealer, error_box = banner_mod.create_banner(
            banner_type="error",
        )

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrapper.append(warning_revealer)
        wrapper.append(error_revealer)
        wrapper.append(split)

        editor.connect("text-changed", self._on_editor_text_changed)

        self._content_stack.add_named(wrapper, file_path)

        tab = self._tab_bar.add_tab(
            file_path, editor, preview,
            warning_banner=warning_revealer, error_banner=error_revealer,
        )
        tab.view_mode = view_mode

        # Mark unsaved tabs with italic styling.
        tab.editor.connect("modified-changed", self._on_editor_modified)
        self._tab_bar._set_tab_unmodified(file_path, tab.editor.is_modified)

        # Sync the header toggle buttons to match the restored view mode.
        self._cb_call("sync_view_toggle", view_mode)

        self._content_stack.set_visible_child_name(file_path)
        self._cb_call("apply_view_mode")
        self._cb_call("refresh_preview")
        self._sidebar.update_for_file(file_path, editor.get_text())
        if not from_nav:
            self._cb_call("push_history", file_path)

    # ── tab change ─────────────────────────────────────────────────

    def on_tab_changed(self, file_path: str) -> None:
        """Handle a tab change event from TabBar.

        Updates the content stack, view mode, preview, sidebar and MRU.
        """
        tab = self._tab_bar.get_current_tab()
        if not tab:
            return
        self._content_stack.set_visible_child_name(file_path)
        self._cb_call("sync_view_toggle", tab.view_mode)
        self._cb_call("apply_view_mode")
        self._cb_call("refresh_preview")
        self._sidebar.update_for_file(file_path, tab.editor.get_text())
        self._cb_call("dump_debug", ["preview_html", "sidebar", "backlink_index"])
        self._cb_call("push_history", file_path)
        self._mru.push(file_path)

    # ── tab navigation ─────────────────────────────────────────────

    def next_tab(self) -> None:
        """Switch to the next tab (linear or MRU depending on settings)."""
        if self._settings.get("tab_switch_mode", "mru") == "mru":
            self._mru_next()
        else:
            self.cycle_tab(+1)

    def prev_tab(self) -> None:
        """Switch to the previous tab (linear or MRU depending on settings)."""
        if self._settings.get("tab_switch_mode", "mru") == "mru":
            self._mru_prev()
        else:
            self.cycle_tab(-1)

    def cycle_tab(self, direction: int) -> None:
        """Cycle through tabs linearly.

        *direction* is ``+1`` for forward, ``-1`` for backward.
        """
        paths = self._tab_bar.get_all_paths()
        if len(paths) < 2:
            return
        current = self._tab_bar.get_current_path()
        try:
            idx = paths.index(current)
        except ValueError:
            return
        self._tab_bar.set_active_tab(paths[(idx + direction) % len(paths)])

    # ── queries ────────────────────────────────────────────────────

    def get_tab_count(self) -> int:
        """Return the number of currently open tabs."""
        return len(self._tab_bar.get_all_paths())

    def is_single_tab(self) -> bool:
        """Return ``True`` if exactly one tab is open."""
        return self.get_tab_count() == 1

    # ── private ────────────────────────────────────────────────────

    def _mru_next(self) -> None:
        """Switch to the previously active tab (MRU next)."""
        target = self._mru.next()
        if target:
            self.open_tab(target, from_nav=True)

    def _mru_prev(self) -> None:
        """Switch forward in MRU list."""
        target = self._mru.prev()
        if target:
            self.open_tab(target, from_nav=True)
