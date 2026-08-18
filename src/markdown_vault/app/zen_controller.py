"""Zen mode: hide the chrome around the text, in two levels.

Extracted from ``MainWindow`` as one responsibility, with the state that makes it
one: the current level *and* the visibility the window had before zen started.
That saved baseline is the whole subtlety — leaving zen must restore what the
user had, not a guessed default, and switching levels re-derives from the same
baseline instead of from the half-hidden state in between.

The six elements stay owned by the window; this object only shows and hides them,
and registers its own two actions so the window hands nothing over.
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from gi.repository import Gio

#: Everything zen can hide.
ELEMENTS = ("header", "tab_bar", "tree", "sidebar", "search", "statusbar")

#: What each level hides. "panels" keeps the header and the tabs, so the user can
#: still see where they are; "total" takes everything.
LEVELS = {
    "panels": ("tree", "sidebar", "search", "statusbar"),
    "total": ("header", "tab_bar", "tree", "sidebar", "search", "statusbar"),
}


class ZenController:
    """Owns the zen level and the pre-zen visibility of the six elements."""

    def __init__(self, *, header, tab_bar, vault_tree, sidebar_toggle,
                 search_toggle, status_bar) -> None:
        self._header = header
        self._tab_bar = tab_bar
        self._vault_tree = vault_tree
        # Sidebar and search are driven through their toggle buttons, not the
        # widgets: the buttons carry the state the rest of the app reads.
        self._sidebar_toggle = sidebar_toggle
        self._search_toggle = search_toggle
        self._status_bar = status_bar
        self._level: str | None = None
        self._saved: dict | None = None

    @property
    def level(self) -> str | None:
        """The active level, or ``None`` outside zen."""
        return self._level

    def register_actions(self, window: Gio.ActionMap) -> None:
        """Add toggle-zen (cycles) and toggle-zen-total to *window*."""
        for name, handler in (
            ("toggle-zen", lambda *_: self.cycle()),
            ("toggle-zen-total", lambda *_: self.toggle("total")),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            window.add_action(action)

    # ── element access ─────────────────────────────────────────────

    def _get(self, name: str) -> bool:
        if name == "header":
            return self._header.get_visible()
        if name == "tab_bar":
            return self._tab_bar.get_visible()
        if name == "tree":
            return self._vault_tree.get_visible()
        if name == "sidebar":
            return self._sidebar_toggle.get_active()
        if name == "statusbar":
            return self._status_bar.get_visible()
        return self._search_toggle.get_active()          # "search"

    def _set(self, name: str, shown: bool) -> None:
        if name == "header":
            self._header.set_visible(shown)
        elif name == "tab_bar":
            self._tab_bar.set_visible(shown)
        elif name == "tree":
            self._vault_tree.set_visible(shown)
        elif name == "sidebar":
            self._sidebar_toggle.set_active(shown)       # drives the sidebar
        elif name == "statusbar":
            self._status_bar.set_visible(shown)
        else:                                            # "search"
            self._search_toggle.set_active(shown)        # drives the search bar

    # ── levels ─────────────────────────────────────────────────────

    def set_level(self, level) -> None:
        """Enter or switch to *level* (or leave zen when ``None``).

        The pre-zen visibility is captured on first entry and restored on exit;
        switching between levels re-derives from that same saved baseline.
        """
        if level is None:
            saved = self._saved or {}
            for name in ELEMENTS:
                self._set(name, saved.get(name, True))
            self._level = None
            self._saved = None
            return
        if self._level is None:
            self._saved = {n: self._get(n) for n in ELEMENTS}
        self._apply(level)
        self._level = level

    def _apply(self, level: str) -> None:
        """Hide the level's elements; restore the rest to their saved state."""
        hide = LEVELS[level]
        saved = self._saved or {}
        for name in ELEMENTS:
            self._set(name, False if name in hide else saved.get(name, True))

    def cycle(self) -> None:
        """Ctrl+B cycles: normal → panels → total → normal."""
        nxt = {None: "panels", "panels": "total", "total": None}
        self.set_level(nxt[self._level])

    def toggle(self, level: str) -> None:
        """Toggle *level* directly (Ctrl+Shift+B for total); off restores."""
        self.set_level(None if self._level == level else level)
