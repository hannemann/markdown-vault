"""In-view find and replace (Ctrl+F / Ctrl+R) for the editor or the preview.

Extracted from ``MainWindow`` as one responsibility. The state is what makes it
one and it moved along: which view is being searched, which handler is connected
to it, and which half is dimmed while the bar is open. Those three have to be
kept consistent — a target swapped without clearing the old pane's highlight, or
a dimmed widget nobody un-dims, is exactly the kind of leak that used to hide in
a window with a hundred other concerns.

From the outside it needs two things it genuinely cannot own: the current tab and
where the keyboard focus sits (the search target follows the focused pane).
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from gi.repository import Gio

#: How far the view that is *not* being searched is faded.
DIM_OPACITY = 0.35


class FindController:
    """Owns the find bar's wiring, the search target and the dimmed view."""

    def __init__(self, find_bar, *, get_current_tab, get_focus) -> None:
        self._bar = find_bar
        self._get_current_tab = get_current_tab
        self._get_focus = get_focus
        self._target = None
        self._info_handler = 0
        self._dimmed = None

        self._bar.connect("search-changed", self._on_text_changed)
        self._bar.connect("search-next", lambda *_: self._navigate(True))
        self._bar.connect("search-prev", lambda *_: self._navigate(False))
        self._bar.connect("options-changed", self._on_options_changed)
        self._bar.connect("replace-one", lambda *_: self._replace(False))
        self._bar.connect("replace-all", lambda *_: self._replace(True))
        self._bar.connect("closed", self._on_closed)

    def register_actions(self, window: Gio.ActionMap) -> None:
        """Add find-in-view and replace-in-view to *window*."""
        for name, handler in (
            ("find-in-view", lambda *_: self.open_find()),
            ("replace-in-view", lambda *_: self.open_replace()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            window.add_action(action)

    # ── state the window still asks about ──────────────────────────

    @property
    def visible(self) -> bool:
        return self._bar.get_visible()

    def close(self) -> None:
        """Close the bar (a view-mode switch or Esc does this)."""
        self._bar.close()

    # ── choosing what to search ────────────────────────────────────

    def _active_target(self):
        """The editor or preview to search — whichever is focused, falling back
        to the current tab's view mode."""
        tab = self._get_current_tab()
        if not tab:
            return None
        widget = self._get_focus()
        while widget is not None:
            if widget is tab.preview:
                return tab.preview
            if widget is tab.editor:
                return tab.editor
            widget = widget.get_parent()
        mode = getattr(tab, "view_mode", "edit")
        return tab.preview if mode == "render" else tab.editor

    def _set_target(self, target) -> None:
        if target is self._target:
            return
        if self._target is not None:
            self._target.search_clear()   # drop the old pane's highlight (R21.10)
            if self._info_handler:
                self._target.disconnect(self._info_handler)
        self._target = target
        self._info_handler = target.connect(
            "search-info-changed", lambda *_: self._update_count(),
        )

    # ── opening ────────────────────────────────────────────────────

    def open_find(self) -> None:
        """Ctrl+F — search the active view."""
        # Already open: just refocus the entry — do NOT recompute the target
        # (focus is in the find entry, which would flip the target — R21.11).
        if self._bar.get_visible() and self._target is not None:
            self._bar.open()
            return
        target = self._active_target()
        if target is None:
            return
        self._set_target(target)
        tab = self._get_current_tab()
        is_editor = tab is not None and target is tab.editor
        self._bar.set_editor_mode(is_editor)
        self._bar.set_replace_visible(False)          # Ctrl+F = search only
        if is_editor:
            self._apply_options()
        self._dim_inactive(target)
        self._bar.open()

    def open_replace(self) -> None:
        """Ctrl+R — find + replace, editor only.

        Works in the editor (including a split's edit pane) and is a no-op while
        the preview is the active view, which is read-only.
        """
        tab = self._get_current_tab()
        if tab is None:
            return
        target = self._active_target()
        if target is not tab.editor:
            return
        self._set_target(target)
        self._bar.set_editor_mode(True)
        self._apply_options()
        self._dim_inactive(target)
        self._bar.open()
        self._bar.focus_replace()

    # ── dimming ────────────────────────────────────────────────────

    def _dim_inactive(self, target) -> None:
        """Fade the view that is NOT being searched so it reads as inactive."""
        self._restore_dimmed()
        tab = self._get_current_tab()
        if tab is None:
            return
        other = tab.preview if target is tab.editor else tab.editor
        other.set_opacity(DIM_OPACITY)
        self._dimmed = other

    def _restore_dimmed(self) -> None:
        """Un-dim the exact widget we dimmed (independent of the current tab)."""
        if self._dimmed is not None:
            self._dimmed.set_opacity(1.0)
            self._dimmed = None

    # ── searching ──────────────────────────────────────────────────

    def _update_count(self) -> None:
        if self._target is not None:
            current, total = self._target.search_info()
            self._bar.set_count(current, total)

    def _on_text_changed(self, _bar, text: str) -> None:
        if self._target is None:
            return
        # search_set_text already positions on the first match; do NOT advance
        # again or refining the query walks matches away (R21.9).
        self._target.search_set_text(text)
        self._update_count()

    def _navigate(self, forward: bool) -> None:
        if self._target is None:
            return
        if forward:
            self._target.search_next()
        else:
            self._target.search_prev()
        self._update_count()

    def _apply_options(self) -> None:
        """Push the bar's case/word/regex toggles onto an editor target."""
        if self._target is not None and hasattr(self._target, "set_search_options"):
            self._target.set_search_options(*self._bar.get_options())

    def _on_options_changed(self, _bar) -> None:
        if self._target is None:
            return
        self._apply_options()
        # Re-run the query so highlight and selection reflect the new mode.
        self._target.search_set_text(self._bar.get_text())
        self._update_count()

    def _replace(self, all_matches: bool) -> None:
        target = self._target
        if target is None or not hasattr(target, "replace_current"):
            return
        replacement = self._bar.get_replace_text()
        if all_matches:
            target.replace_all(replacement)
        else:
            target.replace_current(replacement)
        self._update_count()

    def _on_closed(self, _bar) -> None:
        if self._target is not None:
            self._target.search_clear()
            if self._info_handler:
                self._target.disconnect(self._info_handler)
                self._info_handler = 0
            self._target = None
        self._restore_dimmed()
