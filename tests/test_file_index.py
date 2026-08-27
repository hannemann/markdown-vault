"""Tests for markdown_vault.vault.file_index — stem-to-path index for file tracking."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from unittest import mock

import markdown_vault.core.config as _cfg
from markdown_vault.core import state_fs
from markdown_vault.vault.file_index import FileIndex


class TestFileIndexBuild(unittest.TestCase):
    """Tests for building the index from scratch."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_finds_md_files(self):
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / "Note.md").write_text("# Note")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "Page.md")))
        self.assertTrue(idx.has_path(str(self._vault / "Note.md")))

    def test_build_root_only_subdirectories_skipped(self):
        """R12.2: Root-only — subdirectory files are NOT indexed."""
        sub = self._vault / "Sub" / "Deep"
        sub.mkdir(parents=True)
        (sub / "DeepFile.md").write_text("# Deep")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertFalse(idx.has_path(str(sub / "DeepFile.md")))

    def test_build_ignores_non_md_files(self):
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / "Page.txt").write_text("Page")
        (self._vault / "Note.md").write_text("# Note")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "Page.md")))
        self.assertTrue(idx.has_path(str(self._vault / "Note.md")))
        self.assertFalse(idx.has_path(str(self._vault / "Page.txt")))

    def test_build_ignores_hidden_files(self):
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / ".Hidden.md").write_text("# Hidden")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "Page.md")))
        self.assertFalse(idx.has_path(str(self._vault / ".Hidden.md")))

    def test_build_ignores_hidden_directories(self):
        hidden = self._vault / ".git"
        hidden.mkdir()
        (hidden / "Objects.md").write_text("# Git objects")
        (self._vault / "Page.md").write_text("# Page")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "Page.md")))
        self.assertFalse(idx.has_path(str(hidden / "Objects.md")))

    def test_build_space_underscore_no_normalization(self):
        """R12.2: No space↔underscore normalization — exact stem match only."""
        (self._vault / "Datei B.md").write_text("# Datei B")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "Datei B.md")))

    def test_build_only_indexes_root_md_files(self):
        """R12.2: Root-only — only .md files in vault root are indexed."""
        (self._vault / "RootNote.md").write_text("# Root")
        sub = self._vault / "sub"
        sub.mkdir()
        (sub / "SubNote.md").write_text("# Sub")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "RootNote.md")))
        self.assertFalse(idx.has_path(str(sub / "SubNote.md")))

    def test_build_handles_conflicting_stems_same_vault(self):
        """R12.2: Root-only — same stem only valid in root.
        If two files with the same stem exist, the first one wins."""
        sub_a = self._vault / "A"
        sub_b = self._vault / "B"
        sub_a.mkdir()
        sub_b.mkdir()
        (sub_a / "Same.md").write_text("# A")
        (sub_b / "Same.md").write_text("# B")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        # Subdirectory files are not indexed at all
        self.assertFalse(idx.has_path(str(sub_a / "Same.md")))
        self.assertFalse(idx.has_path(str(sub_b / "Same.md")))

    def test_build_multiple_vaults(self):
        vault2 = Path(self._tmp) / "vault2"
        vault2.mkdir()
        (self._vault / "Page.md").write_text("# Page")
        (vault2 / "Other.md").write_text("# Other")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)},
                   {"name": "Vault2", "path": str(vault2)}])
        self.assertTrue(idx.has_path(str(self._vault / "Page.md")))
        self.assertTrue(idx.has_path(str(vault2 / "Other.md")))

    def test_build_empty_vault(self):
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertEqual(len(idx._stem_to_path), 0)

    def test_build_deep_nested(self):
        """R12.2: Root-only — deeply nested files are NOT indexed."""
        deep = self._vault / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "VeryDeep.md").write_text("# Very Deep")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertFalse(idx.has_path(str(deep / "VeryDeep.md")))


class TestFileIndexIncremental(unittest.TestCase):
    """Tests for incremental updates to the index."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]
        self._idx = FileIndex()
        self._idx.build([{"name": "vault", "path": str(self._vault)}])

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_add_file(self):
        (self._vault / "NewFile.md").write_text("# New")
        self._idx.add_file(str(self._vault / "NewFile.md"), vault_path=str(self._vault))
        self.assertTrue(self._idx.has_path(str(self._vault / "NewFile.md")))

    def test_remove_file(self):
        (self._vault / "Page.md").write_text("# Page")
        self._idx.add_file(str(self._vault / "Page.md"), vault_path=str(self._vault))
        self.assertTrue(self._idx.has_path(str(self._vault / "Page.md")))
        self._idx.remove_file(str(self._vault / "Page.md"))
        self.assertFalse(self._idx.has_path(str(self._vault / "Page.md")))

    def test_rename_file(self):
        (self._vault / "OldName.md").write_text("# Old")
        old = str(self._vault / "OldName.md")
        new = str(self._vault / "NewName.md")
        self._idx.add_file(old, vault_path=str(self._vault))
        os.rename(old, new)
        self._idx.rename_file(old, new)
        self.assertFalse(self._idx.has_path(old))
        self.assertTrue(self._idx.has_path(new))

    def test_rename_file_with_spaces(self):
        """R12.2: Rename to a file with spaces — no underscore variant."""
        (self._vault / "OldName.md").write_text("# Old")
        old = str(self._vault / "OldName.md")
        new = str(self._vault / "New File.md")
        self._idx.add_file(old, vault_path=str(self._vault))
        os.rename(old, new)
        self._idx.rename_file(old, new)
        self.assertFalse(self._idx.has_path(old))
        self.assertTrue(self._idx.has_path(new))


class TestFileIndexSharedIndex(unittest.TestCase):
    """Verify that multiple consumers can share a single FileIndex instance."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_shared_index_reflects_changes_for_all(self):
        """Two references to the same index see the same data."""
        shared = FileIndex()
        (self._vault / "A.md").write_text("# A")
        shared.build([{"name": "vault", "path": str(self._vault)}])
        ref_a = shared
        ref_b = shared
        self.assertTrue(ref_a.has_path(str(self._vault / "A.md")))
        self.assertTrue(ref_b.has_path(str(self._vault / "A.md")))
        shared.remove_file(str(self._vault / "A.md"))
        self.assertFalse(ref_a.has_path(str(self._vault / "A.md")))
        self.assertFalse(ref_b.has_path(str(self._vault / "A.md")))

    def test_dump_to_file(self):
        """dump_to_file writes a valid JSON snapshot of the index."""
        import json
        idx = FileIndex()
        (self._vault / "Page.md").write_text("# Page")
        (self._vault / "Note.md").write_text("# Note")
        idx.build([{"name": "vault", "path": str(self._vault)}])
        dump_path = Path(self._tmp) / "dump.json"
        # The dump now writes through StateFS; allow the temp dir as a state root.
        with mock.patch.object(state_fs, "_state_roots", return_value=[self._tmp]), \
             mock.patch.object(state_fs, "_vault_roots", return_value=[]):
            idx.dump_to_file(dump_path)
        data = json.loads(dump_path.read_text(encoding="utf-8"))
        self.assertEqual(data["Page"], str(self._vault / "Page.md"))
        self.assertEqual(data["Note"], str(self._vault / "Note.md"))

    def test_dump_to_file_overwrites(self):
        """dump_to_file overwrites an existing file."""
        import json
        idx = FileIndex()
        dump_path = Path(self._tmp) / "dump.json"
        dump_path.write_text("old")
        with mock.patch.object(state_fs, "_state_roots", return_value=[self._tmp]), \
             mock.patch.object(state_fs, "_vault_roots", return_value=[]):
            idx.dump_to_file(dump_path)
        data = json.loads(dump_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)


class TestFileIndexEdgeCases(unittest.TestCase):
    """Edge cases for FileIndex."""

    def test_empty_vault_paths(self):
        idx = FileIndex()
        idx.build([])
        self.assertEqual(len(idx._stem_to_path), 0)

    def test_vault_path_does_not_exist(self):
        """Indexing a non-existent path should not raise."""
        idx = FileIndex()
        idx.build([{"name": "nonexistent", "path": "/nonexistent/vault/path/xyz"}])
        self.assertEqual(len(idx._stem_to_path), 0)

    def test_duplicate_vault_paths_deduplicated(self):
        """Same vault path listed twice should not cause issues."""
        tmp = tempfile.mkdtemp()
        try:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "Page.md").write_text("# Page")
            idx = FileIndex()
            idx.build([{"name": vault.name, "path": str(vault)},
                       {"name": vault.name, "path": str(vault)}])
            self.assertTrue(idx.has_path(str(vault / "Page.md")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_build_preserves_multiple_files_same_name_different_dirs(self):
        """R12.2: Root-only — files in subdirectories are NOT indexed."""
        tmp = tempfile.mkdtemp()
        try:
            vault = Path(tmp) / "vault"
            a = vault / "DirA"
            b = vault / "DirB"
            a.mkdir(parents=True)
            b.mkdir()
            (a / "Same.md").write_text("# A")
            (b / "Same.md").write_text("# B")
            (vault / "Same.md").write_text("# Root")
            idx = FileIndex()
            idx.build([{"name": vault.name, "path": str(vault)}])
            self.assertTrue(idx.has_path(str(vault / "Same.md")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFileIndexStemTieBreak(unittest.TestCase):
    """R12.2: Root-only — no shallowest-wins, no space↔underscore."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_root_only_no_subdirectories(self):
        """R12.2: Root-only — subdirectory files are NOT indexed."""
        sub = self._vault / "sub"
        sub.mkdir()
        (self._vault / "root.md").write_text("# Root")
        (sub / "root.md").write_text("# Sub")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "root.md")))
        self.assertFalse(idx.has_path(str(sub / "root.md")))

    def test_no_underscore_variant(self):
        """R12.2: No space↔underscore normalization."""
        (self._vault / "my note.md").write_text("# Root")
        idx = FileIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertTrue(idx.has_path(str(self._vault / "my note.md")))


if __name__ == "__main__":
    unittest.main()
