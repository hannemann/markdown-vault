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
from markdown_vault.graph.graph_view import GraphView, LENS_DEFAULTS, LENS_RANGES


class GraphExplorer(Gtk.Box):
    __gsignals__ = {
        "node-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # node-carded(file_path, colour): a single click collected the node as a card.
        "node-carded": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
    }

    def __init__(self, get_payload=None, lens_config=None,
                 on_lens_config_changed=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._get_payload = get_payload
        self._active_tags: set = set()
        self._lens = dict(LENS_DEFAULTS, **(lens_config or {}))
        self._on_lens_config_changed = on_lens_config_changed

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
        self._view_btn = self._build_view_menu()
        bar.append(self._search)
        bar.append(self._scope)
        bar.append(self._tags_btn)
        bar.append(self._fit)
        bar.append(self._view_btn)
        self.append(bar)

        # Collect mode: in the full explorer a single click collects a card and a
        # double click opens; the sidebar mini-graph keeps the default (click opens).
        self._graph = GraphView(collect_on_click=True)
        self._graph.set_vexpand(True)
        # The overlay launcher always opens in a tab, so a middle-click node is
        # routed the same as a plain click.
        self._graph.connect(
            "node-activated", lambda _v, path: self.emit("node-activated", path))
        self._graph.connect(
            "node-activated-new-tab", lambda _v, path: self.emit("node-activated", path))
        self._graph.connect(
            "node-carded", lambda _v, path, color: self.emit("node-carded", path, color))
        self.append(self._graph)
        self._apply_lens()   # push the initial config; the view applies it on load

    # ── cursor fisheye lens controls ──

    def _build_view_menu(self) -> Gtk.MenuButton:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self._fisheye_chk = Gtk.CheckButton(label=_("Fisheye lens"))
        self._fisheye_chk.set_active(self._lens["fisheye"])
        self._fisheye_chk.connect("toggled", self._on_lens_changed)
        box.append(self._fisheye_chk)

        self._labels_chk = Gtk.CheckButton(label=_("Cursor labels"))
        self._labels_chk.set_active(self._lens["labels"])
        self._labels_chk.connect("toggled", self._on_lens_changed)
        box.append(self._labels_chk)

        # LENS_RANGES are symmetric around the defaults, so the slider centre (marked with
        # a tick) is the recommended value.
        box.append(Gtk.Label(label=_("Lens size"), xalign=0, margin_top=4))
        self._radius_scale = self._slider("radius")
        box.append(self._radius_scale)

        box.append(Gtk.Label(label=_("Strength"), xalign=0, margin_top=4))
        self._strength_scale = self._slider("strength")
        box.append(self._strength_scale)

        box.append(Gtk.Label(label=_("Label radius"), xalign=0, margin_top=4))
        self._label_radius_scale = self._slider("label_radius")
        box.append(self._label_radius_scale)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6))
        self._sidebar_chk = Gtk.CheckButton(label=_("Also in the sidebar graph"))
        self._sidebar_chk.set_active(self._lens["lens_in_sidebar"])
        self._sidebar_chk.set_tooltip_text(
            _("Apply the cursor lens to the small graph in the sidebar too"))
        self._sidebar_chk.connect("toggled", self._on_lens_changed)
        box.append(self._sidebar_chk)

        pop = Gtk.Popover()
        pop.set_child(box)
        btn = Gtk.MenuButton(label=_("View"), popover=pop)
        btn.set_always_show_arrow(True)
        btn.set_tooltip_text(_("Cursor lens options"))
        return btn

    def _slider(self, key) -> Gtk.Scale:
        # Range, step, default tick and the seed value all come from `key`, so a slider
        # cannot be built for one option but seeded from another (LENS_* is the one source).
        lo, hi, step = LENS_RANGES[key]
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.add_mark(LENS_DEFAULTS[key], Gtk.PositionType.BOTTOM, None)   # tick = default
        scale.set_value(self._lens[key])   # set before connecting, so it fires no change
        scale.set_size_request(180, -1)
        scale.set_draw_value(False)
        scale.connect("value-changed", self._on_lens_changed)
        return scale

    def _on_lens_changed(self, *_a) -> None:
        self._lens = {
            "fisheye": self._fisheye_chk.get_active(),
            "labels": self._labels_chk.get_active(),
            "radius": self._radius_scale.get_value(),
            "strength": self._strength_scale.get_value(),
            "label_radius": self._label_radius_scale.get_value(),
            "lens_in_sidebar": self._sidebar_chk.get_active(),
        }
        self._apply_lens()
        if self._on_lens_config_changed is not None:
            self._on_lens_config_changed(dict(self._lens))

    def _apply_lens(self) -> None:
        self._graph.set_lens_config(
            self._lens["fisheye"], self._lens["labels"], self._lens["radius"],
            self._lens["strength"], self._lens["label_radius"])

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
