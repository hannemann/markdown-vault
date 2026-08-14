"""Tests for the shared OKF frontmatter helpers."""

import datetime
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


if __name__ == "__main__":
    unittest.main()
