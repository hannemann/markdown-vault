"""Tests for the shared FenceTracker (CommonMark fenced-code state)."""

import unittest

from markdown_vault.markdown.md_fences import FenceTracker


class TestFenceTracker(unittest.TestCase):
    def test_plain_text_is_never_fenced(self):
        t = FenceTracker()
        self.assertFalse(t.feed("just some prose"))
        self.assertFalse(t.in_fence)
        self.assertFalse(t.feed("| a | b |"))

    def test_opener_enters_fence(self):
        t = FenceTracker()
        self.assertTrue(t.feed("```"))
        self.assertTrue(t.in_fence)
        self.assertTrue(t.opened)
        self.assertFalse(t.closed)

    def test_content_between_markers_is_fenced(self):
        t = FenceTracker()
        t.feed("```")
        self.assertTrue(t.feed("code here"))
        self.assertTrue(t.in_fence)
        self.assertFalse(t.opened)

    def test_matching_closer_leaves_fence(self):
        t = FenceTracker()
        t.feed("```")
        t.feed("code")
        self.assertTrue(t.feed("```"))     # the closing marker is still "fenced"
        self.assertFalse(t.in_fence)
        self.assertTrue(t.closed)

    def test_info_string_captured_on_opener(self):
        t = FenceTracker()
        t.feed("```python")
        self.assertEqual(t.info, "python")

    def test_inner_shorter_run_does_not_close_longer_fence(self):
        t = FenceTracker()
        t.feed("````")                     # opener of length 4
        self.assertTrue(t.feed("```"))     # 3 < 4 → content, not a close
        self.assertTrue(t.in_fence)

    def test_longer_run_closes_shorter_fence(self):
        t = FenceTracker()
        t.feed("```")
        t.feed("````")                     # 4 >= 3 → closes
        self.assertFalse(t.in_fence)

    def test_different_fence_char_does_not_close(self):
        t = FenceTracker()
        t.feed("```")
        self.assertTrue(t.feed("~~~"))     # tilde cannot close a backtick fence
        self.assertTrue(t.in_fence)

    def test_line_with_trailing_text_does_not_close(self):
        t = FenceTracker()
        t.feed("```")
        self.assertTrue(t.feed("``` still code"))   # not a bare fence run
        self.assertTrue(t.in_fence)

    def test_tilde_fence(self):
        t = FenceTracker()
        self.assertTrue(t.feed("~~~"))
        self.assertTrue(t.in_fence)
        self.assertTrue(t.feed("~~~"))
        self.assertFalse(t.in_fence)

    def test_indented_opener_and_closer(self):
        t = FenceTracker()
        self.assertTrue(t.feed("   ```"))
        self.assertTrue(t.feed("   ```"))
        self.assertFalse(t.in_fence)

    def test_flags_reset_between_feeds(self):
        t = FenceTracker()
        t.feed("```")                      # opened
        t.feed("code")                     # opened must reset
        self.assertFalse(t.opened)
        self.assertEqual(t.info, "")


if __name__ == "__main__":
    unittest.main()
