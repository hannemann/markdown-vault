"""Markdown Vault — tab management.

Provides a ``Tab`` data class and a ``TabBar`` widget that owns one
``Editor`` and ``Preview`` instance per open file.  This ensures that
each tab retains its own buffer state and scroll position.
"""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject


class Tab:
    """Represents a single open file with its own editor and preview.

    Attributes:
        file_path: Absolute path of the Markdown file.
        title: Display name shown in the tab bar.
        editor: The ``Editor`` widget for this tab.
        preview: The ``Preview`` widget for this tab.
        view_mode: Current view mode (``"edit"``, ``"render"``, ``"split"``).
    """

    def __init__(self, file_path: str, title: str, editor, preview) -> None:
        self.file_path = file_path
        self.title = title
        self.editor = editor
        self.preview = preview
        self.view_mode = "edit"


class TabBar(Gtk.Box):
    """Horizontal bar of tabs with close buttons.

    Signals:
        tab-changed(str): Emitted when the active tab switches.
        tab-closed(str): Emitted when a tab is closed.
    """

    __gsignals__ = {
        "tab-changed": (GObject.SIGNAL_RUN_LAST, None, (str,)),
        "tab-closed": (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._tabs: dict[str, Tab] = {}
        self._current_path: str | None = None

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._box.add_css_class("tab-bar")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._box)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.append(scrolled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_tab(self, file_path: str, editor, preview) -> Tab:
        """Register a new tab or activate an existing one.

        If *file_path* is already open, the existing tab is selected
        and no new ``Editor``/``Preview`` pair is created.
        """
        if file_path in self._tabs:
            self.set_active_tab(file_path)
            return self._tabs[file_path]

        title = Path(file_path).name
        tab = Tab(file_path, title, editor, preview)
        self._tabs[file_path] = tab

        tab_widget = self._build_tab_widget(file_path, title)
        self._box.append(tab_widget)
        self.set_active_tab(file_path)
        return tab

    def set_active_tab(self, file_path: str) -> None:
        """Select the tab for *file_path* and emit ``tab-changed``."""
        if file_path not in self._tabs:
            return
        self._current_path = file_path
        self._update_tab_styles()
        self.emit("tab-changed", file_path)

    def close_tab(self, file_path: str) -> None:
        """Remove the tab for *file_path* and emit ``tab-closed``."""
        if file_path not in self._tabs:
            return
        self._tabs.pop(file_path)
        self._remove_tab_widget(file_path)
        self.emit("tab-closed", file_path)
        if self._current_path == file_path:
            remaining = list(self._tabs.keys())
            if remaining:
                self.set_active_tab(remaining[-1])
            else:
                self._current_path = None

    def get_current_tab(self) -> Tab | None:
        """Return the ``Tab`` for the active tab, or ``None``."""
        if self._current_path and self._current_path in self._tabs:
            return self._tabs[self._current_path]
        return None

    def get_current_path(self) -> str | None:
        """Return the file path of the active tab, or ``None``."""
        return self._current_path

    def has_tabs(self) -> bool:
        """Return ``True`` if at least one tab is open."""
        return bool(self._tabs)

    def get_all_paths(self) -> list[str]:
        """Return file paths of all open tabs."""
        return list(self._tabs.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_tab_widget(self, file_path: str, title: str) -> Gtk.Box:
        """Create the visual widget for a single tab."""
        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        container.add_css_class("tab")
        container.set_margin_top(4)
        container.set_margin_bottom(4)
        container.set_margin_start(2)
        container.set_margin_end(2)

        label = Gtk.Label(label=title)
        label.set_ellipsize(3)
        container.append(label)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_size_request(24, 24)
        close_btn.set_tooltip_text("Close tab")
        close_btn.connect(
            "clicked", lambda _btn, fp=file_path: self.close_tab(fp)
        )
        container.append(close_btn)

        gesture = Gtk.GestureClick()
        gesture.connect(
            "released",
            lambda _g, _n, _x, _y, fp=file_path: self.set_active_tab(fp),
        )
        container.add_controller(gesture)

        # Stash the path on the widget for style look-ups.
        container._file_path = file_path  # type: ignore[attr-defined]
        return container

    def _remove_tab_widget(self, file_path: str) -> None:
        """Remove the visual widget for *file_path*."""
        for child in self._box:
            if getattr(child, "_file_path", None) == file_path:
                self._box.remove(child)
                break

    def _update_tab_styles(self) -> None:
        """Highlight the active tab and dim all others."""
        for child in self._box:
            fp = getattr(child, "_file_path", None)
            if fp is None:
                continue
            child.add_css_class("tab")
            if fp == self._current_path:
                child.add_css_class("active")
            else:
                child.remove_css_class("active")
