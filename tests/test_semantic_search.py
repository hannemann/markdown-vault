"""Tests for markdown_vault.semantic_search (Phase 5 core, backend-agnostic)."""

import unittest

from markdown_vault import semantic_search as ss


class _StubEmbedder:
    """Deterministic bag-of-marker-words embedder (no model needed).

    Each of a few marker words maps to one dimension, so a chunk's vector
    reflects which markers it contains — enough to test ranking.
    """

    _VOCAB = {"alpha": 0, "beta": 1, "gamma": 2, "delta": 3}

    def embed(self, texts, is_query=False):
        vecs = []
        for t in texts:
            v = [0.0, 0.0, 0.0, 0.0]
            for w in t.lower().split():
                if w in self._VOCAB:
                    v[self._VOCAB[w]] += 1.0
            vecs.append(v)
        return vecs


class TestChunkMarkdown(unittest.TestCase):
    def test_paragraphs_are_separate_chunks(self):
        # Distinct paragraphs stay separate so a topic sentence isn't diluted.
        text = "# Title\n\nfirst paragraph here.\n\nsecond paragraph here.\n"
        chunks = ss.chunk_markdown(text, "/n.md")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, "# Title\nfirst paragraph here.")  # heading + body
        self.assertEqual(chunks[1].text, "second paragraph here.")

    def test_heading_couples_with_its_body(self):
        text = "## Section\n\nthe body text goes here.\n"
        chunks = ss.chunk_markdown(text, "/n.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "## Section\nthe body text goes here.")

    def test_topic_sentence_isolated_from_stray_markup(self):
        # Mirrors the csp-test case: heading + code/link noise, then a topic
        # sentence — the sentence must be its own clean chunk.
        text = "# H\n\n```\n\n[[x]]\n\nIch weiß was über den Planeten Neptun.\n"
        chunks = ss.chunk_markdown(text, "/n.md")
        texts = [c.text for c in chunks]
        self.assertIn("Ich weiß was über den Planeten Neptun.", texts)

    def test_skips_frontmatter(self):
        text = "---\ntitle: x\ntags: [a]\n---\n\nbody line here\n"
        chunks = ss.chunk_markdown(text, "/n.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "body line here")
        self.assertEqual(chunks[0].line, 6)

    def test_splits_oversized_block(self):
        text = "word " * 600  # ~3000 chars, single block, no headings
        chunks = ss.chunk_markdown(text, "/n.md")
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(c.text) <= ss._MAX_CHARS for c in chunks))

    def test_empty_text(self):
        self.assertEqual(ss.chunk_markdown(""), [])


class TestOllamaPrefixes(unittest.TestCase):
    def test_nomic_gets_task_prefixes(self):
        e = ss.OllamaEmbedder("nomic-embed-text")
        self.assertEqual(e._prep(["hi"], is_query=False), ["search_document: hi"])
        self.assertEqual(e._prep(["hi"], is_query=True), ["search_query: hi"])

    def test_non_nomic_gets_no_prefix(self):
        e = ss.OllamaEmbedder("mxbai-embed-large")
        self.assertEqual(e._prep(["hi"], is_query=True), ["hi"])
        self.assertEqual(e._prep(["hi"], is_query=False), ["hi"])


class TestOnnxMeanPool(unittest.TestCase):
    def test_masked_mean_ignores_padding(self):
        import numpy as np
        # 1 sentence, 3 tokens, hidden=2; last token is padding (mask 0).
        emb = np.array([[[1.0, 1.0], [3.0, 3.0], [99.0, 99.0]]], dtype="float32")
        mask = np.array([[1, 1, 0]], dtype="int64")
        pooled = ss.OnnxEmbedder._mean_pool(emb, mask)
        # mean of the two real tokens (1 and 3) = 2, padding ignored.
        self.assertEqual(pooled.tolist(), [[2.0, 2.0]])

    def test_all_padding_does_not_divide_by_zero(self):
        import numpy as np
        emb = np.zeros((1, 2, 2), dtype="float32")
        mask = np.zeros((1, 2), dtype="int64")
        pooled = ss.OnnxEmbedder._mean_pool(emb, mask)
        self.assertEqual(pooled.tolist(), [[0.0, 0.0]])


class TestVectorIndex(unittest.TestCase):
    def _index(self):
        idx = ss.VectorIndex(_StubEmbedder())
        idx.build([
            ss.Chunk("/a.md", 1, "alpha alpha topic"),
            ss.Chunk("/b.md", 1, "beta subject"),
            ss.Chunk("/c.md", 1, "gamma delta notes"),
        ])
        return idx

    def test_query_ranks_closest_first(self):
        idx = self._index()
        top = idx.query("beta", top_k=3)
        self.assertEqual(top[0][0].path, "/b.md")
        self.assertGreater(top[0][1], top[1][1])  # strictly best

    def test_empty_index_returns_nothing(self):
        idx = ss.VectorIndex(_StubEmbedder())
        idx.build([])
        self.assertEqual(idx.query("alpha"), [])
        self.assertEqual(len(idx), 0)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self._index().query(""), [])

    def test_len_reflects_chunks(self):
        self.assertEqual(len(self._index()), 3)


class TestSemanticProvider(unittest.TestCase):
    def _provider(self, min_score=0.3):
        idx = ss.VectorIndex(_StubEmbedder())
        idx.build([
            ss.Chunk("/vault/alpha-note.md", 1, "alpha alpha"),
            ss.Chunk("/vault/alpha-note.md", 5, "alpha again"),  # same file
            ss.Chunk("/vault/beta-note.md", 1, "beta"),
        ])
        return ss.SemanticProvider(idx, min_score=min_score)

    def test_returns_quickresults_deduped_by_file(self):
        res = self._provider().search("alpha", limit=10)
        paths = [r.path for r in res]
        self.assertEqual(paths.count("/vault/alpha-note.md"), 1)  # deduped
        self.assertEqual(res[0].path, "/vault/alpha-note.md")
        self.assertEqual(res[0].name, "alpha-note")  # .md stripped
        self.assertEqual(res[0].source, "semantic")

    def test_min_score_filters_unrelated(self):
        # "gamma" has cosine 0 with every alpha/beta chunk → filtered out.
        self.assertEqual(self._provider(min_score=0.3).search("gamma"), [])

    def test_empty_query(self):
        self.assertEqual(self._provider().search(""), [])


if __name__ == "__main__":
    unittest.main()
