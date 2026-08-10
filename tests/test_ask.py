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


def _src(n, text):
    return ask.Source(n=n, path=f"/v/s{n}.md", line=1, text=text)


class TestVerifyCitations(unittest.TestCase):
    """Stufe 1 (referential integrity) + Stufe 2a (numeric attribution)."""

    def test_valid_citations_pass_through(self):
        srcs = [_src(1, "Jupiter 318 Erdmassen"), _src(2, "Mars 0.1")]
        text, cited, warns = ask.verify_citations(
            "Jupiter is heaviest [1].", srcs)
        self.assertEqual(text, "Jupiter is heaviest [1].")
        self.assertEqual([s.n for s in cited], [1])  # only the cited source
        self.assertEqual(warns, [])

    def test_invented_source_number_is_stripped_and_warned(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        text, cited, warns = ask.verify_citations(
            "Foo [1] and bar [7].", srcs)
        self.assertEqual(text, "Foo [1] and bar.")   # [7] removed, " ." tidied
        self.assertEqual([s.n for s in cited], [1])
        self.assertTrue(any("[7]" in w for w in warns))

    def test_partial_group_keeps_valid_drops_invalid(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        text, _cited, warns = ask.verify_citations("X [1, 9].", srcs)
        self.assertEqual(text, "X [1].")
        self.assertTrue(any("[9]" in w for w in warns))

    def test_no_citations_falls_back_to_all_sources(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        _text, cited, warns = ask.verify_citations("No cites here.", srcs)
        self.assertEqual([s.n for s in cited], [1, 2])
        self.assertEqual(warns, [])

    def test_attributed_number_present_in_excerpt_is_ok(self):
        srcs = [_src(1, "Jupiter has 318 Earth masses")]
        _t, _c, warns = ask.verify_citations("Jupiter is 318 Earth masses [1].", srcs)
        self.assertEqual(warns, [])

    def test_attributed_number_absent_from_excerpt_warns(self):
        srcs = [_src(1, "Jupiter is the fifth planet")]
        _t, _c, warns = ask.verify_citations("Jupiter has 95 moons [1].", srcs)
        self.assertTrue(any("95" in w for w in warns))

    def test_number_formatting_normalises(self):
        srcs = [_src(1, "Mass 1.898 kg")]  # thousand sep in excerpt
        _t, _c, warns = ask.verify_citations("Mass is 1898 kg [1].", srcs)
        self.assertEqual(warns, [])

    def test_list_number_not_bridged_across_newline_to_later_citation(self):
        # Real case: "- Jupiter: 95" belongs to the [1] list; a later "Aus [4]"
        # must not pull the 95 across the paragraph break.
        srcs = [_src(1, "moon counts per planet"), _src(4, "Saturn has the most")]
        text = ("Aus [1] die Anzahl der Monde:\n- Jupiter: 95\n\n"
                "Aus [4] ist Saturn am höchsten.")
        _t, _c, warns = ask.verify_citations(text, srcs)
        self.assertEqual(warns, [])

    def test_synthesised_count_not_attributed_is_not_flagged(self):
        # "5" is not directly before a citation → left alone (derivation).
        srcs = [_src(1, "Mercury"), _src(2, "Venus")]
        _t, _c, warns = ask.verify_citations(
            "We know 5 planets in total, see [1] and [2].", srcs)
        self.assertEqual(warns, [])


class TestVerifyCitationsThorough(unittest.TestCase):
    """Systematic edge coverage for verify_citations (see ask-next-steps)."""

    # --- Stufe 1: referential integrity -------------------------------

    def test_multiple_valid_citations_reduce_to_cited_subset_in_order(self):
        srcs = [_src(1, "a"), _src(2, "b"), _src(3, "c")]
        _t, cited, warns = ask.verify_citations("See [3] and [1].", srcs)
        self.assertEqual([s.n for s in cited], [1, 3])  # ascending, only cited
        self.assertEqual(warns, [])

    def test_duplicate_valid_citation_listed_once(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        _t, cited, _w = ask.verify_citations("[1] and again [1].", srcs)
        self.assertEqual([s.n for s in cited], [1])

    def test_all_invalid_falls_back_to_all_sources_and_warns(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        text, cited, warns = ask.verify_citations("Only [9] here.", srcs)
        self.assertEqual(text, "Only here.")
        self.assertEqual([s.n for s in cited], [1, 2])  # fallback: all
        self.assertTrue(any("[9]" in w for w in warns))

    def test_grouped_valid_citation_maps_both_sources(self):
        srcs = [_src(1, "a"), _src(2, "b"), _src(3, "c")]
        _t, cited, _w = ask.verify_citations("Both [1, 2].", srcs)
        self.assertEqual([s.n for s in cited], [1, 2])

    def test_whitespace_inside_brackets_is_parsed(self):
        srcs = [_src(1, "a"), _src(2, "b")]
        text, cited, warns = ask.verify_citations("X [ 1 , 2 ].", srcs)
        self.assertEqual([s.n for s in cited], [1, 2])
        self.assertEqual(warns, [])
        self.assertEqual(text, "X [1, 2].")  # normalised spacing

    def test_leading_citation_stripped_and_trimmed(self):
        srcs = [_src(1, "a")]
        text, _c, _w = ask.verify_citations("[9] Jupiter is big.", srcs)
        self.assertEqual(text, "Jupiter is big.")

    def test_invented_number_reported_once_when_repeated(self):
        srcs = [_src(1, "a")]
        _t, _c, warns = ask.verify_citations("[7] foo [7] bar.", srcs)
        self.assertEqual(len(warns), 1)

    def test_non_contiguous_source_numbers_stay_valid(self):
        # After eingedampfen the model may cite e.g. [1] and [4]; both real.
        srcs = [_src(1, "a"), _src(4, "d")]
        _t, cited, warns = ask.verify_citations("See [1] and [4].", srcs)
        self.assertEqual([s.n for s in cited], [1, 4])
        self.assertEqual(warns, [])

    def test_singular_excerpt_grammar(self):
        srcs = [_src(1, "a")]
        _t, _c, warns = ask.verify_citations("Bad [5].", srcs)
        self.assertTrue(any("1 excerpt to cite" in w for w in warns))

    def test_no_sources_does_not_crash(self):
        text, cited, warns = ask.verify_citations("Answer [1].", [])
        self.assertEqual(text, "Answer.")
        self.assertEqual(cited, [])
        self.assertTrue(warns)

    def test_plain_text_without_citations_is_unchanged(self):
        srcs = [_src(1, "a")]
        text, cited, warns = ask.verify_citations("Just prose, no cites.", srcs)
        self.assertEqual(text, "Just prose, no cites.")
        self.assertEqual([s.n for s in cited], [1])
        self.assertEqual(warns, [])

    # --- Stufe 2a: numeric attribution --------------------------------

    def test_comma_clause_breaks_attribution(self):
        srcs = [_src(1, "no numbers here at all")]
        _t, _c, warns = ask.verify_citations("5 planets, see [1].", srcs)
        self.assertEqual(warns, [])  # "5" separated by a comma → not attributed

    def test_decimal_comma_normalises(self):
        srcs = [_src(1, "Umlaufzeit 11,86 Jahre")]
        _t, _c, warns = ask.verify_citations("Es sind 11,86 Jahre [1].", srcs)
        self.assertEqual(warns, [])

    def test_percent_value_present(self):
        srcs = [_src(1, "etwa 50% der Masse")]
        _t, _c, warns = ask.verify_citations("Rund 50% davon [1].", srcs)
        self.assertEqual(warns, [])

    def test_zero_value_present(self):
        srcs = [_src(1, "Merkur hat 0 Monde")]
        _t, _c, warns = ask.verify_citations("Merkur: 0 Monde [1].", srcs)
        self.assertEqual(warns, [])

    def test_group_attribution_present_in_one_excerpt_is_ok(self):
        srcs = [_src(1, "Jupiter 318 Erdmassen"), _src(2, "nichts")]
        _t, _c, warns = ask.verify_citations("Masse 318 [1, 2].", srcs)
        self.assertEqual(warns, [])

    def test_group_attribution_absent_from_all_excerpts_warns(self):
        srcs = [_src(1, "keine zahl"), _src(2, "auch nicht")]
        _t, _c, warns = ask.verify_citations("Masse 318 [1, 2].", srcs)
        self.assertTrue(any("318" in w for w in warns))

    def test_mixed_attributed_numbers_flag_only_absent(self):
        srcs = [_src(1, "hat 318 Erdmassen")]
        _t, _c, warns = ask.verify_citations(
            "A 318 [1] und B 999 [1].", srcs)
        joined = " ".join(warns)
        self.assertIn("999", joined)
        self.assertNotIn("318", joined)

    def test_repeated_absent_value_warned_once(self):
        srcs = [_src(1, "leer")]
        _t, _c, warns = ask.verify_citations("X 999 [1]. Y 999 [1].", srcs)
        self.assertEqual(len(warns), 1)

    def test_adjacent_citations_do_not_attribute_bracket_digits(self):
        # "[1][2]" — the "1" inside the first bracket must not be read as a
        # value attributed to [2].
        srcs = [_src(1, "a"), _src(2, "b")]
        _t, _c, warns = ask.verify_citations("Facts [1][2].", srcs)
        self.assertEqual(warns, [])


class TestOllamaChatPayload(unittest.TestCase):
    """The Ollama request must raise num_ctx above the truncating default."""

    def _capture(self, **kwargs):
        import io, json
        from unittest import mock
        captured = {}

        class FakeResp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return FakeResp(json.dumps({"message": {"content": "hi"}}).encode())

        with mock.patch("markdown_vault.ask.urllib.request.urlopen", fake_urlopen):
            out = ask.OllamaChat("m", "http://x", **kwargs).chat("s", "u")
        return out, captured["body"]

    def test_default_num_ctx_is_large(self):
        out, body = self._capture()
        self.assertEqual(out, "hi")
        self.assertEqual(body["options"]["num_ctx"], ask.OllamaChat.DEFAULT_NUM_CTX)
        self.assertGreaterEqual(body["options"]["num_ctx"], 8192)

    def test_num_ctx_is_overridable(self):
        _out, body = self._capture(num_ctx=4096)
        self.assertEqual(body["options"]["num_ctx"], 4096)


class TestAnswer(unittest.TestCase):
    def test_no_hits_returns_grounded_fallback(self):
        a = ask.answer("q", [], chat=None)  # chat must not be called with no hits
        self.assertIsNone(a.error)
        self.assertIn("couldn't find", a.text.lower())

    def test_answer_runs_verification(self):
        hits = [(_chunk("/v/a.md", 1, "Jupiter is the fifth planet"), 0.9)]
        chat = SimpleNamespace(chat=lambda system, user: "Jupiter has 95 moons [1] [4].")
        a = ask.answer("q", hits, chat=chat)
        self.assertEqual([s.n for s in a.sources], [1])   # [4] not shown
        self.assertNotIn("[4]", a.text)                   # invented, stripped
        self.assertTrue(a.warnings)                        # invented + numeric


if __name__ == "__main__":
    unittest.main()
