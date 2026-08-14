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


class _ReasoningModel:
    """A reasoning model: its own chat template understands ``enable_thinking``,
    so LlamaCppChat renders the prompt itself and streams ``create_completion``
    (raw ``text`` chunks). The tiny template opens ``<think>`` when thinking is on
    and closes it empty when off — exactly how a real reasoning template gates it.
    """
    metadata = {"tokenizer.chat_template":
                "{% for m in messages %}<{{ m.role }}>{{ m.content }}"
                "{% endfor %}<assistant>"
                "{% if enable_thinking is defined and enable_thinking %}<think>"
                "{% else %}<think></think>{% endif %}"}

    def __init__(self, reply="ok"):
        self.reply = reply
        self.prompt = None
        self.kwargs = None

    def token_eos(self):
        return 0

    def token_bos(self):
        return 1

    def detokenize(self, tokens):
        return b""

    def create_completion(self, prompt, stream=False, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs

        def gen():
            for ch in self.reply:
                yield {"choices": [{"text": ch}]}
        return gen()


class _PrefillThinkModel:
    """A DeepSeek-R1-distill-style model: its chat template prefills an open
    ``<think>`` and has no ``enable_thinking`` switch, so generation starts inside
    a think block with no opening tag in the output. Uses create_chat_completion
    (the think=None production path)."""
    metadata = {"tokenizer.chat_template":
                "{% for m in messages %}<{{ m.role }}>{{ m.content }}"
                "{% endfor %}<assistant><think>\n"}

    def __init__(self, reply="reasoning</think>Answer", finish_reason="length"):
        self.reply = reply
        self.finish_reason = finish_reason

    def create_chat_completion(self, messages, temperature, stream=False, **kwargs):
        def gen():
            for ch in self.reply:
                yield {"choices": [{"delta": {"content": ch}}]}
            yield {"choices": [{"delta": {}, "finish_reason": self.finish_reason}]}
        return gen()


class _SelfThinkModel:
    """A Qwen3-style reasoning model: its template has an ``enable_thinking`` switch
    (so hide_until_close is false), and the model emits its OWN ``<think>`` opening
    tag in the output. Uses create_chat_completion (the think=None production
    path)."""
    metadata = {"tokenizer.chat_template":
                "chat template with an enable_thinking switch and <think>"}

    def __init__(self, reply="<think>still weighing", finish_reason="length"):
        self.reply = reply
        self.finish_reason = finish_reason

    def create_chat_completion(self, messages, temperature, stream=False, **kwargs):
        def gen():
            for ch in self.reply:
                yield {"choices": [{"delta": {"content": ch}}]}
            yield {"choices": [{"delta": {}, "finish_reason": self.finish_reason}]}
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
        self.assertEqual(tokens[-1], "hi there")        # full visible text each step
        self.assertIn("reading", phases)                # prefill phase
        self.assertNotIn("writing", phases)             # live text replaces it

    @unittest.skipUnless(L.is_available(), "needs llama_cpp (make install-ai)")
    def test_reasoning_off_renders_enable_thinking_false(self):
        # Reasoning off → the template renders an empty <think></think>, so the
        # model never thinks; the raw prompt goes to create_completion.
        stub = _ReasoningModel("ok")
        L.LlamaCppChat("/x", think=False, _model=stub).chat("s", "u")
        self.assertIn("<think></think>", stub.prompt)

    @unittest.skipUnless(L.is_available(), "needs llama_cpp (make install-ai)")
    def test_reasoning_on_renders_enable_thinking_true(self):
        # Reasoning on → the template opens <think>, so the model reasons. The
        # reply closes the block so it isn't flagged as budget-exhausted.
        stub = _ReasoningModel("r</think>ok")
        L.LlamaCppChat("/x", think=True, _model=stub).chat("s", "u")
        self.assertTrue(stub.prompt.endswith("<assistant><think>"))

    @unittest.skipUnless(L.is_available(), "needs llama_cpp (make install-ai)")
    def test_reasoning_answer_is_text_after_close_think(self):
        # create_completion streams reasoning then the answer; only the text after
        # the final </think> is the grounded answer.
        stub = _ReasoningModel("weighing it up</think>Real answer")
        out = L.LlamaCppChat("/x", think=True, _model=stub).chat("s", "u")
        self.assertEqual(out, "Real answer")

    def test_on_token_carries_full_visible_text_not_a_broken_delta(self):
        # A preamble before a <think> block used to splice the live stream into
        # "Sure. s red." (a char-delta over a shrinking string). on_token must
        # carry the FULL visible text each time so the consumer can replace it.
        tokens = []
        out = L.LlamaCppChat(
            "/x", _model=_StubModel("Sure. <think>hmm</think>Mars is red."),
            on_token=tokens.append).chat("s", "u")
        self.assertEqual(out, "Mars is red.")
        self.assertEqual(tokens[-1], "Mars is red.")   # final streamed == answer

    @unittest.skipUnless(L.is_available(), "needs llama_cpp (make install-ai)")
    def test_on_token_hides_reasoning_for_prefilled_think(self):
        # A template that prefills <think> emits reasoning with no opening tag; the
        # live stream must stay suppressed until </think>, so the chain of thought
        # is never shown — only the answer after it.
        tokens = []
        out = L.LlamaCppChat(
            "/x", think=True,
            _model=_ReasoningModel("weighing it up</think>Real answer"),
            on_token=tokens.append).chat("s", "u")
        self.assertEqual(out, "Real answer")
        self.assertEqual(tokens[-1], "Real answer")
        self.assertNotIn("weighing", "".join(tokens))   # reasoning never streamed

    def test_on_token_hides_reasoning_on_production_path(self):
        # The real leak: a prefilled-<think>, no-enable_thinking template on the
        # app's reasoning-on path (think=None) — the stream must stay suppressed
        # until </think>, keyed on the template, not on think.
        tokens = []
        out = L.LlamaCppChat(
            "/x", _model=_PrefillThinkModel("weighing 4 vs 95</think>95 moons."),
            on_token=tokens.append).chat("s", "u")
        self.assertEqual(out, "95 moons.")
        self.assertEqual(tokens[-1], "95 moons.")
        self.assertNotIn("weighing", "".join(tokens))   # reasoning never streamed

    def test_reasoning_that_never_closes_raises_budget_exhausted(self):
        # A prefilled-<think> model that hits max_tokens still inside the block
        # (no </think>, finish_reason='length') must signal exhaustion, not hand
        # back its raw reasoning.
        model = _PrefillThinkModel("The note says 4 moons but I should reconsider",
                                   finish_reason="length")
        with self.assertRaises(L.ReasoningBudgetExhausted):
            L.LlamaCppChat("/x", _model=model).chat("s", "u")

    def test_no_close_tag_but_finished_is_not_exhaustion(self):
        # False-positive guard (R56.2): a template with a literal <think> but a
        # model that finishes normally (finish_reason='stop') with no </think> is a
        # good answer, not exhaustion — return it, don't raise.
        model = _PrefillThinkModel("Jupiter has 95 moons.", finish_reason="stop")
        out = L.LlamaCppChat("/x", _model=model).chat("s", "u")
        self.assertEqual(out, "Jupiter has 95 moons.")

    def test_self_emitted_think_that_runs_out_raises_budget_exhausted(self):
        # R57.1: a Qwen3-style model emits its own <think> and hits max_tokens
        # before closing it. hide_until_close is false, but _visible_text is empty
        # and finish_reason='length' → exhaustion, not "(empty answer)".
        model = _SelfThinkModel("<think>still weighing 4 vs 95",
                                finish_reason="length")
        with self.assertRaises(L.ReasoningBudgetExhausted):
            L.LlamaCppChat("/x", _model=model).chat("s", "u")

    def test_self_emitted_think_with_answer_is_not_exhaustion(self):
        # A truncated but present answer after the model's own </think> is still an
        # answer (partial), not exhaustion.
        model = _SelfThinkModel("<think>reasoning</think>Answer",
                                finish_reason="length")
        out = L.LlamaCppChat("/x", _model=model).chat("s", "u")
        self.assertEqual(out, "Answer")

    def test_chat_serializes_generations_on_the_shared_context(self):
        # R52.2: two in-flight questions must not drive the one cached Llama
        # context at once. _CHAT_LOCK serializes chat(), so peak concurrency is 1.
        import threading
        import time
        state = {"n": 0, "max": 0}
        guard = threading.Lock()

        class _SlowModel:
            def create_chat_completion(self, messages, temperature,
                                       stream=False, **kw):
                def gen():
                    with guard:
                        state["n"] += 1
                        state["max"] = max(state["max"], state["n"])
                    time.sleep(0.03)
                    yield {"choices": [{"delta": {"content": "x"}}]}
                    with guard:
                        state["n"] -= 1
                return gen()

        model = _SlowModel()
        threads = [threading.Thread(
            target=lambda: L.LlamaCppChat("/x", _model=model).chat("s", "u"))
            for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(state["max"], 1)

    def test_reasoning_default_uses_plain_chat_completion(self):
        # think=None → no preference → the proven create_chat_completion path.
        stub = _StubModel("ok")
        L.LlamaCppChat("/x", think=None, _model=stub).chat("s", "USER")
        self.assertTrue(stub.calls)
        self.assertEqual(stub.calls[0][0][1]["content"], "USER")

    def test_non_reasoning_model_keeps_plain_chat_completion(self):
        # A model without enable_thinking in its template is never routed through
        # the formatter, even with a thinking preference set.
        stub = _StubModel("ok")
        L.LlamaCppChat("/x", think=False, _model=stub).chat("s", "u")
        self.assertTrue(stub.calls)

    def test_think_block_is_stripped_and_never_streamed(self):
        tokens = []
        out = L.LlamaCppChat(
            "/x", _model=_StubModel("<think>secret reasoning</think>Final answer"),
            on_token=tokens.append).chat("s", "u")
        self.assertEqual(out, "Final answer")
        self.assertEqual(tokens[-1], "Final answer")        # final streamed == answer
        self.assertNotIn("secret", "".join(tokens))         # reasoning never shown

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
             mmap=True, n_batch=0, n_ubatch=0):
        return ("/m.gguf", 8192, 0, n_threads, tk, tv, flash, offload, mmap,
                n_batch, n_ubatch)

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

    def test_batch_and_ubatch_flow_independently(self):
        kw = L._llama_kwargs(self._key(n_batch=2048, n_ubatch=1024))
        self.assertEqual(kw["n_batch"], 2048)
        self.assertEqual(kw["n_ubatch"], 1024)

    def test_batch_alone_leaves_ubatch_default(self):
        kw = L._llama_kwargs(self._key(n_batch=2048))
        self.assertEqual(kw["n_batch"], 2048)
        self.assertNotIn("n_ubatch", kw)          # 0 → llama.cpp default

    def test_zero_batch_keeps_llama_default(self):
        # 0 means "don't set it" → llama.cpp's own defaults (2048 / 512), never a
        # literal batch of 0.
        kw = L._llama_kwargs(self._key(n_batch=0, n_ubatch=0))
        self.assertNotIn("n_batch", kw)
        self.assertNotIn("n_ubatch", kw)

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
