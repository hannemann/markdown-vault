"""Regression tests for the semantic-search review fixes (Round 22).

All targets are static/class methods, so no GTK widget is instantiated.
"""

import unittest

from markdown_vault.search import SearchBar
from markdown_vault.app_window import MainWindow


class TestStripOperators(unittest.TestCase):
    """R22.14 — operator/filter tokens must not reach the embedder."""

    def test_drops_filters_exclusions_and_quotes(self):
        self.assertEqual(
            SearchBar._strip_operators('planeten -foo tag:bar "neptun" path:x/y'),
            "planeten neptun",
        )

    def test_plain_query_unchanged(self):
        self.assertEqual(SearchBar._strip_operators("den planeten"), "den planeten")

    def test_only_operators_becomes_empty(self):
        self.assertEqual(SearchBar._strip_operators('-a tag:b vault:c'), "")


class TestHasOperators(unittest.TestCase):
    """Structured queries must suppress the (operator-ignoring) semantic merge."""

    def test_field_filter_and_exclusion_detected(self):
        self.assertTrue(SearchBar._has_operators('tag:foo -bar "neptun"'))
        self.assertTrue(SearchBar._has_operators("path:x/y planets"))
        self.assertTrue(SearchBar._has_operators("planets -pluto"))

    def test_plain_prose_and_quotes_are_not_operators(self):
        self.assertFalse(SearchBar._has_operators("den planeten neptun"))
        self.assertFalse(SearchBar._has_operators('"neptun"'))
        self.assertFalse(SearchBar._has_operators("foo-bar"))  # hyphen mid-word


class TestUnderVault(unittest.TestCase):
    """R22.7 — scope filter must be separator-aware, not a raw prefix."""

    def test_sibling_vault_does_not_leak(self):
        self.assertFalse(
            SearchBar._under_vault("/home/u/notes-archive/a.md", ["/home/u/notes"]))

    def test_file_inside_vault_matches(self):
        self.assertTrue(
            SearchBar._under_vault("/home/u/notes/a.md", ["/home/u/notes"]))

    def test_vault_root_itself_matches(self):
        self.assertTrue(
            SearchBar._under_vault("/home/u/notes", ["/home/u/notes"]))


class TestOnnxSignature(unittest.TestCase):
    """R22.2 — the ONNX cache signature must fold in file identity."""

    def test_missing_files_are_marked(self):
        sig = MainWindow._onnx_sig("/nope/model.onnx", "/nope/tok.json")
        self.assertEqual(sig, "onnx:/nope/model.onnx:missing|/nope/tok.json:missing")

    def test_size_and_mtime_change_the_signature(self):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".onnx", delete=False) as f:
            f.write(b"a")
            model = f.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"{}")
            tok = f.name
        try:
            sig1 = MainWindow._onnx_sig(model, tok)
            with open(model, "wb") as fh:  # different size → different signature
                fh.write(b"aaaa")
                fh.flush()
                os.utime(model, (0, 100))
            sig2 = MainWindow._onnx_sig(model, tok)
            self.assertNotEqual(sig1, sig2)
        finally:
            os.unlink(model)
            os.unlink(tok)


if __name__ == "__main__":
    unittest.main()
