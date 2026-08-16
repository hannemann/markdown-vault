"""Tests for OKF reserved files (index.md / log.md) in the vault tree:
their icons and index.md sorting to the top of its folder.
"""

import unittest

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio

from markdown_vault.vault.vault_tree import (
    VaultNode, FILE_ICON, FOLDER_ICON, _insert_sorted,
)


def _order(nodes):
    """Names in the order _insert_sorted (via _node_cmp) settles them."""
    store = Gio.ListStore(item_type=VaultNode)
    for n in nodes:
        _insert_sorted(store, n)
    return [store.get_item(i).name for i in range(store.get_n_items())]


class TestReservedIcons(unittest.TestCase):
    def test_index_icon(self):
        self.assertEqual(VaultNode("index.md", "/v/index.md", False).icon,
                         "emblem-documents-symbolic")

    def test_log_icon(self):
        self.assertEqual(VaultNode("log.md", "/v/log.md", False).icon,
                         "document-open-recent-symbolic")

    def test_case_insensitive(self):
        self.assertEqual(VaultNode("INDEX.md", "/v/INDEX.md", False).icon,
                         "emblem-documents-symbolic")

    def test_regular_file_keeps_default_icon(self):
        self.assertEqual(VaultNode("erde.md", "/v/erde.md", False).icon, FILE_ICON)

    def test_directory_keeps_folder_icon(self):
        self.assertEqual(VaultNode("sub", "/v/sub", True).icon, FOLDER_ICON)

    def test_index_named_directory_is_not_reserved(self):
        # A folder literally named "index.md" is still a folder, not the overview.
        self.assertEqual(VaultNode("index.md", "/v/index.md", True).icon, FOLDER_ICON)


class TestSorting(unittest.TestCase):
    def _n(self, name, is_dir=False):
        return VaultNode(name, "/v/" + name, is_dir)

    def test_index_leads_before_directories_and_files(self):
        order = _order([
            self._n("beta.md"),
            self._n("alpha", is_dir=True),
            self._n("index.md"),
            self._n("apple.md"),
        ])
        self.assertEqual(order, ["index.md", "alpha", "apple.md", "beta.md"])

    def test_log_sorts_as_a_regular_file(self):
        order = _order([
            self._n("zeta.md"),
            self._n("log.md"),
            self._n("alpha.md"),
        ])
        self.assertEqual(order, ["alpha.md", "log.md", "zeta.md"])

    def test_directories_still_precede_files_without_index(self):
        order = _order([
            self._n("note.md"),
            self._n("sub", is_dir=True),
        ])
        self.assertEqual(order, ["sub", "note.md"])


if __name__ == "__main__":
    unittest.main()
