"""Tests for markdown_vault.ask — the RAG prompt builder (pure logic)."""

import unittest
from types import SimpleNamespace

from markdown_vault import ask


def _chunk(path, line, text):
    return SimpleNamespace(path=path, line=line, text=text)


class TestBuildMessages(unittest.TestCase):
    def test_numbering_and_source_mapping(self):
        hits = [(_chunk("/v/erde.md", 3, "Erde ist blau"), 0.9),
                (_chunk("/v/mars.md", 1, "Mars ist rot"), 0.8)]
        system, user, sources = ask.build_messages("Welche Farbe?", hits, language="German")
        self.assertIn("[1] erde.md (line 3):", user)
        self.assertIn("[2] mars.md (line 1):", user)
        self.assertIn("Erde ist blau", user)
        self.assertEqual([s.n for s in sources], [1, 2])
        self.assertEqual(sources[0].path, "/v/erde.md")
        self.assertEqual(sources[1].line, 1)

    def test_language_is_substituted(self):
        system, _u, _s = ask.build_messages("q", [], language="German")
        self.assertIn("German", system)
        self.assertNotIn("{language}", system)

    def test_no_excerpts_path(self):
        _system, user, sources = ask.build_messages("q", [])
        self.assertIn("(no excerpts)", user)
        self.assertEqual(sources, [])

    def test_custom_prompt_with_stray_braces_does_not_crash(self):
        # str.replace (not str.format) → stray {} in a user prompt is safe.
        tmpl = "Reply in {language}. Keep {this} and {} verbatim."
        system, _u, _s = ask.build_messages(
            "q", [], language="French", system_template=tmpl)
        self.assertIn("Reply in French.", system)
        self.assertIn("{this}", system)
        self.assertIn("{}", system)


class TestAnswer(unittest.TestCase):
    def test_no_hits_returns_grounded_fallback(self):
        a = ask.answer("q", [], chat=None)  # chat must not be called with no hits
        self.assertIsNone(a.error)
        self.assertIn("couldn't find", a.text.lower())


if __name__ == "__main__":
    unittest.main()
