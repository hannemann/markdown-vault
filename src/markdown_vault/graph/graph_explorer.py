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

from markdown_vault.core.i18n import _
from markdown_vault.graph.graph_view import GraphView


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
        self._search.set_placeholder_text(_("Search nodes…"))
        self._search.connect("search-changed", self._on_search)
        self._scope = Gtk.DropDown.new_from_strings([_("Current vault"), _("All vaults")])
        self._scope.set_tooltip_text(_("Graph scope"))
        self._scope.connect("notify::selected", lambda *_: self.refresh())

        # Tags live behind a single dropdown button so the list can't crowd out
        # the graph. A vertical checklist in the popover — one tag per row,
        # scrolls when there are many.
        self._chips = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._chips.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_width=200, max_content_height=360,
            propagate_natural_height=True)
        scroller.set_child(self._chips)
        pop = Gtk.Popover()
        pop.set_child(scroller)
        self._tags_btn = Gtk.MenuButton(label=_("Tags"), popover=pop)
        self._tags_btn.set_always_show_arrow(True)
        self._tags_btn.set_tooltip_text(_("Filter by tag"))

        self._fit = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic")
        self._fit.set_tooltip_text(_("Fit graph to view"))
        self._fit.connect("clicked", lambda *_: self._graph.fit())
        bar.append(self._search)
        bar.append(self._scope)
        bar.append(self._tags_btn)
        bar.append(self._fit)
        self.append(bar)

        self._graph = GraphView()
        self._graph.set_vexpand(True)
        # The overlay launcher always opens in a tab, so a middle-click node is
        # routed the same as a plain click.
        self._graph.connect(
            "node-activated", lambda _v, path: self.emit("node-activated", path))
        self._graph.connect(
            "node-activated-new-tab", lambda _v, path: self.emit("node-activated", path))
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
        row = self._chips.get_row_at_index(0)
        while row is not None:
            self._chips.remove(row)
            row = self._chips.get_row_at_index(0)
        tags = sorted({t for n in payload.get("nodes", []) for t in n.get("tags", [])})
        self._tags_btn.set_sensitive(bool(tags))
        for tag in tags:
            btn = Gtk.CheckButton(label=tag)
            btn.connect("toggled", self._on_chip, tag)
            self._chips.append(btn)
        self._update_tags_label()

    def _on_chip(self, btn: Gtk.CheckButton, tag: str) -> None:
        if btn.get_active():
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)
        self._graph.set_tag_filter(sorted(self._active_tags))
        self._update_tags_label()

    def _update_tags_label(self) -> None:
        n = len(self._active_tags)
        self._tags_btn.set_label(_("Tags ({n})").format(n=n) if n else _("Tags"))

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._graph.search(entry.get_text())

    def teardown(self) -> None:
        self._graph.teardown()
