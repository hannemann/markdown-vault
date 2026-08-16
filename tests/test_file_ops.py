"""Tests for the file operations module (src/file_ops.py)."""

import os
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from markdown_vault.file_ops import FileOps
import markdown_vault.core.config as _cfg


class TestResolveActiveVault(unittest.TestCase):
    """Tests for FileOps.resolve_active_vault priority logic."""

    def setUp(self):
        self._vaults = ["/vault/a", "/vault/b", "/vault/c"]
        self._skip = MagicMock()
        self._ops = FileOps(skip_fn=self._skip)
        _cfg._vaults_cache = [
            {"name": "a", "path": "/vault/a"},
            {"name": "b", "path": "/vault/b"},
            {"name": "c", "path": "/vault/c"},
        ]

    def tearDown(self):
        _cfg._vaults_cache = None

    def _make_tab(self, file_path: str | None = None):
        tab = MagicMock()
        tab.editor.file_path = file_path
        return tab

    def test_tab_file_derives_vault(self):
        """Tab with open file → derive vault from file's parent."""
        tab = self._make_tab("/vault/a/sub/note.md")
        result = self._ops.resolve_active_vault(tab, None, None, self._vaults)
        self.assertEqual(result, "/vault/a")

    def test_tab_file_non_vault_fallback(self):
        """Tab file not in any vault → skip to next priority."""
        tab = self._make_tab("/other/file.md")
        result = self._ops.resolve_active_vault(tab, "/vault/b", None, self._vaults)
        self.assertEqual(result, "/vault/b")

    def test_tree_selection_priority(self):
        """No tab → tree selection is used."""
        result = self._ops.resolve_active_vault(None, "/vault/b/some.md", None, self._vaults)
        self.assertEqual(result, "/vault/b")

    def test_active_vault_fallback(self):
        """No tab, no tree selection → active vault."""
        result = self._ops.resolve_active_vault(None, None, "/vault/c", self._vaults)
        self.assertEqual(result, "/vault/c")

    def test_last_resort_vault(self):
        """Nothing else → last vault."""
        result = self._ops.resolve_active_vault(None, None, None, self._vaults)
        self.assertEqual(result, "/vault/c")

    def test_active_vault_not_in_list_fallback(self):
        """Active vault not in vaults list → falls through to last vault."""
        result = self._ops.resolve_active_vault(None, None, "/vault/x", self._vaults)
        self.assertEqual(result, "/vault/c")

    def test_tab_takes_priority_over_tree(self):
        """Tab file takes priority over tree selection."""
        tab = self._make_tab("/vault/c/deep/note.md")
        result = self._ops.resolve_active_vault(tab, "/vault/a", None, self._vaults)
        self.assertEqual(result, "/vault/c")


class TestCreateFile(unittest.TestCase):
    """Tests for FileOps.create_file."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._skip = MagicMock()
        self._ops = FileOps(skip_fn=self._skip)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_simple_file(self):
        err = self._ops.create_file(self._tmp, "note.md")
        self.assertIsNone(err)
        self.assertTrue(Path(self._tmp, "note.md").exists())
        # 2 skip events for touch() on existing monitor
        self.assertEqual(self._skip.call_count, 2)

    def test_create_file_adds_md_extension(self):
        err = self._ops.create_file(self._tmp, "note")
        self.assertIsNone(err)
        self.assertTrue(Path(self._tmp, "note.md").exists())

    def test_create_file_with_subdirectory(self):
        err = self._ops.create_file(self._tmp, "sub/deep/note.md")
        self.assertIsNone(err)
        self.assertTrue(Path(self._tmp, "sub", "deep", "note.md").exists())
        # 1 skip for parent dir + 1 skip for file
        self.assertEqual(self._skip.call_count, 2)

    def test_create_file_invalid_path_returns_error(self):
        """Path traversal resolves outside tmp → touch fails with an error."""
        err = self._ops.create_file(self._tmp, "../../escape.md")
        self.assertIsNotNone(err)

    def test_create_file_existing_dir_no_error(self):
        """Creating a file in an existing dir works."""
        os.makedirs(os.path.join(self._tmp, "sub"), exist_ok=True)
        err = self._ops.create_file(self._tmp, "sub/note.md")
        self.assertIsNone(err)
        self.assertTrue(Path(self._tmp, "sub", "note.md").exists())


class TestCreateFolder(unittest.TestCase):
    """Tests for FileOps.create_folder."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._skip = MagicMock()
        self._ops = FileOps(skip_fn=self._skip)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_folder(self):
        err = self._ops.create_folder(self._tmp, "mydir")
        self.assertIsNone(err)
        self.assertTrue(Path(self._tmp, "mydir").is_dir())

    def test_create_folder_already_exists_returns_error(self):
        os.makedirs(os.path.join(self._tmp, "existing"), exist_ok=True)
        err = self._ops.create_folder(self._tmp, "existing")
        self.assertIsNotNone(err)
        self.assertIn("Errno 17", err)


class TestDeletePath(unittest.TestCase):
    """Tests for FileOps.delete_path."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_delete_file(self):
        fp = Path(self._tmp, "file.md")
        fp.touch()
        err = FileOps.delete_path(str(fp))
        self.assertIsNone(err)
        self.assertFalse(fp.exists())

    def test_delete_directory(self):
        d = Path(self._tmp, "dir")
        d.mkdir()
        (d / "file.md").touch()
        err = FileOps.delete_path(str(d))
        self.assertIsNone(err)
        self.assertFalse(d.exists())

    def test_delete_nonexistent_returns_error(self):
        err = FileOps.delete_path("/nonexistent/path/file.md")
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
