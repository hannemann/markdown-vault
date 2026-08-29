"""Tests for note_writer — the shared slug + collision-free path helpers."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

import support

from markdown_vault.vault import note_writer as nw


class TestSlug(unittest.TestCase):
    def test_kebab_and_strip_punctuation(self):
        self.assertEqual(nw.slug("Hello, World! (Draft)"), "hello-world-draft")

    def test_collapses_separators(self):
        self.assertEqual(nw.slug("a  b__c--d"), "a-b-c-d")

    def test_unicode_words_kept(self):
        self.assertEqual(nw.slug("Gödel & Türme"), "gödel-türme")

    def test_length_cap(self):
        self.assertEqual(len(nw.slug("x" * 200, max_len=60)), 60)

    def test_fallback_when_empty(self):
        self.assertEqual(nw.slug("!!!"), "untitled")
        self.assertEqual(nw.slug("", fallback="imported-page"), "imported-page")


class TestUniquePath(unittest.TestCase):
    def test_free_name_used_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(nw.unique_path(d, "note"), Path(d) / "note.md")

    def test_collision_gets_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "note.md").write_text("x")
            self.assertEqual(nw.unique_path(d, "note"), Path(d) / "note-2.md")
            (Path(d) / "note-2.md").write_text("x")
            self.assertEqual(nw.unique_path(d, "note"), Path(d) / "note-3.md")

    def test_custom_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(nw.unique_path(d, "a", suffix=".txt"), Path(d) / "a.txt")


class TestReservePath(unittest.TestCase):
    """reserve_path CREATES the file it returns, exclusively. unique_path only tested a name
    and handed it back, so anything appearing between that test and the write got truncated —
    and an importer does real work (storing images) in exactly that window."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))
        ctx = support.vault_roots(self._tmp)      # the create goes through VaultFS
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def test_it_creates_the_file_it_returns(self):
        p = nw.reserve_path(self._tmp, "note")
        self.assertEqual(p, Path(self._tmp) / "note.md")
        self.assertTrue(p.exists())
        self.assertEqual(p.read_text(), "")

    def test_it_skips_a_name_already_taken(self):
        (Path(self._tmp) / "note.md").write_text("existing")
        p = nw.reserve_path(self._tmp, "note")
        self.assertEqual(p, Path(self._tmp) / "note-2.md")
        self.assertEqual((Path(self._tmp) / "note.md").read_text(), "existing")

    def test_it_does_not_hand_out_the_same_name_twice(self):
        # The property unique_path could not give: two reservations in a row must differ even
        # though neither has written its content yet.
        a = nw.reserve_path(self._tmp, "note")
        b = nw.reserve_path(self._tmp, "note")
        self.assertNotEqual(a, b)

    def test_it_gives_up_instead_of_spinning_forever(self):
        # It CREATES in a loop, unlike unique_path which only tested — so a pathological case
        # (something creating each candidate) must end, not hang the import thread.
        with unittest.mock.patch("markdown_vault.core.vault_fs.write_text",
                                 side_effect=FileExistsError("always taken")):
            with self.assertRaises(FileExistsError):
                nw.reserve_path(self._tmp, "note")


if __name__ == "__main__":
    unittest.main()
