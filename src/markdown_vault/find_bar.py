"""Markdown Vault — in-view find bar (Ctrl+F).

A small inline bar with a search entry, match counter, prev/next buttons
and a close button.  It is UI-only: it emits signals and the main window
wires them to whichever view is being searched (editor or preview).
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Gdk, GObject


class FindBar(Gtk.Box):
    """Inline find bar. Backend-agnostic — driven via signals.

    Signals:
        search-changed(str): the query text changed.
        search-next(): go to the next match (Enter / down button).
        search-prev(): go to the previous match (Shift+Enter / up button).
        closed(): the bar was dismissed (Esc / close button).
    """

    __gsignals__ = {
        "search-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "search-next": (GObject.SignalFlags.RUN_LAST, None, ()),
        "search-prev": (GObject.SignalFlags.RUN_LAST, None, ()),
        "closed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("find-bar")
        self.set_visible(False)

        self._entry = Gtk.SearchEntry(hexpand=True)
        self._entry.set_placeholder_text("Find in view…")
        self._entry.connect(
            "search-changed",
            lambda e: self.emit("search-changed", e.get_text()),
        )
        self._entry.connect("activate", lambda _e: self.emit("search-next"))
        # Capture phase so Esc/Enter reach us before the SearchEntry's own
        # stop-search/activate handling.
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)
        self.append(self._entry)

        self._counter = Gtk.Label()
        self._counter.add_css_class("find-counter")
        self._counter.add_css_class("dim-label")
        self.append(self._counter)

        prev_btn = Gtk.Button(icon_name="go-up-symbolic")
        prev_btn.add_css_class("flat")
        prev_btn.set_tooltip_text("Previous match (Shift+Enter)")
        prev_btn.connect("clicked", lambda *_: self.emit("search-prev"))
        self.append(prev_btn)

        next_btn = Gtk.Button(icon_name="go-down-symbolic")
        next_btn.add_css_class("flat")
        next_btn.set_tooltip_text("Next match (Enter)")
        next_btn.connect("clicked", lambda *_: self.emit("search-next"))
        self.append(next_btn)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Close (Esc)")
        close_btn.connect("clicked", lambda *_: self.close())
        self.append(close_btn)

    # ── Public API ───────────────────────────────────────────────────

    def open(self) -> None:
        """Show the bar, focus the entry, and re-run any existing query."""
        self.set_visible(True)
        self._entry.grab_focus()
        if self._entry.get_text():
            self.emit("search-changed", self._entry.get_text())

    def close(self) -> None:
        self.set_visible(False)
        self.emit("closed")

    def get_text(self) -> str:
        return self._entry.get_text()

    def set_count(self, current: int, total: int) -> None:
        """Update the match counter. ``total`` < 0 means 'still counting'."""
        if not self._entry.get_text():
            self._counter.set_text("")
        elif total < 0 or current > total:
            # Count not settled yet (async) — a match is already selected but
            # the total is still being computed; show "…" not a bogus "0/0".
            self._counter.set_text("…")
        elif total == 0:
            self._counter.set_text("0/0")
        elif current > 0:
            self._counter.set_text(f"{current}/{total}")
        else:
            self._counter.set_text(f"{total}")

    # ── Internal ─────────────────────────────────────────────────────

    def _on_key(self, _ctrl, keyval: int, _keycode: int, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                self.emit("search-prev")
            else:
                self.emit("search-next")
            return True
        return False
