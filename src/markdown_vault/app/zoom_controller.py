"""Zoom: keyboard shortcuts and Ctrl+Wheel, applied to whichever half the pointer
is over.

Extracted from ``MainWindow`` as one **responsibility**, not as a pile of moved
methods — which is why it takes the pointer state with it. The window used to
carry ``_ptr_x``/``_ptr_y`` and hand three methods to its actions and two event
controllers; it now hands over nothing for zoom, because this object registers
its own actions with its own handlers.

What it needs from the outside is deliberately small: the content stack (for
coordinates and for the controllers) and a way to ask for the current tab. Those
are the two things zoom genuinely cannot own.
"""

import logging

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, Gio

#: One step per keypress / wheel notch.
ZOOM_STEP = 0.1


class ZoomController:
    """Owns the pointer position and the zoom of the active tab's two halves."""

    def __init__(self, content_stack: Gtk.Widget, get_current_tab) -> None:
        self._content_stack = content_stack
        self._get_current_tab = get_current_tab
        # Pointer position in content-stack coordinates — the whole reason this
        # is stateful: a keyboard shortcut has to know where the mouse is.
        self._ptr_x: float = 0.0
        self._ptr_y: float = 0.0

        self._scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        self._scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._scroll_ctrl.connect("scroll", self._on_scroll)
        content_stack.add_controller(self._scroll_ctrl)

        self._motion_ctrl = Gtk.EventControllerMotion.new()
        self._motion_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._motion_ctrl.connect("motion", self._on_motion)
        content_stack.add_controller(self._motion_ctrl)

    # ── actions ────────────────────────────────────────────────────

    def register_actions(self, window: Gio.ActionMap) -> None:
        """Add zoom-in / zoom-out / zoom-reset to *window*.

        Registering here rather than in the window is the point of the split: the
        handler and the action stay together, so the window hands nothing over.
        """
        for name, handler in (
            ("zoom-in", lambda *_: self.zoom(+1)),
            ("zoom-out", lambda *_: self.zoom(-1)),
            ("zoom-reset", lambda *_: self.reset()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            window.add_action(action)

    # ── pointer ────────────────────────────────────────────────────

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        """Track the pointer inside the content stack."""
        self._ptr_x = x
        self._ptr_y = y

    def _widget_origin_in_stack(self, widget: Gtk.Widget) -> tuple[int, int]:
        """Walk up from *widget* to the content stack, accumulating offsets."""
        x, y = 0, 0
        cur = widget
        while cur is not None and cur is not self._content_stack:
            a = cur.get_allocation()
            x += a.x
            y += a.y
            cur = cur.get_parent()
        return x, y

    def pointer_over_preview(self, tab) -> bool:
        """Whether the pointer sits over *tab*'s preview half."""
        if not tab.preview.get_visible():
            return False
        ox, oy = self._widget_origin_in_stack(tab.preview)
        return (ox <= self._ptr_x < ox + tab.preview.get_width()
                and oy <= self._ptr_y < oy + tab.preview.get_height())

    # ── zooming ────────────────────────────────────────────────────

    def zoom(self, direction: int) -> None:
        """Zoom the half under the pointer by one step."""
        tab = self._get_current_tab()
        if not tab:
            return
        self._apply(tab, direction)

    def reset(self) -> None:
        """Return the half under the pointer to 100 %."""
        tab = self._get_current_tab()
        if not tab:
            return
        if self.pointer_over_preview(tab):
            tab.preview.zoom_level = 1.0
        else:
            tab.editor.zoom_factor = 1.0

    def _apply(self, tab, direction: int) -> None:
        if self.pointer_over_preview(tab):
            tab.preview.zoom_level = round(
                tab.preview.zoom_level + direction * ZOOM_STEP, 2)
        else:
            tab.editor.zoom_factor = round(
                tab.editor.zoom_factor + direction * ZOOM_STEP, 2)

    def _on_scroll(self, ctrl, _dx, dy: float) -> bool:
        """Ctrl+Wheel — same rule as the shortcuts, opposite sign convention."""
        event = ctrl.get_current_event()
        if event is None:
            return False
        if not (event.get_modifier_state() & Gdk.ModifierType.CONTROL_MASK):
            return False
        tab = self._get_current_tab()
        if not tab:
            return False
        self._apply(tab, -1 if dy > 0 else 1)
        return True
