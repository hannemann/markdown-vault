"""Tests for markdown_vault.backlink_index — incremental backlink index."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.backlink_index import BacklinkIndex


class TestBacklinkIndexBuild(unittest.TestCase):
    """Tests for building the index from scratch."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_finds_backlinks(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)
        self.assertTrue(backlinks[0].endswith("Note.md"))

    def test_build_ignores_non_md_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.txt").write_text("See [[Page]].\n")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_build_skips_unreadable_files(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Bad.md").write_bytes(b"\xff\xfe\x00binary")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_empty_when_no_links(self):
        (self._vault / "Page.md").write_text("# Page\n")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 0)

    def test_find_backlinks_sorted(self):
        (self._vault / "Target.md").write_text("# Target\n")
        (self._vault / "C.md").write_text("[[Target]].\n")
        (self._vault / "A.md").write_text("[[Target]].\n")
        (self._vault / "B.md").write_text("[[Target]].\n")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Target.md")
        names = [Path(p).name for p in backlinks]
        self.assertEqual(names, ["A.md", "B.md", "C.md"])


class TestBacklinkIndexIncremental(unittest.TestCase):
    """Tests for incremental updates to the index."""

    def setUp(self):
        self._idx = BacklinkIndex()

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

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_alias_does_not_create_separate_target(self):
        (self._vault / "Page.md").write_text("# Page\n")
        (self._vault / "Note.md").write_text("[[Page|my alias]].\n")
        idx = BacklinkIndex()
        idx.build([str(self._vault)])
        backlinks = idx.find_backlinks(self._vault / "Page.md")
        self.assertEqual(len(backlinks), 1)


if __name__ == "__main__":
    unittest.main()
