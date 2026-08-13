"""Tests for llama_runtime: availability checks, chat wrapping, model cache.

No real model or the llama_cpp binding is needed — the availability check is
driven via a monkeypatched is_available, and chat/caching use injected stubs.
"""

import tempfile
import unittest

from markdown_vault import llama_runtime as L


class _StubModel:
    def __init__(self, reply="hi"):
        self.reply = reply
        self.calls = []

    def create_chat_completion(self, messages, temperature, stream=False,
                               **kwargs):
        self.calls.append((messages, temperature))
        self.kwargs = kwargs
        if not stream:
            return {"choices": [{"message": {"content": self.reply}}]}

        def gen():
            yield {"choices": [{"delta": {"role": "assistant"}}]}   # no content
            for ch in self.reply:
                yield {"choices": [{"delta": {"content": ch}}]}
        return gen()


class TestAvailability(unittest.TestCase):
    def setUp(self):
        self._orig = L.is_available
        self.addCleanup(lambda: setattr(L, "is_available", self._orig))

    def test_reports_missing_binding(self):
        L.is_available = lambda: False
        self.assertIn("llama-cpp-python", L.availability("/whatever.gguf"))

    def test_reports_missing_model_file(self):
        L.is_available = lambda: True
        self.assertIn("No local model file", L.availability("/no/such.gguf"))

    def test_available_when_binding_and_valid_gguf_present(self):
        L.is_available = lambda: True
        with tempfile.NamedTemporaryFile(suffix=".gguf") as f:
            f.write(b"GGUF\x00\x00\x00\x00")
            f.flush()
            self.assertIsNone(L.availability(f.name))

    def test_reports_non_gguf_file(self):
        L.is_available = lambda: True
        with tempfile.NamedTemporaryFile(suffix=".gguf") as f:
            f.write(b"<!DOCTYPE html>")
            f.flush()
            self.assertIn("not a valid GGUF", L.availability(f.name))


class TestChat(unittest.TestCase):
    def test_builds_messages_and_returns_stripped_content(self):
        stub = _StubModel("  answer [1]  ")
        chat = L.LlamaCppChat("/x.gguf", num_ctx=4096, temperature=0.2,
                              _model=stub)
        self.assertEqual(chat.chat("SYS", "USER"), "answer [1]")
        messages, temperature = stub.calls[0]
        self.assertEqual(temperature, 0.2)
        self.assertEqual(messages[0], {"role": "system", "content": "SYS"})
        self.assertEqual(messages[1], {"role": "user", "content": "USER"})

    def test_streams_tokens_and_fires_reading_phase(self):
        phases, tokens = [], []
        chat = L.LlamaCppChat("/x", _model=_StubModel("hi there"),
                              on_phase=phases.append, on_token=tokens.append)
        self.assertEqual(chat.chat("s", "u"), "hi there")
        self.assertEqual("".join(tokens), "hi there")   # streamed piece by piece
        self.assertIn("reading", phases)                # prefill phase
        self.assertNotIn("writing", phases)             # live text replaces it

    def test_max_tokens_and_repeat_penalty_bound_the_generation(self):
        stub = _StubModel("x")
        L.LlamaCppChat("/x", max_tokens=256, repeat_penalty=1.4,
                       _model=stub).chat("s", "u")
        self.assertEqual(stub.kwargs["max_tokens"], 256)
        self.assertEqual(stub.kwargs["repeat_penalty"], 1.4)

    def test_tolerates_empty_and_malformed_reply(self):
        self.assertEqual(L.LlamaCppChat("/x", _model=_StubModel("")).chat("s", "u"),
                         "")
        broken = type("M", (), {"create_chat_completion":
                                lambda self, **kw: {}})()
        self.assertEqual(L.LlamaCppChat("/x", _model=broken).chat("s", "u"), "")


class TestCapabilities(unittest.TestCase):
    def test_supports_gpu_returns_bool(self):
        self.assertIsInstance(L.supports_gpu(), bool)

    def test_physical_cores_is_positive(self):
        self.assertGreaterEqual(L.physical_cores(), 1)

    def test_default_threads_is_half_physical_at_least_one(self):
        import os
        t = L.default_threads()
        self.assertGreaterEqual(t, 1)
        self.assertLessEqual(t, os.cpu_count() or 2)
        self.assertEqual(t, max(1, L.physical_cores() // 2))


class TestLogging(unittest.TestCase):
    def test_install_logging_is_safe_without_binding(self):
        # no llama_cpp in CI → no-op, never raises; idempotent
        L._LOG_INSTALLED = False
        L._install_llama_logging()
        L._install_llama_logging()

    def test_dedicated_llama_logger(self):
        from markdown_vault import logging_setup
        lg = logging_setup.get_llama_logger()
        self.assertEqual(lg.name, "markdown_vault.llama")
        self.assertFalse(lg.propagate)          # kept out of the main app log
        self.assertTrue(lg.handlers)            # writes to its own file


class _RaisingModel:
    def create_chat_completion(self, **kw):
        raise RuntimeError("decode aborted")


class TestCancellation(unittest.TestCase):
    def test_chat_returns_empty_when_cancelled(self):
        chat = L.LlamaCppChat("/x", should_cancel=lambda: True,
                              _model=_RaisingModel())
        self.assertEqual(chat.chat("s", "u"), "")   # aborted → discarded quietly

    def test_chat_reraises_a_real_error(self):
        chat = L.LlamaCppChat("/x", should_cancel=lambda: False,
                              _model=_RaisingModel())
        with self.assertRaises(RuntimeError):
            chat.chat("s", "u")

    def test_abort_predicate_reads_the_holder(self):
        L._ABORT_HOLDER["fn"] = lambda: True
        try:
            self.assertTrue(L._abort_predicate())
        finally:
            L._ABORT_HOLDER["fn"] = None
        self.assertFalse(L._abort_predicate())      # no predicate → never aborts


class TestGpuRecommendation(unittest.TestCase):
    def test_vram_bytes_is_int_or_none(self):
        self.assertIsInstance(L.vram_bytes(), (int, type(None)))

    def test_is_amd_gpu_returns_bool(self):
        self.assertIsInstance(L.is_amd_gpu(), bool)

    def test_flash_attn_risky_only_for_amd_shared_memory(self):
        oa, ov, og = L.is_amd_gpu, L.vram_bytes, L.gtt_bytes
        L.vram_bytes = lambda: 2 * 1024 ** 3
        L.gtt_bytes = lambda: 14 * 1024 ** 3            # shared (gtt >> vram)
        try:
            L.is_amd_gpu = lambda: True
            self.assertTrue(L.flash_attn_risky())       # AMD + shared iGPU
            L.is_amd_gpu = lambda: False
            self.assertFalse(L.flash_attn_risky())      # not AMD → no warning
            L.is_amd_gpu = lambda: True
            L.gtt_bytes = lambda: 1 * 1024 ** 3         # dedicated (gtt < vram)
            self.assertFalse(L.flash_attn_risky())      # AMD but dedicated → fine
        finally:
            L.is_amd_gpu, L.vram_bytes, L.gtt_bytes = oa, ov, og

    def test_recommended_layers_estimate(self):
        import os
        import tempfile
        from markdown_vault import config
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.write(b"x" * 800)      # 800-byte "model", 10 layers → 80 B/layer
        f.close()
        ov, ol = L.vram_bytes, config.gguf_n_layers
        L.vram_bytes = lambda: 1000            # reserve = 500 → usable 500
        config.gguf_n_layers = lambda p: 10
        try:
            self.assertEqual(L.recommended_gpu_layers(f.name), 6)   # 500 // 80
        finally:
            L.vram_bytes, config.gguf_n_layers = ov, ol
            os.unlink(f.name)

    def test_advice_shared_memory_is_all_or_nothing(self):
        import os
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.write(b"x" * 100)
        f.close()
        ov, og = L.vram_bytes, L.gtt_bytes
        L.vram_bytes = lambda: 2 * 1024 ** 3
        L.gtt_bytes = lambda: 14 * 1024 ** 3        # gtt >> vram → APU
        try:
            advice = L.gpu_layers_advice(f.name)
            self.assertIn("Shared-memory", advice)
            self.assertIn("999", advice)
            self.assertIn("0", advice)
            self.assertNotIn("layers fit", advice)   # never a partial count
        finally:
            L.vram_bytes, L.gtt_bytes = ov, og
            os.unlink(f.name)

    def test_advice_dedicated_gpu_that_fits(self):
        import os
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        f.write(b"x" * 100)
        f.close()
        ov, og = L.vram_bytes, L.gtt_bytes
        L.vram_bytes = lambda: 8 * 1024 ** 3
        L.gtt_bytes = lambda: 1 * 1024 ** 3          # gtt < vram → dedicated
        try:
            self.assertIn("Fits", L.gpu_layers_advice(f.name))
        finally:
            L.vram_bytes, L.gtt_bytes = ov, og
            os.unlink(f.name)

    def test_recommended_layers_none_without_vram(self):
        from markdown_vault import config
        ov, ol = L.vram_bytes, config.gguf_n_layers
        L.vram_bytes = lambda: None
        config.gguf_n_layers = lambda p: 10
        try:
            self.assertIsNone(L.recommended_gpu_layers("/whatever.gguf"))
        finally:
            L.vram_bytes, config.gguf_n_layers = ov, ol


class TestKvModes(unittest.TestCase):
    def test_kv_ggml_mapping(self):
        self.assertEqual(L._KV_GGML.get("q8_0"), "GGML_TYPE_Q8_0")
        self.assertEqual(L._KV_GGML.get("q4_0"), "GGML_TYPE_Q4_0")
        self.assertIsNone(L._KV_GGML.get("f16"))        # f16 = llama.cpp default

    def test_kv_needs_flash_is_driven_by_v(self):
        self.assertFalse(L.kv_needs_flash("f16"))       # V=f16 → no flash
        self.assertTrue(L.kv_needs_flash("q8_0"))       # V quantized → flash
        self.assertTrue(L.kv_needs_flash("q4_0"))


class TestModelCache(unittest.TestCase):
    def setUp(self):
        L._MODEL = None
        L._MODEL_KEY = None
        self._orig = L._load
        self.addCleanup(self._reset)

    def _reset(self):
        L._load = self._orig
        L._MODEL = None
        L._MODEL_KEY = None

    def _key(self, n_threads=0, tk="f16", tv="f16", flash=False, offload=True,
             mmap=True):
        return ("/m.gguf", 8192, 0, n_threads, tk, tv, flash, offload, mmap)

    def test_thread_cap_applies_to_prefill_too(self):
        # n_threads alone caps only generation; the prompt-processing burst is
        # governed by n_threads_batch, so a cap must set both or it does nothing.
        kw = L._llama_kwargs(self._key(n_threads=4))
        self.assertEqual(kw["n_threads"], 4)
        self.assertEqual(kw["n_threads_batch"], 4)

    def test_auto_threads_leave_both_pools_unset(self):
        kw = L._llama_kwargs(self._key(n_threads=0))
        self.assertNotIn("n_threads", kw)
        self.assertNotIn("n_threads_batch", kw)

    def test_flash_offload_mmap_flow_into_kwargs(self):
        kw = L._llama_kwargs(self._key(flash=True, offload=False, mmap=False))
        self.assertTrue(kw["flash_attn"])
        self.assertFalse(kw["offload_kqv"])
        self.assertFalse(kw["use_mmap"])
        # KV type isn't resolved here (needs the binding) — no type_k in kwargs.
        self.assertNotIn("type_k", kw)

    def test_caches_and_reloads_only_on_key_change(self):
        loads = []
        L._load = lambda key: loads.append(key) or ("model", key)
        m1 = L.get_model("/a.gguf", 4096)
        m2 = L.get_model("/a.gguf", 4096)     # same key → cached, no reload
        self.assertIs(m1, m2)
        L.get_model("/a.gguf", 8192)          # different n_ctx → reload
        self.assertEqual(len(loads), 2)


if __name__ == "__main__":
    unittest.main()
