"""Markdown Vault — tab management.

Provides a ``Tab`` data class and a ``TabBar`` widget that owns one
``Editor`` and ``Preview`` instance per open file.  This ensures that
each tab retains its own buffer state and scroll position.
"""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, GObject, Gio, Gdk

logger = logging.getLogger(__name__)


class Tab:
    """Represents a single open file with its own editor and preview.

    Attributes:
        file_path: Absolute path of the Markdown file.
        title: Display name shown in the tab bar.
        editor: The ``Editor`` widget for this tab.
        preview: The ``Preview`` widget for this tab.
        view_mode: Current view mode (``"edit"``, ``"render"``, ``"split"``).
    """

    def __init__(self, file_path: str, title: str, editor, preview, banner=None) -> None:
        self.file_path = file_path
        self.title = title
        self.editor = editor
        self.preview = preview
        self.view_mode = "edit"
        self.banner = banner

    def reload_editor(self, file_path: str) -> None:
        """Reload editor content from disk."""
        try:
            new_text = Path(file_path).read_text(encoding="utf-8")
            start = self.editor._buffer.get_start_iter()
            end = self.editor._buffer.get_end_iter()
            self.editor._buffer.delete(start, end)
            self.editor._buffer.insert(start, new_text)
            self.editor._buffer.set_modified(False)
        except OSError:
            logger.warning("Could not reload editor from %s", file_path, exc_info=True)


class TabBar(Gtk.Box):
    """Horizontal bar of tabs with close buttons.

    Signals:
        tab-changed(str): Emitted when the active tab switches.
        tab-closed(str): Emitted when a tab is closed.
        tab-renamed(str, str): Emitted when a tab is renamed.
        tab-copy-path(str): Emitted when the user copies a tab path.
    """

    __gsignals__ = {
        "tab-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "tab-closed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "tab-renamed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "tab-copy-path": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._tabs: dict[str, Tab] = {}
        self._current_path: str | None = None
        self._vault_paths: list[str] = []
        self._context_menu_target: str | None = None
        self._min_width = 100
        self._css_provider = Gtk.CssProvider()

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._box.add_css_class("tab-bar")

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_child(self._box)
        self._scrolled.set_hexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.append(self._scrolled)

        self._setup_actions()

    # ------------------------------------------------------------------
    # Actions for context menu
    # ------------------------------------------------------------------

    def _setup_actions(self) -> None:
        self._tab_actions = Gio.SimpleActionGroup()
        self.insert_action_group("tab", self._tab_actions)

        action_copy = Gio.SimpleAction.new("copy-path", None)
        action_copy.connect("activate", self._on_action_copy_path)
        self._tab_actions.add_action(action_copy)

        action_close = Gio.SimpleAction.new("close", None)
        action_close.connect("activate", self._on_action_close)
        self._tab_actions.add_action(action_close)

        action_close_others = Gio.SimpleAction.new("close-others", None)
        action_close_others.connect("activate", self._on_action_close_others)
        self._tab_actions.add_action(action_close_others)

        action_close_left = Gio.SimpleAction.new("close-left", None)
        action_close_left.connect("activate", self._on_action_close_left)
        self._tab_actions.add_action(action_close_left)

        action_close_right = Gio.SimpleAction.new("close-right", None)
        action_close_right.connect("activate", self._on_action_close_right)
        self._tab_actions.add_action(action_close_right)

    def _on_action_copy_path(self, _action, _param) -> None:
        path = self._context_menu_target
        if not path:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set(path)
        self.emit("tab-copy-path", path)
        logger.debug("Copied tab path to clipboard: %s", path)

    def _on_action_close(self, _action, _param) -> None:
        if self._context_menu_target:
            self.close_tab(self._context_menu_target)

    def _on_action_close_others(self, _action, _param) -> None:
        if self._context_menu_target:
            self.close_others(self._context_menu_target)

    def _on_action_close_left(self, _action, _param) -> None:
        if self._context_menu_target:
            self.close_left(self._context_menu_target)

    def _on_action_close_right(self, _action, _param) -> None:
        if self._context_menu_target:
            self.close_right(self._context_menu_target)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_vault_paths(self, vault_paths: list[str]) -> None:
        """Set the vault root paths for computing relative tooltip paths."""
        self._vault_paths = list(vault_paths)

    def set_tab_min_width(self, min_width: int) -> None:
        """Set the minimum width for tab widgets in pixels."""
        self._min_width = min_width
        css = f".tab {{ min-width: {min_width}px; }}"
        try:
            self._css_provider.load_from_string(css)
        except TypeError:
            self._css_provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def add_tab(self, file_path: str, editor, preview, banner=None) -> Tab:
        """Register a new tab or activate an existing one.

        If *file_path* is already open, the existing tab is selected
        and no new ``Editor``/``Preview`` pair is created.
        """
        if file_path in self._tabs:
            self.set_active_tab(file_path)
            return self._tabs[file_path]

        title = Path(file_path).name
        tab = Tab(file_path, title, editor, preview, banner=banner)
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
        self._scroll_to_active_tab()
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

    def close_others(self, file_path: str) -> None:
        """Close all tabs except the one at *file_path*."""
        if file_path not in self._tabs:
            return
        for path in list(self._tabs.keys()):
            if path != file_path:
                self.close_tab(path)
        self.set_active_tab(file_path)

    def close_left(self, file_path: str) -> None:
        """Close all tabs to the left of *file_path*."""
        paths = self.get_all_paths()
        if file_path not in paths:
            return
        idx = paths.index(file_path)
        for path in paths[:idx]:
            self.close_tab(path)

    def close_right(self, file_path: str) -> None:
        """Close all tabs to the right of *file_path*."""
        paths = self.get_all_paths()
        if file_path not in paths:
            return
        idx = paths.index(file_path)
        for path in paths[idx + 1:]:
            self.close_tab(path)

    def get_tab(self, file_path: str) -> Tab | None:
        """Return the ``Tab`` for *file_path*, or ``None``."""
        return self._tabs.get(file_path)

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

    def update_path(self, old_path: str, new_path: str) -> None:
        """Rename an open tab from *old_path* to *new_path*.

        Updates the internal dict key, ``Tab`` attributes, the tab
        widget, and emits ``tab-renamed`` so that the content stack
        can be updated by the caller.
        """
        if old_path not in self._tabs:
            return
        tab = self._tabs.pop(old_path)
        tab.file_path = new_path
        if tab.editor:
            tab.editor.set_file_path(new_path)
        tab.title = Path(new_path).name
        self._tabs[new_path] = tab

        # Update _current_path if this was the active tab.
        if self._current_path == old_path:
            self._current_path = new_path

        # Update the tab widget label, tooltip, and stash.
        for child in self._box:
            if getattr(child, "_file_path", None) == old_path:
                child._file_path = new_path  # type: ignore[attr-defined]
                for grandchild in child:
                    if isinstance(grandchild, Gtk.Label):
                        grandchild.set_label(tab.title)
                child.set_tooltip_text(self._compute_relative_path(new_path))
                break

        self.emit("tab-renamed", old_path, new_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_relative_path(self, file_path: str) -> str:
        """Compute a path relative to the matching vault root, or fall back to filename."""
        for vault in self._vault_paths:
            try:
                rel = str(Path(file_path).relative_to(vault))
                return rel
            except ValueError:
                continue
        return Path(file_path).name

    def _build_tab_widget(self, file_path: str, title: str) -> Gtk.Box:
        """Create the visual widget for a single tab."""
        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        container.add_css_class("tab")
        container.set_margin_top(4)
        container.set_margin_bottom(4)
        container.set_margin_start(2)
        container.set_margin_end(2)

        label = Gtk.Label(label=title)
        label.add_css_class("label")
        label.set_ellipsize(3)
        container.append(label)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.set_size_request(24, 24)
        close_btn.set_tooltip_text("Close tab")
        close_btn.connect(
            "clicked", lambda _btn, cw=container: self.close_tab(cw._file_path)
        )
        container.append(close_btn)

        # Left-click: activate tab.
        gesture = Gtk.GestureClick()
        gesture.connect(
            "released",
            lambda _g, _n, _x, _y, cw=container: self.set_active_tab(cw._file_path),
        )
        container.add_controller(gesture)

        # Right-click: context menu.
        right_click = Gtk.GestureClick()
        right_click.set_button(3)  # GDK_BUTTON_SECONDARY
        right_click.connect(
            "released",
            lambda _g, _n, _x, _y, cw=container: self._show_context_menu(cw, _x, _y),
        )
        container.add_controller(right_click)

        # Tooltip with relative path.
        container.set_tooltip_text(self._compute_relative_path(file_path))

        # Stash the path on the widget for style look-ups.
        container._file_path = file_path  # type: ignore[attr-defined]
        return container

    def _show_context_menu(self, widget: Gtk.Box, x: float, y: float) -> None:
        """Show the context menu for *widget* at the given coordinates."""
        path = getattr(widget, "_file_path", None)
        if not path:
            return
        self._context_menu_target = path

        model = Gio.Menu()
        model.append("Copy path", "tab.copy-path")
        model.append("Close", "tab.close")
        model.append("Close others", "tab.close-others")
        model.append("Close left", "tab.close-left")
        model.append("Close right", "tab.close-right")

        popover = Gtk.PopoverMenu.new_from_model(model)
        popover.set_parent(widget)
        popover.set_has_arrow(False)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

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

    def _scroll_to_active_tab(self) -> None:
        """Scroll the tab bar so the active tab widget is visible."""
        for child in self._box:
            fp = getattr(child, "_file_path", None)
            if fp == self._current_path:
                adj = self._scrolled.get_hadjustment()
                alloc = child.get_allocation()
                page_size = adj.get_page_size()
                new_val = alloc.x + alloc.width / 2 - page_size / 2
                adj.set_value(max(0, new_val))
                return

    def _set_tab_unmodified(self, file_path: str, dirty: bool) -> None:
        """Add/remove the ``tab-unmodified`` CSS class to mark unsaved tabs."""
        for child in self._box:
            fp = getattr(child, "_file_path", None)
            if fp is None or fp != file_path:
                continue
            if dirty:
                child.add_css_class("tab-unmodified")
            else:
                child.remove_css_class("tab-unmodified")

    def set_tab_warning(self, file_path: str, active: bool) -> None:
        """Add/remove warning highlight on the tab for *file_path*."""
        for child in self._box:
            fp = getattr(child, "_file_path", None)
            if fp is None or fp != file_path:
                continue
            if active:
                child.add_css_class("warning")
            else:
                child.remove_css_class("warning")
