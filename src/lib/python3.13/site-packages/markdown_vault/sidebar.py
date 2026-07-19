"""Markdown Vault — right sidebar.

Provides four switchable sub-views:

* **Outline** — headings extracted from the current Markdown file.
* **Backlinks** — files linking to the current file via ``[[wikilink]]``.
* **Git** — working-tree status and diff preview.
* **Details** — file metadata (path, word count, size, last modified).
"""

import logging
import re
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GLib, GObject

from . import git_integration, tags
from .backlink_index import BacklinkIndex

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class Sidebar(Gtk.Box):
    """Toggleable right sidebar with tabbed sub-views.

    Signals:
        file-open-requested(str): Emitted when the user clicks a
            backlink, requesting the referenced file to be opened.
        outline-clicked(int): Emitted when an outline heading is clicked.
            The argument is the 0-based line number in the editor.
    """

    __gsignals__ = {
        "file-open-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "outline-clicked": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(self, backlink_index: BacklinkIndex | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_size_request(260, -1)
        self.set_visible(False)

        self._current_file: str | None = None
        self._vault_paths: list[str] = []
        self._backlink_index = backlink_index or BacklinkIndex()
        self._git_generation: int = 0

        # --- Sub-view stack ---
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_vexpand(True)

        self._outline_list = self._make_scrollable_list()
        self._stack.add_titled(self._outline_list["parent"], "outline", "Outline")

        self._backlinks_list = self._make_scrollable_list()
        self._stack.add_titled(self._backlinks_list["parent"], "backlinks", "Backlinks")

        self._git_page = self._build_git_page()
        self._stack.add_titled(self._git_page, "git", "Git")

        self._details_page = self._build_details_page()
        self._stack.add_titled(self._details_page, "details", "Details")

        switcher = Gtk.StackSwitcher(stack=self._stack)
        switcher.set_margin_top(6)
        switcher.set_margin_bottom(6)
        self.append(switcher)
        self.append(self._stack)

        # Lazy git refresh: load when user switches to Git tab.
        self._stack.connect(
            "notify::visible-child-name", self._on_stack_page_changed,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_vault_paths(self, paths: list[str]) -> None:
        """Set the list of vault root paths (used for backlink search)."""
        self._vault_paths = list(paths)

    def update_for_file(self, file_path: str | None, text: str = "") -> None:
        """Refresh all sub-views for *file_path*.

        Pass ``None`` to reset all views to their empty state.
        """
        self._current_file = file_path
        self._refresh_outline(text)
        self._refresh_backlinks(file_path)
        if self.get_visible() and self._stack.get_visible_child_name() == "git":
            self._refresh_git(file_path)
        self._refresh_details(file_path, text)

    def update_text_only(self, file_path: str | None, text: str = "") -> None:
        """Refresh only outline and details (cheap, safe for every keystroke)."""
        self._current_file = file_path
        self._refresh_outline(text)
        self._refresh_details(file_path, text)

    def _on_stack_page_changed(self, _stack, _pspec) -> None:
        """Refresh git when the user switches to the Git tab."""
        if self._stack.get_visible_child_name() == "git" and self._current_file:
            self._refresh_git(self._current_file)

    # ------------------------------------------------------------------
    # Outline
    # ------------------------------------------------------------------

    def _refresh_outline(self, text: str) -> None:
        """Populate the outline list from Markdown headings in *text*.

        Skips headings that appear inside fenced code blocks (``` or ~~~).
        """
        self._clear_list(self._outline_list["list"])
        if not text:
            return

        in_fence = False
        fence_char = None
        fence_indent = 0

        # Single pass through lines to track fence state
        lines = text.split('\n')
        for line_num, line in enumerate(lines):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            # Check for fence start/end
            if stripped.startswith('```') or stripped.startswith('~~~'):
                if not in_fence:
                    in_fence = True
                    fence_char = stripped[0]
                    fence_indent = indent
                elif stripped[0] == fence_char and indent == fence_indent:
                    in_fence = False
                    fence_char = None
                    fence_indent = 0

            # Check for heading
            if not in_fence:
                match = _HEADING_RE.match(line)
                if match:
                    level = len(match.group(1))
                    heading = match.group(2)
                    label = Gtk.Label(label=f"{'  ' * (level - 1)}\u25cf {heading}")
                    label.set_xalign(0)
                    label.add_css_class("outline-item")
                    label.set_size_request(-1, 28)
                    gesture = Gtk.GestureClick()
                    gesture.connect(
                        "released",
                        lambda _g, _n, _x, _y, ln=line_num: self.emit("outline-clicked", ln),
                    )
                    label.add_controller(gesture)
                    self._outline_list["list"].append(label)

    # ------------------------------------------------------------------
    # Backlinks
    # ------------------------------------------------------------------

    def _refresh_backlinks(self, file_path: str | None) -> None:
        """Populate the backlinks list from the index."""
        self._clear_list(self._backlinks_list["list"])
        if not file_path or not self._vault_paths:
            self._backlinks_list["list"].append(
                self._empty_label("Open a file to see backlinks")
            )
            return
        backlinks = [
            Path(p) for p in self._backlink_index.find_backlinks(file_path)
        ]
        if not backlinks:
            self._backlinks_list["list"].append(
                self._empty_label("No backlinks found")
            )
            return
        for bl in backlinks:
            btn = Gtk.Button(label=bl.name)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.START)
            btn.set_tooltip_text(str(bl))
            btn.connect(
                "clicked",
                lambda _b, p=str(bl): self.emit("file-open-requested", p),
            )
            self._backlinks_list["list"].append(btn)

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    def _build_git_page(self) -> Gtk.ScrolledWindow:
        """Create the git status / diff sub-view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        self._git_status_label = Gtk.Label(label="No git repo")
        self._git_status_label.set_xalign(0)
        self._git_status_label.set_wrap(True)
        box.append(self._git_status_label)

        self._git_diff_label = Gtk.Label(label="")
        self._git_diff_label.set_xalign(0)
        self._git_diff_label.set_wrap(True)
        self._git_diff_label.add_css_class("mono")
        box.append(self._git_diff_label)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(box)
        scrolled.set_vexpand(True)
        return scrolled

    def _refresh_git(self, file_path: str | None) -> None:
        """Update the git sub-view for the file's repository (async)."""
        if not file_path:
            self._git_status_label.set_text("No file open")
            self._git_diff_label.set_text("")
            return
        repo_dir = Path(file_path).parent
        self._git_generation += 1
        gen = self._git_generation
        status_label = self._git_status_label
        diff_label = self._git_diff_label

        def _work():
            if not git_integration.is_git_repo(repo_dir):
                return gen, False, "", ""
            status = git_integration.get_status(repo_dir)
            diff = git_integration.get_diff(repo_dir)
            return gen, True, status, diff

        def _apply(res):
            g, is_repo, status, diff = res
            if g != self._git_generation:
                return False
            if not is_repo:
                status_label.set_text("Not a git repository")
                diff_label.set_text("")
            elif status:
                lines = [f"{e['status']}  {e['path']}" for e in status]
                status_label.set_text("\n".join(lines))
                diff_label.set_text(diff[:2000] if diff else "")
            else:
                status_label.set_text("Working tree clean")
                diff_label.set_text(diff[:2000] if diff else "")
            return False

        def _run():
            try:
                res = _work()
            except Exception:
                logger.warning("Git worker exception", exc_info=True)
                return
            GLib.idle_add(_apply, res)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Details
    # ------------------------------------------------------------------

    def _build_details_page(self) -> Gtk.ScrolledWindow:
        """Create the file-details sub-view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        self._details_label = Gtk.Label(label="No file open")
        self._details_label.set_xalign(0)
        self._details_label.set_wrap(True)
        box.append(self._details_label)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(box)
        scrolled.set_vexpand(True)
        return scrolled

    def _refresh_details(self, file_path: str | None, text: str) -> None:
        """Update file metadata display."""
        if not file_path:
            self._details_label.set_text("No file open")
            return
        p = Path(file_path)
        try:
            stat = p.stat()
        except OSError:
            logger.warning("Cannot stat file: %s", file_path, exc_info=True)
            self._details_label.set_text("Cannot read file info")
            return
        word_count = len(text.split()) if text else 0
        line_count = text.count("\n") + 1 if text else 0
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        self._details_label.set_text(
            f"File:  {p.name}\n"
            f"Path:  {p.parent}\n"
            f"Words: {word_count}\n"
            f"Lines: {line_count}\n"
            f"Size:  {stat.st_size:,} bytes\n"
            f"Modified: {modified}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_scrollable_list() -> dict:
        """Create a ``Gtk.Box`` wrapped in a ``Gtk.ScrolledWindow``."""
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_margin_top(8)
        inner.set_margin_start(8)
        inner.set_margin_end(8)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(inner)
        scrolled.set_vexpand(True)
        return {"parent": scrolled, "list": inner}

    @staticmethod
    def _clear_list(box: Gtk.Box) -> None:
        """Remove all children from *box*."""
        for child in list(box):
            box.remove(child)

    @staticmethod
    def _empty_label(text: str) -> Gtk.Label:
        """Return a dimmed placeholder label."""
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.add_css_class("dim-label")
        return lbl
