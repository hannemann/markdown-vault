"""Tests for markdown_vault.core.path_utils — vault name-to-path resolver."""

import os
import tempfile
import unittest
from pathlib import Path

# Need to patch config before importing path_utils.
import support

import markdown_vault.core.config as _cfg


class _TempConfigMixin:
    """Redirect config to a temporary directory for each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_dir = _cfg.CONFIG_DIR
        self._orig_file = _cfg.CONFIG_FILE
        _cfg.CONFIG_DIR = Path(self._tmpdir)
        _cfg.CONFIG_FILE = Path(self._tmpdir) / "settings.yaml"
        # The settings write goes through StateFS, whose guard reads paths.CONFIG_DIR — the
        # OWNER — not config's rebindable alias, so the two rebinds above do not move the
        # allowed root. (Same block as test_config's copy of this mixin; the duplication is
        # pre-existing.)
        ctx = support.state_roots(self._tmpdir)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        # Invalidate cache so tests start fresh.
        if hasattr(_cfg, "_vaults_cache"):
            _cfg._vaults_cache = None

    def tearDown(self):
        _cfg.CONFIG_DIR = self._orig_dir
        _cfg.CONFIG_FILE = self._orig_file
        if hasattr(_cfg, "_vaults_cache"):
            _cfg._vaults_cache = None
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestVaultCache(_TempConfigMixin, unittest.TestCase):
    """Tests for the settings.yaml in-memory cache."""

    def test_cache_miss_reads_from_disk(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n",
            encoding="utf-8",
        )
        result = _cfg.load_vaults()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Notes")

    def test_cache_hit_returns_equal_list(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n",
            encoding="utf-8",
        )
        first = _cfg.load_vaults()
        second = _cfg.load_vaults()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_mutating_returned_list_does_not_pollute_cache(self):
        """load_vaults returns a copy, so callers cannot alias the cache."""
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: A\n    path: /tmp/a\n",
            encoding="utf-8",
        )
        first = _cfg.load_vaults()
        first.append({"name": "Sneaky", "path": "/tmp/sneaky"})
        second = _cfg.load_vaults()
        self.assertEqual(len(second), 1)

    def test_cache_invalidated_after_save_vaults(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Old\n    path: /tmp/old\n",
            encoding="utf-8",
        )
        first = _cfg.load_vaults()
        self.assertEqual(first[0]["name"], "Old")
        _cfg.save_vaults([{"name": "New", "path": "/tmp/new"}])
        second = _cfg.load_vaults()
        self.assertEqual(second[0]["name"], "New")
        self.assertIsNot(first, second)

    def test_cache_invalidated_after_add_vault(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: A\n    path: /tmp/a\n",
            encoding="utf-8",
        )
        first = _cfg.load_vaults()
        self.assertEqual(len(first), 1)
        _cfg.add_vault("B", "/tmp/b")
        second = _cfg.load_vaults()
        self.assertEqual(len(second), 2)

    def test_cache_invalidated_after_remove_vault(self):
        _cfg.add_vault("A", "/tmp/a")
        _cfg.add_vault("B", "/tmp/b")
        first = _cfg.load_vaults()
        self.assertEqual(len(first), 2)
        _cfg.remove_vault("/tmp/b")
        second = _cfg.load_vaults()
        self.assertEqual(len(second), 1)

    def test_cache_invalidated_after_save_settings(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n",
            encoding="utf-8",
        )
        first = _cfg.load_vaults()
        self.assertEqual(first[0]["name"], "Notes")
        _cfg.save_settings({"autosave_interval": 60})
        # Settings save also preserves vaults, but should invalidate cache.
        second = _cfg.load_vaults()
        self.assertEqual(second[0]["name"], "Notes")


class TestResolveVaultPath(_TempConfigMixin, unittest.TestCase):
    """Tests for resolve_vault_path()."""

    def setUp(self):
        super().setUp()
        from markdown_vault.core.path_utils import resolve_vault_path
        self._resolve = resolve_vault_path

    def test_returns_path_for_known_name(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n"
            "  - name: Notes\n    path: /tmp/notes\n"
            "  - name: Work\n    path: /home/user/work\n",
            encoding="utf-8",
        )
        self.assertEqual(self._resolve("Notes"), "/tmp/notes")
        self.assertEqual(self._resolve("Work"), "/home/user/work")

    def test_returns_none_for_unknown_name(self):
        self.assertIsNone(self._resolve("NonExistent"))

    def test_returns_none_for_empty_name(self):
        self.assertIsNone(self._resolve(""))

    def test_resolves_relative_paths(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Rel\n    path: relative/path\n",
            encoding="utf-8",
        )
        self.assertTrue(os.path.isabs(self._resolve("Rel")))

    def test_cache_is_used(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/notes\n",
            encoding="utf-8",
        )
        first = self._resolve("Notes")
        # Modify file on disk — cache should still return old value.
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: Notes\n    path: /tmp/changed\n",
            encoding="utf-8",
        )
        second = self._resolve("Notes")
        self.assertEqual(first, second)
        # After invalidate, should get new value.
        _cfg._invalidate_cache()
        third = self._resolve("Notes")
        self.assertNotEqual(first, third)


class TestVaultRelativeName(_TempConfigMixin, unittest.TestCase):
    """Tests for vault_relative_name() — the hit-row title."""

    def setUp(self):
        super().setUp()
        from markdown_vault.core.path_utils import vault_relative_name
        self._name = vault_relative_name
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n"
            "  - name: Wissenschaft\n    path: /vaults/Wissenschaft\n"
            "  - name: Business\n    path: /vaults/Business\n",
            encoding="utf-8",
        )

    def test_file_at_vault_root(self):
        self.assertEqual(self._name("/vaults/Wissenschaft/Jupiter.md"),
                         "Wissenschaft/Jupiter")

    def test_file_in_subdir(self):
        self.assertEqual(self._name("/vaults/Wissenschaft/Planeten/Mars.md"),
                         "Wissenschaft/Planeten/Mars")

    def test_file_outside_vaults_falls_back_to_stem(self):
        self.assertEqual(self._name("/elsewhere/notes/loose.md"), "loose")


class TestResolveWikilink(_TempConfigMixin, unittest.TestCase):
    """Tests for resolve_wikilink()."""

    def setUp(self):
        super().setUp()
        from markdown_vault.core.path_utils import resolve_wikilink
        self._resolve = resolve_wikilink

    def test_resolves_simple_stem(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        result = self._resolve("VaultA", "Page")
        self.assertEqual(result, "/tmp/Vault-A/Page.md")

    def test_resolves_subdirectory_path(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        result = self._resolve("VaultA", "sub/nested/Page")
        self.assertEqual(result, "/tmp/Vault-A/sub/nested/Page.md")

    def test_returns_none_for_unknown_vault(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        self.assertIsNone(self._resolve("Unknown", "Page"))

    def test_returns_none_for_empty_path(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        self.assertIsNone(self._resolve("VaultA", ""))

    def test_handles_trailing_slash_in_path(self):
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        result = self._resolve("VaultA", "sub/Page")
        self.assertEqual(result, "/tmp/Vault-A/sub/Page.md")

    def test_rejects_parent_traversal_outside_vault(self):
        """R14.1: ``..`` segments must not escape the vault root."""
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        self.assertIsNone(self._resolve("VaultA", "../secret/Page"))
        self.assertIsNone(self._resolve("VaultA", "sub/../../secret/Page"))

    def test_rejects_absolute_relative_path(self):
        """R14.1: an absolute relative_path must not resolve outside the vault."""
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        self.assertIsNone(self._resolve("VaultA", "/etc/hostname"))

    def test_resolves_subdir_with_dot_segments_still_valid(self):
        """R14.1: harmless dot segments inside the vault keep working."""
        _cfg.CONFIG_FILE.write_text(
            "vaults:\n  - name: VaultA\n    path: /tmp/Vault-A\n",
            encoding="utf-8",
        )
        result = self._resolve("VaultA", "sub/./Page")
        self.assertEqual(result, "/tmp/Vault-A/sub/Page.md")


class TestWikilinkUrl(unittest.TestCase):
    """Tests for wikilink_url()/parse_wikilink_url() — canonical vault: URLs."""

    def setUp(self):
        from markdown_vault.core.path_utils import (
            parse_wikilink_url,
            wikilink_url,
        )
        self._build = wikilink_url
        self._parse = parse_wikilink_url

    def test_build_simple(self):
        self.assertEqual(self._build("VaultA", "Page"), "vault:VaultA?path=Page")

    def test_build_subdir_path(self):
        self.assertEqual(
            self._build("VaultA", "sub/Page"),
            "vault:VaultA?path=sub/Page",
        )

    def test_build_encodes_spaces(self):
        self.assertEqual(
            self._build("Vault A", "Datei B"),
            "vault:Vault%20A?path=Datei%20B",
        )

    def test_build_with_fragment(self):
        self.assertEqual(
            self._build("VaultA", "Page", "Sec 1"),
            "vault:VaultA?path=Page#Sec%201",
        )

    def test_build_without_fragment(self):
        self.assertNotIn("#", self._build("VaultA", "Page"))

    def test_parse_round_trip(self):
        url = self._build("Vault A", "sub/Datei B", "Sec 1")
        self.assertEqual(
            self._parse(url),
            ("Vault A", "sub/Datei B", "Sec 1"),
        )

    def test_parse_plain(self):
        self.assertEqual(
            self._parse("vault:VaultA?path=Page"),
            ("VaultA", "Page", ""),
        )

    def test_parse_empty_path(self):
        self.assertEqual(
            self._parse("vault:VaultA"),
            ("VaultA", "", ""),
        )

    def test_parse_preserves_encoded_slashes(self):
        self.assertEqual(
            self._parse("vault:VaultA?path=sub%2FPage"),
            ("VaultA", "sub/Page", ""),
        )


if __name__ == "__main__":
    unittest.main()
