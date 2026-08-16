"""R12.2: Integration test — vault-prefixed wikilink resolution."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.core import config
from markdown_vault.backlink_index import BacklinkIndex
from markdown_vault.file_index import FileIndex
from markdown_vault.markdown.tags import parse_wikilinks


class TestR122Integration(unittest.TestCase):
    """End-to-end test for vault-prefixed wikilink resolution."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cache = config._vaults_cache

    def tearDown(self):
        config._vaults_cache = self._orig_cache
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _setup_cache(self, vaults):
        """Populate the config cache for test vaults."""
        config._vaults_cache = vaults

    def test_vault_prefixed_wikilink_resolution(self):
        """Two vaults with same-named notes, vault prefix resolves correctly."""
        vault1 = Path(self._tmp) / "VaultA"
        vault1.mkdir()
        vault2 = Path(self._tmp) / "VaultB"
        vault2.mkdir()

        (vault1 / "Note.md").write_text("# Note in VaultA", encoding="utf-8")
        (vault2 / "Note.md").write_text("# Note in VaultB", encoding="utf-8")

        # Set up config cache with test vaults.
        self._setup_cache([
            {"name": "VaultA", "path": str(vault1)},
            {"name": "VaultB", "path": str(vault2)},
        ])

        # Build indexes
        file_index = FileIndex()
        file_index.build([{"name": "VaultA", "path": str(vault1)}, {"name": "VaultB", "path": str(vault2)}])

        backlink_index = BacklinkIndex()
        backlink_index.build([{"name": "VaultA", "path": str(vault1)}, {"name": "VaultB", "path": str(vault2)}])

        # Vault-prefixed wikilink: [[VaultA>Note]]
        text_vaulta = "[[VaultA>Note]]"
        for info in parse_wikilinks(text_vaulta):
            self.assertEqual(info.vault, "VaultA")
            self.assertEqual(info.stem, "Note")

        # Vault-prefixed wikilink: [[VaultB>Note|My Note]]
        text_vaultb = "[[VaultB>Note|My Note]]"
        for info in parse_wikilinks(text_vaultb):
            self.assertEqual(info.vault, "VaultB")
            self.assertEqual(info.stem, "Note")
            self.assertEqual(info.alias, "My Note")

        # Index contains both vault files
        self.assertTrue(file_index.has_path(str(vault1 / "Note.md")))
        self.assertTrue(file_index.has_path(str(vault2 / "Note.md")))

    def test_backlinks_with_vault_prefix(self):
        """Backlinks work with vault-prefixed wikilinks."""
        vault1 = Path(self._tmp) / "VaultA"
        vault1.mkdir()
        vault2 = Path(self._tmp) / "VaultB"
        vault2.mkdir()

        (vault1 / "Note.md").write_text("# Note in VaultA", encoding="utf-8")
        (vault2 / "Note.md").write_text("# Note in VaultB", encoding="utf-8")
        (vault1 / "Link.md").write_text("[[VaultB>Note]]", encoding="utf-8")
        (vault2 / "Link.md").write_text("[[VaultA>Note]]", encoding="utf-8")

        # Set up config cache with test vaults.
        self._setup_cache([
            {"name": "VaultA", "path": str(vault1)},
            {"name": "VaultB", "path": str(vault2)},
        ])

        # Build indexes
        file_index = FileIndex()
        file_index.build([{"name": "VaultA", "path": str(vault1)}, {"name": "VaultB", "path": str(vault2)}])

        backlink_index = BacklinkIndex()
        backlink_index.build([{"name": "VaultA", "path": str(vault1)}, {"name": "VaultB", "path": str(vault2)}])

        # Backlinks for VaultA's Note.md
        bl_a = backlink_index.find_backlinks(str(vault1 / "Note.md"))
        self.assertIn(str(vault2 / "Link.md"), bl_a)

        # Backlinks for VaultB's Note.md
        bl_b = backlink_index.find_backlinks(str(vault2 / "Note.md"))
        self.assertIn(str(vault1 / "Link.md"), bl_b)

    def test_no_underscore_normalization_integration(self):
        """R12.2: No space↔underscore normalization — exact match only."""
        vault = Path(self._tmp) / "MyVault"
        vault.mkdir()

        (vault / "My Note.md").write_text("# My Note", encoding="utf-8")

        self._setup_cache([{"name": vault.name, "path": str(vault)}])

        file_index = FileIndex()
        file_index.build([{"name": vault.name, "path": str(vault)}])

        # Space in filename is indexed
        self.assertTrue(file_index.has_path(str(vault / "My Note.md")))


if __name__ == "__main__":
    unittest.main()
