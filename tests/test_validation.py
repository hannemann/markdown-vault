"""Tests for validation module (src/validation.py)."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault.validation import (
    validate_rename,
    validate_drop,
    validate_new_item,
    validate_vault_name,
    sanitize_vault_name,
)


class TestValidateVaultName(unittest.TestCase):
    """Tests for validate_vault_name() — R19.4 key-format safety."""

    def test_accepts_plain_name(self):
        self.assertIsNone(validate_vault_name("My Notes"))

    def test_accepts_uniquified_suffix(self):
        self.assertIsNone(validate_vault_name("Notes (2)"))

    def test_rejects_empty_and_whitespace(self):
        self.assertIsNotNone(validate_vault_name(""))
        self.assertIsNotNone(validate_vault_name("   "))

    def test_rejects_surrounding_whitespace(self):
        self.assertIsNotNone(validate_vault_name(" Notes"))
        self.assertIsNotNone(validate_vault_name("Notes "))

    def test_rejects_forbidden_chars(self):
        for bad in ("a/b", "a\\b", "a>b", "a|b", "a#b", "a[b", "a]b"):
            with self.subTest(name=bad):
                self.assertIsNotNone(validate_vault_name(bad))


class TestSanitizeVaultName(unittest.TestCase):
    """Tests for sanitize_vault_name() — load-time hardening (R19.4)."""

    def test_strips_forbidden_chars(self):
        self.assertEqual(sanitize_vault_name("a>b|c#d/e[f]"), "abcdef")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(sanitize_vault_name("  Notes  "), "Notes")

    def test_plain_name_unchanged(self):
        self.assertEqual(sanitize_vault_name("My Notes (2)"), "My Notes (2)")

    def test_all_forbidden_returns_empty(self):
        self.assertEqual(sanitize_vault_name(">>||"), "")

    def test_sanitized_result_passes_validation(self):
        self.assertIsNone(validate_vault_name(sanitize_vault_name("a>b|c")))


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


class TestValidateNewItem(unittest.TestCase):
    """Tests for validate_new_item()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_empty_name(self):
        self.assertIsNotNone(validate_new_item("", str(self._vault)))

    def test_whitespace_name(self):
        self.assertIsNotNone(validate_new_item("   ", str(self._vault)))

    def test_leading_trailing_whitespace(self):
        self.assertIsNotNone(validate_new_item("  note.md  ", str(self._vault)))

    def test_absolute_path_rejected(self):
        self.assertIsNotNone(validate_new_item("/tmp/note.md", str(self._vault)))

    def test_absolute_path_no_dir(self):
        self.assertIsNotNone(validate_new_item("/etc/note", str(self._vault)))

    def test_path_traversal_simple(self):
        self.assertIsNotNone(validate_new_item("../note.md", str(self._vault)))

    def test_path_traversal_nested(self):
        self.assertIsNotNone(validate_new_item("sub/../../note.md", str(self._vault)))

    def test_path_traversal_deep(self):
        self.assertIsNotNone(validate_new_item("../../tmp/pwned", str(self._vault)))

    def test_valid_simple_name(self):
        self.assertIsNone(validate_new_item("note.md", str(self._vault)))

    def test_valid_subdirectory(self):
        self.assertIsNone(validate_new_item("sub/note.md", str(self._vault)))

    def test_valid_nested_subdirectory(self):
        self.assertIsNone(validate_new_item("a/b/c/note.md", str(self._vault)))

    def test_valid_folder_name(self):
        self.assertIsNone(validate_new_item("My Folder", str(self._vault)))

    def test_valid_folder_with_subdir(self):
        self.assertIsNone(validate_new_item("My Folder/nested.md", str(self._vault)))

    def test_backslash_is_valid_filename_on_linux(self):
        """On Linux \\ is a valid filename character, not a path separator."""
        self.assertIsNone(validate_new_item("sub\\note.md", str(self._vault)))

    def test_resolved_path_containment(self):
        """Realpath containment: parent_dir itself is a realpath."""
        self.assertIsNone(validate_new_item("note.md", str(self._vault)))


if __name__ == "__main__":
    unittest.main()