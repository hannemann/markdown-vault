"""Tests for markdown_vault.search.semantic_search (Phase 5 core, backend-agnostic)."""

import unittest
import urllib.error

from markdown_vault.search import semantic_search as ss


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

    def test_markup_only_block_is_dropped(self):
        # A lone horizontal rule carries no meaning and must not become a chunk.
        text = "----------------------------------\n\nreal content here.\n"
        chunks = ss.chunk_markdown(text, "/n.md")
        self.assertEqual([c.text for c in chunks], ["real content here."])


class TestOllamaPrefixes(unittest.TestCase):
    def test_nomic_gets_task_prefixes(self):
        e = ss.OllamaEmbedder("nomic-embed-text")
        self.assertEqual(e._prep(["hi"], is_query=False), ["search_document: hi"])
        self.assertEqual(e._prep(["hi"], is_query=True), ["search_query: hi"])

    def test_non_nomic_gets_no_prefix(self):
        e = ss.OllamaEmbedder("mxbai-embed-large")
        self.assertEqual(e._prep(["hi"], is_query=True), ["hi"])
        self.assertEqual(e._prep(["hi"], is_query=False), ["hi"])


class TestOllamaEndpointFallback(unittest.TestCase):
    """R24.1: fall back to the legacy endpoint only for a missing endpoint —
    a timeout or connection error must propagate, not trigger a per-prompt pass."""

    def _embedder(self):
        return ss.OllamaEmbedder("test-model", "http://localhost:11434")

    @staticmethod
    def _raise(exc):
        def _boom(_texts):
            raise exc
        return _boom

    def test_falls_back_on_404(self):
        e = self._embedder()
        e._embed_batch = self._raise(
            urllib.error.HTTPError("u", 404, "not found", {}, None))
        called = []
        e._embed_singular = lambda texts: called.append(texts) or [[1.0]]
        self.assertEqual(e.embed(["hi"]), [[1.0]])
        self.assertTrue(called)

    def test_timeout_propagates_without_fallback(self):
        e = self._embedder()
        e._embed_batch = self._raise(TimeoutError("hung"))
        e._embed_singular = lambda texts: [[9.0]]  # must NOT run
        with self.assertRaises(TimeoutError):
            e.embed(["hi"])

    def test_non_404_http_error_propagates(self):
        e = self._embedder()
        e._embed_batch = self._raise(
            urllib.error.HTTPError("u", 500, "server error", {}, None))
        e._embed_singular = lambda texts: [[9.0]]  # must NOT run
        with self.assertRaises(urllib.error.HTTPError):
            e.embed(["hi"])


class TestOpenAIEmbedder(unittest.TestCase):
    """The OpenAI-compatible embedding backend: POST /v1/embeddings."""

    def test_posts_model_and_input_and_parses_data(self):
        e = ss.OpenAIEmbedder("my-model", "http://h:8080")
        seen = {}

        def fake_post(payload):
            seen.update(payload)
            return {"data": [{"embedding": [1.0, 2.0]}]}

        e._post = fake_post
        self.assertEqual(e.embed(["hi"]), [[1.0, 2.0]])
        self.assertEqual(seen, {"model": "my-model", "input": ["hi"]})

    def test_is_query_is_ignored(self):
        # The OpenAI embeddings API has no task-prefix concept — the input is
        # sent verbatim whether it is a query or a passage.
        e = ss.OpenAIEmbedder("m", "http://h:8080")
        seen = {}
        e._post = lambda p: seen.update(p) or {"data": [{"embedding": [0.0]}]}
        e.embed(["hi"], is_query=True)
        self.assertEqual(seen["input"], ["hi"])

    def test_batches_by_batch_size(self):
        e = ss.OpenAIEmbedder("m", "http://h:8080", batch=2)
        calls = []

        def fake_post(payload):
            calls.append(list(payload["input"]))
            return {"data": [{"embedding": [1.0]} for _ in payload["input"]]}

        e._post = fake_post
        out = e.embed(["a", "b", "c"])
        self.assertEqual(len(out), 3)
        self.assertEqual(calls, [["a", "b"], ["c"]])  # 2 + 1, not one request

    def test_wrong_count_raises(self):
        e = ss.OpenAIEmbedder("m", "http://h:8080")
        e._post = lambda p: {"data": [{"embedding": [1.0]}]}  # one for two inputs
        with self.assertRaises(ValueError):
            e.embed(["a", "b"])

    def test_url_is_normalised_and_endpoint_appended(self):
        import unittest.mock as m
        # A base spelled with a trailing /v1 must not become /v1/v1/embeddings.
        e = ss.OpenAIEmbedder("m", "http://h:8080/v1")
        self.assertEqual(e.url, "http://h:8080")
        with m.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"data": [{"embedding": [1.0]}]}')
            e._post({"model": "m", "input": ["hi"]})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://h:8080/v1/embeddings")

    def test_auth_header_only_with_key(self):
        import unittest.mock as m

        def _capture(api_key):
            e = ss.OpenAIEmbedder("m", "http://h:8080", api_key=api_key)
            with m.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = (
                    b'{"data": []}')
                e._post({"model": "m", "input": []})
            return urlopen.call_args[0][0]

        self.assertEqual(_capture("secret").get_header("Authorization"),
                         "Bearer secret")
        self.assertIsNone(_capture("").get_header("Authorization"))


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

    def test_embed_query_none_on_empty(self):
        self.assertIsNone(self._index().embed_query(""))

    def test_search_vector_uses_precomputed_query(self):
        idx = self._index()
        q = idx.embed_query("beta")
        hits = idx.search_vector(q, top_k=3)
        self.assertEqual(hits[0][0].path, "/b.md")

    def test_len_reflects_chunks(self):
        self.assertEqual(len(self._index()), 3)


class TestBuildEmbedder(unittest.TestCase):
    """The window-facing factory: backend choice + cache signature, no window."""

    def test_ollama_backend(self):
        emb, tag = ss.build_embedder(
            {"semantic_backend": "ollama", "semantic_ollama_model": "nomic",
             "semantic_ollama_url": "http://h:11434"})
        self.assertIsInstance(emb, ss.OllamaEmbedder)
        self.assertEqual(tag, "ollama:nomic")

    def test_onnx_backend_loads_from_the_dir_but_the_signature_drops_it(self):
        import unittest.mock as m
        with m.patch.object(ss, "OnnxEmbedder") as OE:
            emb, tag = ss.build_embedder(
                {"semantic_backend": "onnx", "semantic_onnx_dir": "/models/x"})
        self.assertIs(emb, OE.return_value)
        self.assertTrue(OE.call_args[0][0].startswith("/models/x"))  # embedder loads it
        self.assertTrue(tag.startswith("onnx:"))
        self.assertNotIn("/models/x", tag)                           # signature: no dir

    def test_openai_backend_and_normalised_tag(self):
        import unittest.mock as m
        with m.patch("markdown_vault.core.secret_store.get_secret", return_value=""):
            emb, tag = ss.build_embedder(
                {"semantic_backend": "openai",
                 "semantic_openai_url": "http://h:8080/v1",   # cosmetic /v1
                 "semantic_openai_model": "bge-m3"})
        self.assertIsInstance(emb, ss.OpenAIEmbedder)
        # D3: base URL in the tag but normalised — a cosmetic /v1 must not change it,
        # so it does not force a rebuild.
        self.assertEqual(tag, "openai:http://h:8080|bge-m3")

    def test_openai_key_comes_from_the_endpoint_keyring_name(self):
        # D2: the key is read from an endpoint-scoped, embedding-own keyring name
        # (never the Ask entry) and handed to the embedder.
        import unittest.mock as m
        with m.patch("markdown_vault.core.secret_store.get_secret",
                     return_value="sk-emb") as gs:
            emb, _ = ss.build_embedder(
                {"semantic_backend": "openai",
                 "semantic_openai_url": "http://h:8080", "semantic_openai_model": "m"})
        gs.assert_called_once_with("semantic_api_key:openai|http://h:8080")
        self.assertEqual(emb.api_key, "sk-emb")


if __name__ == "__main__":
    unittest.main()
