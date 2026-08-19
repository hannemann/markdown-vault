"""Reading-position memory for the navigation history.

``ScrollMemory`` remembers where the reader was in each navigation-history
entry and puts it back on back/forward. It bridges the two sides of that job:
the tab widgets, which hold the *live* position (editor caret + scroll, preview
scroll), and the history entries, which *persist* it across navigation and
restarts.

The window owns none of this — it builds a ``ScrollMemory`` and wires its three
methods in: ``save_leaving`` as the InputManager's save-on-leave callback,
``restore_current`` as its restore-on-return callback (and the vault switch's
post-open callback), and ``record_from_tab`` at the one in-place spot that has
to capture before the editor buffer is replaced.
"""

import logging

logger = logging.getLogger(__name__)

# View modes that show each surface — the editor in edit/split, the preview in
# render/split. Named once so record and restore can't drift apart.
_EDITOR_MODES = ("edit", "split")
_PREVIEW_MODES = ("render", "split")


class ScrollMemory:
    """Save the reader's position into history entries and restore it back.

    Depends only on the navigation history and the tab bar (narrow collaborators,
    no window reference), so it is testable as a plain object.
    """

    def __init__(self, nav_history, tab_bar) -> None:
        self._nav_history = nav_history
        self._tab_bar = tab_bar

    def record_from_tab(self, tab) -> None:
        """Write *tab*'s current reading position into the current history entry,
        per its view mode: the editor caret+scroll in edit, the preview scroll in
        render, both in split. The editor is read synchronously; the preview
        value was kept live by its scroll handler."""
        mode = getattr(tab, "view_mode", "edit")
        fields: dict = {}
        if mode in _EDITOR_MODES:
            scroll, cursor = tab.editor.capture_scroll_position()
            fields["editor_scroll"] = scroll
            fields["editor_cursor"] = cursor
        if mode in _PREVIEW_MODES:
            fields["preview_scroll"] = tab.preview.preview_scroll_position()
        if fields:
            self._nav_history.update_current(**fields)

    def save_leaving(self) -> None:
        """Record the reader's position in the note we are about to leave, so
        back/forward can return to it. Reads the tab that still holds the current
        history path — its widgets are intact until it is re-keyed (the in-place
        case captures earlier, before its buffer is replaced).

        Suppressed while a programmatic open runs (the vault switch's own tab
        restore): saving then would overwrite the entry being opened with its
        fresh, scroll-0 position — the cross-vault back/forward failure."""
        if self._nav_history.suppress:
            return
        leaving = self._nav_history.current
        if not leaving:
            return
        tab = self._tab_bar.get_tab(leaving)
        if tab is not None:
            self.record_from_tab(tab)

    def restore_current(self) -> None:
        """Restore the saved position of the current history entry after a
        back/forward opened its note. The editor caret+scroll are set clamped to
        the buffer (and the editor focused so the caret is ready to type); the
        preview scroll is applied once its render finishes. A no-op when there is
        nothing saved or the landed tab isn't that note (e.g. a cross-vault
        switch still in flight)."""
        entry = self._nav_history.current_entry
        if entry is None or not entry.has_position():
            return
        tab = self._tab_bar.get_current_tab()
        if tab is None or tab.file_path != entry.path:
            return
        mode = getattr(tab, "view_mode", "edit")
        if mode in _EDITOR_MODES and (entry.editor_scroll is not None
                                      or entry.editor_cursor is not None):
            tab.editor.restore_scroll_position(entry.editor_scroll, entry.editor_cursor)
            tab.editor.grab_editor_focus()   # so the restored caret is ready to type
        if mode in _PREVIEW_MODES and entry.preview_scroll is not None:
            tab.preview.scroll_to_position(entry.preview_scroll)
