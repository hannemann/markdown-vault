"""Graph-cards sidebar panel — a pinboard of nodes collected from the graph.

A single click on a node in the full-graph explorer collects it here as a card
holding the node's info-panel content (title, description, vault) plus a link to the
file. Clicking a card opens the file; a per-card ✕ and a header "remove all" button
clear them. The collection itself is the pure :class:`CardStore`; this is its view.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, GObject, Gtk, Pango

from markdown_vault.core.i18n import _
from markdown_vault.ui.card_store import Card, CardStore

logger = logging.getLogger(__name__)


class GraphCardsPanel(Gtk.Box):
    __gsignals__ = {
        # card-open-requested(file_path): the user clicked a card to open its file.
        "card-open-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._store = CardStore()
        self._rows: dict[str, Gtk.Widget] = {}

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(6)
        header.set_margin_bottom(4)
        title = Gtk.Label(label=_("Cards"), xalign=0, hexpand=True)
        title.add_css_class("heading")
        self._clear_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self._clear_btn.add_css_class("flat")
        self._clear_btn.set_tooltip_text(_("Remove all cards"))
        self._clear_btn.set_sensitive(False)
        self._clear_btn.connect("clicked", lambda *_: self.clear())
        header.append(title)
        header.append(self._clear_btn)
        self.append(header)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list.set_margin_start(8)
        self._list.set_margin_end(8)
        self._list.set_margin_bottom(8)
        self._empty = Gtk.Label(
            label=_("No cards yet.\n\nClick a node in the graph to collect it here; "
                    "double-click a node to open its file."))
        self._empty.add_css_class("dim-label")
        self._empty.set_wrap(True)
        self._empty.set_justify(Gtk.Justification.CENTER)
        self._empty.set_margin_top(16)
        self._list.append(self._empty)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self._list)
        self.append(scroller)

    def add_card(self, path: str, title: str, desc: str,
                 vault: str, color: str) -> bool:
        """Collect a node as a card. Returns False if it was already present."""
        card = Card(path=path, title=title, desc=desc, vault=vault, color=color)
        if not self._store.add(card):
            return False
        row = self._make_row(card)
        self._rows[path] = row
        self._list.append(row)
        self._sync_empty()
        return True

    def remove(self, path: str) -> None:
        if not self._store.remove(path):
            return
        row = self._rows.pop(path, None)
        if row is not None:
            self._list.remove(row)
        self._sync_empty()

    def clear(self) -> None:
        for row in self._rows.values():
            self._list.remove(row)
        self._rows.clear()
        self._store.clear()
        self._sync_empty()

    def _sync_empty(self) -> None:
        has = len(self._store) > 0
        self._empty.set_visible(not has)
        self._clear_btn.set_sensitive(has)

    def _make_row(self, card: Card) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        row.add_css_class("card")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=card.title, xalign=0)
        title.add_css_class("heading")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(title)
        if card.desc:
            desc = Gtk.Label(label=card.desc, xalign=0)
            desc.add_css_class("dim-label")
            desc.add_css_class("caption")
            desc.set_wrap(True)
            desc.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            desc.set_lines(2)
            desc.set_ellipsize(Pango.EllipsizeMode.END)
            content.append(desc)
        if card.vault:
            content.append(self._vault_line(card))

        opener = Gtk.Button()
        opener.add_css_class("flat")
        opener.set_hexpand(True)
        opener.set_child(content)
        opener.set_tooltip_text(_("Open file"))
        opener.connect("clicked", lambda *_: self.emit("card-open-requested", card.path))
        row.append(opener)

        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.set_valign(Gtk.Align.START)
        close.set_tooltip_text(_("Remove card"))
        close.connect("clicked", lambda *_: self.remove(card.path))
        row.append(close)
        return row

    def _vault_line(self, card: Card) -> Gtk.Label:
        """A dim 'vault' line prefixed by the node's colour as a bullet."""
        name = GLib.markup_escape_text(card.vault)
        if card.color:
            color = GLib.markup_escape_text(card.color)
            markup = '<span foreground="%s">●</span> %s' % (color, name)
        else:
            markup = name
        label = Gtk.Label(xalign=0)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_markup(markup)
        return label
