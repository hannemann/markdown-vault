"""Tests for markdown_vault.backlink_index — incremental backlink index."""

import shutil
import tempfile
import unittest
from pathlib import Path

import markdown_vault.config as _cfg
from markdown_vault.backlink_index import BacklinkIndex, scan_vaults


class TestScanVaults(unittest.TestCase):
    """Tests for the pure scan_vaults() function (R5.3 off-thread build)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_scan_vaults_returns_backlink_maps(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        self.assertIn(
            str(self._vault / "Note.md"),
            next(iter(target_to_sources.values())),
        )
        self.assertEqual(
            len(source_to_targets[str(self._vault / "Note.md")]), 1,
        )

    def test_scan_vaults_returns_fresh_dicts(self):
        t1, s1 = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        t2, s2 = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        self.assertIsNot(t1, t2)
        self.assertIsNot(s1, s2)

    def test_scan_vaults_empty_vault(self):
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        self.assertEqual(target_to_sources, {})
        self.assertEqual(source_to_targets, {})

    def test_build_matches_scan_vaults(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        target_to_sources, source_to_targets = scan_vaults(
            [{"name": "vault", "path": str(self._vault)}]
        )
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        self.assertEqual(idx._target_to_sources, target_to_sources)
        self.assertEqual(idx._source_to_targets, source_to_targets)

    def test_set_index_swaps_maps(self):
        idx = BacklinkIndex()
        idx.set_index({}, {})
        t, s = scan_vaults([{"name": "vault", "path": str(self._vault)}])
        idx.set_index(t, s)
        self.assertIs(idx._target_to_sources, t)
        self.assertIs(idx._source_to_targets, s)


class TestApplyBacklinkBuild(unittest.TestCase):
    """R5.3: main-thread swap callback for the async backlink scan."""

    def test_apply_backlink_build_swaps_index(self):
        import unittest.mock
        import markdown_vault.app_window as aw

        class FakeWindow:
            def __init__(self):
                self._backlink_index = BacklinkIndex()
                self._dump_debug = unittest.mock.Mock()

            _apply_backlink_build = aw.MainWindow._apply_backlink_build

        win = FakeWindow()
        target_to_sources = {"vault:VaultA?path=Page": {"/tmp/VaultA/Note.md"}}
        source_to_targets = {"/tmp/VaultA/Note.md": {"vault:VaultA?path=Page"}}
        result = win._apply_backlink_build(target_to_sources, source_to_targets)
        self.assertFalse(result)
        self.assertIs(win._backlink_index._target_to_sources, target_to_sources)
        self.assertIs(win._backlink_index._source_to_targets, source_to_targets)
        win._dump_debug.assert_called_once_with(["backlink_index"])


class TestBacklinkIndexBuild(unittest.TestCase):
    """Tests for building the index from scratch."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_finds_backlinks(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)
        self.assertTrue(backlinks[0].endswith("Note.md"))

    def test_build_ignores_non_md_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.txt").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_build_skips_unreadable_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Bad.md").write_bytes(b"\xff\xfe\x00binary")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_empty_when_no_links(self):
        (self._vault / "Page.md").write_text("# Page\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_sorted(self):
        (self._vault / "Target.md").write_text("# Target\n")
        (self._vault / "C.md").write_text("[[Target]].\n")
        (self._vault / "A.md").write_text("[[Target]].\n")
        (self._vault / "B.md").write_text("[[Target]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Target.md")
        names = [Path(p).name for p in backlinks]
        self.assertEqual(names, ["A.md", "B.md", "C.md"])

    def test_unqualified_link_is_same_vault_only(self):
        """[[Page]] in vault A must NOT backlink vault B's Page.md."""
        vault_b = Path(self._tmp) / "vault_b"
        vault_b.mkdir()
        (vault_b / "Page.md").write_text("# Page B\n")
        (self._vault / "Note.md").write_text("[[Page]].\n")
        idx = BacklinkIndex()
        idx.build([
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ])
        self.assertNotIn(
            str(self._vault / "Note.md"),
            idx.find_backlinks(vault_b / "Page.md"),
        )

    def test_cross_vault_qualified_link(self):
        """[[VaultB>Page]] from vault A must backlink vault B's Page.md."""
        vault_b = Path(self._tmp) / "vault_b"
        vault_b.mkdir()
        (vault_b / "Page.md").write_text("# Page B\n")
        (self._vault / "Note.md").write_text("[[vault_b>Page]].\n")
        idx = BacklinkIndex()
        _cfg._vaults_cache = [
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ]
        idx.build([
            {"name": "vault", "path": str(self._vault)},
            {"name": "vault_b", "path": str(vault_b)},
        ])
        self.assertIn(
            str(self._vault / "Note.md"),
            idx.find_backlinks(vault_b / "Page.md"),
        )


class TestBacklinkIndexIncremental(unittest.TestCase):
    """Tests for incremental updates to the index."""

    def setUp(self):
        self._idx = BacklinkIndex()
        _cfg._vaults_cache = [{"name": "vault", "path": "/vault"}]

    def test_update_file_adds_links(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertIn(path, backlinks)

    def test_update_file_removes_old_links(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        self._idx.update_file(path, "No links here.\n")
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertNotIn(path, backlinks)

    def test_remove_file(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "See [[Page]].\n")
        self._idx.remove_file(path)
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertEqual(len(backlinks), 0)

    def test_rename_file(self):
        old_path = "/vault/Old.md"
        new_path = "/vault/New.md"
        self._idx.update_file(old_path, "See [[Page]].\n")
        self._idx.rename_file(old_path, new_path)
        backlinks = self._idx.find_backlinks(Path("/vault/Page.md"))
        self.assertIn(new_path, backlinks)
        self.assertNotIn(old_path, backlinks)

    def test_rename_file_preserves_other_targets(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "[[A]] and [[B]].\n")
        self._idx.rename_file(path, "/vault/Renamed.md")
        self.assertIn("/vault/Renamed.md", self._idx.find_backlinks(Path("/vault/A.md")))
        self.assertIn("/vault/Renamed.md", self._idx.find_backlinks(Path("/vault/B.md")))

    def test_remove_file_cleans_empty_stems(self):
        path = "/vault/Note.md"
        self._idx.update_file(path, "[[Only]].\n")
        self._idx.remove_file(path)
        # Internal state should be clean.
        self.assertEqual(len(self._idx._target_to_sources), 0)
        self.assertEqual(len(self._idx._source_to_targets), 0)


class TestBacklinkIndexAlias(unittest.TestCase):
    """Tests for wikilink alias parsing in the index."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()
        _cfg._vaults_cache = [{"name": "vault", "path": str(self._vault)}]

    def tearDown(self):
        _cfg._vaults_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_alias_does_not_create_separate_target(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("[[Page|my alias]].\n")
        idx = BacklinkIndex()
        idx.build([{"name": "vault", "path": str(self._vault)}])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)


if __name__ == "__main__":
    unittest.main()
