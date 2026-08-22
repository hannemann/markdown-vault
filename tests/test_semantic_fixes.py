"""Regression tests for the semantic-search review fixes (Round 22).

All targets are static/class methods, so no GTK widget is instantiated.
"""

import unittest

from markdown_vault.search.search import SearchBar
from markdown_vault.search import semantic_search


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
        # Basename only, in the error branch too — the directory is not part of
        # the model's identity (a move must not change the signature).
        sig = semantic_search._onnx_sig("/nope/model.onnx", "/nope/tok.json")
        self.assertEqual(sig, "onnx:model.onnx:missing|tok.json:missing")

    def test_same_file_new_location_keeps_the_signature(self):
        # The bug: a folder move changed the signature and forced a 70-minute
        # full rebuild, though the model was byte-identical. Same basename + size
        # + mtime at a different path must yield the same signature.
        import tempfile
        import os
        from pathlib import Path
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        for d in (d1, d2):
            for name, data in (("model.onnx", b"aaaa"), ("tokenizer.json", b"{}")):
                p = Path(d) / name
                p.write_bytes(data)
                os.utime(p, (0, 12345))          # identical mtime in both dirs
        sig1 = semantic_search._onnx_sig(str(Path(d1) / "model.onnx"),
                                    str(Path(d1) / "tokenizer.json"))
        sig2 = semantic_search._onnx_sig(str(Path(d2) / "model.onnx"),
                                    str(Path(d2) / "tokenizer.json"))
        self.assertEqual(sig1, sig2)             # a move must not invalidate
        self.assertNotIn(d1, sig1)               # no directory in the signature

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
            sig1 = semantic_search._onnx_sig(model, tok)
            with open(model, "wb") as fh:  # different size → different signature
                fh.write(b"aaaa")
                fh.flush()
                os.utime(model, (0, 100))
            sig2 = semantic_search._onnx_sig(model, tok)
            self.assertNotEqual(sig1, sig2)
        finally:
            os.unlink(model)
            os.unlink(tok)


if __name__ == "__main__":
    unittest.main()
