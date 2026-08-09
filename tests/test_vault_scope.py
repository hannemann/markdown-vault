"""Tests for markdown_vault.vault_scope.VaultScope — the shared scope dropdown."""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from markdown_vault.vault_scope import VaultScope

_VAULTS = [("A", "/v/a"), ("B", "/v/b"), ("C", "/v/c")]


class TestVaultScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def _make(self, active="/v/b", scope="current"):
        self.state = {"scope": scope}
        return VaultScope(
            lambda: _VAULTS, lambda: active,
            lambda: self.state["scope"],
            lambda s: self.state.__setitem__("scope", s))

    def _labels(self, vs):
        model = vs.get_model()
        return [model.get_string(i) for i in range(model.get_n_items())]

    def test_order_active_first_then_all_then_rest(self):
        vs = self._make(active="/v/b", scope="current")
        self.assertEqual(self._labels(vs), ["B (current)", "All vaults", "A", "C"])
        self.assertEqual(vs.get_selected(), 0)  # current preselected

    def test_select_all_vaults(self):
        vs = self._make()
        vs.set_selected(1)
        self.assertEqual(self.state["scope"], "all")

    def test_select_specific_vault_stores_its_path(self):
        vs = self._make()
        vs.set_selected(2)  # "A"
        self.assertEqual(self.state["scope"], "/v/a")

    def test_stale_scope_falls_back_to_first_item(self):
        vs = self._make(scope="/v/gone")  # no longer a configured vault
        self.assertEqual(vs.get_selected(), 0)

    def test_no_active_vault_omits_current_entry(self):
        vs = self._make(active=None, scope="current")
        self.assertEqual(self._labels(vs), ["All vaults", "A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
