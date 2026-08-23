"""Shared vault-scope selector for all searches (full-text, semantic, Ask).

A small ``Gtk.DropDown`` whose items are: the active vault (``current``, shown
first and preselected), ``All vaults``, then the remaining vaults by name. The
selected scope lives in the host (via ``get_scope``/``set_scope``) so every
search surface — the bottom search bar and the quick-open palette — shares one
setting; each embeds its own instance and calls :meth:`refresh` when shown or
when the active vault changes.

Scope values: ``"current"`` (follow the active vault), ``"all"``, or a specific
vault root path.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_vault.core.i18n import _


class VaultScope(Gtk.DropDown):
    def __init__(self, get_vaults, get_active, get_scope, set_scope, on_change=None):
        super().__init__()
        self._get_vaults = get_vaults    # () -> list[(name, path)]
        self._get_active = get_active    # () -> path | None
        self._get_scope = get_scope      # () -> "current" | "all" | path
        self._set_scope = set_scope      # (scope) -> None
        self._on_change = on_change      # optional: re-run the host's search
        self._values: list = []
        self._syncing = False
        self._model = Gtk.StringList()
        self.set_model(self._model)
        self.add_css_class("flat")
        self.set_tooltip_text(
            _("Search scope — current vault, all vaults, or a specific vault"))
        self.connect("notify::selected", self._on_selected)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the item list (active vault → All → the rest) and re-select
        the stored scope. Call on show and when the active vault changes."""
        vaults = list(self._get_vaults())
        active = self._get_active()
        active_name = next((n for n, p in vaults if p == active), None)
        items, values = [], []
        if active and active_name is not None:
            items.append(_("{name} (current)").format(name=active_name))
            values.append("current")
        items.append(_("All vaults"))
        values.append("all")
        for name, path in vaults:
            if path != active:
                items.append(name)
                values.append(path)
        self._values = values
        self._syncing = True
        self._model.splice(0, self._model.get_n_items(), items)
        scope = self._get_scope()
        try:
            idx = values.index(scope)
        except ValueError:
            # stored scope no longer offered (vault removed) → select the first item
            idx = 0
        self.set_selected(idx)
        self._syncing = False

    def _on_selected(self, *_args) -> None:
        if self._syncing:
            return
        i = self.get_selected()
        if 0 <= i < len(self._values):
            self._set_scope(self._values[i])
            if self._on_change:
                self._on_change()
