"""Tests for markdown_vault.search_backend (ripgrep + Python fallback)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault import search_backend as sb


class TestSearchPython(unittest.TestCase):
    """The pure-Python fallback scanner (spans-aware)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, query, max_results=50):
        return sb._search_python(
            query, [str(self._tmp)], max_results, sb.SearchOptions(),
        )

    def test_finds_match_with_span(self):
        (self._tmp / "doc.md").write_text("Hello World", encoding="utf-8")
        res = self._run("World")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].line, 1)
        self.assertEqual(res[0].text, "Hello World")
        self.assertEqual(res[0].spans, [(6, 11)])

    def test_case_insensitive(self):
        (self._tmp / "doc.md").write_text("MARKDOWN", encoding="utf-8")
        res = self._run("markdown")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].spans, [(0, 8)])

    def test_multiple_spans_one_line(self):
        (self._tmp / "doc.md").write_text("a a a", encoding="utf-8")
        res = self._run("a")
        self.assertEqual(len(res), 1)  # one line -> one result
        self.assertEqual(res[0].spans, [(0, 1), (2, 3), (4, 5)])

    def test_ignores_non_md(self):
        (self._tmp / "doc.txt").write_text("target", encoding="utf-8")
        self.assertEqual(self._run("target"), [])

    def test_skips_dotdirs(self):
        hidden = self._tmp / ".trash"
        hidden.mkdir()
        (hidden / "gone.md").write_text("needle", encoding="utf-8")
        (self._tmp / "keep.md").write_text("needle", encoding="utf-8")
        res = self._run("needle")
        self.assertEqual(len(res), 1)
        self.assertIn("keep.md", res[0].path)

    def test_line_number(self):
        (self._tmp / "doc.md").write_text("l1\nneedle\nl3", encoding="utf-8")
        self.assertEqual(self._run("needle")[0].line, 2)

    def test_max_results(self):
        for i in range(20):
            (self._tmp / f"f{i}.md").write_text("needle", encoding="utf-8")
        self.assertEqual(len(self._run("needle", max_results=5)), 5)

    def test_no_match(self):
        (self._tmp / "doc.md").write_text("nothing", encoding="utf-8")
        self.assertEqual(self._run("xyz"), [])


class TestSearchEntry(unittest.TestCase):
    """The public ``search`` entry point (ripgrep when present, else Python)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        (self._tmp / "a.md").write_text("alpha needle beta", encoding="utf-8")
        (self._tmp / "b.md").write_text("no match here", encoding="utf-8")
        sub = self._tmp / "sub"
        sub.mkdir()
        (sub / "c.md").write_text("nested needle", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_finds_across_tree(self):
        res = sb.search("needle", [str(self._tmp)])
        paths = sorted(Path(m.path).name for m in res)
        self.assertEqual(paths, ["a.md", "c.md"])
        for m in res:
            self.assertTrue(m.spans)
            s, e = m.spans[0]
            self.assertEqual(m.text[s:e].lower(), "needle")

    def test_empty_query(self):
        self.assertEqual(sb.search("", [str(self._tmp)]), [])

    def test_nonexistent_path(self):
        self.assertEqual(sb.search("x", ["/no/such/dir"]), [])

    def test_parity_backend_vs_python(self):
        """Whatever backend runs must agree with the Python reference."""
        got = {(Path(m.path).name, m.line) for m in sb.search("needle", [str(self._tmp)])}
        ref = {(Path(m.path).name, m.line)
               for m in sb._search_python("needle", [str(self._tmp)], 50, sb.SearchOptions())}
        self.assertEqual(got, ref)


class TestSearchOptions(unittest.TestCase):
    """case-sensitive / whole-word / regex modifiers (via the Python path)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, query, **opts):
        return sb._search_python(
            query, [str(self._tmp)], 50, sb.SearchOptions(**opts),
        )

    def test_case_insensitive_default(self):
        (self._tmp / "d.md").write_text("Foo", encoding="utf-8")
        self.assertEqual(len(self._run("foo")), 1)

    def test_case_sensitive(self):
        (self._tmp / "d.md").write_text("Foo", encoding="utf-8")
        self.assertEqual(self._run("foo", case_sensitive=True), [])
        self.assertEqual(len(self._run("Foo", case_sensitive=True)), 1)

    def test_whole_word(self):
        (self._tmp / "d.md").write_text("category and a cat", encoding="utf-8")
        res = self._run("cat", whole_word=True)
        self.assertEqual(len(res), 1)
        # Only the standalone "cat" matches, not the one inside "category".
        s, e = res[0].spans[0]
        self.assertEqual(res[0].text[s:e], "cat")
        self.assertEqual(len(res[0].spans), 1)

    def test_regex(self):
        (self._tmp / "d.md").write_text("needle", encoding="utf-8")
        self.assertEqual(len(self._run("ne+dle", regex=True)), 1)
        # Without regex the '+' is literal, so no match.
        self.assertEqual(self._run("ne+dle"), [])

    def test_invalid_regex_returns_empty(self):
        (self._tmp / "d.md").write_text("anything", encoding="utf-8")
        self.assertEqual(self._run("[", regex=True), [])

    def test_options_via_ripgrep_entry(self):
        (self._tmp / "d.md").write_text("Foo bar", encoding="utf-8")
        # case-sensitive through the public entry (ripgrep when present).
        self.assertEqual(
            sb.search("foo", [str(self._tmp)], options=sb.SearchOptions(case_sensitive=True)),
            [],
        )
        self.assertEqual(
            len(sb.search("Foo", [str(self._tmp)], options=sb.SearchOptions(case_sensitive=True))),
            1,
        )


class TestSearchGrouped(unittest.TestCase):
    """Grouping per file + relevance ranking (Phase 2)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name, text):
        p = self._tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _grouped(self, query, **kw):
        return sb.search_grouped(query, [str(self._tmp)], **kw)

    def test_groups_matches_by_file(self):
        self._write("a.md", "needle\nneedle\nother")
        res = self._grouped("needle")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].total_matches, 2)
        self.assertEqual(len(res[0].matches), 2)

    def test_filename_only_match_included(self):
        self._write("needle-notes.md", "nothing relevant")
        res = self._grouped("needle")
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].name_hit)
        self.assertEqual(res[0].total_matches, 0)

    def test_name_hit_outranks_body_hit(self):
        self._write("needle.md", "unrelated body")          # name hit
        self._write("other.md", "needle needle needle")     # body hits only
        res = self._grouped("needle")
        self.assertEqual(Path(res[0].path).name, "needle.md")

    def test_heading_outranks_plain_body(self):
        self._write("head.md", "# needle heading")           # heading hit
        self._write("body.md", "a needle in the body")       # body hit
        res = self._grouped("needle")
        self.assertEqual(Path(res[0].path).name, "head.md")
        self.assertEqual(res[0].heading_hits, 1)

    def test_title_hit_detected(self):
        self._write("t.md", "---\ntitle: needle doc\n---\nbody")
        res = self._grouped("needle")
        self.assertTrue(res[0].title_hit)

    def test_max_lines_caps_shown_but_keeps_total(self):
        self._write("many.md", "\n".join(["needle"] * 30))
        res = self._grouped("needle", max_lines=5)
        self.assertEqual(len(res[0].matches), 5)
        self.assertEqual(res[0].total_matches, 30)

    def test_max_files_caps_files(self):
        for i in range(10):
            self._write(f"f{i}.md", "needle")
        res = self._grouped("needle", max_files=3)
        self.assertEqual(len(res), 3)


class TestSubmatchSpans(unittest.TestCase):
    def test_byte_to_char_multibyte(self):
        # "aéb": 'é' is 2 bytes, so byte span 1..3 -> char span 1..2.
        self.assertEqual(sb._submatch_spans("aéb", [{"start": 1, "end": 3}]), [(1, 2)])

    def test_skips_malformed(self):
        self.assertEqual(sb._submatch_spans("abc", [{"start": 0}]), [])


class TestHighlightMarkup(unittest.TestCase):
    def setUp(self):
        from markdown_vault.search import _highlight_markup
        self._mk = _highlight_markup

    def test_bolds_span(self):
        self.assertEqual(self._mk("Hello World", [(6, 11)]), "Hello <b>World</b>")

    def test_escapes_markup(self):
        out = self._mk("a<b>x", [(0, 1)])
        self.assertIn("&lt;b&gt;", out)
        self.assertTrue(out.startswith("<b>a</b>"))

    def test_leading_whitespace_shift(self):
        self.assertEqual(self._mk("   Hello World", [(9, 14)]), "Hello <b>World</b>")

    def test_no_spans(self):
        self.assertEqual(self._mk("plain text", []), "plain text")


if __name__ == "__main__":
    unittest.main()
