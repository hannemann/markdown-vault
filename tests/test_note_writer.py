"""Tests for note_writer — the shared slug + collision-free path helpers."""

import tempfile
import unittest
from pathlib import Path

from markdown_vault import note_writer as nw


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


if __name__ == "__main__":
    unittest.main()
