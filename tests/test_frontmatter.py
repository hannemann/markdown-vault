"""Tests for the shared OKF frontmatter helpers."""

import datetime
import os
import tempfile
import unittest

from markdown_vault import frontmatter as fm


class TestParse(unittest.TestCase):
    def test_no_frontmatter_is_empty(self):
        self.assertEqual(fm.parse("# just a heading\n"), {})
        self.assertEqual(fm.parse(""), {})

    def test_parses_leading_block(self):
        meta = fm.parse("---\ntitle: Erde\ntags: [a, b]\n---\nbody")
        self.assertEqual(meta["title"], "Erde")
        self.assertEqual(meta["tags"], ["a", "b"])

    def test_invalid_yaml_is_empty(self):
        self.assertEqual(fm.parse("---\ntitle: : :\n- broken\n---\n"), {})

    def test_non_mapping_is_empty(self):
        self.assertEqual(fm.parse("---\n- just\n- a list\n---\n"), {})


class TestStatus(unittest.TestCase):
    def test_absent_defaults_to_stable(self):
        self.assertEqual(fm.status({}), "stable")

    def test_known_values(self):
        self.assertEqual(fm.status({"status": "draft"}), "draft")
        self.assertEqual(fm.status({"status": "Deprecated"}), "deprecated")

    def test_unknown_falls_back_to_stable(self):
        self.assertEqual(fm.status({"status": "archived"}), "stable")


class TestStale(unittest.TestCase):
    def setUp(self):
        self.today = datetime.date(2026, 8, 14)

    def test_absent_is_not_stale(self):
        self.assertFalse(fm.is_stale({}, today=self.today))

    def test_past_and_today_are_stale(self):
        self.assertTrue(fm.is_stale({"stale_after": "2026-08-01"}, today=self.today))
        self.assertTrue(fm.is_stale({"stale_after": "2026-08-14"}, today=self.today))

    def test_future_is_not_stale(self):
        self.assertFalse(fm.is_stale({"stale_after": "2027-01-01"}, today=self.today))

    def test_yaml_date_object_is_accepted(self):
        self.assertTrue(fm.is_stale({"stale_after": datetime.date(2020, 1, 1)},
                                    today=self.today))

    def test_unparseable_is_not_stale(self):
        self.assertFalse(fm.is_stale({"stale_after": "whenever"}, today=self.today))


class TestTitleDescription(unittest.TestCase):
    def test_present(self):
        meta = {"title": " Erde ", "description": "Der dritte Planet."}
        self.assertEqual(fm.title(meta), "Erde")
        self.assertEqual(fm.description(meta), "Der dritte Planet.")

    def test_absent_is_empty(self):
        self.assertEqual(fm.title({}), "")
        self.assertEqual(fm.description({"description": None}), "")


class TestLifecycleOf(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        fm.invalidate()          # start from a clean cache

    def _write(self, name, text):
        path = os.path.join(self._dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_status_and_stale(self):
        path = self._write(
            "a.md", "---\nstatus: deprecated\nstale_after: 2020-01-01\n---\nx")
        self.assertEqual(fm.lifecycle_of(path, today=datetime.date(2026, 1, 1)),
                         ("deprecated", True))

    def test_not_yet_stale(self):
        path = self._write("b.md", "---\nstale_after: 2099-01-01\n---\nx")
        self.assertEqual(fm.lifecycle_of(path, today=datetime.date(2026, 1, 1)),
                         ("stable", False))

    def test_staleness_recomputed_against_today_despite_cache(self):
        # Same file (same mtime → cache hit), but crossing the date flips stale.
        path = self._write("c.md", "---\nstale_after: 2026-06-15\n---\nx")
        self.assertFalse(fm.lifecycle_of(path, today=datetime.date(2026, 6, 14))[1])
        self.assertTrue(fm.lifecycle_of(path, today=datetime.date(2026, 6, 15))[1])

    def test_status_of_delegates(self):
        path = self._write("d.md", "---\nstatus: draft\n---\nx")
        self.assertEqual(fm.status_of(path), "draft")

    def test_unreadable(self):
        self.assertEqual(fm.lifecycle_of(os.path.join(self._dir, "no.md")),
                         ("stable", False))


class TestTipOf(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()

    def _write(self, name, text):
        path = os.path.join(self._dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_frontmatter_title_and_description(self):
        path = self._write(
            "erde.md",
            "---\ntitle: Die Erde\ndescription: Der dritte Planet.\n---\nbody text")
        self.assertEqual(fm.tip_of(path), ("Die Erde", "Der dritte Planet."))

    def test_falls_back_to_stem_and_body_preview(self):
        path = self._write("mars.md", "The red planet is cold and dusty.")
        title, desc = fm.tip_of(path)
        self.assertEqual(title, "mars")
        self.assertEqual(desc, "The red planet is cold and dusty.")

    def test_title_present_description_from_body(self):
        path = self._write(
            "venus.md", "---\ntitle: Venus\n---\nSecond planet from the sun.")
        self.assertEqual(fm.tip_of(path), ("Venus", "Second planet from the sun."))

    def test_body_preview_is_cut_and_whitespace_collapsed(self):
        body = "word   \n\n  spaced\ttext " + "x" * 400
        path = self._write("long.md", body)
        _title, desc = fm.tip_of(path, preview_chars=200)
        self.assertEqual(len(desc), 200)
        self.assertTrue(desc.startswith("word spaced text "))
        self.assertNotIn("\n", desc)
        self.assertNotIn("  ", desc)

    def test_body_preview_strips_markdown(self):
        path = self._write(
            "venus.md",
            "# Venus\n\nVenus ist der zweite Planet von der [[sonne]] und der "
            "**Erde** [ähnlich](https://x). Siehe [[erde|die Erde]].")
        _title, desc = fm.tip_of(path)
        self.assertEqual(
            desc,
            "Venus Venus ist der zweite Planet von der sonne und der Erde "
            "ähnlich. Siehe die Erde.")

    def test_unreadable_path(self):
        path = os.path.join(self._dir, "missing.md")
        self.assertEqual(fm.tip_of(path), ("missing", ""))


if __name__ == "__main__":
    unittest.main()
