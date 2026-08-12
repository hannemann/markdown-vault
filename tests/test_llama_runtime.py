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

    def create_chat_completion(self, messages, temperature):
        self.calls.append((messages, temperature))
        return {"choices": [{"message": {"content": self.reply}}]}


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

    def test_thread_cap_applies_to_prefill_too(self):
        # n_threads alone caps only generation; the prompt-processing burst is
        # governed by n_threads_batch, so a cap must set both or it does nothing.
        kw = L._llama_kwargs(("/m.gguf", 8192, 0, 4))
        self.assertEqual(kw["n_threads"], 4)
        self.assertEqual(kw["n_threads_batch"], 4)

    def test_auto_threads_leave_both_pools_unset(self):
        kw = L._llama_kwargs(("/m.gguf", 8192, 0, 0))
        self.assertNotIn("n_threads", kw)
        self.assertNotIn("n_threads_batch", kw)

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
