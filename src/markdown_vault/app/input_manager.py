"""InputManager — keyboard shortcuts and navigation history.

Extracted from ``MainWindow`` to eliminate circular dependencies and keep
the god-node small.  Manages application accelerators, dynamic tab shortcuts,
browser-style back/forward navigation and button state.
"""

import logging

from gi.repository import Gtk

logger = logging.getLogger(__name__)


class InputManager:
    """Manages keyboard shortcuts and navigation history."""

    def __init__(self, application, on_nav_file_opened,
                 nav_history, back_btn, forward_btn, settings,
                 in_page_state_fn=None, save_position_fn=None,
                 restore_position_fn=None):
        """Initialize the input manager.

        Args:
            application: :class:`Gtk.Application` instance (for accelerators)
            on_nav_file_opened: Callback ``(file_path, _from_nav)`` to open a file
            nav_history: :class:`history.NavHistory` instance
            back_btn: Back navigation :class:`Gtk.Button`
            forward_btn: Forward navigation :class:`Gtk.Button`
            settings: Settings dict from :mod:`config`
            in_page_state_fn: Optional ``() -> (can_back, can_forward)`` reporting the
                active preview's in-page anchor history, so the buttons stay lit while
                footnote/TOC jumps can still be unwound.
            save_position_fn: Optional ``() -> None`` that records the reader's
                position into the *current* history entry. Called just before the
                history leaves that entry — on a push and before a back/forward —
                so where the reader was is captured before it is lost.
            restore_position_fn: Optional ``() -> None`` that restores the current
                entry's saved position. Called after a back/forward has opened the
                target note.
        """
        self._application = application
        self._on_nav_file_opened = on_nav_file_opened
        self._nav_history = nav_history
        self._back_btn = back_btn
        self._forward_btn = forward_btn
        self._settings = settings
        self._in_page_state_fn = in_page_state_fn
        self._save_position_fn = save_position_fn
        self._restore_position_fn = restore_position_fn
        # True while our own back/forward is opening the target note. The open
        # fires a tab-change that calls push_history again; that re-entrant push
        # must not re-save the freshly-loaded (scroll 0) position over the target
        # entry we are about to restore.
        self._navigating = False
        self._tab_shortcut_ctrl: Gtk.ShortcutController | None = None
        self._tab_shortcuts: list = []

    # ── Shortcuts ──────────────────────────────────────────────────────

    def apply_keybindings(self, tab_shortcut_ctrl, tab_shortcuts) -> None:
        """Set application accelerators and update dynamic tab shortcuts.

        Args:
            tab_shortcut_ctrl: Global :class:`Gtk.ShortcutController`
            tab_shortcuts: List that accumulates created :class:`Gtk.Shortcut` objects
        """
        if not self._application:
            return
        self._tab_shortcut_ctrl = tab_shortcut_ctrl
        self._tab_shortcuts = tab_shortcuts
        self._set_application_accels(self._application.get_application())
        self._update_dynamic_shortcuts()

    def _set_application_accels(self, app) -> None:
        """Configure application-level keyboard accelerators."""
        app.set_accels_for_action("win.nav-back", ["<Alt>Left"])
        app.set_accels_for_action("win.nav-forward", ["<Alt>Right"])
        is_mru = self._settings.get("tab_switch_mode", "mru") == "mru"
        if is_mru:
            next_accel = self._settings.get("keybinding_next_tab", "<Control>Tab")
            prev_accel = self._settings.get("keybinding_prev_tab", "<Shift><Control>Tab")
            app.set_accels_for_action("win.mru-switcher-next", [next_accel] if next_accel else [])
            app.set_accels_for_action("win.mru-switcher-prev", [prev_accel] if prev_accel else [])
            app.set_accels_for_action("win.next-tab", [])
            app.set_accels_for_action("win.prev-tab", [])
        else:
            for setting_key, cycle_action in (
                ("keybinding_next_tab", "next-tab"),
                ("keybinding_prev_tab", "prev-tab"),
            ):
                accel = self._settings.get(setting_key, "")
                if accel:
                    app.set_accels_for_action(f"win.{cycle_action}", [accel])
                else:
                    app.set_accels_for_action(f"win.{cycle_action}", [])
            app.set_accels_for_action("win.mru-switcher-next", [])
            app.set_accels_for_action("win.mru-switcher-prev", [])

    def _update_dynamic_shortcuts(self) -> None:
        """Update dynamic tab switching shortcuts in the global shortcut controller."""
        if not self._tab_shortcut_ctrl:
            return
        for shortcut in self._tab_shortcuts:
            self._tab_shortcut_ctrl.remove_shortcut(shortcut)
        self._tab_shortcuts.clear()

        is_mru = self._settings.get("tab_switch_mode", "mru") == "mru"
        if is_mru:
            return  # MRU mode uses application accelerators only

        next_accel = self._settings.get("keybinding_next_tab", "<Control>Tab")
        prev_accel = self._settings.get("keybinding_prev_tab", "<Shift><Control>Tab")
        if next_accel:
            trigger = Gtk.ShortcutTrigger.parse_string(next_accel)
            action = Gtk.NamedAction.new("win.next-tab")
            shortcut = Gtk.Shortcut.new(trigger, action)
            self._tab_shortcut_ctrl.add_shortcut(shortcut)
            self._tab_shortcuts.append(shortcut)
        if prev_accel:
            trigger = Gtk.ShortcutTrigger.parse_string(prev_accel)
            action = Gtk.NamedAction.new("win.prev-tab")
            shortcut = Gtk.Shortcut.new(trigger, action)
            self._tab_shortcut_ctrl.add_shortcut(shortcut)
            self._tab_shortcuts.append(shortcut)

    def update_tab_shortcuts(self) -> None:
        """Refresh dynamic tab shortcuts when settings change."""
        self._update_dynamic_shortcuts()

    # ── Navigation ─────────────────────────────────────────────────────

    def push_history(self, file_path: str) -> None:
        """Append *file_path* to the navigation history.

        Consecutive duplicates are collapsed and any forward history is
        discarded, matching standard browser behaviour.

        Args:
            file_path: Absolute path of the file to push
        """
        if self._navigating:
            # Re-entrant push from the tab-change our own back/forward fired —
            # ignore it, or it overwrites the target entry's saved position.
            return
        if self._save_position_fn is not None:
            self._save_position_fn()      # record where we were before leaving
        self._nav_history.push(file_path)
        self._update_nav_buttons()

    def nav_back(self) -> None:
        """Navigate to the previous entry in history, skipping missing files."""
        if self._save_position_fn is not None:
            self._save_position_fn()      # record the current spot before leaving it
        file_path = self._nav_history.back()
        if file_path is not None:
            self._open_from_nav(file_path)
        self._update_nav_buttons()

    def nav_forward(self) -> None:
        """Navigate to the next entry in history, skipping missing files."""
        if self._save_position_fn is not None:
            self._save_position_fn()
        file_path = self._nav_history.forward()
        if file_path is not None:
            self._open_from_nav(file_path)
        self._update_nav_buttons()

    def _open_from_nav(self, file_path: str) -> None:
        """Open *file_path* for a back/forward step, then restore its position.

        The open is fenced by ``_navigating`` so the tab-change it triggers can't
        re-save over the target entry; the restore runs after the fence, reading
        the entry's intact saved position.
        """
        self._navigating = True
        try:
            self._on_nav_file_opened(file_path, _from_nav=True)
        finally:
            self._navigating = False
        if self._restore_position_fn is not None:
            self._restore_position_fn()   # land where we were, not at the top

    def _update_nav_buttons(self) -> None:
        """Reflect history state on the nav buttons.

        The buttons stay *sensitive* even when there is nowhere to go, so a
        (fast, repeated) click is always consumed by the button — an insensitive
        button lets the click fall through to the header's title area, where a
        double-click toggle-maximizes the window.  "Nothing to navigate" is shown
        by dimming instead; the click then simply no-ops.
        """
        extra_back, extra_fwd = (self._in_page_state_fn()
                                 if self._in_page_state_fn else (False, False))
        self._back_btn.set_opacity(
            1.0 if (self._nav_history.can_go_back() or extra_back) else 0.35)
        self._forward_btn.set_opacity(
            1.0 if (self._nav_history.can_go_forward() or extra_fwd) else 0.35)

    def update_nav_buttons(self) -> None:
        """Public API: dim/undim the navigation buttons based on history state."""
        self._update_nav_buttons()
