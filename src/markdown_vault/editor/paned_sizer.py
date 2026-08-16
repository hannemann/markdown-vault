"""Sizing policy for a Gtk.Paned side panel (sidebar / vault tree).

Standard Gtk.Paned resize flags are symmetric — a child that shrinks first when
the window narrows also *grows* first when it widens.  That makes a side panel
balloon after de-maximizing (it keeps its huge pixel width and squeezes the
content) — see the sidebar bug.

:class:`PanedSizer` gives the side child asymmetric behaviour:

* the window **widens** → the main child absorbs the extra space; the side
  child stays at the width the user last dragged it to (its *want*);
* the window **narrows** → the side child shrinks first (down to its minimum),
  and only when it can shrink no further does the main child shrink.

It works by making the side child the paned's *resize* child (so it naturally
absorbs shrinking first) and capping its growth to *want* on every width
change.  *want* is updated from genuine user drags, distinguished from
resize-driven position changes via a short "settling" window.

Requirements on the paned (set by the caller):
* the side child is the resize child (``resize`` True) and non-shrinkable
  (``shrink`` False) with a ``size_request`` minimum;
* the main child is non-shrinkable with a ``size_request`` minimum so it cannot
  vanish when the side panel is at its widest.
"""

import logging

from gi.repository import GLib

logger = logging.getLogger(__name__)


class PanedSizer:
    """Keep a paned's side panel from ballooning; shrink it before the main."""

    def __init__(self, paned, side: str) -> None:
        if side not in ("start", "end"):
            raise ValueError("side must be 'start' or 'end'")
        self._paned = paned
        self._side = side
        self._want: int | None = None   # preferred side width (px)
        self._last_width = -1
        self._busy = False              # guards our own set_position
        self._resizing = False          # True while a width change settles
        paned.connect("notify::max-position", self._on_width_changed)
        paned.connect("notify::position", self._on_position_changed)

    # ------------------------------------------------------------------

    def _side_width(self, width: int, position: int) -> int:
        return (width - position) if self._side == "end" else position

    def _position_for_side(self, width: int, side_width: int) -> int:
        return (width - side_width) if self._side == "end" else side_width

    def _on_width_changed(self, paned, _pspec) -> None:
        """The paned's width changed — cap side growth to *want*."""
        if self._busy:
            return
        width = paned.get_width()
        if width <= 1 or width == self._last_width:
            return
        self._last_width = width
        self._resizing = True
        GLib.idle_add(self._settle)  # ignore resize-driven position notifies
        side = self._side_width(width, paned.get_position())
        if self._want is None:
            self._want = side  # first allocation seeds the preferred width
        elif side > self._want:
            self._busy = True
            paned.set_position(self._position_for_side(width, self._want))
            self._busy = False

    def _settle(self) -> bool:
        self._resizing = False
        return False  # one-shot

    def _on_position_changed(self, paned, _pspec) -> None:
        """A genuine user drag (not a resize) updates the preferred width."""
        if self._busy or self._resizing:
            return
        width = paned.get_width()
        if width > 1:
            self._want = self._side_width(width, paned.get_position())
