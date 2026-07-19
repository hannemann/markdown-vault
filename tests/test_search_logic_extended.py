"""Tests for search_logic extract_headings and compute_file_details."""

import tempfile
import unittest
from pathlib import Path
from datetime import datetime

from markdown_vault.search_logic import extract_headings, compute_file_details


class TestExtractHeadings(unittest.TestCase):
    """Tests for extract_headings()."""

    def test_empty_text(self):
        self.assertEqual(extract_headings(""), [])

    def test_single_heading(self):
        results = extract_headings("# Hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], (1, "Hello", 0))

    def test_multiple_headings(self):
        text = "# H1\n\n## H2\n### H3"
        results = extract_headings(text)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], (1, "H1", 0))
        self.assertEqual(results[1], (2, "H2", 2))
        self.assertEqual(results[2], (3, "H3", 3))

    def test_headings_with_content(self):
        text = "# Title\n\nSome text\n\n## Section\n\nMore text"
        results = extract_headings(text)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], (1, "Title", 0))
        self.assertEqual(results[1], (2, "Section", 4))

    def test_heading_levels(self):
        text = "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6"
        results = extract_headings(text)
        self.assertEqual(len(results), 6)
        self.assertEqual([r[0] for r in results], [1, 2, 3, 4, 5, 6])

    def test_headings_with_special_chars(self):
        text = "# Hello World!\n## Test: Special & Characters\n### Umlauts: ÄÖÜ"
        results = extract_headings(text)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0][1], "Hello World!")
        self.assertEqual(results[2][1], "Umlauts: ÄÖÜ")

    def test_line_numbers(self):
        # Heading on line 0
        text = "# First\n\n## Second\n### Third"
        results = extract_headings(text)
        self.assertEqual(results[0][2], 0)
        self.assertEqual(results[1][2], 2)
        self.assertEqual(results[2][2], 3)


class TestComputeFileDetails(unittest.TestCase):
    """Tests for compute_file_details()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vault = Path(self._tmp) / "vault"
        self._vault.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_existing_file(self):
        fpath = self._vault / "test.md"
        fpath.write_text("# Hello\n\nWorld\n\nMore text\n")
        stat = fpath.stat()
        details = compute_file_details(fpath, fpath.read_text())

        self.assertEqual(details["word_count"], 5)  # # Hello World More text (5 words)
        self.assertEqual(details["line_count"], 6)  # 6 lines (trailing \n adds extra)
        self.assertEqual(details["size"], stat.st_size)
        self.assertEqual(details["modified"], datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"))

    def test_empty_file(self):
        fpath = self._vault / "empty.md"
        fpath.write_text("")
        stat = fpath.stat()
        details = compute_file_details(fpath, "")

        self.assertEqual(details["word_count"], 0)
        self.assertEqual(details["line_count"], 0)
        self.assertEqual(details["size"], 0)
        self.assertEqual(details["modified"], datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"))

    def test_multiline_text(self):
        fpath = self._vault / "multi.md"
        fpath.write_text("Line 1\nLine 2\nLine 3")
        details = compute_file_details(fpath, fpath.read_text())

        self.assertEqual(details["line_count"], 3)
        self.assertEqual(details["word_count"], 6)  # Line 1 Line 2 Line 3

    def test_nonexistent_file(self):
        fpath = self._vault / "nonexistent.md"
        details = compute_file_details(fpath, "some text")

        self.assertEqual(details["word_count"], 0)
        self.assertEqual(details["line_count"], 0)
        self.assertEqual(details["size"], 0)
        self.assertEqual(details["modified"], "")

    def test_unicode_content(self):
        fpath = self._vault / "unicode.md"
        text = "日本語テスト\nÉmojis: 🎉🚀\n"
        fpath.write_text(text)
        details = compute_file_details(fpath, text)

        self.assertEqual(details["line_count"], 3)  # text ends with \n so 3 lines
        self.assertEqual(details["word_count"], 3)  # 日本語テスト Émojis: 🎉🚀 (3 words)


if __name__ == "__main__":
    unittest.main()