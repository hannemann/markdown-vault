"""Tests for markdown_vault.search.quick_open (fuzzy switcher backend)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.search import quick_open as qo


class TestFuzzyMatch(unittest.TestCase):
    def test_subsequence_matches(self):
        hit = qo.fuzzy_match("eh", "engineering-handbook")
        self.assertIsNotNone(hit)
        _score, positions = hit
        self.assertEqual("engineering-handbook"[positions[0]], "e")
        self.assertEqual("engineering-handbook"[positions[1]], "h")

    def test_non_subsequence_returns_none(self):
        self.assertIsNone(qo.fuzzy_match("xyz", "engineering"))

    def test_case_insensitive(self):
        self.assertIsNotNone(qo.fuzzy_match("ENG", "engineering"))

    def test_empty_query_matches_everything(self):
        self.assertEqual(qo.fuzzy_match("", "anything"), (0.0, []))

    def test_contiguous_beats_wide_gap_scattered(self):
        # A literal substring should outrank the same letters spread far apart.
        contiguous = qo.fuzzy_match("cat", "cat-notes")[0]
        scattered = qo.fuzzy_match("cat", "concatenation-abstract-tree")[0]
        self.assertGreater(contiguous, scattered)

    def test_word_start_beats_midword(self):
        start = qo.fuzzy_match("h", "my-handbook")[0]      # after '-'
        mid = qo.fuzzy_match("h", "aaah")[0]               # mid-word
        self.assertGreater(start, mid)

    def test_prefix_match_scores_high(self):
        prefix = qo.fuzzy_match("eng", "engineering")[0]
        suffix = qo.fuzzy_match("ing", "engineering")[0]
        self.assertGreater(prefix, suffix)


class TestFilenameProvider(unittest.TestCase):
    def _cands(self, names, mtimes=None):
        out = []
        for i, n in enumerate(names):
            out.append(qo.Candidate(
                path=f"/v/{n}.md", name=n, folder="/v",
                mtime=float(mtimes[i]) if mtimes else 0.0,
            ))
        return out

    def test_query_ranks_by_score(self):
        prov = qo.FilenameProvider(self._cands(["engineering-handbook", "eh-notes"]))
        res = prov.search("eh")
        self.assertEqual(res[0].name, "eh-notes")  # contiguous prefix wins

    def test_query_filters_non_matches(self):
        prov = qo.FilenameProvider(self._cands(["alpha", "beta"]))
        res = prov.search("alp")
        self.assertEqual([r.name for r in res], ["alpha"])

    def test_empty_query_recent_first(self):
        cands = self._cands(["a", "b", "c"], mtimes=[1, 2, 3])
        prov = qo.FilenameProvider(cands, recent_paths=["/v/b.md"])
        res = prov.search("")
        # recent first, then remaining by mtime desc (c=3 before a=1)
        self.assertEqual([r.name for r in res], ["b", "c", "a"])

    def test_empty_query_by_mtime_without_recent(self):
        cands = self._cands(["a", "b"], mtimes=[5, 9])
        res = qo.FilenameProvider(cands).search("")
        self.assertEqual([r.name for r in res], ["b", "a"])

    def test_limit(self):
        prov = qo.FilenameProvider(self._cands([f"n{i}" for i in range(50)]))
        self.assertEqual(len(prov.search("n", limit=5)), 5)

    def test_matches_alias(self):
        c = qo.Candidate(path="/v/2026-adr-1.md", name="2026-adr-1", folder="/v",
                         mtime=0.0, aliases=["Architecture Decision"], rel="2026-adr-1.md")
        res = qo.FilenameProvider([c]).search("architecture")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].matched_text, "Architecture Decision")

    def test_name_match_has_no_matched_text(self):
        c = qo.Candidate(path="/v/meeting.md", name="meeting", folder="/v",
                         mtime=0.0, aliases=["MoM"], rel="meeting.md")
        res = qo.FilenameProvider([c]).search("meet")
        self.assertIsNone(res[0].matched_text)

    def test_path_match_gated_on_slash(self):
        c = qo.Candidate(path="/v/sub/note.md", name="note", folder="/v/sub",
                         mtime=0.0, aliases=[], rel="sub/note.md")
        # A plain fragment matching only the path (not the name) is ignored…
        self.assertEqual(qo.FilenameProvider([c]).search("sub"), [])
        # …but a query with a slash matches the relative path.
        res = qo.FilenameProvider([c]).search("sub/note")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].matched_text, "sub/note.md")


class TestQuickOpenEngine(unittest.TestCase):
    def test_merges_and_dedupes_by_path(self):
        c = qo.Candidate(path="/v/x.md", name="x", folder="/v", mtime=0.0)
        low = _StubProvider([qo.QuickResult(path="/v/x.md", name="x", folder="/v", score=1.0)])
        high = _StubProvider([qo.QuickResult(path="/v/x.md", name="x", folder="/v", score=9.0)])
        eng = qo.QuickOpenEngine([low, high])
        res = eng.search("x")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].score, 9.0)  # best score kept

    def test_orders_by_score_desc(self):
        prov = _StubProvider([
            qo.QuickResult(path="/a.md", name="a", folder="/", score=2.0),
            qo.QuickResult(path="/b.md", name="b", folder="/", score=5.0),
        ])
        res = qo.QuickOpenEngine([prov]).search("q")
        self.assertEqual([r.name for r in res], ["b", "a"])


class TestBuildCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_indexes_md_only_and_skips_dotdirs(self):
        (self._tmp / "note.md").write_text("x", encoding="utf-8")
        (self._tmp / "skip.txt").write_text("x", encoding="utf-8")
        hidden = self._tmp / ".trash"
        hidden.mkdir()
        (hidden / "gone.md").write_text("x", encoding="utf-8")
        cands = qo.build_candidates([str(self._tmp)])
        self.assertEqual([c.name for c in cands], ["note"])

    def test_extracts_aliases_and_relpath(self):
        (self._tmp / "sub").mkdir()
        (self._tmp / "sub" / "n.md").write_text(
            "---\naliases: [ADR, Foo Bar]\n---\nbody", encoding="utf-8")
        c = qo.build_candidates([str(self._tmp)])[0]
        self.assertEqual(c.aliases, ["ADR", "Foo Bar"])
        self.assertEqual(c.rel, "sub/n.md")

    def test_alias_singular_and_no_frontmatter(self):
        (self._tmp / "a.md").write_text("---\nalias: Nick\n---\n", encoding="utf-8")
        (self._tmp / "b.md").write_text("no frontmatter", encoding="utf-8")
        by_name = {c.name: c for c in qo.build_candidates([str(self._tmp)])}
        self.assertEqual(by_name["a"].aliases, ["Nick"])
        self.assertEqual(by_name["b"].aliases, [])


class _StubProvider:
    def __init__(self, results):
        self._results = results

    def search(self, query, limit=30):
        return self._results[:limit]


if __name__ == "__main__":
    unittest.main()
