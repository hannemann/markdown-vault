"""Markdown Vault — app-global status line at the bottom of the window.

A single, full-width place for background-task status (semantic index build and
incremental reindexing) and errors (embedding backend unreachable).  It is
app-global and file-independent — distinct from the per-tab reload banner.

States (mutually exclusive, highest priority wins in the caller):
  * busy      — spinner + message (indeterminate work, e.g. incremental update)
  * progress  — progress bar + message (determinate work, e.g. a full build)
  * error     — warning icon + message + action buttons (e.g. Rebuild / Settings)
  * cleared   — hidden (revealer collapsed)

Wrapped in a Revealer so it slides in/out and takes no space when idle; the
whole widget can additionally be hidden (zen mode) via ``set_visible``.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango


class StatusBar(Gtk.Revealer):
    def __init__(self) -> None:
        super().__init__()
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.set_reveal_child(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("toolbar")
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(3)
        box.set_margin_bottom(3)

        self._spinner = Gtk.Spinner()
        self._icon = Gtk.Image()
        self._label = Gtk.Label(xalign=0.0)
        self._label.set_ellipsize(Pango.EllipsizeMode.END)
        self._label.set_hexpand(True)
        self._label.add_css_class("dim-label")
        self._progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self._progress.set_size_request(160, -1)
        self._buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        for w in (self._spinner, self._icon, self._label,
                  self._progress, self._buttons):
            box.append(w)
        self.set_child(box)
        self._reset()

    def _reset(self) -> None:
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._icon.set_visible(False)
        self._icon.remove_css_class("warning")
        self._progress.set_visible(False)
        self._clear_buttons()

    def _clear_buttons(self) -> None:
        child = self._buttons.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._buttons.remove(child)
            child = nxt

    def show_busy(self, text: str) -> None:
        self._reset()
        self._spinner.set_visible(True)
        self._spinner.start()
        self._label.set_text(text)
        self.set_reveal_child(True)

    def show_progress(self, fraction: float, text: str) -> None:
        self._reset()
        self._progress.set_visible(True)
        self._progress.set_fraction(max(0.0, min(1.0, fraction)))
        self._label.set_text(text)
        self.set_reveal_child(True)

    def show_error(self, text: str, actions=()) -> None:
        """*actions* is an iterable of ``(label, callback)``."""
        self._reset()
        self._icon.set_from_icon_name("dialog-warning-symbolic")
        self._icon.add_css_class("warning")
        self._icon.set_visible(True)
        self._label.set_text(text)
        for label, callback in actions:
            btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b, cb=callback: cb())
            self._buttons.append(btn)
        self.set_reveal_child(True)

    def clear(self) -> None:
        self.set_reveal_child(False)
