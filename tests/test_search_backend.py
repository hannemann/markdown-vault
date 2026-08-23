"""Tests for markdown_vault.search.search_backend (ripgrep + Python fallback)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.search import search_backend as sb


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
        from markdown_vault.search.search import _highlight_markup
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


class TestParseQuery(unittest.TestCase):
    """Query decomposition into operators + filters (Phase 3)."""

    def test_plain_terms_are_positives(self):
        p = sb.parse_query("foo bar")
        self.assertEqual(p.positives, ["foo", "bar"])
        self.assertEqual(p.excludes, [])

    def test_quoted_phrase_kept_whole(self):
        p = sb.parse_query('"foo bar" baz')
        self.assertEqual(p.positives, ["foo bar", "baz"])

    def test_exclusion(self):
        p = sb.parse_query("foo -bar")
        self.assertEqual(p.positives, ["foo"])
        self.assertEqual(p.excludes, ["bar"])

    def test_quoted_exclusion(self):
        p = sb.parse_query('-"no no"')
        self.assertEqual(p.excludes, ["no no"])

    def test_field_filters(self):
        p = sb.parse_query('tag:work path:sub vault:Notes term')
        self.assertEqual(p.tags, ["work"])
        self.assertEqual(p.paths, ["sub"])
        self.assertEqual(p.vaults, ["Notes"])
        self.assertEqual(p.positives, ["term"])

    def test_quoted_filter_value(self):
        p = sb.parse_query('path:"my dir"')
        self.assertEqual(p.paths, ["my dir"])

    def test_unknown_key_is_plain_term(self):
        # A colon that is not a known filter key stays part of the term.
        p = sb.parse_query("http://example.com")
        self.assertEqual(p.positives, ["http://example.com"])
        self.assertEqual(p.paths, [])

    def test_lone_dash_is_term(self):
        self.assertEqual(sb.parse_query("-").positives, ["-"])

    def test_has_operators(self):
        self.assertFalse(sb._has_operators(sb.parse_query("single")))
        self.assertFalse(sb._has_operators(sb.parse_query('"one phrase"')))
        self.assertTrue(sb._has_operators(sb.parse_query("two terms")))
        self.assertTrue(sb._has_operators(sb.parse_query("a -b")))
        self.assertTrue(sb._has_operators(sb.parse_query("tag:x")))


class TestSearchGroupedOperators(unittest.TestCase):
    """Operators + filters end-to-end through search_grouped (Phase 3)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name, text):
        p = self._tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _names(self, query, **kw):
        return sorted(
            Path(r.path).name
            for r in sb.search_grouped(query, [str(self._tmp)], **kw)
        )

    def test_and_requires_all_terms_in_file(self):
        self._write("both.md", "alpha here\nand beta there")
        self._write("one.md", "alpha only")
        self.assertEqual(self._names("alpha beta"), ["both.md"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses file permissions")
    def test_operator_search_logs_when_a_file_is_unreadable(self):
        # An unreadable note is dropped from operator-search results; that drop
        # must log — the ripgrep-fallback twin (_search_python) already logs it.
        self._write("ok.md", "alpha beta here")
        bad = self._write("locked.md", "alpha beta there")
        bad.chmod(0o000)
        try:
            with self.assertLogs("markdown_vault.search.search_backend", level="DEBUG"):
                sb.search_grouped("alpha beta", [str(self._tmp)])
        finally:
            bad.chmod(0o644)                         # let tearDown remove it

    def test_and_term_may_come_from_filename(self):
        self._write("alpha-notes.md", "beta in body")   # alpha via name, beta via body
        self.assertEqual(self._names("alpha beta"), ["alpha-notes.md"])

    def test_phrase_matches_literally(self):
        self._write("hit.md", "the quick brown fox")
        self._write("miss.md", "quick and brown but not adjacent")
        self.assertEqual(self._names('"quick brown"'), ["hit.md"])

    def test_exclusion_removes_file(self):
        self._write("keep.md", "needle without the other")
        self._write("drop.md", "needle with poison here")
        self.assertEqual(self._names("needle -poison"), ["keep.md"])

    def test_tag_filter(self):
        self._write("a.md", "---\ntags: [work, urgent]\n---\nneedle")
        self._write("b.md", "---\ntags: [home]\n---\nneedle")
        self.assertEqual(self._names("needle tag:work"), ["a.md"])

    def test_tag_filter_yaml_list_form(self):
        self._write("a.md", "---\ntags:\n  - work\n  - urgent\n---\nneedle")
        self.assertEqual(self._names("needle tag:urgent"), ["a.md"])

    def test_path_filter(self):
        self._write("sub/a.md", "needle")
        self._write("other/b.md", "needle")
        self.assertEqual(self._names("needle path:sub"), ["a.md"])

    def test_path_filter_is_vault_relative(self):
        # The vault's own directory name must not leak into path: matching.
        vault = self._tmp / "notes-vault"
        vault.mkdir()
        (vault / "a.md").write_text("needle", encoding="utf-8")
        sub = vault / "sub"
        sub.mkdir()
        (sub / "b.md").write_text("needle", encoding="utf-8")
        # "notes" appears only in the vault root path, not in any relative path.
        hit_root = sb.search_grouped("needle path:notes", [str(vault)])
        self.assertEqual(hit_root, [])
        hit_sub = sb.search_grouped("needle path:sub", [str(vault)])
        self.assertEqual([Path(r.path).name for r in hit_sub], ["b.md"])

    def test_empty_effective_queries_return_nothing(self):
        # Half-typed filters / empty phrase / excludes-only must not list all files.
        self._write("a.md", "needle")
        self._write("b.md", "needle")
        self.assertEqual(self._names("tag:"), [])
        self.assertEqual(self._names('""'), [])
        self.assertEqual(self._names("-needle"), [])

    def test_regex_mode_ignores_operators(self):
        # In regex mode a '-' is a literal pattern char, not an exclusion.
        self._write("d.md", "a-b")
        res = self._names("a-b", options=sb.SearchOptions(regex=True))
        self.assertEqual(res, ["d.md"])

    def test_filter_only_query_lists_files(self):
        self._write("a.md", "---\ntags: [work]\n---\nbody")
        self._write("b.md", "---\ntags: [home]\n---\nbody")
        self.assertEqual(self._names("tag:work"), ["a.md"])


class TestVaultFilter(unittest.TestCase):
    def test_restricts_to_named_vault(self):
        base = Path(tempfile.mkdtemp())
        try:
            (base / "Notes").mkdir()
            (base / "Work").mkdir()
            (base / "Notes" / "a.md").write_text("needle", encoding="utf-8")
            (base / "Work" / "b.md").write_text("needle", encoding="utf-8")
            vaults = [str(base / "Notes"), str(base / "Work")]
            res = sb.search_grouped("needle vault:Notes", vaults)
            self.assertEqual([Path(r.path).name for r in res], ["a.md"])
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestPatternError(unittest.TestCase):
    """pattern_error: the user-facing pre-check the search bar runs before a
    search, so an invalid regex surfaces a message instead of an empty result
    set indistinguishable from 'no matches'."""

    def test_invalid_regex_returns_message(self):
        msg = sb.pattern_error("(unclosed", sb.SearchOptions(regex=True))
        self.assertIsNotNone(msg)
        self.assertIn("pattern", msg.lower())

    def test_valid_regex_returns_none(self):
        self.assertIsNone(sb.pattern_error("foo.*bar", sb.SearchOptions(regex=True)))

    def test_regex_special_chars_are_fine_as_literal(self):
        # Non-regex mode escapes the query, so a would-be broken pattern is a
        # valid literal search — no error to surface.
        self.assertIsNone(sb.pattern_error("(unclosed", sb.SearchOptions()))


if __name__ == "__main__":
    unittest.main()
