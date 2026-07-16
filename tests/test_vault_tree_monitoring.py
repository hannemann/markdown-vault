"""Tests for VaultTree — inkrementelle Updates durch VaultMonitor.

Tests:
- _handle_file_created() fügt .md Node ein
- _handle_file_deleted() entfernt Node
- _handle_file_moved() aktualisiert Node
- Keine Duplikate
- Keine Crashes bei nicht-existierenden Paden
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_gio():
    """Erstellt ein gemoddetes Gio für VaultTree."""
    mock_gio = MagicMock()
    mock_gio.Menu = MagicMock
    mock_gio.SimpleActionGroup = MagicMock
    mock_gio.SimpleAction = MagicMock()
    mock_gio.SimpleAction.new = MagicMock()
    return mock_gio


def _load_vaulttree(mock_gio):
    """Lädt vault_tree mit gemoddetem Gio."""
    for mod in list(sys.modules.keys()):
        if mod == 'src.vault_tree' or mod.startswith('src.vault_tree.'):
            del sys.modules[mod]
    import gi.repository
    gi.repository.Gio = mock_gio
    import src.vault_tree
    return src.vault_tree


class TestVaultTreeHandleFileCreated(unittest.TestCase):
    """Phase 3: VaultTree _handle_file_created."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        mock_gio = _make_mock_gio()
        mod = _load_vaulttree(mock_gio)
        VaultTree = mod.VaultTree
        self.tree = VaultTree()

        # Baum initialisieren: vault root + existing file
        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "existing.md").touch()
        self.tree.set_vaults([self.vault_path])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _get_node_paths(self):
        """Gibt alle Dateipfade aus dem TreeStore zurück."""
        paths = []
        def _walk(iter_):
            while iter_:
                if not self.tree._store.get_value(iter_, 2):  # not a directory
                    paths.append(self.tree._store.get_value(iter_, 1))
                child = self.tree._store.iter_children(iter_)
                if child:
                    _walk(child)
                iter_ = self.tree._store.iter_next(iter_)
        _walk(self.tree._store.get_iter_first())
        return paths

    def test_handle_file_created_adds_node(self):
        """Neue .md Datei wird dem Baum hinzugefügt."""
        new_file = Path(self.vault_path) / "newfile.md"
        new_file.touch()

        self.tree._handle_file_created(self.vault_path, str(new_file))

        paths = self._get_node_paths()
        self.assertIn(str(new_file), paths)

    def test_handle_file_created_no_duplicate(self):
        """Wenn Datei schon im Baum → kein Duplicate."""
        # existing.md ist schon im Baum
        self.tree._handle_file_created(self.vault_path, str(Path(self.vault_path) / "existing.md"))

        paths = self._get_node_paths()
        # existing.md sollte genau einmal vorkommen
        self.assertEqual(paths.count(str(Path(self.vault_path) / "existing.md")), 1)

    def test_handle_file_created_subdirectory(self):
        """Neue .md Datei in Unterverzeichnis wird hinzugefügt."""
        subdir = Path(self.vault_path) / "subdir"
        subdir.mkdir(exist_ok=True)
        new_file = subdir / "sub.md"
        new_file.touch()

        self.tree._handle_file_created(str(subdir), str(new_file))

        paths = self._get_node_paths()
        self.assertIn(str(new_file), paths)

    def test_handle_file_created_non_md_ignored(self):
        """Nicht-.md Dateien werden ignoriert."""
        txt_file = Path(self.vault_path) / "file.txt"
        txt_file.touch()

        self.tree._handle_file_created(self.vault_path, str(txt_file))

        paths = self._get_node_paths()
        self.assertNotIn(str(txt_file), paths)

    def test_handle_file_created_parent_not_expanded(self):
        """Parent dir nicht expanded → Node wird hinzugefügt, aber nicht angezeigt."""
        subdir = Path(self.vault_path) / "hidden_sub"
        subdir.mkdir(exist_ok=True)
        new_file = subdir / "test.md"
        new_file.touch()

        self.tree._handle_file_created(str(subdir), str(new_file))

        # Node sollte existieren, auch wenn parent nicht expanded ist
        paths = self._get_node_paths()
        self.assertIn(str(new_file), paths)

    def test_handle_file_created_empty_parent_creates_intermediate_dirs(self):
        """Parent-Verzeichnis existiert im Baum noch nicht → wird erstellt."""
        deep = Path(self.vault_path) / "a" / "b" / "c"
        deep.mkdir(parents=True, exist_ok=True)
        new_file = deep / "deep.md"
        new_file.touch()

        self.tree._handle_file_created(str(deep), str(new_file))

        paths = self._get_node_paths()
        self.assertIn(str(new_file), paths)


class TestVaultTreeHandleFileDeleted(unittest.TestCase):
    """Phase 3: VaultTree _handle_file_deleted."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        mock_gio = _make_mock_gio()
        mod = _load_vaulttree(mock_gio)
        VaultTree = mod.VaultTree
        self.tree = VaultTree()

        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "delete_me.md").touch()
        (Path(self.vault_path) / "keep_me.md").touch()
        self.tree.set_vaults([self.vault_path])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _get_node_paths(self):
        paths = []
        def _walk(iter_):
            while iter_:
                if not self.tree._store.get_value(iter_, 2):
                    paths.append(self.tree._store.get_value(iter_, 1))
                child = self.tree._store.iter_children(iter_)
                if child:
                    _walk(child)
                iter_ = self.tree._store.iter_next(iter_)
        _walk(self.tree._store.get_iter_first())
        return paths

    def test_handle_file_deleted_removes_node(self):
        """Existierende Datei wird aus dem Baum entfernt."""
        delete_me = str(Path(self.vault_path) / "delete_me.md")
        self.tree._handle_file_deleted(delete_me)

        paths = self._get_node_paths()
        self.assertNotIn(delete_me, paths)

    def test_handle_file_deleted_keeps_other_files(self):
        """Andere Dateien bleiben erhalten."""
        delete_me = str(Path(self.vault_path) / "delete_me.md")
        keep_me = str(Path(self.vault_path) / "keep_me.md")

        self.tree._handle_file_deleted(delete_me)

        paths = self._get_node_paths()
        self.assertIn(keep_me, paths)

    def test_handle_file_deleted_nonexistent_is_noop(self):
        """Nicht-existierende Datei → kein Crash."""
        self.tree._handle_file_deleted("/nonexistent/file.md")
        # Sollte nichts passiert sein
        paths = self._get_node_paths()
        self.assertIn(str(Path(self.vault_path) / "keep_me.md"), paths)


class TestVaultTreeHandleFileMoved(unittest.TestCase):
    """Phase 3: VaultTree _handle_file_moved."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        mock_gio = _make_mock_gio()
        mod = _load_vaulttree(mock_gio)
        VaultTree = mod.VaultTree
        self.tree = VaultTree()

        self.vault_path = str(self._tmpdir / "testvault")
        Path(self.vault_path).mkdir(exist_ok=True)
        (Path(self.vault_path) / "subdir").mkdir(exist_ok=True)
        self.moved_file = Path(self.vault_path) / "move_me.md"
        self.moved_file.touch()
        self.tree.set_vaults([self.vault_path])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _get_node_paths(self):
        paths = []
        def _walk(iter_):
            while iter_:
                if not self.tree._store.get_value(iter_, 2):
                    paths.append(self.tree._store.get_value(iter_, 1))
                child = self.tree._store.iter_children(iter_)
                if child:
                    _walk(child)
                iter_ = self.tree._store.iter_next(iter_)
        _walk(self.tree._store.get_iter_first())
        return paths

    def test_handle_file_moved_updates_path(self):
        """Datei wird zum neuen Parent verschoben."""
        new_parent = str(self._tmpdir / "testvault" / "subdir")
        old_path = str(self.moved_file)
        new_path = str(new_parent) + "/move_me.md"

        self.tree._handle_file_moved(old_path, new_parent, new_path)

        paths = self._get_node_paths()
        self.assertNotIn(old_path, paths)
        self.assertIn(new_path, paths)

    def test_handle_file_moved_old_not_in_tree_is_noop(self):
        """Alte Path nicht im Baum → kein Crash."""
        new_parent = str(self._tmpdir / "testvault" / "subdir")
        self.tree._handle_file_moved("/nonexistent/file.md", new_parent, new_parent + "/file.md")

    def test_handle_file_moved_to_nonexistent_parent_is_noop(self):
        """Neuer Parent existiert nicht → kein Crash."""
        old_path = str(self.moved_file)
        self.tree._handle_file_moved(old_path, "/nonexistent", "/nonexistent/file.md")


if __name__ == "__main__":
    unittest.main()
