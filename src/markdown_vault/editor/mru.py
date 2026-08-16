"""MRU (Most Recently Used) tab management.

Provides:
- ``MRUManager`` — business logic for tracking recently used tabs.
- ``MRUSwitcher`` — modal popup widget for switching between MRU tabs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk

logger = logging.getLogger(__name__)


class MRUManager:
    """Manages the most-recently-used tab list and position.

    The MRU list is kept in memory; it is rebuilt on each tab change.
    """

    def __init__(self) -> None:
        self._mru_tabs: list[str] = []
        self._mru_pos: int = 0

    @property
    def tabs(self) -> list[str]:
        """Return a copy of the MRU tab list (most recent first)."""
        return self._mru_tabs[:]

    @property
    def pos(self) -> int:
        """Return the current MRU position (0 = most recent)."""
        return self._mru_pos

    def push(self, file_path: str) -> None:
        """Move *file_path* to the front of the MRU list and reset position."""
        if file_path in self._mru_tabs:
            self._mru_tabs.remove(file_path)
        self._mru_tabs.insert(0, file_path)
        self._mru_pos = 0

    def remove(self, file_path: str) -> None:
        """Remove *file_path* from the MRU list (e.g. when a tab is closed)."""
        if file_path in self._mru_tabs:
            self._mru_tabs.remove(file_path)
            if self._mru_pos >= len(self._mru_tabs):
                self._mru_pos = max(0, len(self._mru_tabs) - 1)

    def clear(self) -> None:
        """Reset the MRU list to empty."""
        self._mru_tabs.clear()
        self._mru_pos = 0

    def rename(self, old_path: str, new_path: str) -> None:
        """Replace *old_path* with *new_path* in-place, preserving index and _mru_pos."""
        if old_path in self._mru_tabs:
            idx = self._mru_tabs.index(old_path)
            self._mru_tabs[idx] = new_path
            # _mru_pos unchanged since we replace at same index

    def next(self) -> str | None:
        """Return the next MRU tab path, skipping missing files.

        Returns ``None`` if no navigable tab exists.
        """
        if len(self._mru_tabs) < 2:
            return None
        new_pos = self._mru_pos
        for _ in range(len(self._mru_tabs) - 1):
            new_pos = min(new_pos + 1, len(self._mru_tabs) - 1)
            if new_pos == self._mru_pos:
                break
            if Path(self._mru_tabs[new_pos]).exists():
                self._mru_pos = new_pos
                return self._mru_tabs[new_pos]
        return None

    def prev(self) -> str | None:
        """Return the previous MRU tab path, skipping missing files.

        Returns ``None`` if no navigable tab exists.
        """
        if len(self._mru_tabs) < 2:
            return None
        new_pos = self._mru_pos
        for _ in range(len(self._mru_tabs) - 1):
            new_pos = max(new_pos - 1, 0)
            if new_pos == self._mru_pos:
                break
            if Path(self._mru_tabs[new_pos]).exists():
                self._mru_pos = new_pos
                return self._mru_tabs[new_pos]
        return None


class MRUSwitcher(Gtk.Window):
    """IntelliJ-style MRU tab switcher (shown on Ctrl+Tab).

    Only one instance may exist at a time.  Ctrl is held when the switcher
    opens; releasing Ctrl commits the selection and closes the dialog.
    """

    _instance: MRUSwitcher | None = None

    @classmethod
    def is_open(cls) -> bool:
        """Return True if a switcher dialog is currently shown."""
        return cls._instance is not None

    @classmethod
    def cycle_existing(cls, direction: int) -> None:
        """Cycle the currently open switcher, if any."""
        if cls._instance is not None:
            cls._instance.cycle_from_accelerator(direction)

    def __init__(
        self,
        parent: Gtk.Window,
        mru_tabs: list[str],
        open_file: Callable[[str], None],
    ) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
        )
        MRUSwitcher._instance = self

        self.set_decorated(False)
        self._mru_tabs = mru_tabs[:]
        self._open_file = open_file
        # MRU[0] = current tab, MRU[1] = previous tab. Start at MRU[1].
        self._selected_idx = 1 if len(mru_tabs) > 1 else 0
        self._accel_handled = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_child(box)

        title = Gtk.Label(label="Switch Tab")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        box.append(title)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.set_focusable(False)
        self._list_box.set_focus_child(None)
        self._list_box.add_css_class("mru-switcher")
        box.append(self._list_box)

        self._populate_list()

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        key_ctrl.connect("key-released", self._on_key_released)
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(key_ctrl)

        self.connect("close-request", self._on_close_request)
        self.present()

    def _populate_list(self) -> None:
        """Fill list with MRU tabs."""
        for child in list(self._list_box):
            self._list_box.remove(child)
        for idx, path in enumerate(self._mru_tabs):
            row = Adw.ActionRow(title=Path(path).name)
            row.set_subtitle(path)
            row.add_css_class("mru-row")
            if idx == 0:
                row.add_css_class("current")
            self._list_box.append(row)
        self._update_selection()

    def _update_selection(self) -> None:
        """Update visual selection."""
        for idx in range(len(self._mru_tabs)):
            row = self._list_box.get_row_at_index(idx)
            if row is None:
                continue
            classes = ["mru-row"]
            if idx == 0:
                classes.append("current")
            if idx == self._selected_idx:
                classes.append("selected")
            row.set_css_classes(classes)

    def cycle(self, direction: int) -> None:
        """Move selection by *direction* (+1 forward, -1 backward)."""
        self._selected_idx = (self._selected_idx + direction) % len(self._mru_tabs)
        self._update_selection()

    def cycle_from_accelerator(self, direction: int) -> None:
        """Called by the app accelerator. Sets flag to prevent key controller double-cycle."""
        self._accel_handled = True
        self.cycle(direction)

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: int) -> bool:
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and is_ctrl:
            if self._accel_handled:
                self._accel_handled = False
                return True
            if is_shift:
                self.cycle(-1)
            else:
                self.cycle(+1)
            return True
        elif keyval == Gdk.KEY_Escape:
            self._cancel()
            return True
        return False

    def _on_key_released(self, _ctrl, keyval: int, _keycode: int, state: int) -> bool:
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self._commit()
            return True
        return False

    def _commit(self) -> None:
        """Activate the selected tab and close the switcher."""
        if self._mru_tabs:
            target = self._mru_tabs[self._selected_idx]
            self._open_file(target)
        self.close()

    def _cancel(self) -> None:
        """Close the switcher without changing the active tab."""
        self.close()

    def _on_close_request(self, *_args) -> bool:
        """Reset the singleton instance and destroy."""
        MRUSwitcher._instance = None
        self.destroy()
        return True
