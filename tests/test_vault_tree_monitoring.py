"""Tests for VaultTree — inkrementelle Updates durch VaultMonitor.

Tests:
- _handle_file_created() fügt .md Node ein
- _handle_file_deleted() entfernt Node
- _handle_file_moved() aktualisiert Node
- Keine Duplikate
- Keine Crashes bei nicht-existierenden Paden

Der VaultTree baut auf den GTK4-List-Widgets (``Gtk.ListView`` +
``Gtk.TreeListModel``) auf; der Baum wird über die :class:`VaultNode`-Hierarchie
(``_iter_all_nodes``) inspiziert statt über einen ``Gtk.TreeStore``.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_vault.vault.vault_tree import VaultTree, VaultNode


def _file_paths(tree: VaultTree) -> list[str]:
    """All non-directory node paths currently in the tree."""
    return [n.path for n in tree._iter_all_nodes() if not n.is_dir]


def _all_paths(tree: VaultTree) -> list[str]:
    """All node paths (files and directories) currently in the tree."""
    return [n.path for n in tree._iter_all_nodes()]


class TestVaultTreeHandleFileCreated(unittest.TestCase):
    """VaultTree._handle_file_created."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "existing.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_handle_file_created_adds_node(self):
        new_file = Path(self.vault_path) / "newfile.md"
        new_file.touch()
        self.tree._handle_file_created(self.vault_path, str(new_file))
        self.assertIn(str(new_file), _file_paths(self.tree))

    def test_handle_file_created_no_duplicate(self):
        existing = str(Path(self.vault_path) / "existing.md")
        self.tree._handle_file_created(self.vault_path, existing)
        self.assertEqual(_file_paths(self.tree).count(existing), 1)

    def test_handle_file_created_subdirectory(self):
        subdir = Path(self.vault_path) / "subdir"
        subdir.mkdir(exist_ok=True)
        new_file = subdir / "sub.md"
        new_file.touch()
        self.tree._handle_file_created(str(subdir), str(new_file))
        self.assertIn(str(new_file), _file_paths(self.tree))

    def test_handle_file_created_non_md_ignored(self):
        txt_file = Path(self.vault_path) / "file.txt"
        txt_file.touch()
        self.tree._handle_file_created(self.vault_path, str(txt_file))
        self.assertNotIn(str(txt_file), _file_paths(self.tree))

    def test_handle_file_created_parent_not_expanded(self):
        subdir = Path(self.vault_path) / "hidden_sub"
        subdir.mkdir(exist_ok=True)
        new_file = subdir / "test.md"
        new_file.touch()
        self.tree._handle_file_created(str(subdir), str(new_file))
        self.assertIn(str(new_file), _file_paths(self.tree))

    def test_handle_file_created_empty_parent_creates_intermediate_dirs(self):
        deep = Path(self.vault_path) / "a" / "b" / "c"
        deep.mkdir(parents=True, exist_ok=True)
        new_file = deep / "deep.md"
        new_file.touch()
        self.tree._handle_file_created(str(deep), str(new_file))
        self.assertIn(str(new_file), _file_paths(self.tree))


class TestVaultTreeHandleFileDeleted(unittest.TestCase):
    """VaultTree._handle_file_deleted."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "delete_me.md").touch()
        (Path(self.vault_path) / "keep_me.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_handle_file_deleted_removes_node(self):
        delete_me = str(Path(self.vault_path) / "delete_me.md")
        self.tree._handle_file_deleted(delete_me)
        self.assertNotIn(delete_me, _file_paths(self.tree))

    def test_handle_file_deleted_keeps_other_files(self):
        delete_me = str(Path(self.vault_path) / "delete_me.md")
        keep_me = str(Path(self.vault_path) / "keep_me.md")
        self.tree._handle_file_deleted(delete_me)
        self.assertIn(keep_me, _file_paths(self.tree))

    def test_handle_file_deleted_nonexistent_is_noop(self):
        self.tree._handle_file_deleted("/nonexistent/file.md")
        self.assertIn(str(Path(self.vault_path) / "keep_me.md"), _file_paths(self.tree))


class TestVaultTreeHandleFileMoved(unittest.TestCase):
    """VaultTree._handle_file_moved."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "subdir").mkdir(exist_ok=True)
        self.moved_file = Path(self.vault_path) / "move_me.md"
        self.moved_file.touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_handle_file_moved_updates_path(self):
        new_parent = str(self._tmpdir / "testvault" / "subdir")
        old_path = str(self.moved_file)
        new_path = str(new_parent) + "/move_me.md"
        self.tree._handle_file_moved(old_path, new_parent, new_path)
        paths = _file_paths(self.tree)
        self.assertNotIn(old_path, paths)
        self.assertIn(new_path, paths)

    def test_handle_file_moved_old_not_in_tree_is_noop(self):
        new_parent = str(self._tmpdir / "testvault" / "subdir")
        self.tree._handle_file_moved("/nonexistent/file.md", new_parent, new_parent + "/file.md")

    def test_handle_file_moved_to_nonexistent_parent_is_noop(self):
        old_path = str(self.moved_file)
        self.tree._handle_file_moved(old_path, "/nonexistent", "/nonexistent/file.md")

    def test_handle_file_moved_to_non_md_removes_old_node(self):
        new_parent = str(self._tmpdir / "testvault" / "subdir")
        old_path = str(self.moved_file)
        new_path = str(new_parent) + "/move_me.txt"
        self.tree._handle_file_moved(old_path, new_parent, new_path)
        paths = _file_paths(self.tree)
        self.assertNotIn(old_path, paths)
        self.assertNotIn(new_path, paths)


class TestVaultTreeDeleteShortcut(unittest.TestCase):
    """DEL Shortcut: emit delete-requested für ausgewähltes Element."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "file.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_delete_shortcut_no_selection_noop(self):
        emitted = []
        self.tree.connect("delete-requested", lambda _, p: emitted.append(p))
        self.tree._on_delete_shortcut()
        self.assertEqual(emitted, [])

    def test_delete_shortcut_vault_root_blocked(self):
        emitted = []
        self.tree.connect("delete-requested", lambda _, p: emitted.append(p))
        with patch.object(self.tree, "get_selected_path", return_value=self.vault_path):
            self.tree._on_delete_shortcut()
        self.assertEqual(emitted, [])

    def test_delete_shortcut_emits_signal(self):
        file_path = str(Path(self.vault_path) / "file.md")
        emitted = []
        self.tree.connect("delete-requested", lambda _, p: emitted.append(p))
        with patch.object(self.tree, "get_selected_path", return_value=file_path):
            self.tree._on_delete_shortcut()
        self.assertEqual(emitted, [file_path])


class TestVaultTreeFocusFile(unittest.TestCase):
    """Tests for VaultTree.focus_file() and focus-in-tree button."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "note.md").touch()
        subdir = Path(self.vault_path) / "sub"
        subdir.mkdir(exist_ok=True)
        (subdir / "deep.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_focus_file_selects_existing_file(self):
        file_path = str(Path(self.vault_path) / "note.md")
        self.tree.focus_file(file_path)
        self.assertEqual(self.tree.get_selected_path(), file_path)

    def test_focus_file_selects_nested_file(self):
        file_path = str(Path(self.vault_path) / "sub" / "deep.md")
        self.tree.focus_file(file_path)
        self.assertEqual(self.tree.get_selected_path(), file_path)

    def test_focus_file_no_crash_on_missing_path(self):
        self.tree.focus_file("/nonexistent/path.md")
        self.assertIsNone(self.tree.get_selected_path())

    def test_focus_file_expands_parent(self):
        """focus_file() expands the parent directory of a nested file."""
        file_path = str(Path(self.vault_path) / "sub" / "deep.md")
        self.tree.focus_file(file_path)
        expanded = self.tree.get_expanded_paths()
        self.assertIn(str(Path(self.vault_path) / "sub"), expanded)

    def test_focus_button_exists(self):
        found = False
        for child in self.tree:
            if isinstance(child, Gtk.Box):
                for btn in child:
                    if isinstance(btn, Gtk.Button) and btn.get_icon_name() == "find-location-symbolic":
                        found = True
                        break
        self.assertTrue(found, "Focus-in-tree button not found in header")

    def test_focus_button_emits_signal(self):
        emitted = []
        self.tree.connect("focus-current-file", lambda _: emitted.append(True))
        for child in self.tree:
            if isinstance(child, Gtk.Box):
                for btn in child:
                    if isinstance(btn, Gtk.Button) and btn.get_icon_name() == "find-location-symbolic":
                        btn.emit("clicked")
                        break
        self.assertEqual(emitted, [True])

    def test_focus_file_empty_string_no_crash(self):
        self.tree.focus_file("")
        self.assertIsNone(self.tree.get_selected_path())


class TestVaultTreeHandleFileMovedDirectory(unittest.TestCase):
    """A moved directory is inserted as a folder node."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        old_dir = Path(self.vault_path) / "olddir"
        old_dir.mkdir(exist_ok=True)
        (old_dir / "file.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_handle_file_moved_directory_creates_folder_node(self):
        new_parent = str(self.vault_path)
        old_path = str(Path(self.vault_path) / "olddir")
        new_path = str(Path(self.vault_path) / "newdir")
        import os
        os.rename(old_path, new_path)
        self.tree._handle_file_moved(old_path, new_parent, new_path)

        moved = [n for n in self.tree._iter_all_nodes() if n.path == new_path]
        self.assertTrue(moved, f"Directory {new_path} not found in tree")
        self.assertTrue(moved[0].is_dir, "Moved directory should be marked is_dir=True")


class TestVaultTreeDeleteVaultRoot(unittest.TestCase):
    """Vault-Root bleibt im Baum wenn alle Dateien gelöscht werden."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "file1.md").touch()
        (Path(self.vault_path) / "file2.md").touch()
        self.tree.set_vaults([{"name": "testvault", "path": self.vault_path}])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_delete_all_files_preserves_vault_root(self):
        file1 = str(Path(self.vault_path) / "file1.md")
        file2 = str(Path(self.vault_path) / "file2.md")
        self.tree._handle_file_deleted(file1)
        self.tree._handle_file_deleted(file2)
        self.assertIn(self.vault_path, _all_paths(self.tree))

    def test_delete_dir_inside_vault_preserves_vault_root(self):
        subdir = Path(self.vault_path) / "subdir"
        subdir.mkdir(exist_ok=True)
        (subdir / "inner.md").touch()
        self.tree._handle_file_deleted(str(subdir))
        self.assertIn(self.vault_path, _all_paths(self.tree))


class TestVaultTreeContextMenuFallback(unittest.TestCase):
    """Tests for _resolve_context_parent_dir."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()
        self.vault_a = str(self._tmpdir / "vault-a")
        self.vault_b = str(self._tmpdir / "vault-b")
        Path(self.vault_a).mkdir()
        Path(self.vault_b).mkdir()
        (Path(self.vault_a) / "a.md").touch()
        (Path(self.vault_b) / "b.md").touch()
        self.tree.set_vaults([
            {"name": "VaultA", "path": self.vault_a},
            {"name": "VaultB", "path": self.vault_b},
        ])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_space_uses_active_vault(self):
        self.tree._active_vault = self.vault_b
        self.tree._context_path = None
        self.tree._context_is_dir = False
        self.assertEqual(self.tree._resolve_context_parent_dir(), self.vault_b)

    def test_empty_space_no_active_vault_uses_first(self):
        self.tree._active_vault = None
        self.tree._context_path = None
        self.tree._context_is_dir = False
        self.assertEqual(self.tree._resolve_context_parent_dir(), self.vault_a)

    def test_empty_space_no_vaults_returns_none(self):
        self.tree._vault_paths = []
        self.tree._active_vault = None
        self.tree._context_path = None
        self.tree._context_is_dir = False
        self.assertIsNone(self.tree._resolve_context_parent_dir())

    def test_dir_context_returns_context_path(self):
        self.tree._context_path = self.vault_b
        self.tree._context_is_dir = True
        self.assertEqual(self.tree._resolve_context_parent_dir(), self.vault_b)

    def test_file_context_returns_parent(self):
        file_path = str(Path(self.vault_b) / "b.md")
        self.tree._context_path = file_path
        self.tree._context_is_dir = False
        self.assertEqual(self.tree._resolve_context_parent_dir(), self.vault_b)


class TestDropDeferredRefresh(unittest.TestCase):
    """Drag-and-drop move: emit ``file-renamed`` and defer the tree rebuild.

    Regression: rebuilding the tree synchronously inside the drop handler threw
    a GtkCssNode assertion and could skip the ``file-renamed`` emit, leaving the
    moved file's tab/sidebar stat-ing the old path (FileNotFoundError spam).
    """

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.tree = VaultTree()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_drop_emits_rename_and_defers_refresh(self):
        import markdown_vault.vault.vault_tree as mod
        (self._tmpdir / "note.md").write_text("x")
        (self._tmpdir / "sub").mkdir()
        src = str(self._tmpdir / "note.md")
        target_dir = str(self._tmpdir / "sub")
        expected_dest = str(Path(target_dir) / "note.md")

        target_node = VaultNode("sub", target_dir, True)

        emitted = []
        self.tree.connect("file-renamed", lambda _t, o, n: emitted.append((o, n)))
        deferred = []

        with patch.object(mod.GLib, "idle_add",
                          side_effect=lambda cb, *a: deferred.append(cb)), \
                patch.object(mod.validation, "validate_drop", return_value=None), \
                patch("shutil.move") as mv, \
                patch.object(self.tree, "refresh") as refresh:
            result = self.tree._perform_drop(src, target_node)

        self.assertTrue(result)
        mv.assert_called_once_with(src, expected_dest)
        self.assertEqual(emitted, [(src, expected_dest)])
        refresh.assert_not_called()
        self.assertEqual(deferred, [self.tree._refresh_after_drop])


if __name__ == "__main__":
    unittest.main()
