"""Tests for validation module (src/validation.py)."""

import tempfile
import unittest
from pathlib import Path

from src.validation import validate_rename, validate_drop


class TestValidateRename(unittest.TestCase):
    """Tests for validate_rename()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        (self._vault / "Page.md").write_text("# Page")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_empty_name(self):
        self.assertIsNotNone(validate_rename("", "old.md", []))

    def test_path_separator_rejected(self):
        self.assertIsNotNone(validate_rename("a/b.md", "old.md", []))
        self.assertIsNotNone(validate_rename("a\\b.md", "old.md", []))

    def test_vault_root_rename_rejected(self):
        err = validate_rename("newname", "vault", [], is_vault_root=True)
        self.assertEqual(err, "Vault root directory cannot be renamed.")

    def test_duplicate_name_rejected(self):
        err = validate_rename("Page.md", "old.md", ["Page.md", "Other.md"])
        self.assertEqual(err, "A file with this name already exists (case-insensitive).")

    def test_same_name_no_change(self):
        self.assertEqual(validate_rename("old.md", "old.md", []), "Name is unchanged.")

    def test_target_exists_rejected(self):
        # Target already exists on filesystem - simulated via target_exists flag
        err = validate_rename("Existing.md", "old.md", [], target_exists=True)
        self.assertEqual(err, "A file with this name already exists.")

    def test_leading_trailing_whitespace_rejected(self):
        self.assertIsNotNone(validate_rename("  name.md", "old.md", []))
        self.assertIsNotNone(validate_rename("name.md  ", "old.md", []))

    def test_valid_rename(self):
        self.assertIsNone(validate_rename("NewName.md", "old.md", []))


class TestValidateDrop(unittest.TestCase):
    """Tests for validate_drop()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        self._source = self._vault / "source.md"
        self._source.touch()
        self._target_dir = self._vault / "target"
        self._target_dir.mkdir()
        self._dest = self._target_dir / "source.md"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_drop_on_self(self):
        self.assertIsNotNone(validate_drop("/vault/file.md", "/vault/file.md", False))

    def test_drop_dir_into_own_child(self):
        self.assertIsNotNone(validate_drop("/vault/parent", "/vault/parent/child", True))

    def test_drop_on_same_parent(self):
        self.assertIsNotNone(validate_drop("/vault/file.md", "/vault", True))

    def test_dest_exists_rejected(self):
        self._dest.touch()
        self.assertIsNotNone(validate_drop(str(self._source), str(self._target_dir), True))

    def test_valid_drop(self):
        self.assertIsNone(validate_drop(str(self._source), str(self._target_dir), True))

    def test_drop_on_file_rejected(self):
        self.assertIsNotNone(validate_drop("/vault/file.md", "/vault/other_file.md", False))


if __name__ == "__main__":
    unittest.main()