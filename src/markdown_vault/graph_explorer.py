"""Full-graph explorer — the main-area widget for the Graph view mode.

Wraps a :class:`GraphView` with a toolbar: a search box (highlight + zoom to
matches), tag chips (show only nodes carrying a selected tag), and a scope
toggle (current vault / all vaults).  Clicking a node re-emits ``node-activated``
so the host can open the file in the current tab.

Data comes from a ``get_payload(scope)`` callback (scope is ``"current"`` or
``"all"``), so the widget stays decoupled from how the graph is assembled.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GObject

from .graph_view import GraphView


class GraphExplorer(Gtk.Box):
    __gsignals__ = {
        "node-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, get_payload=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._get_payload = get_payload
        self._active_tags: set = set()

        # ── toolbar ──
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(6)
        bar.set_margin_bottom(4)
        self._search = Gtk.SearchEntry(hexpand=True)
        self._search.set_placeholder_text("Search nodes…")
        self._search.connect("search-changed", self._on_search)
        self._scope = Gtk.DropDown.new_from_strings(["Current vault", "All vaults"])
        self._scope.set_tooltip_text("Graph scope")
        self._scope.connect("notify::selected", lambda *_: self.refresh())
        bar.append(self._search)
        bar.append(self._scope)
        self.append(bar)

        # ── tag chips (wrap) ──
        self._chips = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=100,
            row_spacing=4, column_spacing=4)
        self._chips.set_margin_start(8)
        self._chips.set_margin_end(8)
        self._chips.set_margin_bottom(4)
        self.append(self._chips)

        self._graph = GraphView()
        self._graph.set_vexpand(True)
        self._graph.connect(
            "node-activated", lambda _v, path: self.emit("node-activated", path))
        self.append(self._graph)

    def scope(self) -> str:
        return "all" if self._scope.get_selected() == 1 else "current"

    def refresh(self) -> None:
        """Reload the payload for the current scope and rebuild the UI."""
        if self._get_payload is None:
            return
        payload = self._get_payload(self.scope())
        self._search.set_text("")
        self._active_tags.clear()
        self._build_chips(payload)
        self._graph.set_graph(payload)

    # ── tags ──

    def _build_chips(self, payload: dict) -> None:
        child = self._chips.get_child_at_index(0)
        while child is not None:
            self._chips.remove(child)
            child = self._chips.get_child_at_index(0)
        tags = sorted({t for n in payload.get("nodes", []) for t in n.get("tags", [])})
        self._chips.set_visible(bool(tags))
        for tag in tags:
            btn = Gtk.ToggleButton(label=tag)
            btn.add_css_class("chip")
            btn.connect("toggled", self._on_chip, tag)
            self._chips.append(btn)

    def _on_chip(self, btn: Gtk.ToggleButton, tag: str) -> None:
        if btn.get_active():
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)
        self._graph.set_tag_filter(sorted(self._active_tags))

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._graph.search(entry.get_text())

    def teardown(self) -> None:
        self._graph.teardown()
