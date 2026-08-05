"""Behavioral tests for the editor's in-view search options and replace."""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from markdown_vault.editor import Editor


class TestEditorReplace(unittest.TestCase):
    def setUp(self):
        self.editor = Editor()

    def _text(self):
        buf = self.editor._buffer
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def test_replace_current_then_advances(self):
        self.editor._buffer.set_text("foo bar foo")
        self.editor.set_search_options(False, False, False)
        self.editor.search_set_text("foo")
        self.assertTrue(self.editor.replace_current("X"))
        self.assertEqual(self._text(), "X bar foo")

    def test_replace_all(self):
        self.editor._buffer.set_text("a a a")
        self.editor.set_search_options(False, False, False)
        self.editor.search_set_text("a")
        self.assertEqual(self.editor.replace_all("b"), 3)
        self.assertEqual(self._text(), "b b b")

    def test_case_sensitive_option(self):
        self.editor._buffer.set_text("Foo foo")
        self.editor.set_search_options(True, False, False)  # case sensitive
        self.editor.search_set_text("foo")
        self.assertEqual(self.editor.replace_all("z"), 1)
        self.assertEqual(self._text(), "Foo z")

    def test_whole_word_option(self):
        self.editor._buffer.set_text("cat category cat")
        self.editor.set_search_options(False, True, False)  # whole word
        self.editor.search_set_text("cat")
        self.assertEqual(self.editor.replace_all("dog"), 2)
        self.assertEqual(self._text(), "dog category dog")

    def test_regex_option(self):
        self.editor._buffer.set_text("a1 a2 a3")
        self.editor.set_search_options(False, False, True)  # regex
        self.editor.search_set_text(r"a\d")
        self.assertEqual(self.editor.replace_all("N"), 3)
        self.assertEqual(self._text(), "N N N")

    def test_replace_all_no_match(self):
        self.editor._buffer.set_text("nothing")
        self.editor.set_search_options(False, False, False)
        self.editor.search_set_text("xyz")
        self.assertEqual(self.editor.replace_all("q"), 0)
        self.assertEqual(self._text(), "nothing")


if __name__ == "__main__":
    unittest.main()
