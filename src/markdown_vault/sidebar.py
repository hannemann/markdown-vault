"""Markdown Vault — right sidebar.

Provides four switchable sub-views:

* **Outline** — headings extracted from the current Markdown file.
* **Backlinks** — files linking to the current file via ``[[wikilink]]``.
* **Git** — working-tree status and diff preview.
* **Details** — file metadata (path, word count, size, last modified).
"""

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

import yaml

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GLib, GObject, Gdk

from . import git_integration, tags
from .backlink_index import BacklinkIndex
from .event_router import FileEvent
from .path_utils import HEADING_RE

logger = logging.getLogger(__name__)


# Leading YAML frontmatter block: --- ... --- at the very top of the file.
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.DOTALL)

# (name, symbolic icon, tooltip) for the vertical section rail — icons are
# placeholders and can be swapped later.
_SIDEBAR_SECTIONS = [
    ("outline", "view-list-symbolic", "Outline"),
    ("backlinks", "insert-link-symbolic", "Backlinks"),
    ("graph", "network-wired-symbolic", "Graph"),
    ("metadata", "document-properties-symbolic", "Metadaten"),
    ("git", "media-flash-symbolic", "Git"),
    ("details", "dialog-information-symbolic", "Details"),
]


def _parse_frontmatter(text: str) -> dict:
    """Return the leading YAML frontmatter as a dict, or ``{}`` if none/invalid."""
    if not text:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _format_meta_value(value) -> str:
    """Render a frontmatter value as a compact string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


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
        # Middle-click / Ctrl+click on a backlink or graph node → new tab.
        "file-open-new-tab": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "outline-clicked": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(
        self,
        backlink_index: BacklinkIndex | None = None,
        get_active_tab_info=None,
        get_graph_payload=None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.set_size_request(280, -1)
        self.set_visible(False)
        self.add_css_class("app-sidebar")

        self._current_file: str | None = None
        self._vault_paths: list[str] = []
        self._backlink_index = backlink_index or BacklinkIndex()
        self._git_generation: int = 0
        self._get_active_tab_info = get_active_tab_info
        # Graph panel: a callback returns the local-graph payload for a file; the
        # GraphView (a WebKit WebView) is created lazily on first use so the web
        # process only spawns if the user actually opens the Graph tab.
        self._get_graph_payload = get_graph_payload
        self._graph_view = None

        # --- Sub-view stack ---
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_vexpand(True)

        self._outline_list = self._make_scrollable_list()
        self._outline_list["list"].set_spacing(0)  # continuous indent guides
        self._stack.add_titled(self._outline_list["parent"], "outline", "Outline")

        self._backlinks_list = self._make_scrollable_list()
        self._stack.add_titled(self._backlinks_list["parent"], "backlinks", "Backlinks")

        # Graph panel — an empty host; the GraphView fills it on first switch.
        self._graph_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._graph_panel.set_vexpand(True)
        self._stack.add_titled(self._graph_panel, "graph", "Graph")

        self._metadata_list = self._make_scrollable_list()
        self._stack.add_titled(self._metadata_list["parent"], "metadata", "Metadaten")

        self._git_page = self._build_git_page()
        self._stack.add_titled(self._git_page, "git", "Git")

        self._details_page = self._build_details_page()
        self._stack.add_titled(self._details_page, "details", "Details")

        self._stack.set_hexpand(True)
        self.append(self._stack)
        self.append(self._build_rail())

        # Lazy git refresh: load when user switches to Git tab.
        self._stack.connect(
            "notify::visible-child-name", self._on_stack_page_changed,
        )

    def _build_rail(self) -> Gtk.Box:
        """Vertical icon rail (JetBrains-style) that switches the section stack."""
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail.add_css_class("sidebar-rail")
        self._rail_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for name, icon, tooltip in _SIDEBAR_SECTIONS:
            btn = Gtk.ToggleButton(icon_name=icon)
            btn.set_tooltip_text(tooltip)
            btn.add_css_class("flat")
            btn.add_css_class("sidebar-rail-btn")
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_rail_toggled, name)
            rail.append(btn)
            self._rail_buttons[name] = btn
        self._rail_buttons["outline"].set_active(True)
        return rail

    def _on_rail_toggled(self, btn: Gtk.ToggleButton, name: str) -> None:
        if btn.get_active():
            self._stack.set_visible_child_name(name)

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
        self._refresh_metadata(text)
        if self.get_visible() and self._stack.get_visible_child_name() == "git":
            self._refresh_git(file_path)
        if self.get_visible() and self._stack.get_visible_child_name() == "graph":
            self._refresh_graph()  # re-centre on the new current file
        self._refresh_details(file_path, text)

    def update_text_only(self, file_path: str | None, text: str = "") -> None:
        """Refresh only outline, metadata and details (cheap, per keystroke)."""
        self._current_file = file_path
        self._refresh_outline(text)
        self._refresh_metadata(text)
        self._refresh_details(file_path, text)

    def refresh_backlinks(self, file_path: str | None) -> None:
        """Refresh only the backlinks sub-view for *file_path*."""
        self._current_file = file_path
        self._refresh_backlinks(file_path)

    def refresh(self, event: FileEvent) -> None:
        """Refresh the sidebar in response to a file event.

        Acts as the single entry-point for all file-system events
        coming from ``FileEventDispatcher``.

        External file events must never hijack the sidebar away from
        the active tab.  Always refresh with the active tab's file
        path and editor text, not the event's file path with empty
        text.
        """
        get_info = getattr(self, "_get_active_tab_info", None)
        if get_info is not None:
            file_path, text = self._get_active_tab_info()
            if file_path is not None:
                match event.event_type:
                    case "content_changed":
                        self.update_text_only(file_path, text)
                    case _:
                        self.update_for_file(file_path, text)
                return

    def _on_stack_page_changed(self, _stack, _pspec) -> None:
        """Lazily refresh the git / graph panels when switched to."""
        name = self._stack.get_visible_child_name()
        if name == "git" and self._current_file:
            self._refresh_git(self._current_file)
        elif name == "graph":
            self._refresh_graph()

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------

    def _refresh_graph(self) -> None:
        """(Re)centre the graph on the current file — lazily creating the view."""
        if self._get_graph_payload is None:
            return
        if self._graph_view is None:
            from .graph_view import GraphView
            self._graph_view = GraphView()
            self._graph_view.connect(
                "node-activated",
                lambda _v, path: self.emit("file-open-requested", path))
            self._graph_view.connect(
                "node-activated-new-tab",
                lambda _v, path: self.emit("file-open-new-tab", path))
            self._graph_panel.append(self._graph_view)
        self._graph_view.set_graph(self._get_graph_payload(self._current_file))

    def teardown(self) -> None:
        """Release the graph WebView (clean WebKit shutdown)."""
        if self._graph_view is not None:
            self._graph_view.teardown()

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
                match = HEADING_RE.match(line)
                if match:
                    level = len(match.group(1))
                    heading = match.group(2)
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                    for _ in range(level - 1):
                        guide = Gtk.Box()
                        guide.set_size_request(20, -1)
                        guide.add_css_class("outline-guide")
                        row.append(guide)
                    label = Gtk.Label(label=heading)
                    label.set_xalign(0)
                    btn = Gtk.Button()
                    btn.set_child(label)
                    btn.add_css_class("flat")
                    btn.add_css_class("outline-item")
                    btn.add_css_class(f"outline-l{min(level, 4)}")
                    btn.set_halign(Gtk.Align.START)
                    btn.connect(
                        "clicked",
                        lambda _b, ln=line_num: self.emit("outline-clicked", ln),
                    )
                    row.append(btn)
                    self._outline_list["list"].append(row)

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
        box = self._backlinks_list["list"]
        vault_roots = [Path(v) for v in self._vault_paths]
        groups: dict[str, list[Path]] = {}
        for bl in backlinks:
            groups.setdefault(
                self._vault_for_path(bl, vault_roots), []
            ).append(bl)
        first = True
        for vault_name in sorted(groups):
            header = Gtk.Label(label=vault_name)
            header.set_xalign(0)
            header.add_css_class("dim-label")
            header.add_css_class("heading")
            if not first:
                header.set_margin_top(8)
            first = False
            box.append(header)
            for bl in sorted(groups[vault_name], key=lambda p: p.name.lower()):
                btn = Gtk.Button(label=bl.name)
                btn.add_css_class("flat")
                btn.set_halign(Gtk.Align.START)
                btn.set_margin_start(8)
                btn.set_tooltip_text(str(bl))
                btn.connect(
                    "clicked",
                    lambda _b, p=str(bl): self.emit("file-open-requested", p),
                )
                # Middle-click → open in a new tab (browser-style).
                mid = Gtk.GestureClick(button=2)
                mid.connect(
                    "released",
                    lambda _g, _n, _x, _y, p=str(bl): self.emit("file-open-new-tab", p),
                )
                btn.add_controller(mid)
                # Ctrl+left-click → new tab too: a CAPTURE-phase gesture sees the
                # press before the button and claims it so "clicked" (in-place)
                # doesn't also fire.
                ctrl = Gtk.GestureClick(button=1)
                ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                ctrl.connect("pressed", self._on_backlink_ctrl_pressed, str(bl))
                btn.add_controller(ctrl)
                box.append(btn)

    def _on_backlink_ctrl_pressed(self, gesture, _n, _x, _y, path: str) -> None:
        if gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # suppress in-place
            self.emit("file-open-new-tab", path)

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
    # Metadata (frontmatter)
    # ------------------------------------------------------------------

    def _refresh_metadata(self, text: str) -> None:
        """Show the file's YAML frontmatter as key/value rows."""
        box = self._metadata_list["list"]
        self._clear_list(box)
        fields = _parse_frontmatter(text)
        if not fields:
            box.append(self._empty_label("No frontmatter"))
            return
        for key, value in fields.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            key_lbl = Gtk.Label(label=str(key))
            key_lbl.set_xalign(0)
            key_lbl.set_valign(Gtk.Align.START)
            key_lbl.add_css_class("dim-label")
            val_lbl = Gtk.Label(label=_format_meta_value(value))
            val_lbl.set_xalign(0)
            val_lbl.set_wrap(True)
            val_lbl.set_selectable(True)
            val_lbl.set_hexpand(True)
            row.append(key_lbl)
            row.append(val_lbl)
            box.append(row)

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
    def _vault_for_path(path: Path, vault_roots: list[Path]) -> str:
        """Return the name of the vault containing *path*.

        Picks the most specific (longest) matching vault root; falls back to
        "Other" when the path lies outside every known vault.
        """
        best: Path | None = None
        for root in vault_roots:
            if path == root or path.is_relative_to(root):
                if best is None or len(str(root)) > len(str(best)):
                    best = root
        return best.name if best is not None else "Other"

    @staticmethod
    def _empty_label(text: str) -> Gtk.Label:
        """Return a dimmed placeholder label."""
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.add_css_class("dim-label")
        return lbl

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_to_file(self, path: str | Path) -> None:
        """Write sidebar state as JSON to *path* (overwrites)."""
        try:
            backlinks = []
            if self._current_file:
                backlinks = self._backlink_index.find_backlinks(self._current_file)
            data = {
                "current_file": self._current_file,
                "vault_paths": self._vault_paths,
                "backlinks": backlinks,
            }
            Path(path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to dump Sidebar to %s", path, exc_info=True)
